//! Strict ENDF-6 MF=33 parsing, canonical covariance sidecars and spectrum collapse.
//!
//! The activation library remains the stable P5/P10 wire format. Covariance data live in a separate
//! `actinv-covariance-1` NPZ so an ordinary run neither reads nor allocates uncertainty data.

use crate::endf::{parse_sections, read_cont_checked, read_list_checked, ContRecord};
use crate::library::{
    ensure_eof, read_f64_values, read_i64, read_npy_header, temporary_sibling, write_npy_header,
    Library, NpyDtype,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::ffi::OsStr;
use std::io::{BufReader, Read, Seek, Write};
use std::path::{Path, PathBuf};

const COMPONENT_COLUMNS: usize = 9;
const MAX_ARRAY_BYTES: u64 = 1_000_000_000;
// Bump when parsing, target mapping or per-source sidecar storage changes. Aggregation,
// reporting and CLI-only edits deliberately do not invalidate every target checkpoint.
const COVARIANCE_CHECKPOINT_FINGERPRINT: &str =
    "24122d5e4a0de89f31228ae9407f12ac7adaf5ea690217097d362ad945fdcdb6";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[repr(i64)]
pub enum ComponentKind {
    Absolute = 0,
    Relative = 1,
    ShortRange8 = 8,
    ShortRange9 = 9,
}

impl ComponentKind {
    fn parse(value: i64) -> Result<Self, String> {
        match value {
            0 => Ok(Self::Absolute),
            1 => Ok(Self::Relative),
            8 => Ok(Self::ShortRange8),
            9 => Ok(Self::ShortRange9),
            _ => Err(format!("unknown covariance component kind {value}")),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ParsedComponent {
    pub mat: i32,
    pub mt: i32,
    pub mt1: i32,
    pub lb: i32,
    pub kind: ComponentKind,
    pub row_grid: Vec<f64>,
    pub column_grid: Vec<f64>,
    /// Dense row-major interval matrix for `Absolute`/`Relative`; one F per interval for LB=8/9.
    pub values: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StoredComponent {
    pub target: usize,
    pub mt: i32,
    pub mt1: i32,
    pub lb: i32,
    pub kind: ComponentKind,
    pub row_grid: usize,
    pub column_grid: usize,
    pub value_offset: usize,
    pub value_len: usize,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct CovarianceLibrary {
    pub components: Vec<StoredComponent>,
    pub grids: Vec<Vec<f64>>,
    pub values: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CollapsedCovariance {
    /// Activation-library row index for each matrix parameter.
    pub row_indices: Vec<usize>,
    pub one_group_barns: Vec<f64>,
    /// Dense row-major covariance matrix in barn^2.
    pub covariance_barn2: Vec<f64>,
    pub uncovered_rows: Vec<usize>,
    pub absent_cross_parameter_pairs: usize,
    pub maximum_asymmetry_barn2: f64,
}

#[derive(Clone, Debug)]
pub struct CovarianceBuildOptions {
    pub workers: usize,
    pub cache: Option<PathBuf>,
}

#[derive(Clone, Debug)]
pub struct CovarianceBuildSummary {
    pub output: PathBuf,
    pub index: PathBuf,
    pub files: usize,
    pub files_with_mf33: usize,
    pub targets: usize,
    pub sections: usize,
    pub components: usize,
    pub cache_hits: usize,
    pub sha256_npz: String,
    pub builder_fingerprint: String,
}

#[derive(Clone, Debug, Deserialize)]
struct ActivationTarget {
    file: String,
    source_sha256: String,
    mat: i32,
    za: i32,
    liso: i32,
}

#[derive(Clone, Debug, Deserialize)]
struct ActivationIndex {
    schema: String,
    projectile: String,
    group_boundary_sha256: String,
    targets: Vec<ActivationTarget>,
    sha256_npz: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CovarianceTargetIndex {
    target: usize,
    file: String,
    source_sha256: String,
    mat: i32,
    za: i32,
    liso: i32,
    mf33_sections: usize,
    components: usize,
    lb_counts: BTreeMap<i32, usize>,
}

#[derive(Clone, Debug, Serialize)]
struct CovarianceIndex {
    schema: &'static str,
    projectile: &'static str,
    activation_library: String,
    activation_library_sha256: String,
    activation_index: String,
    activation_index_sha256: String,
    group_boundary_sha256: String,
    builder_fingerprint: String,
    source_manifest_sha256: String,
    targets: Vec<CovarianceTargetIndex>,
    files: usize,
    files_with_mf33: usize,
    mf33_sections: usize,
    components: usize,
    lb_counts: BTreeMap<i32, usize>,
    columns: &'static str,
    sha256_npz: String,
}

#[derive(Clone, Debug)]
struct BuiltCovarianceSource {
    library: CovarianceLibrary,
    targets: Vec<CovarianceTargetIndex>,
    sections: usize,
    from_cache: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CacheIndex {
    schema: String,
    key: String,
    source_sha256: String,
    activation_target_identity_sha256: String,
    npz_sha256: String,
    targets: Vec<CovarianceTargetIndex>,
    sections: usize,
}

fn checked_grid(values: Vec<f64>, context: &str) -> Result<Vec<f64>, String> {
    if values.len() < 2 {
        return Err(format!(
            "{context}: covariance grid has fewer than two energies"
        ));
    }
    if values
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!(
            "{context}: covariance energies must be finite and nonnegative"
        ));
    }
    if values.windows(2).any(|pair| pair[1] <= pair[0]) {
        return Err(format!(
            "{context}: covariance energies are not strictly increasing"
        ));
    }
    Ok(values)
}

fn checked_values(values: &[f64], context: &str) -> Result<(), String> {
    if values.iter().any(|value| !value.is_finite()) {
        return Err(format!("{context}: covariance contains a nonfinite value"));
    }
    Ok(())
}

fn pair_table(values: &[f64], pairs: usize, context: &str) -> Result<(Vec<f64>, Vec<f64>), String> {
    if values.len() != 2 * pairs {
        return Err(format!(
            "{context}: pair table has {} values, expected {}",
            values.len(),
            2 * pairs
        ));
    }
    let mut energies = Vec::with_capacity(pairs);
    let mut factors = Vec::with_capacity(pairs.saturating_sub(1));
    for (index, pair) in values.as_chunks::<2>().0.iter().enumerate() {
        energies.push(pair[0]);
        if index + 1 < pairs {
            factors.push(pair[1]);
        } else if pair[1] != 0.0 {
            return Err(format!(
                "{context}: final covariance-table F value must be zero, got {}",
                pair[1]
            ));
        }
    }
    Ok((checked_grid(energies, context)?, factors))
}

fn outer(left: &[f64], right: &[f64]) -> Vec<f64> {
    let mut values = Vec::with_capacity(left.len() * right.len());
    for &a in left {
        values.extend(right.iter().map(|&b| a * b));
    }
    values
}

fn diagonal(values: &[f64]) -> Vec<f64> {
    let mut matrix = vec![0.0; values.len() * values.len()];
    for (index, value) in values.iter().enumerate() {
        matrix[index * values.len() + index] = *value;
    }
    matrix
}

fn interval(grid: &[f64], energy: f64) -> Option<usize> {
    let upper = grid.partition_point(|value| *value <= energy);
    (upper > 0 && upper < grid.len()).then_some(upper - 1)
}

fn union_grid(left: &[f64], right: &[f64], context: &str) -> Result<Vec<f64>, String> {
    let mut bits: BTreeSet<u64> = left
        .iter()
        .chain(right)
        .map(|value| value.to_bits())
        .collect();
    let mut values: Vec<f64> = bits.iter().map(|bits| f64::from_bits(*bits)).collect();
    values.sort_by(|a, b| a.total_cmp(b));
    bits.clear();
    checked_grid(values, context)
}

fn parse_lb_0_to_4(
    mat: i32,
    mt: i32,
    mt1: i32,
    record: &crate::endf::CheckedListRecord,
    context: &str,
) -> Result<ParsedComponent, String> {
    let lb = record.head.l2;
    let lt = usize::try_from(record.head.l1)
        .map_err(|_| format!("{context}: negative LT={}", record.head.l1))?;
    let np = record.head.n2;
    if record.head.n1 != 2 * np || lt >= np {
        return Err(format!(
            "{context}: LB={lb} requires NT=2*NP and LT<NP; NT={}, NP={np}, LT={lt}",
            record.head.n1
        ));
    }
    if lb <= 2 && lt != 0 {
        return Err(format!("{context}: LB={lb} requires LT=0, got {lt}"));
    }
    if lb >= 3 && lt == 0 {
        return Err(format!("{context}: LB={lb} requires a second E-table"));
    }
    let first_pairs = np - lt;
    let split = 2 * first_pairs;
    let (first_grid, first_f) = pair_table(&record.values[..split], first_pairs, context)?;
    let (row_grid, column_grid, values) = match lb {
        0 | 1 => (first_grid.clone(), first_grid, diagonal(&first_f)),
        2 => (first_grid.clone(), first_grid, outer(&first_f, &first_f)),
        3 => {
            let (second_grid, second_f) = pair_table(&record.values[split..], lt, context)?;
            (first_grid, second_grid, outer(&first_f, &second_f))
        }
        4 => {
            let (second_grid, second_f) = pair_table(&record.values[split..], lt, context)?;
            let grid = union_grid(&first_grid, &second_grid, context)?;
            let n = grid.len() - 1;
            let mut matrix = vec![0.0; n * n];
            for row in 0..n {
                let row_energy = grid[row] + (grid[row + 1] - grid[row]) / 2.0;
                let coarse_row = interval(&first_grid, row_energy);
                let fine_row = interval(&second_grid, row_energy);
                for column in 0..n {
                    let column_energy = grid[column] + (grid[column + 1] - grid[column]) / 2.0;
                    let coarse_column = interval(&first_grid, column_energy);
                    let fine_column = interval(&second_grid, column_energy);
                    if let (Some(k), Some(l), Some(k1), Some(l1)) =
                        (coarse_row, fine_row, coarse_column, fine_column)
                    {
                        if k == k1 {
                            matrix[row * n + column] = first_f[k] * second_f[l] * second_f[l1];
                        }
                    }
                }
            }
            (grid.clone(), grid, matrix)
        }
        _ => unreachable!(),
    };
    let kind = if lb == 0 {
        ComponentKind::Absolute
    } else {
        ComponentKind::Relative
    };
    checked_values(&values, context)?;
    Ok(ParsedComponent {
        mat,
        mt,
        mt1,
        lb,
        kind,
        row_grid,
        column_grid,
        values,
    })
}

fn parse_lb_5_or_6(
    mat: i32,
    mt: i32,
    mt1: i32,
    record: &crate::endf::CheckedListRecord,
    context: &str,
) -> Result<ParsedComponent, String> {
    let lb = record.head.l2;
    let nt = record.head.n1;
    if record.values.len() != nt {
        return Err(format!(
            "{context}: LIST payload has {} values, expected NT={nt}",
            record.values.len()
        ));
    }
    let (row_grid, column_grid, values) = if lb == 5 {
        let ne = record.head.n2;
        let intervals = ne
            .checked_sub(1)
            .ok_or_else(|| format!("{context}: LB=5 NE must be positive"))?;
        let ls = record.head.l1;
        let expected_values = match ls {
            0 => intervals
                .checked_mul(intervals)
                .ok_or_else(|| format!("{context}: LB=5 dimensions overflow"))?,
            1 => ne
                .checked_mul(intervals)
                .and_then(|value| value.checked_div(2))
                .ok_or_else(|| format!("{context}: LB=5 dimensions overflow"))?,
            _ => return Err(format!("{context}: LB=5 LS must be 0 or 1, got {ls}")),
        };
        if nt != ne + expected_values {
            return Err(format!(
                "{context}: LB=5 NT={nt}, expected {} for NE={ne}, LS={ls}",
                ne + expected_values
            ));
        }
        let grid = checked_grid(record.values[..ne].to_vec(), context)?;
        let packed = &record.values[ne..];
        let matrix = if ls == 0 {
            packed.to_vec()
        } else {
            let mut matrix = vec![0.0; intervals * intervals];
            let mut next = 0;
            for row in 0..intervals {
                for column in row..intervals {
                    let value = packed[next];
                    next += 1;
                    matrix[row * intervals + column] = value;
                    matrix[column * intervals + row] = value;
                }
            }
            matrix
        };
        (grid.clone(), grid, matrix)
    } else {
        if record.head.l1 != 0 {
            return Err(format!(
                "{context}: LB=6 first integer field must be zero, got {}",
                record.head.l1
            ));
        }
        let ner = record.head.n2;
        if ner < 2 || !(nt - 1).is_multiple_of(ner) {
            return Err(format!(
                "{context}: LB=6 cannot derive NEC from NT={nt}, NER={ner}"
            ));
        }
        let nec = (nt - 1) / ner;
        if nec < 2 {
            return Err(format!("{context}: LB=6 NEC must be at least two"));
        }
        let matrix_len = (ner - 1)
            .checked_mul(nec - 1)
            .ok_or_else(|| format!("{context}: LB=6 dimensions overflow"))?;
        if ner + nec + matrix_len != nt {
            return Err(format!("{context}: inconsistent LB=6 dimensions"));
        }
        let row_grid = checked_grid(record.values[..ner].to_vec(), context)?;
        let column_grid = checked_grid(record.values[ner..ner + nec].to_vec(), context)?;
        (row_grid, column_grid, record.values[ner + nec..].to_vec())
    };
    checked_values(&values, context)?;
    Ok(ParsedComponent {
        mat,
        mt,
        mt1,
        lb,
        kind: ComponentKind::Relative,
        row_grid,
        column_grid,
        values,
    })
}

fn parse_short_range(
    mat: i32,
    mt: i32,
    mt1: i32,
    record: &crate::endf::CheckedListRecord,
    context: &str,
) -> Result<ParsedComponent, String> {
    let lb = record.head.l2;
    if record.head.l1 != 0 || record.head.n1 != 2 * record.head.n2 {
        return Err(format!("{context}: LB={lb} requires LT=0 and NT=2*NP"));
    }
    if mt != mt1 {
        return Err(format!(
            "{context}: short-range LB={lb} is only valid for self-covariance"
        ));
    }
    let (grid, values) = pair_table(&record.values, record.head.n2, context)?;
    checked_values(&values, context)?;
    Ok(ParsedComponent {
        mat,
        mt,
        mt1,
        lb,
        kind: if lb == 8 {
            ComponentKind::ShortRange8
        } else {
            ComponentKind::ShortRange9
        },
        row_grid: grid.clone(),
        column_grid: grid,
        values,
    })
}

fn parse_ni(
    mat: i32,
    mt: i32,
    mt1: i32,
    record: &crate::endf::CheckedListRecord,
) -> Result<ParsedComponent, String> {
    let context = format!("MAT={mat}/MF=33/MT={mt}/MT1={mt1}/LB={}", record.head.l2);
    match record.head.l2 {
        0..=4 => parse_lb_0_to_4(mat, mt, mt1, record, &context),
        5 | 6 => parse_lb_5_or_6(mat, mt, mt1, record, &context),
        8 | 9 => parse_short_range(mat, mt, mt1, record, &context),
        lb => Err(format!("{context}: unsupported MF=33 LB={lb}")),
    }
}

/// Parse every supported MF=33 NI component in an ENDF tape. NC/lumped/foreign references fail closed.
pub fn parse_mf33(text: &str) -> Result<Vec<ParsedComponent>, String> {
    let sections = parse_sections(text)?;
    let mut components = Vec::new();
    for section in sections.iter().filter(|section| section.mf == 33) {
        let context = format!("MAT={}/MF=33/MT={}", section.mat, section.mt);
        let head = ContRecord::parse(
            section
                .lines
                .first()
                .ok_or_else(|| format!("{context}: empty section"))?,
        )?;
        if head.l1 != 0 || head.n1 != 0 {
            return Err(format!("{context}: invalid MF=33 HEAD reserved fields"));
        }
        if head.l2 != 0 {
            return Err(format!(
                "{context}: lumped MTL={} covariance is unsupported",
                head.l2
            ));
        }
        let mut next = 1;
        for subsection in 0..head.n2 {
            let (subhead, after_head) = read_cont_checked(&section.lines, next)
                .map_err(|error| format!("{context}/subsection={}: {error}", subsection + 1))?;
            next = after_head;
            if subhead.c1 != 0.0 || subhead.c2 != 0.0 {
                return Err(format!(
                    "{context}/subsection={}: only File-3 references with XLFS1=0 are supported",
                    subsection + 1
                ));
            }
            if subhead.l1 != 0 && subhead.l1 != section.mat {
                return Err(format!(
                    "{context}/subsection={}: foreign MAT1={} is unsupported",
                    subsection + 1,
                    subhead.l1
                ));
            }
            if subhead.l2 <= 0 {
                return Err(format!(
                    "{context}/subsection={}: MT1 must be positive",
                    subsection + 1
                ));
            }
            if subhead.n1 != 0 {
                return Err(format!(
                    "{context}/subsection={}: NC={} is unsupported",
                    subsection + 1,
                    subhead.n1
                ));
            }
            for ni in 0..subhead.n2 {
                let (record, after) = read_list_checked(&section.lines, next).map_err(|error| {
                    format!("{context}/MT1={}/NI={}: {error}", subhead.l2, ni + 1)
                })?;
                next = after;
                components.push(parse_ni(section.mat, section.mt, subhead.l2, &record)?);
            }
        }
        if next != section.lines.len() {
            return Err(format!(
                "{context}: {} unconsumed record(s)",
                section.lines.len() - next
            ));
        }
    }
    Ok(components)
}

impl CovarianceLibrary {
    pub fn from_parsed(
        components: impl IntoIterator<Item = (usize, ParsedComponent)>,
    ) -> Result<Self, String> {
        let mut library = Self::default();
        let mut grids: BTreeMap<Vec<u64>, usize> = BTreeMap::new();
        for (target, component) in components {
            let mut intern = |grid: Vec<f64>, stored: &mut Vec<Vec<f64>>| {
                let key: Vec<u64> = grid.iter().map(|value| value.to_bits()).collect();
                if let Some(index) = grids.get(&key) {
                    *index
                } else {
                    let index = stored.len();
                    stored.push(grid);
                    grids.insert(key, index);
                    index
                }
            };
            let row_grid = intern(component.row_grid, &mut library.grids);
            let column_grid = intern(component.column_grid, &mut library.grids);
            let value_offset = library.values.len();
            let value_len = component.values.len();
            library.values.extend(component.values);
            library.components.push(StoredComponent {
                target,
                mt: component.mt,
                mt1: component.mt1,
                lb: component.lb,
                kind: component.kind,
                row_grid,
                column_grid,
                value_offset,
                value_len,
            });
        }
        library.validate()?;
        Ok(library)
    }

    pub fn validate(&self) -> Result<(), String> {
        for (index, grid) in self.grids.iter().enumerate() {
            checked_grid(grid.clone(), &format!("covariance grid {index}"))?;
        }
        if self.values.iter().any(|value| !value.is_finite()) {
            return Err("covariance sidecar contains a nonfinite value".into());
        }
        for (index, component) in self.components.iter().enumerate() {
            let expected_kind = match component.lb {
                0 => ComponentKind::Absolute,
                1..=6 => ComponentKind::Relative,
                8 => ComponentKind::ShortRange8,
                9 => ComponentKind::ShortRange9,
                value => {
                    return Err(format!(
                        "component {index} has unsupported covariance representation LB={value}"
                    ));
                }
            };
            if component.kind != expected_kind {
                return Err(format!(
                    "component {index} LB={} has kind {:?}, expected {:?}",
                    component.lb, component.kind, expected_kind
                ));
            }
            let row_grid = self.grids.get(component.row_grid).ok_or_else(|| {
                format!(
                    "component {index} row grid {} is absent",
                    component.row_grid
                )
            })?;
            let column_grid = self.grids.get(component.column_grid).ok_or_else(|| {
                format!(
                    "component {index} column grid {} is absent",
                    component.column_grid
                )
            })?;
            let expected = match component.kind {
                ComponentKind::Absolute | ComponentKind::Relative => (row_grid.len() - 1)
                    .checked_mul(column_grid.len() - 1)
                    .ok_or("covariance component dimensions overflow")?,
                ComponentKind::ShortRange8 | ComponentKind::ShortRange9 => {
                    if component.row_grid != component.column_grid || component.mt != component.mt1
                    {
                        return Err(format!(
                            "component {index} short-range covariance is not a self matrix"
                        ));
                    }
                    row_grid.len() - 1
                }
            };
            if component.value_len != expected
                || component
                    .value_offset
                    .checked_add(component.value_len)
                    .is_none_or(|end| end > self.values.len())
            {
                return Err(format!(
                    "component {index} has invalid value range {}+{}; expected {expected}",
                    component.value_offset, component.value_len
                ));
            }
        }
        Ok(())
    }

    fn append(
        &mut self,
        other: CovarianceLibrary,
        known: &mut BTreeMap<Vec<u64>, usize>,
    ) -> Result<(), String> {
        other.validate()?;
        let mut remap = Vec::with_capacity(other.grids.len());
        for grid in other.grids {
            let key: Vec<u64> = grid.iter().map(|value| value.to_bits()).collect();
            let mapped = if let Some(index) = known.get(&key) {
                *index
            } else {
                let index = self.grids.len();
                self.grids.push(grid);
                known.insert(key, index);
                index
            };
            remap.push(mapped);
        }
        let base = self.values.len();
        self.values.extend(other.values);
        for component in other.components {
            self.components.push(StoredComponent {
                row_grid: remap[component.row_grid],
                column_grid: remap[component.column_grid],
                value_offset: base + component.value_offset,
                ..component
            });
        }
        // Each checkpoint is validated above. The combined library is validated once by
        // `write_npz`; validating the full growing prefix here makes a corpus build quadratic.
        Ok(())
    }

    fn component_values(&self, component: &StoredComponent) -> &[f64] {
        &self.values[component.value_offset..component.value_offset + component.value_len]
    }

    fn base_rows(library: &Library) -> HashMap<(usize, i32), usize> {
        library
            .rows
            .iter()
            .enumerate()
            .filter(|(_, row)| row.zap == -1)
            .map(|(index, row)| ((row.target, row.mt), index))
            .collect()
    }

    fn vector_for_grid(
        library: &Library,
        phi: &[f64],
        total_flux: f64,
        row_index: usize,
        base_index: usize,
        grid: &[f64],
        relative: bool,
    ) -> Result<Vec<f64>, String> {
        let row_sigma = library.sigma(row_index);
        let base_sigma = library.sigma(base_index);
        let mut vector = vec![0.0; grid.len() - 1];
        for (group, &group_flux) in phi.iter().enumerate() {
            if group_flux == 0.0 {
                continue;
            }
            let low = library.bounds[group];
            let high = library.bounds[group + 1];
            let density = group_flux / (high - low) / total_flux;
            let multiplier = if relative {
                row_sigma[group]
            } else if row_index == base_index {
                1.0
            } else if base_sigma[group] > 0.0 {
                row_sigma[group] / base_sigma[group]
            } else if row_sigma[group] == 0.0 {
                0.0
            } else {
                return Err(format!(
                    "activation row {row_index} is nonzero where base row {base_index} is zero"
                ));
            };
            if multiplier == 0.0 {
                continue;
            }
            for bin in 0..grid.len() - 1 {
                let width = (high.min(grid[bin + 1]) - low.max(grid[bin])).max(0.0);
                if width > 0.0 {
                    vector[bin] += density * width * multiplier;
                }
            }
        }
        Ok(vector)
    }

    fn matrix_component(&self, component: &StoredComponent, left: &[f64], right: &[f64]) -> f64 {
        let matrix = self.component_values(component);
        let columns = right.len();
        let mut sum = 0.0;
        for (row, left_value) in left.iter().enumerate() {
            for (column, right_value) in right.iter().enumerate() {
                sum += left_value * matrix[row * columns + column] * right_value;
            }
        }
        sum
    }

    #[allow(clippy::too_many_arguments)]
    fn short_component(
        &self,
        component: &StoredComponent,
        library: &Library,
        phi: &[f64],
        total_flux: f64,
        left_row: usize,
        left_base: usize,
        right_row: usize,
        right_base: usize,
    ) -> Result<f64, String> {
        let grid = &self.grids[component.row_grid];
        let factors = self.component_values(component);
        let left_sigma = library.sigma(left_row);
        let left_base_sigma = library.sigma(left_base);
        let right_sigma = library.sigma(right_row);
        let right_base_sigma = library.sigma(right_base);
        let ratio = |row: usize, base: usize, sigma: &[f64], base_sigma: &[f64], group: usize| {
            if row == base {
                Ok(1.0)
            } else if base_sigma[group] > 0.0 {
                Ok(sigma[group] / base_sigma[group])
            } else if sigma[group] == 0.0 {
                Ok(0.0)
            } else {
                Err(format!(
                    "activation row {row} is nonzero where base row {base} is zero"
                ))
            }
        };
        let mut sum = 0.0;
        for (group, &group_flux) in phi.iter().enumerate() {
            if group_flux == 0.0 {
                continue;
            }
            let low = library.bounds[group];
            let high = library.bounds[group + 1];
            let group_width = high - low;
            let left_ratio = ratio(left_row, left_base, left_sigma, left_base_sigma, group)?;
            let right_ratio = ratio(right_row, right_base, right_sigma, right_base_sigma, group)?;
            for bin in 0..grid.len() - 1 {
                let segment_width = (high.min(grid[bin + 1]) - low.max(grid[bin])).max(0.0);
                if segment_width == 0.0 {
                    continue;
                }
                let covariance_width = grid[bin + 1] - grid[bin];
                let segment_weight = group_flux * segment_width / group_width / total_flux;
                let variance = match component.kind {
                    ComponentKind::ShortRange8 => factors[bin] * covariance_width / segment_width,
                    ComponentKind::ShortRange9 => {
                        factors[bin] * (1.0 - segment_width / covariance_width)
                    }
                    _ => unreachable!(),
                };
                sum += segment_weight * segment_weight * left_ratio * right_ratio * variance;
            }
        }
        Ok(sum)
    }

    /// Collapse the represented MF=33 covariance onto selected activation rows under one group-integrated spectrum.
    pub fn collapse(
        &self,
        library: &Library,
        phi: &[f64],
        selected_rows: &[usize],
    ) -> Result<CollapsedCovariance, String> {
        self.validate()?;
        library.validate()?;
        if phi.len() != library.ngroups
            || phi.iter().any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err(
                "covariance collapse flux must match the library and be finite/nonnegative".into(),
            );
        }
        let mut selected = BTreeSet::new();
        for &row in selected_rows {
            if row >= library.rows.len() {
                return Err(format!("selected activation row {row} is out of range"));
            }
            if !selected.insert(row) {
                return Err(format!("duplicate selected activation row {row}"));
            }
        }
        let self_covered: BTreeSet<(usize, i32)> = self
            .components
            .iter()
            .filter(|component| component.mt == component.mt1)
            .map(|component| (component.target, component.mt))
            .collect();
        let mut row_indices = Vec::new();
        let mut uncovered_rows = Vec::new();
        for row in selected {
            let descriptor = library.rows[row];
            if descriptor.lmf != 10 && self_covered.contains(&(descriptor.target, descriptor.mt)) {
                row_indices.push(row);
            } else {
                uncovered_rows.push(row);
            }
        }
        let total_flux: f64 = phi.iter().sum();
        let one_group_barns = row_indices
            .iter()
            .map(|row| library.one_group(*row, phi))
            .collect::<Vec<_>>();
        let size = row_indices.len();
        let mut covariance_barn2 = vec![0.0; size * size];
        if total_flux == 0.0 {
            return Ok(CollapsedCovariance {
                row_indices,
                one_group_barns,
                covariance_barn2,
                uncovered_rows,
                absent_cross_parameter_pairs: size * size.saturating_sub(1) / 2,
                maximum_asymmetry_barn2: 0.0,
            });
        }
        let base_rows = Self::base_rows(library);
        let mut by_key: BTreeMap<(usize, i32), Vec<usize>> = BTreeMap::new();
        for (parameter, &row_index) in row_indices.iter().enumerate() {
            let row = library.rows[row_index];
            by_key
                .entry((row.target, row.mt))
                .or_default()
                .push(parameter);
        }
        let represented_pairs: BTreeSet<(usize, i32, i32)> = self
            .components
            .iter()
            .map(|component| {
                (
                    component.target,
                    component.mt.min(component.mt1),
                    component.mt.max(component.mt1),
                )
            })
            .collect();
        let mut vector_cache: HashMap<(usize, usize, bool), Vec<f64>> = HashMap::new();
        for component in &self.components {
            let Some(left_parameters) = by_key.get(&(component.target, component.mt)) else {
                continue;
            };
            let Some(right_parameters) = by_key.get(&(component.target, component.mt1)) else {
                continue;
            };
            for &left_parameter in left_parameters {
                let left_row = row_indices[left_parameter];
                let left_base = *base_rows
                    .get(&(component.target, component.mt))
                    .ok_or_else(|| {
                        format!(
                            "no loss row for target {}/MT{}",
                            component.target, component.mt
                        )
                    })?;
                for &right_parameter in right_parameters {
                    let right_row = row_indices[right_parameter];
                    let right_base = *base_rows
                        .get(&(component.target, component.mt1))
                        .ok_or_else(|| {
                            format!(
                                "no loss row for target {}/MT{}",
                                component.target, component.mt1
                            )
                        })?;
                    let value = match component.kind {
                        ComponentKind::Relative | ComponentKind::Absolute => {
                            let relative = component.kind == ComponentKind::Relative;
                            let left_key = (left_row, component.row_grid, relative);
                            if let std::collections::hash_map::Entry::Vacant(entry) =
                                vector_cache.entry(left_key)
                            {
                                let vector = Self::vector_for_grid(
                                    library,
                                    phi,
                                    total_flux,
                                    left_row,
                                    left_base,
                                    &self.grids[component.row_grid],
                                    relative,
                                )?;
                                entry.insert(vector);
                            }
                            let right_key = (right_row, component.column_grid, relative);
                            if let std::collections::hash_map::Entry::Vacant(entry) =
                                vector_cache.entry(right_key)
                            {
                                let vector = Self::vector_for_grid(
                                    library,
                                    phi,
                                    total_flux,
                                    right_row,
                                    right_base,
                                    &self.grids[component.column_grid],
                                    relative,
                                )?;
                                entry.insert(vector);
                            }
                            self.matrix_component(
                                component,
                                vector_cache.get(&left_key).expect("inserted vector"),
                                vector_cache.get(&right_key).expect("inserted vector"),
                            )
                        }
                        ComponentKind::ShortRange8 | ComponentKind::ShortRange9 => self
                            .short_component(
                                component, library, phi, total_flux, left_row, left_base,
                                right_row, right_base,
                            )?,
                    };
                    covariance_barn2[left_parameter * size + right_parameter] += value;
                    if component.mt != component.mt1 {
                        covariance_barn2[right_parameter * size + left_parameter] += value;
                    }
                }
            }
        }
        let mut maximum_asymmetry_barn2 = 0.0f64;
        let mut absent_cross_parameter_pairs = 0usize;
        for left in 0..size {
            let left_row = library.rows[row_indices[left]];
            for right in left + 1..size {
                let right_row = library.rows[row_indices[right]];
                maximum_asymmetry_barn2 = maximum_asymmetry_barn2.max(
                    (covariance_barn2[left * size + right] - covariance_barn2[right * size + left])
                        .abs(),
                );
                if left_row.target != right_row.target
                    || !represented_pairs.contains(&(
                        left_row.target,
                        left_row.mt.min(right_row.mt),
                        left_row.mt.max(right_row.mt),
                    ))
                {
                    absent_cross_parameter_pairs += 1;
                }
            }
        }
        Ok(CollapsedCovariance {
            row_indices,
            one_group_barns,
            covariance_barn2,
            uncovered_rows,
            absent_cross_parameter_pairs,
            maximum_asymmetry_barn2,
        })
    }
}

pub fn covariance_fingerprint() -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-COVARIANCE-BUILDER-v1\0");
    hash.update(include_bytes!("covariance.rs"));
    hash.update(include_bytes!("endf.rs"));
    hash.update(include_bytes!("activation.rs"));
    hash.update(include_bytes!("library.rs"));
    format!("{:x}", hash.finalize())
}

pub fn index_path(output: impl AsRef<Path>) -> Result<PathBuf, String> {
    let output = output.as_ref();
    if output.extension().and_then(OsStr::to_str) != Some("npz") {
        return Err("covariance-sidecar output must end in .npz".into());
    }
    let stem = output
        .file_stem()
        .and_then(OsStr::to_str)
        .ok_or("covariance-sidecar output needs a UTF-8 filename")?;
    Ok(output.with_file_name(format!("{stem}_index.json")))
}

fn activation_target_identity(expected: &[(usize, ActivationTarget)]) -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-COVARIANCE-TARGET-IDENTITY-v1\0");
    for (index, target) in expected {
        hash.update((*index as u64).to_le_bytes());
        for value in [&target.file, &target.source_sha256] {
            hash.update(value.as_bytes());
            hash.update([0]);
        }
        for value in [target.mat, target.za, target.liso] {
            hash.update(value.to_le_bytes());
        }
    }
    format!("{:x}", hash.finalize())
}

fn cache_key(source_sha256: &str, activation_target_identity_sha256: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-COVARIANCE-CHECKPOINT-v1\0");
    hash.update(source_sha256.as_bytes());
    hash.update([0]);
    hash.update(activation_target_identity_sha256.as_bytes());
    hash.update([0]);
    hash.update(COVARIANCE_CHECKPOINT_FINGERPRINT.as_bytes());
    format!("{:x}", hash.finalize())
}

fn cache_paths(directory: &Path, key: &str) -> (PathBuf, PathBuf) {
    (
        directory.join(format!("{key}.cov.npz")),
        directory.join(format!("{key}.cov.json")),
    )
}

fn load_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    activation_target_identity_sha256: &str,
) -> Option<BuiltCovarianceSource> {
    let (npz_path, index_path) = cache_paths(directory, key);
    let index: CacheIndex =
        serde_json::from_str(&std::fs::read_to_string(index_path).ok()?).ok()?;
    if index.schema != "actinv-covariance-checkpoint-1"
        || index.key != key
        || index.source_sha256 != source_sha256
        || index.activation_target_identity_sha256 != activation_target_identity_sha256
        || crate::builder::sha256_file(&npz_path).ok()? != index.npz_sha256
    {
        return None;
    }
    Some(BuiltCovarianceSource {
        library: read_npz(npz_path).ok()?,
        targets: index.targets,
        sections: index.sections,
        from_cache: true,
    })
}

fn store_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    activation_target_identity_sha256: &str,
    source: &BuiltCovarianceSource,
) -> Result<(), String> {
    let (npz_path, index_path) = cache_paths(directory, key);
    write_npz(&npz_path, &source.library)?;
    let npz_sha256 = crate::builder::sha256_file(&npz_path)?;
    let index = CacheIndex {
        schema: "actinv-covariance-checkpoint-1".into(),
        key: key.into(),
        source_sha256: source_sha256.into(),
        activation_target_identity_sha256: activation_target_identity_sha256.into(),
        npz_sha256,
        targets: source.targets.clone(),
        sections: source.sections,
    };
    if let Err(error) = crate::builder::write_json_atomic(&index_path, &index) {
        let _ = std::fs::remove_file(npz_path);
        return Err(error);
    }
    Ok(())
}

fn build_source(
    path: &Path,
    activation_targets: &BTreeMap<String, Vec<(usize, ActivationTarget)>>,
    cache: Option<&Path>,
) -> Result<BuiltCovarianceSource, String> {
    let filename = path
        .file_name()
        .and_then(OsStr::to_str)
        .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?;
    let before = crate::builder::sha256_file(path)?;
    let expected = activation_targets.get(&before).ok_or_else(|| {
        format!(
            "{} SHA-256 {before} is absent from the activation-library index",
            path.display()
        )
    })?;
    if expected.iter().any(|(_, target)| target.file != filename) {
        return Err(format!(
            "{} filename does not match its activation-library target record",
            path.display()
        ));
    }
    let target_identity_sha256 = activation_target_identity(expected);
    let key = cache_key(&before, &target_identity_sha256);
    if let Some(directory) = cache {
        if let Some(source) = load_checkpoint(directory, &key, &before, &target_identity_sha256) {
            if crate::builder::sha256_file(path)? != before {
                return Err(format!(
                    "source {} changed while its covariance checkpoint was validated",
                    path.display()
                ));
            }
            return Ok(source);
        }
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
    let evaluations =
        crate::activation::parse_evaluations(&text, Some(crate::activation::Projectile::Neutron))
            .map_err(|error| format!("{}: {error}", path.display()))?;
    let metadata: BTreeMap<i32, (i32, i32)> = evaluations
        .iter()
        .map(|evaluation| {
            (
                evaluation.metadata.mat,
                (evaluation.metadata.za, evaluation.metadata.liso),
            )
        })
        .collect();
    if metadata.len() != evaluations.len() {
        return Err(format!(
            "{} contains duplicate material numbers",
            path.display()
        ));
    }
    for (_, target) in expected {
        if metadata.get(&target.mat) != Some(&(target.za, target.liso)) {
            return Err(format!(
                "{} MAT={} target identity does not match activation index ZA={}/LISO={}",
                path.display(),
                target.mat,
                target.za,
                target.liso
            ));
        }
    }
    let target_by_mat: BTreeMap<i32, usize> = expected
        .iter()
        .map(|(index, target)| (target.mat, *index))
        .collect();
    let parsed = parse_mf33(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    let sections = parsed
        .iter()
        .map(|component| (component.mat, component.mt))
        .collect::<BTreeSet<_>>()
        .len();
    let mut indexed = Vec::with_capacity(parsed.len());
    let mut sections_by_target: BTreeMap<usize, BTreeSet<i32>> = BTreeMap::new();
    let mut components_by_target: BTreeMap<usize, usize> = BTreeMap::new();
    let mut lb_by_target: BTreeMap<usize, BTreeMap<i32, usize>> = BTreeMap::new();
    for component in &parsed {
        let target = *target_by_mat.get(&component.mat).ok_or_else(|| {
            format!(
                "{} MF=33 MAT={} has no matching activation target",
                path.display(),
                component.mat
            )
        })?;
        sections_by_target
            .entry(target)
            .or_default()
            .insert(component.mt);
        *components_by_target.entry(target).or_default() += 1;
        *lb_by_target
            .entry(target)
            .or_default()
            .entry(component.lb)
            .or_default() += 1;
    }
    for component in parsed {
        let target = target_by_mat[&component.mat];
        indexed.push((target, component));
    }
    let library = CovarianceLibrary::from_parsed(indexed)?;
    let mut targets = Vec::with_capacity(expected.len());
    for (target, activation) in expected {
        targets.push(CovarianceTargetIndex {
            target: *target,
            file: activation.file.clone(),
            source_sha256: activation.source_sha256.clone(),
            mat: activation.mat,
            za: activation.za,
            liso: activation.liso,
            mf33_sections: sections_by_target.get(target).map_or(0, BTreeSet::len),
            components: components_by_target.get(target).copied().unwrap_or(0),
            lb_counts: lb_by_target.remove(target).unwrap_or_default(),
        });
    }
    if crate::builder::sha256_file(path)? != before {
        return Err(format!(
            "source {} changed during the covariance build",
            path.display()
        ));
    }
    let source = BuiltCovarianceSource {
        library,
        targets,
        sections,
        from_cache: false,
    };
    if let Some(directory) = cache {
        store_checkpoint(directory, &key, &before, &target_identity_sha256, &source)?;
    }
    Ok(source)
}

fn ensure_cache(input: &Path, cache: Option<&Path>) -> Result<(), String> {
    let Some(cache) = cache else {
        return Ok(());
    };
    if input.is_dir() && cache.starts_with(input) {
        return Err("covariance checkpoint cache must be outside the input directory".into());
    }
    match std::fs::symlink_metadata(cache) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => Err(format!(
            "covariance checkpoint cache {} is not a real directory",
            cache.display()
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir_all(cache).map_err(|error| {
                format!(
                    "cannot create covariance checkpoint cache {}: {error}",
                    cache.display()
                )
            })
        }
        Err(error) => Err(format!(
            "cannot inspect covariance checkpoint cache {}: {error}",
            cache.display()
        )),
    }
}

/// Build and atomically publish a covariance sidecar matched to one activation library.
pub fn build_covariance(
    input: impl AsRef<Path>,
    activation: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &CovarianceBuildOptions,
) -> Result<CovarianceBuildSummary, String> {
    if !(1..=256).contains(&options.workers) {
        return Err("covariance workers must be between 1 and 256".into());
    }
    let input = input.as_ref();
    let activation = activation.as_ref();
    let output = output.as_ref();
    if activation == output {
        return Err("covariance output cannot overwrite the activation library".into());
    }
    let output_index = index_path(output)?;
    let activation_index_path = crate::builder::index_path(activation)?;
    let activation_library_sha256 = crate::builder::sha256_file(activation)?;
    let activation_index_sha256 = crate::builder::sha256_file(&activation_index_path)?;
    let activation_index: ActivationIndex = serde_json::from_str(
        &std::fs::read_to_string(&activation_index_path).map_err(|error| {
            format!(
                "cannot read activation index {}: {error}",
                activation_index_path.display()
            )
        })?,
    )
    .map_err(|error| format!("cannot parse activation index: {error}"))?;
    if activation_index.schema != "actinv-library-index-1"
        || activation_index.projectile != "neutron"
        || activation_index.sha256_npz != activation_library_sha256
    {
        return Err("activation library/index identity is inconsistent or not neutron".into());
    }
    let mut activation_targets: BTreeMap<String, Vec<(usize, ActivationTarget)>> = BTreeMap::new();
    let mut identities = BTreeSet::new();
    for (index, target) in activation_index.targets.iter().cloned().enumerate() {
        if target.source_sha256.len() != 64
            || !target
                .source_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
            || !identities.insert((target.za, target.liso))
        {
            return Err(format!("invalid or duplicate activation target {index}"));
        }
        activation_targets
            .entry(target.source_sha256.clone())
            .or_default()
            .push((index, target));
    }
    ensure_cache(input, options.cache.as_deref())?;
    let files = crate::builder::discover_inputs(input, Some(output))?;
    if files.len() != activation_targets.len() {
        return Err(format!(
            "covariance input has {} files but activation index has {} distinct sources",
            files.len(),
            activation_targets.len()
        ));
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(options.workers)
        .build()
        .map_err(|error| format!("cannot create covariance worker pool: {error}"))?;
    let sources: Vec<BuiltCovarianceSource> = pool.install(|| {
        files
            .par_iter()
            .map(|path| build_source(path, &activation_targets, options.cache.as_deref()))
            .collect::<Result<_, _>>()
    })?;
    let files_with_mf33 = sources
        .iter()
        .filter(|source| !source.library.components.is_empty())
        .count();
    let cache_hits = sources.iter().filter(|source| source.from_cache).count();
    let sections = sources.iter().map(|source| source.sections).sum();
    let mut library = CovarianceLibrary::default();
    let mut known_grids = BTreeMap::new();
    let mut targets = Vec::with_capacity(activation_index.targets.len());
    for source in sources {
        library.append(source.library, &mut known_grids)?;
        targets.extend(source.targets);
    }
    targets.sort_by_key(|target| target.target);
    if targets.len() != activation_index.targets.len()
        || targets
            .iter()
            .enumerate()
            .any(|(index, target)| target.target != index)
    {
        return Err("covariance sources do not cover each activation target exactly once".into());
    }
    write_npz(output, &library)?;
    let sha256_npz = crate::builder::sha256_file(output)?;
    let mut lb_counts = BTreeMap::new();
    for component in &library.components {
        *lb_counts.entry(component.lb).or_insert(0) += 1;
    }
    let mut source_manifest = Sha256::new();
    let mut unique_sources = BTreeMap::new();
    for target in &targets {
        unique_sources.insert(target.file.as_str(), target.source_sha256.as_str());
    }
    for (file, hash) in unique_sources {
        source_manifest.update(file.as_bytes());
        source_manifest.update([0]);
        source_manifest.update(hash.as_bytes());
        source_manifest.update([0]);
    }
    let fingerprint = covariance_fingerprint();
    let index = CovarianceIndex {
        schema: "actinv-covariance-index-1",
        projectile: "neutron",
        activation_library: activation.display().to_string(),
        activation_library_sha256,
        activation_index: activation_index_path.display().to_string(),
        activation_index_sha256,
        group_boundary_sha256: activation_index.group_boundary_sha256,
        builder_fingerprint: fingerprint.clone(),
        source_manifest_sha256: format!("{:x}", source_manifest.finalize()),
        targets,
        files: files.len(),
        files_with_mf33,
        mf33_sections: sections,
        components: library.components.len(),
        lb_counts,
        columns: "components: (target, MT, MT1, LB, kind, row_grid, column_grid, value_offset, value_len)",
        sha256_npz: sha256_npz.clone(),
    };
    if let Err(error) = crate::builder::write_json_atomic(&output_index, &index) {
        let _ = std::fs::remove_file(output);
        return Err(error);
    }
    Ok(CovarianceBuildSummary {
        output: output.to_path_buf(),
        index: output_index,
        files: files.len(),
        files_with_mf33,
        targets: index.targets.len(),
        sections,
        components: library.components.len(),
        cache_hits,
        sha256_npz,
        builder_fingerprint: fingerprint,
    })
}

fn zip_options() -> Result<zip::write::SimpleFileOptions, String> {
    use zip::{CompressionMethod, DateTime};
    let timestamp =
        DateTime::from_date_and_time(1980, 1, 1, 0, 0, 0).map_err(|error| error.to_string())?;
    Ok(zip::write::SimpleFileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .compression_level(Some(6))
        .last_modified_time(timestamp)
        .unix_permissions(0o600))
}

pub fn write_npz(path: impl AsRef<Path>, library: &CovarianceLibrary) -> Result<(), String> {
    use zip::ZipWriter;
    library.validate()?;
    let path = path.as_ref();
    let temporary = temporary_sibling(path)?;
    let result = (|| {
        let file = std::fs::File::create(&temporary)
            .map_err(|error| format!("cannot create {}: {error}", temporary.display()))?;
        let mut archive = ZipWriter::new(file);
        let options = zip_options()?;
        archive
            .start_file("components.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(
            &mut archive,
            "<i8",
            &[library.components.len(), COMPONENT_COLUMNS],
        )?;
        for component in &library.components {
            let fields = [
                i64::try_from(component.target).map_err(|_| "target exceeds i64")?,
                i64::from(component.mt),
                i64::from(component.mt1),
                i64::from(component.lb),
                component.kind as i64,
                i64::try_from(component.row_grid).map_err(|_| "row grid exceeds i64")?,
                i64::try_from(component.column_grid).map_err(|_| "column grid exceeds i64")?,
                i64::try_from(component.value_offset).map_err(|_| "value offset exceeds i64")?,
                i64::try_from(component.value_len).map_err(|_| "value length exceeds i64")?,
            ];
            for value in fields {
                archive
                    .write_all(&value.to_le_bytes())
                    .map_err(|error| error.to_string())?;
            }
        }
        let mut offsets = Vec::with_capacity(library.grids.len() + 1);
        let mut grid_values: Vec<f64> = Vec::new();
        offsets.push(0usize);
        for grid in &library.grids {
            grid_values.extend(grid);
            offsets.push(grid_values.len());
        }
        archive
            .start_file("grid_offsets.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<i8", &[offsets.len()])?;
        for offset in offsets {
            archive
                .write_all(
                    &i64::try_from(offset)
                        .map_err(|_| "grid offset exceeds i64")?
                        .to_le_bytes(),
                )
                .map_err(|error| error.to_string())?;
        }
        archive
            .start_file("grid_values.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<f8", &[grid_values.len()])?;
        for value in grid_values {
            archive
                .write_all(&value.to_le_bytes())
                .map_err(|error| error.to_string())?;
        }
        archive
            .start_file("values.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<f8", &[library.values.len()])?;
        for value in &library.values {
            archive
                .write_all(&value.to_le_bytes())
                .map_err(|error| error.to_string())?;
        }
        let file = archive.finish().map_err(|error| error.to_string())?;
        file.sync_all()
            .map_err(|error| format!("cannot sync {}: {error}", temporary.display()))?;
        std::fs::rename(&temporary, path).map_err(|error| {
            format!(
                "cannot publish {} as {}: {error}",
                temporary.display(),
                path.display()
            )
        })
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

fn validate_members<R: Read + Seek>(archive: &mut zip::ZipArchive<R>) -> Result<(), String> {
    let required = [
        "components.npy",
        "grid_offsets.npy",
        "grid_values.npy",
        "values.npy",
    ];
    let mut names = BTreeSet::new();
    for index in 0..archive.len() {
        let member = archive.by_index(index).map_err(|error| error.to_string())?;
        if member.size() >= MAX_ARRAY_BYTES {
            return Err(format!(
                "{} is {} bytes; one covariance array must be below {MAX_ARRAY_BYTES} bytes",
                member.name(),
                member.size()
            ));
        }
        if !required.contains(&member.name()) {
            return Err(format!(
                "unexpected covariance-sidecar member '{}'",
                member.name()
            ));
        }
        if !names.insert(member.name().to_owned()) {
            return Err(format!(
                "duplicate covariance-sidecar member '{}'",
                member.name()
            ));
        }
    }
    for name in required {
        if !names.contains(name) {
            return Err(format!("covariance sidecar has no {name}"));
        }
    }
    Ok(())
}

fn read_i64_array<R: Read + Seek>(
    archive: &mut zip::ZipArchive<R>,
    name: &str,
    dimensions: usize,
) -> Result<(Vec<i64>, Vec<usize>), String> {
    let member = archive.by_name(name).map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::I64 || header.shape.len() != dimensions {
        return Err(format!(
            "{name} must have dtype <i8 and {dimensions} dimension(s), got {header:?}"
        ));
    }
    let mut values = Vec::with_capacity(header.elements);
    for _ in 0..header.elements {
        values.push(read_i64(&mut reader)?);
    }
    ensure_eof(&mut reader, name)?;
    Ok((values, header.shape))
}

fn read_f64_array<R: Read + Seek>(
    archive: &mut zip::ZipArchive<R>,
    name: &str,
) -> Result<Vec<f64>, String> {
    let member = archive.by_name(name).map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::F64 || header.shape.len() != 1 {
        return Err(format!(
            "{name} must be a one-dimensional <f8 array, got {header:?}"
        ));
    }
    let values = read_f64_values(&mut reader, header.elements)?;
    ensure_eof(&mut reader, name)?;
    Ok(values)
}

pub fn read_npz(path: impl AsRef<Path>) -> Result<CovarianceLibrary, String> {
    let file = std::fs::File::open(path.as_ref()).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    validate_members(&mut archive)?;
    let (raw_components, shape) = read_i64_array(&mut archive, "components.npy", 2)?;
    if shape[1] != COMPONENT_COLUMNS {
        return Err(format!(
            "components.npy must have {COMPONENT_COLUMNS} columns, got {}",
            shape[1]
        ));
    }
    let as_usize = |value: i64, label: &str| {
        usize::try_from(value).map_err(|_| format!("negative or oversized {label} {value}"))
    };
    let as_i32 = |value: i64, label: &str| {
        i32::try_from(value).map_err(|_| format!("oversized {label} {value}"))
    };
    let mut components = Vec::with_capacity(shape[0]);
    for fields in raw_components.as_chunks::<COMPONENT_COLUMNS>().0 {
        components.push(StoredComponent {
            target: as_usize(fields[0], "target")?,
            mt: as_i32(fields[1], "MT")?,
            mt1: as_i32(fields[2], "MT1")?,
            lb: as_i32(fields[3], "LB")?,
            kind: ComponentKind::parse(fields[4])?,
            row_grid: as_usize(fields[5], "row-grid index")?,
            column_grid: as_usize(fields[6], "column-grid index")?,
            value_offset: as_usize(fields[7], "value offset")?,
            value_len: as_usize(fields[8], "value length")?,
        });
    }
    let (raw_offsets, offset_shape) = read_i64_array(&mut archive, "grid_offsets.npy", 1)?;
    if offset_shape[0] == 0 {
        return Err("grid_offsets.npy is empty".into());
    }
    let offsets: Vec<usize> = raw_offsets
        .into_iter()
        .map(|value| as_usize(value, "grid offset"))
        .collect::<Result<_, _>>()?;
    let grid_values = read_f64_array(&mut archive, "grid_values.npy")?;
    if offsets[0] != 0
        || offsets.windows(2).any(|pair| pair[1] <= pair[0])
        || *offsets.last().expect("nonempty") != grid_values.len()
    {
        return Err("covariance grid offsets are inconsistent".into());
    }
    let grids = offsets
        .windows(2)
        .map(|pair| grid_values[pair[0]..pair[1]].to_vec())
        .collect();
    let values = read_f64_array(&mut archive, "values.npy")?;
    let library = CovarianceLibrary {
        components,
        grids,
        values,
    };
    library.validate()?;
    Ok(library)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(fields: [&str; 6], mat: i32, mf: i32, mt: i32, ns: i32) -> String {
        format!(
            "{:<11}{:<11}{:>11}{:>11}{:>11}{:>11}{mat:>4}{mf:>2}{mt:>3}{ns:>5}",
            fields[0], fields[1], fields[2], fields[3], fields[4], fields[5]
        )
    }

    fn synthetic_lb5() -> String {
        let mat = 2631;
        let mut lines = vec![
            record(["26056", "55.45", "0", "0", "0", "1"], mat, 33, 102, 1),
            record(["0", "0", "0", "102", "0", "1"], mat, 33, 102, 2),
            record(["0", "0", "1", "5", "6", "3"], mat, 33, 102, 3),
            record(["1", "2", "4", "0.01", "0.002", "0.04"], mat, 33, 102, 4),
            record(["0", "0", "0", "0", "0", "0"], mat, 33, 0, 5),
            record(["0", "0", "0", "0", "0", "0"], 0, 0, 0, 6),
        ];
        lines.push(String::new());
        lines.join("\n")
    }

    #[test]
    fn symmetric_lb5_parses_and_expands() {
        let parsed = parse_mf33(&synthetic_lb5()).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].row_grid, vec![1.0, 2.0, 4.0]);
        assert_eq!(parsed[0].values, vec![0.01, 0.002, 0.002, 0.04]);
    }

    #[test]
    fn sidecar_round_trip_is_deterministic() {
        let parsed = parse_mf33(&synthetic_lb5()).unwrap().remove(0);
        let library = CovarianceLibrary::from_parsed([(0, parsed)]).unwrap();
        let nonce = format!("{}", std::process::id());
        let first = std::env::temp_dir().join(format!("actinv-cov-{nonce}-a.npz"));
        let second = std::env::temp_dir().join(format!("actinv-cov-{nonce}-b.npz"));
        write_npz(&first, &library).unwrap();
        write_npz(&second, &library).unwrap();
        assert_eq!(
            std::fs::read(&first).unwrap(),
            std::fs::read(&second).unwrap()
        );
        assert_eq!(read_npz(&first).unwrap(), library);
        std::fs::remove_file(first).unwrap();
        std::fs::remove_file(second).unwrap();
    }

    #[test]
    fn relative_matrix_collapses_on_boundary_union() {
        let component = ParsedComponent {
            mat: 1,
            mt: 102,
            mt1: 102,
            lb: 5,
            kind: ComponentKind::Relative,
            row_grid: vec![1.0, 3.0, 5.0],
            column_grid: vec![1.0, 3.0, 5.0],
            values: vec![0.04, 0.01, 0.01, 0.09],
        };
        let covariance = CovarianceLibrary::from_parsed([(0, component)]).unwrap();
        let activation = Library {
            rows: vec![
                crate::library::Row {
                    target: 0,
                    mt: 102,
                    zap: -1,
                    lfs: -1,
                    lmf: 0,
                },
                crate::library::Row {
                    target: 0,
                    mt: 102,
                    zap: 26057,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig: vec![2.0, 4.0, 2.0, 4.0],
            ngroups: 2,
            bounds: vec![1.0, 2.0, 5.0],
        };
        let collapsed = covariance
            .collapse(&activation, &[1.0, 3.0], &[0, 1])
            .unwrap();
        assert_eq!(collapsed.row_indices, vec![0, 1]);
        for value in collapsed.covariance_barn2 {
            assert!((value - 0.51).abs() < 1e-14, "{value}");
        }
    }
}
