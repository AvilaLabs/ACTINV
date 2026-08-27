#![allow(non_snake_case)] // temperature_K is part of the public/canonical wire vocabulary.
//! Deterministic activation-library assembly from strict ENDF evaluations.

use crate::activation::{parse_evaluations, Evaluation, Mf6Product, ProductRef, Projectile};
use crate::groups::{GroupStructure, Tabulated};
use crate::library::{write_npz, Library, Row};
use crate::processing::{has_resonance_contribution, process_reaction, ProcessedReaction};
use crate::resonance::{omitted_fission_total_width_count, validate_rmatrix_limited, RangeData};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::ffi::OsStr;
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LibraryFormat {
    Auto,
    Tendl,
    Eaf,
}

impl LibraryFormat {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "auto" => Ok(Self::Auto),
            "tendl" => Ok(Self::Tendl),
            "eaf" => Ok(Self::Eaf),
            _ => Err(format!(
                "unknown library format '{value}'; expected auto, tendl or eaf"
            )),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Tendl => "tendl",
            Self::Eaf => "eaf",
        }
    }
}

#[derive(Clone, Debug)]
pub struct BuildOptions {
    pub format: LibraryFormat,
    pub projectile: Option<Projectile>,
    pub groups: GroupStructure,
    pub temperature_K: f64,
    pub workers: usize,
    pub cache: Option<PathBuf>,
    pub grid_density: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TargetIndex {
    pub file: String,
    pub source_sha256: String,
    pub mat: i32,
    pub za: i32,
    pub liso: i32,
    pub awr: f64,
    pub evaluation_temperature_K: f64,
    pub n_mf2: usize,
    pub n_mf3: usize,
    pub n_mf6: usize,
    pub n_mf8: usize,
    pub n_mf9: usize,
    pub n_mf10: usize,
    pub n_rows: usize,
    pub ledger: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
struct CanonicalOptions {
    grid_density: f64,
}

#[derive(Clone, Debug, Serialize)]
struct BuildIndex {
    schema: &'static str,
    format: String,
    projectile: String,
    temperature_K: f64,
    groups: String,
    group_boundary_sha256: String,
    weighting: &'static str,
    builder_fingerprint: String,
    options: CanonicalOptions,
    targets: Vec<TargetIndex>,
    n_rows: usize,
    columns: &'static str,
    sha256_npz: String,
}

#[derive(Clone, Debug)]
pub struct BuildSummary {
    pub output: PathBuf,
    pub index: PathBuf,
    pub projectile: Projectile,
    pub targets: usize,
    pub rows: usize,
    pub cache_hits: usize,
    pub sha256_npz: String,
    pub builder_fingerprint: String,
}

#[derive(Clone, Debug)]
struct BuiltRow {
    mt: i32,
    zap: i32,
    lfs: i32,
    lmf: i32,
    sigma: Vec<f64>,
}

#[derive(Clone, Debug)]
struct BuiltTarget {
    index: TargetIndex,
    rows: Vec<BuiltRow>,
}

#[derive(Clone, Copy)]
struct EvaluationBuildSettings<'a> {
    groups: &'a GroupStructure,
    temperature_K: f64,
    grid_density: f64,
}

#[derive(Clone, Debug)]
struct BuiltSource {
    format: LibraryFormat,
    projectile: Projectile,
    targets: Vec<BuiltTarget>,
    from_cache: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CacheIndex {
    schema: String,
    key: String,
    source_sha256: String,
    format: String,
    projectile: String,
    npz_sha256: String,
    targets: Vec<TargetIndex>,
    target_float_bits: Vec<[u64; 2]>,
}

#[derive(Deserialize)]
struct MtProductJson {
    table: BTreeMap<String, [i32; 2]>,
}

fn mt_products() -> Result<BTreeMap<i32, (i32, i32)>, String> {
    let raw: MtProductJson =
        serde_json::from_str(include_str!("../../../data/mt_products.json"))
            .map_err(|error| format!("invalid vendored MT product table: {error}"))?;
    raw.table
        .into_iter()
        .map(|(mt, delta)| {
            Ok((
                mt.parse::<i32>()
                    .map_err(|_| format!("invalid MT product key '{mt}'"))?,
                (delta[0], delta[1]),
            ))
        })
        .collect()
}

pub fn builder_fingerprint() -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-RUST-BUILDER-v1\0");
    hash.update(include_bytes!("builder.rs"));
    hash.update(include_bytes!("activation.rs"));
    hash.update(include_bytes!("endf.rs"));
    hash.update(include_bytes!("groups.rs"));
    hash.update(include_bytes!("doppler.rs"));
    hash.update(include_bytes!("resonance.rs"));
    hash.update(include_bytes!("processing.rs"));
    hash.update(include_bytes!("library.rs"));
    hash.update(include_bytes!("../../../data/mt_products.json"));
    format!("{:x}", hash.finalize())
}

pub fn sha256_file(path: impl AsRef<Path>) -> Result<String, String> {
    let path = path.as_ref();
    let mut file = std::fs::File::open(path)
        .map_err(|error| format!("cannot open {} for hashing: {error}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn checkpoint_key(source_sha256: &str, options: &BuildOptions) -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-TARGET-CHECKPOINT-v1\0");
    hash.update(source_sha256.as_bytes());
    hash.update([0]);
    hash.update(options.format.name().as_bytes());
    hash.update([0]);
    hash.update(
        options
            .projectile
            .map(Projectile::name)
            .unwrap_or("auto")
            .as_bytes(),
    );
    hash.update(options.temperature_K.to_bits().to_le_bytes());
    hash.update(options.grid_density.to_bits().to_le_bytes());
    hash.update(options.groups.hash().as_bytes());
    hash.update(builder_fingerprint().as_bytes());
    format!("{:x}", hash.finalize())
}

fn cache_paths(directory: &Path, key: &str) -> (PathBuf, PathBuf) {
    (
        directory.join(format!("{key}.npz")),
        directory.join(format!("{key}.json")),
    )
}

fn restore_target_float_bits(target: &mut TargetIndex, bits: [u64; 2]) {
    target.awr = f64::from_bits(bits[0]);
    target.evaluation_temperature_K = f64::from_bits(bits[1]);
}

fn source_library(source: &BuiltSource, groups: &GroupStructure) -> Library {
    let mut rows = Vec::new();
    let mut sig = Vec::new();
    for (target_index, target) in source.targets.iter().enumerate() {
        for row in &target.rows {
            rows.push(Row {
                target: target_index,
                mt: row.mt,
                zap: row.zap,
                lfs: row.lfs,
                lmf: row.lmf,
            });
            sig.extend_from_slice(&row.sigma);
        }
    }
    Library {
        rows,
        sig,
        ngroups: groups.groups(),
        bounds: groups.boundaries_ev.clone(),
    }
}

fn load_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    groups: &GroupStructure,
) -> Option<BuiltSource> {
    let (npz_path, index_path) = cache_paths(directory, key);
    let index: CacheIndex =
        serde_json::from_str(&std::fs::read_to_string(index_path).ok()?).ok()?;
    if index.schema != "actinv-target-checkpoint-2"
        || index.key != key
        || index.source_sha256 != source_sha256
        || sha256_file(&npz_path).ok()? != index.npz_sha256
        || index.target_float_bits.len() != index.targets.len()
    {
        return None;
    }
    let format = match index.format.as_str() {
        "tendl" => LibraryFormat::Tendl,
        "eaf" => LibraryFormat::Eaf,
        _ => return None,
    };
    let projectile = Projectile::parse(&index.projectile).ok()?;
    let library = crate::library::read_npz(npz_path.to_str()?).ok()?;
    if library.ngroups != groups.groups()
        || library
            .bounds
            .iter()
            .zip(&groups.boundaries_ev)
            .any(|(left, right)| left.to_bits() != right.to_bits())
    {
        return None;
    }
    let mut row_offset = 0usize;
    let mut targets = Vec::with_capacity(index.targets.len());
    for (target_number, (mut target_index, float_bits)) in index
        .targets
        .into_iter()
        .zip(index.target_float_bits)
        .enumerate()
    {
        restore_target_float_bits(&mut target_index, float_bits);
        let end = row_offset.checked_add(target_index.n_rows)?;
        let source_rows = library.rows.get(row_offset..end)?;
        if source_rows.iter().any(|row| row.target != target_number) {
            return None;
        }
        let rows = source_rows
            .iter()
            .enumerate()
            .map(|(local, row)| BuiltRow {
                mt: row.mt,
                zap: row.zap,
                lfs: row.lfs,
                lmf: row.lmf,
                sigma: library.sigma(row_offset + local).to_vec(),
            })
            .collect();
        targets.push(BuiltTarget {
            index: target_index,
            rows,
        });
        row_offset = end;
    }
    if row_offset != library.rows.len() {
        return None;
    }
    Some(BuiltSource {
        format,
        projectile,
        targets,
        from_cache: true,
    })
}

fn store_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    source: &BuiltSource,
    groups: &GroupStructure,
) -> Result<(), String> {
    let (npz_path, index_path) = cache_paths(directory, key);
    let library = source_library(source, groups);
    write_npz(&npz_path, &library)?;
    let index = CacheIndex {
        schema: "actinv-target-checkpoint-2".into(),
        key: key.into(),
        source_sha256: source_sha256.into(),
        format: source.format.name().into(),
        projectile: source.projectile.name().into(),
        npz_sha256: sha256_file(&npz_path)?,
        targets: source
            .targets
            .iter()
            .map(|target| target.index.clone())
            .collect(),
        target_float_bits: source
            .targets
            .iter()
            .map(|target| {
                [
                    target.index.awr.to_bits(),
                    target.index.evaluation_temperature_K.to_bits(),
                ]
            })
            .collect(),
    };
    write_json_atomic(&index_path, &index)
}

pub(crate) fn discover_inputs(input: &Path, output: Option<&Path>) -> Result<Vec<PathBuf>, String> {
    let metadata = std::fs::symlink_metadata(input)
        .map_err(|error| format!("cannot inspect input {}: {error}", input.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!("input {} is a symlink", input.display()));
    }
    if metadata.is_file() {
        if output.is_some_and(|output| input == output) {
            return Err("activation-library output cannot overwrite its ENDF input".into());
        }
        return Ok(vec![input.to_path_buf()]);
    }
    if !metadata.is_dir() {
        return Err(format!(
            "input {} is not a regular file or directory",
            input.display()
        ));
    }
    let input_directory = input
        .canonicalize()
        .map_err(|error| format!("cannot resolve input {}: {error}", input.display()))?;
    if let Some(parent) = output
        .and_then(Path::parent)
        .filter(|parent| parent.exists())
    {
        if parent.canonicalize().ok().as_ref() == Some(&input_directory) {
            return Err("activation-library output must be outside its input directory".into());
        }
    }
    let mut files = Vec::new();
    for entry in std::fs::read_dir(input)
        .map_err(|error| format!("cannot read input directory {}: {error}", input.display()))?
    {
        let entry = entry.map_err(|error| format!("cannot read directory entry: {error}"))?;
        let path = entry.path();
        let metadata = std::fs::symlink_metadata(&path)
            .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(format!(
                "input directory contains non-regular entry {}",
                path.display()
            ));
        }
        files.push(path);
    }
    files.sort_by(|left, right| os_bytes(left.file_name()).cmp(os_bytes(right.file_name())));
    if files.is_empty() {
        return Err(format!("input directory {} is empty", input.display()));
    }
    Ok(files)
}

/// Inspect the first bytewise-ordered evaluation to select projectile-dependent CLI defaults. The full build still
/// validates every file and rejects a mixed directory.
pub fn inspect_projectile(input: impl AsRef<Path>) -> Result<Projectile, String> {
    let files = discover_inputs(input.as_ref(), None)?;
    let first = &files[0];
    let text = std::fs::read_to_string(first)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", first.display()))?;
    parse_evaluations(&text, None)
        .map_err(|error| format!("{}: {error}", first.display()))?
        .first()
        .map(|evaluation| evaluation.metadata.projectile)
        .ok_or_else(|| format!("{} contains no evaluation", first.display()))
}

fn os_bytes(value: Option<&OsStr>) -> &[u8] {
    value.map(OsStr::as_encoded_bytes).unwrap_or_default()
}

fn validate_options(options: &BuildOptions) -> Result<(), String> {
    options.groups.validate()?;
    if !options.temperature_K.is_finite() || options.temperature_K < 0.0 {
        return Err("library temperature must be finite and nonnegative".into());
    }
    if !(1..=256).contains(&options.workers) {
        return Err("workers must be between 1 and 256".into());
    }
    if !options.grid_density.is_finite() || options.grid_density <= 0.0 {
        return Err("grid density must be finite and positive".into());
    }
    if let Some(projectile) = options.projectile {
        if projectile != Projectile::Neutron && options.temperature_K != 0.0 {
            return Err(format!(
                "{} activation libraries require exactly 0 K",
                projectile.name()
            ));
        }
        let expected_groups = match projectile {
            Projectile::Neutron => 709,
            Projectile::Proton | Projectile::Deuteron | Projectile::Alpha => 162,
        };
        if options.groups.name.starts_with("fispact-") && options.groups.groups() != expected_groups
        {
            return Err(format!(
                "{} uses the {}-group structure, not {} groups",
                projectile.name(),
                expected_groups,
                options.groups.groups()
            ));
        }
    }
    Ok(())
}

fn detected_format(text: &str, evaluation: &Evaluation) -> Result<LibraryFormat, String> {
    if text.contains("EAF-2010") || text.contains("EAF-20100") {
        Ok(LibraryFormat::Eaf)
    } else if text.contains("TENDL-")
        || !evaluation.mf2_sections.is_empty()
        || !evaluation.mf6.is_empty()
    {
        Ok(LibraryFormat::Tendl)
    } else {
        Err("cannot auto-detect ENDF library format; pass --format tendl or --format eaf".into())
    }
}

fn checked_collapse(
    groups: &GroupStructure,
    table: &Tabulated,
    label: &str,
) -> Result<Vec<f64>, String> {
    if table.y.iter().any(|value| *value < 0.0) {
        return Err(format!("{label} contains a negative cross section"));
    }
    checked_groups(groups.collapse(table)?, label)
}

fn checked_product(
    groups: &GroupStructure,
    tables: &[&Tabulated],
    label: &str,
) -> Result<Vec<f64>, String> {
    if tables
        .iter()
        .any(|table| table.y.iter().any(|value| *value < 0.0))
    {
        return Err(format!(
            "{label} contains a negative cross section or yield"
        ));
    }
    checked_groups(groups.collapse_product(tables)?, label)
}

fn checked_groups(values: Vec<f64>, label: &str) -> Result<Vec<f64>, String> {
    if values
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        Err(format!(
            "{label} collapsed to a nonfinite or negative group value"
        ))
    } else {
        Ok(values)
    }
}

fn sum_groups(total: &mut [f64], addend: &[f64]) -> Result<(), String> {
    if total.len() != addend.len() {
        return Err("cannot sum mismatched group arrays".into());
    }
    for (left, right) in total.iter_mut().zip(addend) {
        *left += *right;
    }
    Ok(())
}

fn skip_mt(mt: i32, projectile: Projectile, has_mf6: bool) -> bool {
    matches!(mt, 1 | 2 | 3 | 27 | 101 | 444 | 19 | 20 | 21 | 38)
        || (mt == 5 && !(projectile != Projectile::Neutron && has_mf6))
        || (201..=207).contains(&mt)
        || (600..=849).contains(&mt)
        || mt >= 1000
}

fn inelastic(mt: i32) -> bool {
    mt == 4 || (51..=91).contains(&mt)
}

fn descriptor_set(products: &[ProductRef], lmf: i32) -> Result<BTreeSet<(i32, i32)>, String> {
    let mut set = BTreeSet::new();
    for product in products
        .iter()
        .filter(|product| product.lmf == lmf && product.zap >= 0)
    {
        if !set.insert((product.zap, product.lfs)) {
            return Err(format!(
                "duplicate MF=8 product ZAP={}/LFS={}/LMF={lmf}",
                product.zap, product.lfs
            ));
        }
    }
    Ok(set)
}

fn validate_descriptors(
    products: &[ProductRef],
    lmf: i32,
    actual: &BTreeSet<(i32, i32)>,
) -> Result<(), String> {
    let declared = descriptor_set(products, lmf)?;
    if !declared.is_empty() && &declared != actual {
        return Err(format!(
            "MF=8/LMF={lmf} products {declared:?} conflict with MF={lmf} products {actual:?}"
        ));
    }
    Ok(())
}

type MatchedMf6<'a> = (Vec<(&'a Mf6Product, ProductRef)>, bool);

fn match_mf6<'a>(
    products: &[ProductRef],
    yields: &'a [Mf6Product],
) -> Result<MatchedMf6<'a>, String> {
    let declared: Vec<_> = products
        .iter()
        .filter(|product| product.lmf == 6)
        .copied()
        .collect();
    let mut used = vec![false; yields.len()];
    let mut matched = Vec::with_capacity(declared.len());
    let mut omitted_neutron = false;
    for descriptor in declared {
        let index = yields
            .iter()
            .enumerate()
            .position(|(index, product)| !used[index] && product.zap == descriptor.zap)
            .ok_or_else(|| {
                format!(
                    "MF=8 declares ZAP={}/LFS={} with no matching MF=6 yield",
                    descriptor.zap, descriptor.lfs
                )
            })?;
        used[index] = true;
        if descriptor.zap == 1 {
            omitted_neutron = true;
        } else if descriptor.zap != 0 {
            matched.push((&yields[index], descriptor));
        }
    }
    for (used, product) in used.into_iter().zip(yields) {
        if !used && product.zap != 0 {
            return Err(format!(
                "MF=6 ZAP={} yield has no matching MF=8/LMF=6 declaration",
                product.zap
            ));
        }
    }
    Ok((matched, omitted_neutron))
}

fn residual_product(
    target_za: i32,
    projectile: Projectile,
    neutron_delta: (i32, i32),
) -> Option<i32> {
    let target = (target_za / 1000, target_za % 1000);
    let incident = projectile.za();
    let z = target.0 + neutron_delta.0 + incident.0;
    let a = target.1 + neutron_delta.1 + incident.1 - 1;
    (z > 0 && a > 0 && a >= z).then_some(z * 1000 + a)
}

fn remap_levels(rows: &mut [BuiltRow], ledger: &mut Vec<String>) {
    let mut levels: BTreeMap<(i32, i32), BTreeSet<i32>> = BTreeMap::new();
    for row in rows.iter().filter(|row| row.lfs > 0) {
        levels.entry((row.mt, row.zap)).or_default().insert(row.lfs);
    }
    for ((mt, zap), values) in levels {
        let original: Vec<i32> = values.into_iter().collect();
        let canonical: Vec<i32> = (1..=original.len() as i32).collect();
        if original == canonical {
            continue;
        }
        let mapping: BTreeMap<i32, i32> = original
            .iter()
            .copied()
            .zip(canonical.iter().copied())
            .collect();
        for row in rows
            .iter_mut()
            .filter(|row| row.mt == mt && row.zap == zap && row.lfs > 0)
        {
            row.lfs = mapping[&row.lfs];
        }
        ledger.push(format!(
            "MT{mt}->{zap}: LFS {original:?} remapped to decay isomers {canonical:?}"
        ));
    }
}

fn build_evaluation(
    evaluation: Evaluation,
    format: LibraryFormat,
    file: &str,
    source_sha256: &str,
    settings: EvaluationBuildSettings<'_>,
    products_by_mt: &BTreeMap<i32, (i32, i32)>,
) -> Result<BuiltTarget, String> {
    let EvaluationBuildSettings {
        groups,
        temperature_K,
        grid_density,
    } = settings;
    let metadata = &evaluation.metadata;
    let mut ledger = Vec::new();
    if metadata.projectile != Projectile::Neutron && temperature_K != 0.0 {
        return Err(format!(
            "{} target requires 0 K, requested {temperature_K} K",
            metadata.projectile.name()
        ));
    }
    let mut processed: BTreeMap<i32, ProcessedReaction> = BTreeMap::new();
    if format != LibraryFormat::Eaf && !evaluation.mf2_sections.is_empty() {
        if metadata.projectile != Projectile::Neutron {
            return Err(format!(
                "MF=2 resonance processing is unsupported for {} evaluations",
                metadata.projectile.name()
            ));
        }
        if metadata.evaluation_temperature_k != 0.0 {
            return Err(format!(
                "raw MF=2 processing requires a 0 K evaluation, got {} K",
                metadata.evaluation_temperature_k
            ));
        }
        if evaluation.mf2_sections != BTreeSet::from([151]) {
            return Err(format!(
                "unsupported MF=2 sections {:?}; expected only MT=151",
                evaluation.mf2_sections
            ));
        }
        let resonance = evaluation
            .resonance
            .as_ref()
            .ok_or("MF=2/MT=151 was declared without parsed resonance parameters")?;
        let omitted_fission_totals = omitted_fission_total_width_count(resonance);
        if omitted_fission_totals > 0 {
            ledger.push(format!(
                "MF=2: {omitted_fission_totals} LRX=0 Breit-Wigner GT fields omit GF; effective total widths reconstructed from GN+GG+GF per P10 Amendment D"
            ));
        }
        for isotope in &resonance.isotopes {
            for range in &isotope.ranges {
                if matches!(range.data, RangeData::RMatrixLimited(_)) {
                    validate_rmatrix_limited(range)
                        .map_err(|error| format!("RML validation: {error}"))?;
                }
            }
        }
        for mt in [18, 102] {
            if has_resonance_contribution(resonance, mt) {
                // ENDF-6 permits File 3 background cross sections in resonance ranges but does not require them.
                // Represent an absent optional background explicitly so resonance-only reactions remain complete.
                let zero_background = (!evaluation.mf3.contains_key(&mt)).then(|| Tabulated {
                    interpolation: vec![(2, 2)],
                    x: vec![
                        groups.boundaries_ev[0],
                        groups.boundaries_ev[groups.groups()],
                    ],
                    y: vec![0.0, 0.0],
                });
                let background = evaluation
                    .mf3
                    .get(&mt)
                    .or(zero_background.as_ref())
                    .expect("an MF=3 or explicit zero background is present");
                if zero_background.is_some() {
                    ledger.push(format!(
                        "MT{mt}: optional MF=3 resonance background absent; explicit zero background used"
                    ));
                }
                let reaction = process_reaction(
                    resonance,
                    background,
                    groups,
                    mt,
                    temperature_K,
                    grid_density,
                )
                .map_err(|error| format!("MT{mt} resonance processing: {error}"))?;
                ledger.push(format!(
                    "MT{mt}: raw 0 K MF=2+MF=3 reconstructed on {} points and processed to {} K on {} points",
                    reaction.certificate.zero_k_points,
                    temperature_K,
                    reaction.certificate.output_points
                ));
                for line in &reaction.certificate.ultra_narrow_lines {
                    ledger.push(format!(
                        "MT{mt}: ultra-narrow ZAI={} E={:.17e} eV Gamma={:.17e} eV, Gamma/Gamma_D={:.17e} at {:.17e} K, direct area={:.17e} barn eV, closed area={:.17e} barn eV, abundance-weighted area={:.17e} barn eV, group={}, range-edge={}, core=[{:.17e},{:.17e}] eV",
                        line.isotope_zai,
                        line.energy_ev,
                        line.total_width_ev,
                        line.width_to_doppler_ratio,
                        line.classification_temperature_k,
                        line.direct_area_barn_ev,
                        line.closed_form_area_barn_ev,
                        line.weighted_area_barn_ev,
                        line.affected_group,
                        line.range_edge_decomposition,
                        line.core_low_ev,
                        line.core_high_ev
                    ));
                }
                processed.insert(mt, reaction);
            }
        }
    } else if metadata.evaluation_temperature_k.to_bits() != temperature_K.to_bits() {
        return Err(format!(
            "tabulated evaluation is at {} K but {temperature_K} K was requested and no raw MF=2 reconstruction applies",
            metadata.evaluation_temperature_k
        ));
    }

    let mut rows = Vec::new();
    if format == LibraryFormat::Eaf && !evaluation.mf2_sections.is_empty() {
        ledger.push(format!(
            "EAF processed MF=3 used at its declared temperature; MF=2 sections {:?} are not added a second time",
            evaluation.mf2_sections
        ));
    }
    let mut mts: BTreeSet<i32> = evaluation.mf3.keys().copied().collect();
    mts.extend(evaluation.mf10.keys().copied());
    mts.extend(processed.keys().copied());
    for mt in mts {
        let has_lmf6 = evaluation
            .mf8
            .get(&mt)
            .is_some_and(|products| products.iter().any(|product| product.lmf == 6));
        if skip_mt(mt, metadata.projectile, has_lmf6) {
            if mt == 5 && evaluation.mf8.contains_key(&mt) {
                ledger.push("MT5 aggregate products omitted for a neutron evaluation".into());
            }
            continue;
        }
        let descriptors = evaluation.mf8.get(&mt).map(Vec::as_slice).unwrap_or(&[]);

        if inelastic(mt) {
            let products: Vec<_> = evaluation.mf10.get(&mt).into_iter().flatten().collect();
            if products.iter().any(|product| product.zap < 0) {
                return Err(format!("MT{mt}/MF=10 contains an invalid negative product"));
            }
            let actual: BTreeSet<_> = products
                .iter()
                .map(|product| (product.zap, product.lfs))
                .collect();
            if actual.len() != products.len() {
                return Err(format!(
                    "MT{mt}/MF=10 contains duplicate product identities"
                ));
            }
            // Inelastic MF=10 commonly tabulates both return to the ground state and production of a metastable
            // state. Validate the complete declaration before intentionally retaining only transmuting LFS>0 rows.
            validate_descriptors(descriptors, 10, &actual)?;
            let metastable: Vec<_> = products
                .into_iter()
                .filter(|product| product.lfs > 0)
                .collect();
            if metastable.is_empty() {
                continue;
            }
            let retained: BTreeSet<_> = metastable
                .iter()
                .map(|product| (product.zap, product.lfs))
                .collect();
            let mut loss = vec![0.0; groups.groups()];
            let mut product_rows = Vec::new();
            for product in metastable {
                let sigma = checked_collapse(groups, &product.table, &format!("MT{mt}/MF=10"))?;
                sum_groups(&mut loss, &sigma)?;
                product_rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 10,
                    sigma,
                });
            }
            rows.push(BuiltRow {
                mt,
                zap: -1,
                lfs: -1,
                lmf: 0,
                sigma: loss,
            });
            rows.extend(product_rows);
            ledger.push(format!(
                "MT{mt}: retained {} metastable inelastic product channel(s)",
                retained.len()
            ));
            continue;
        }

        let total = if let Some(reaction) = processed.get(&mt) {
            checked_groups(reaction.collapse(groups)?, &format!("MT{mt}/MF=2+MF=3"))?
        } else if let Some(table) = evaluation.mf3.get(&mt) {
            checked_collapse(groups, table, &format!("MT{mt}/MF=3"))?
        } else {
            let products = evaluation
                .mf10
                .get(&mt)
                .ok_or_else(|| format!("MT{mt} has neither MF=3 nor MF=10 data"))?;
            if let Some(total_fission) = products.iter().find(|product| product.zap == -1) {
                checked_collapse(
                    groups,
                    &total_fission.table,
                    &format!("MT{mt}/MF=10 total-fission sentinel"),
                )?
            } else {
                let mut total = vec![0.0; groups.groups()];
                for product in products.iter().filter(|product| product.zap >= 0) {
                    sum_groups(
                        &mut total,
                        &checked_collapse(groups, &product.table, &format!("MT{mt}/MF=10"))?,
                    )?;
                }
                total
            }
        };
        rows.push(BuiltRow {
            mt,
            zap: -1,
            lfs: -1,
            lmf: 0,
            sigma: total.clone(),
        });
        let mut done = HashSet::new();

        if let Some(products) = evaluation.mf10.get(&mt) {
            let total_fission = products.iter().filter(|product| product.zap == -1).count();
            if total_fission > 1 || (total_fission == 1 && mt != 18) {
                return Err(format!(
                    "MT{mt}/MF=10 contains an invalid total-fission sentinel"
                ));
            }
            if total_fission == 1 {
                ledger.push(
                    "MT18: MF=10 IZAP=-1 total-fission sentinel validated and omitted as an inventory product"
                        .into(),
                );
            }
            let actual: BTreeSet<_> = products
                .iter()
                .filter(|product| product.zap >= 0)
                .map(|product| (product.zap, product.lfs))
                .collect();
            if actual.len() + total_fission != products.len() {
                return Err(format!(
                    "MT{mt}/MF=10 contains duplicate product identities"
                ));
            }
            validate_descriptors(descriptors, 10, &actual)?;
            for product in products.iter().filter(|product| product.zap >= 0) {
                if !done.insert((product.zap, product.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        product.zap, product.lfs
                    ));
                }
                rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 10,
                    sigma: checked_collapse(groups, &product.table, &format!("MT{mt}/MF=10"))?,
                });
            }
        }

        if let Some(products) = evaluation.mf9.get(&mt) {
            let reaction = evaluation
                .mf3
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=9 has no matching MF=3 reaction"))?;
            let actual: BTreeSet<_> = products
                .iter()
                .map(|product| (product.zap, product.lfs))
                .collect();
            if actual.len() != products.len() {
                return Err(format!("MT{mt}/MF=9 contains duplicate product identities"));
            }
            validate_descriptors(descriptors, 9, &actual)?;
            for product in products {
                if !done.insert((product.zap, product.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        product.zap, product.lfs
                    ));
                }
                let sigma = if let Some(processed) = processed.get(&mt) {
                    checked_groups(
                        processed.collapse_product(groups, &[&product.table])?,
                        &format!("MT{mt}/MF=2+MF=3*MF=9"),
                    )?
                } else {
                    checked_product(
                        groups,
                        &[reaction, &product.table],
                        &format!("MT{mt}/MF=3*MF=9"),
                    )?
                };
                rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 9,
                    sigma,
                });
            }
        }

        for descriptor in descriptors.iter().filter(|product| product.lmf == 3) {
            if !done.insert((descriptor.zap, descriptor.lfs)) {
                return Err(format!(
                    "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                    descriptor.zap, descriptor.lfs
                ));
            }
            rows.push(BuiltRow {
                mt,
                zap: descriptor.zap,
                lfs: descriptor.lfs,
                lmf: 3,
                sigma: total.clone(),
            });
        }

        if has_lmf6 {
            let reaction = evaluation
                .mf3
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=6 has no matching MF=3 reaction"))?;
            let yields = evaluation
                .mf6
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=8 declares LMF=6 but MF=6 is missing"))?;
            let (products, omitted_neutron) = match_mf6(descriptors, yields)?;
            if omitted_neutron {
                ledger.push(format!(
                    "MT{mt}: emitted free-neutron MF=6 product omitted from inventory"
                ));
            }
            for (product, descriptor) in products {
                if !done.insert((descriptor.zap, descriptor.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        descriptor.zap, descriptor.lfs
                    ));
                }
                rows.push(BuiltRow {
                    mt,
                    zap: descriptor.zap,
                    lfs: descriptor.lfs,
                    lmf: 6,
                    sigma: checked_product(
                        groups,
                        &[reaction, &product.yield_table],
                        &format!("MT{mt}/MF=3*MF=6"),
                    )?,
                });
            }
        }

        if done.is_empty() {
            if mt == 18 {
                rows.push(BuiltRow {
                    mt,
                    zap: 0,
                    lfs: 0,
                    lmf: 0,
                    sigma: total,
                });
            } else if let Some(delta) = products_by_mt.get(&mt) {
                if let Some(zap) = residual_product(metadata.za, metadata.projectile, *delta) {
                    rows.push(BuiltRow {
                        mt,
                        zap,
                        lfs: 0,
                        lmf: -1,
                        sigma: total,
                    });
                } else {
                    rows.push(BuiltRow {
                        mt,
                        zap: 0,
                        lfs: 0,
                        lmf: -2,
                        sigma: total,
                    });
                    ledger.push(format!(
                        "MT{mt}: residual arithmetic is not a bound nuclide"
                    ));
                }
            } else {
                rows.push(BuiltRow {
                    mt,
                    zap: 0,
                    lfs: 0,
                    lmf: -2,
                    sigma: total,
                });
                ledger.push(format!("MT{mt}: product is unmapped leakage"));
            }
        }
    }

    remap_levels(&mut rows, &mut ledger);
    let index = TargetIndex {
        file: file.into(),
        source_sha256: source_sha256.into(),
        mat: metadata.mat,
        za: metadata.za,
        liso: metadata.liso,
        awr: metadata.awr,
        evaluation_temperature_K: metadata.evaluation_temperature_k,
        n_mf2: evaluation.mf2_sections.len(),
        n_mf3: evaluation.mf3.len(),
        n_mf6: evaluation.mf6.len(),
        n_mf8: evaluation.mf8.len(),
        n_mf9: evaluation.mf9.len(),
        n_mf10: evaluation.mf10.len(),
        n_rows: rows.len(),
        ledger,
    };
    Ok(BuiltTarget { index, rows })
}

fn build_source(
    path: &Path,
    options: &BuildOptions,
    products_by_mt: &BTreeMap<i32, (i32, i32)>,
) -> Result<BuiltSource, String> {
    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?;
    let before = sha256_file(path)?;
    let key = checkpoint_key(&before, options);
    if let Some(cache) = &options.cache {
        if let Some(source) = load_checkpoint(cache, &key, &before, &options.groups) {
            if sha256_file(path)? != before {
                return Err(format!(
                    "source {} changed while its checkpoint was validated",
                    path.display()
                ));
            }
            return Ok(source);
        }
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
    let evaluations = parse_evaluations(&text, options.projectile)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let first = evaluations
        .first()
        .ok_or_else(|| format!("{} contains no evaluations", path.display()))?;
    let detected = detected_format(&text, first);
    let format = match options.format {
        LibraryFormat::Auto => detected?,
        explicit => {
            if let Ok(detected) = detected {
                if detected != explicit {
                    return Err(format!(
                        "{} identifies as {}, not requested {} format",
                        path.display(),
                        detected.name(),
                        explicit.name()
                    ));
                }
            }
            explicit
        }
    };
    let projectile = first.metadata.projectile;
    if format == LibraryFormat::Eaf && projectile != Projectile::Neutron {
        return Err(format!(
            "{}: EAF format requires neutron data",
            path.display()
        ));
    }
    let mut targets = Vec::with_capacity(evaluations.len());
    for evaluation in evaluations {
        if evaluation.metadata.projectile != projectile {
            return Err(format!(
                "{} contains mixed {} and {} evaluations",
                path.display(),
                projectile.name(),
                evaluation.metadata.projectile.name()
            ));
        }
        targets.push(
            build_evaluation(
                evaluation,
                format,
                filename,
                &before,
                EvaluationBuildSettings {
                    groups: &options.groups,
                    temperature_K: options.temperature_K,
                    grid_density: options.grid_density,
                },
                products_by_mt,
            )
            .map_err(|error| format!("{}: {error}", path.display()))?,
        );
    }
    let after = sha256_file(path)?;
    if before != after {
        return Err(format!(
            "source {} changed during the library build",
            path.display()
        ));
    }
    let source = BuiltSource {
        format,
        projectile,
        targets,
        from_cache: false,
    };
    if let Some(cache) = &options.cache {
        store_checkpoint(cache, &key, &before, &source, &options.groups)?;
    }
    Ok(source)
}

pub fn index_path(output: impl AsRef<Path>) -> Result<PathBuf, String> {
    let output = output.as_ref();
    if output.extension().and_then(OsStr::to_str) != Some("npz") {
        return Err("activation-library output must end in .npz".into());
    }
    let stem = output
        .file_stem()
        .and_then(OsStr::to_str)
        .ok_or("activation-library output needs a UTF-8 filename")?;
    Ok(output.with_file_name(format!("{stem}_index.json")))
}

fn duplicate_target_error(previous: &TargetIndex, current: &TargetIndex) -> String {
    format!(
        "duplicate target ZA={}/LISO={} in files '{}' and '{}'",
        current.za, current.liso, previous.file, current.file
    )
}

pub(crate) fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<(), String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT_JSON_TEMPORARY: AtomicU64 = AtomicU64::new(0);
    let text = serde_json::to_string_pretty(value)
        .map_err(|error| format!("cannot serialize library index: {error}"))?;
    let nonce = NEXT_JSON_TEMPORARY.fetch_add(1, Ordering::Relaxed);
    let temporary = path.with_file_name(format!(
        ".{}.{}.{nonce}.tmp",
        path.file_name().and_then(OsStr::to_str).unwrap_or("index"),
        std::process::id()
    ));
    std::fs::write(&temporary, format!("{text}\n"))
        .map_err(|error| format!("cannot write {}: {error}", temporary.display()))?;
    std::fs::rename(&temporary, path)
        .map_err(|error| format!("cannot publish {}: {error}", path.display()))
}

/// Build and atomically publish a deterministic activation-library NPZ and canonical index.
pub fn build_library(
    input: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &BuildOptions,
) -> Result<BuildSummary, String> {
    validate_options(options)?;
    let input = input.as_ref();
    let output = output.as_ref();
    let index_path = index_path(output)?;
    let files = discover_inputs(input, Some(output))?;
    if let Some(cache) = &options.cache {
        if input.is_dir() && cache.starts_with(input) {
            return Err("checkpoint cache must be outside the input directory".into());
        }
        match std::fs::symlink_metadata(cache) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(format!(
                    "checkpoint cache {} is not a real directory",
                    cache.display()
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                std::fs::create_dir_all(cache).map_err(|error| {
                    format!(
                        "cannot create checkpoint cache {}: {error}",
                        cache.display()
                    )
                })?;
            }
            Err(error) => {
                return Err(format!(
                    "cannot inspect checkpoint cache {}: {error}",
                    cache.display()
                ));
            }
        }
    }
    let products_by_mt = mt_products()?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(options.workers)
        .build()
        .map_err(|error| format!("cannot create builder worker pool: {error}"))?;
    let sources: Vec<BuiltSource> = pool.install(|| {
        files
            .par_iter()
            .map(|path| build_source(path, options, &products_by_mt))
            .collect::<Result<Vec<_>, _>>()
    })?;
    let projectile = sources
        .first()
        .ok_or("activation-library build produced no sources")?
        .projectile;
    let format = sources[0].format;
    if sources.iter().any(|source| source.projectile != projectile) {
        return Err("input directory contains mixed projectile evaluations".into());
    }
    if sources.iter().any(|source| source.format != format) {
        return Err("input directory contains mixed TENDL/EAF evaluations".into());
    }
    let cache_hits = sources.iter().filter(|source| source.from_cache).count();
    if projectile != Projectile::Neutron && options.temperature_K != 0.0 {
        return Err(format!("{} libraries require 0 K", projectile.name()));
    }
    if options.groups.name.starts_with("fispact-") {
        let expected = if projectile == Projectile::Neutron {
            709
        } else {
            162
        };
        if options.groups.groups() != expected {
            return Err(format!(
                "{} requires fispact-{expected}, got {}",
                projectile.name(),
                options.groups.name
            ));
        }
    }

    let mut targets = Vec::new();
    let mut seen_targets = BTreeMap::new();
    let mut rows = Vec::new();
    let mut sig = Vec::new();
    for source in sources {
        for target in source.targets {
            let identity = (target.index.za, target.index.liso);
            if let Some(previous) = seen_targets.insert(identity, targets.len()) {
                return Err(duplicate_target_error(&targets[previous], &target.index));
            }
            let target_number = targets.len();
            for row in target.rows {
                rows.push(Row {
                    target: target_number,
                    mt: row.mt,
                    zap: row.zap,
                    lfs: row.lfs,
                    lmf: row.lmf,
                });
                sig.extend(row.sigma);
            }
            targets.push(target.index);
        }
    }
    let library = Library {
        rows,
        sig,
        ngroups: options.groups.groups(),
        bounds: options.groups.boundaries_ev.clone(),
    };
    library.validate()?;
    write_npz(output, &library)?;
    let npz_hash = sha256_file(output)?;
    let fingerprint = builder_fingerprint();
    let index = BuildIndex {
        schema: "actinv-library-index-1",
        format: format.name().into(),
        projectile: projectile.name().into(),
        temperature_K: options.temperature_K,
        groups: options.groups.name.clone(),
        group_boundary_sha256: options.groups.hash(),
        weighting: "flat-lethargy",
        builder_fingerprint: fingerprint.clone(),
        options: CanonicalOptions {
            grid_density: options.grid_density,
        },
        targets,
        n_rows: library.rows.len(),
        columns: "rows: (target, MT, ZAP, LFS, LMF)",
        sha256_npz: npz_hash.clone(),
    };
    if let Err(error) = write_json_atomic(&index_path, &index) {
        let _ = std::fs::remove_file(output);
        return Err(error);
    }
    Ok(BuildSummary {
        output: output.to_path_buf(),
        index: index_path,
        projectile,
        targets: index.targets.len(),
        rows: library.rows.len(),
        cache_hits,
        sha256_npz: npz_hash,
        builder_fingerprint: fingerprint,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table(y: [f64; 2]) -> Tabulated {
        Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![1.0, 4.0],
            y: y.to_vec(),
        }
    }

    fn evaluation(projectile: Projectile) -> Evaluation {
        Evaluation {
            metadata: crate::activation::TargetMetadata {
                mat: 1,
                za: 26056,
                awr: 55.45,
                liso: 0,
                awi: f64::from(projectile.za().1),
                nsub: projectile.nsub(),
                projectile,
                evaluation_temperature_k: 0.0,
            },
            mf2_sections: BTreeSet::new(),
            resonance: None,
            mf3: BTreeMap::from([(102, table([2.0, 2.0]))]),
            mf6: BTreeMap::new(),
            mf8: BTreeMap::new(),
            mf9: BTreeMap::new(),
            mf10: BTreeMap::new(),
        }
    }

    fn target_index(file: &str) -> TargetIndex {
        TargetIndex {
            file: file.into(),
            source_sha256: "0".repeat(64),
            mat: 1,
            za: 26056,
            liso: 0,
            awr: 55.45,
            evaluation_temperature_K: 0.0,
            n_mf2: 0,
            n_mf3: 1,
            n_mf6: 0,
            n_mf8: 0,
            n_mf9: 0,
            n_mf10: 0,
            n_rows: 2,
            ledger: Vec::new(),
        }
    }

    #[test]
    fn duplicate_target_error_names_both_sources() {
        let error =
            duplicate_target_error(&target_index("first.endf"), &target_index("second.endf"));
        assert!(error.contains("ZA=26056/LISO=0"), "{error}");
        assert!(error.contains("first.endf"), "{error}");
        assert!(error.contains("second.endf"), "{error}");
    }

    #[test]
    fn aggregate_mt1_and_mt3_values_cannot_change_emitted_rows() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut left = evaluation(Projectile::Neutron);
        left.mf3.insert(1, table([1.0, 2.0]));
        left.mf3.insert(3, table([3.0, 4.0]));
        let mut right = left.clone();
        right.mf3.insert(1, table([1e100, 1e-100]));
        right.mf3.insert(3, table([7.0, 8.0]));
        let settings = EvaluationBuildSettings {
            groups: &groups,
            temperature_K: 0.0,
            grid_density: 1.0,
        };
        let products = BTreeMap::from([(102, (0, 1))]);
        let left = build_evaluation(
            left,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings,
            &products,
        )
        .unwrap();
        let right = build_evaluation(
            right,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings,
            &products,
        )
        .unwrap();
        assert_eq!(left.rows.len(), right.rows.len());
        for (left, right) in left.rows.iter().zip(&right.rows) {
            assert_eq!(
                (left.mt, left.zap, left.lfs, left.lmf, &left.sigma),
                (right.mt, right.zap, right.lfs, right.lmf, &right.sigma)
            );
        }
    }

    #[test]
    fn checkpoint_restores_exact_target_float_bits() {
        let original = target_index("rb94.endf");
        let awr = 105.98700000000001_f64;
        let temperature = -0.0_f64;
        let mut cached = original;
        cached.awr = awr;
        cached.evaluation_temperature_K = temperature;
        let bits = [
            cached.awr.to_bits(),
            cached.evaluation_temperature_K.to_bits(),
        ];
        let text = serde_json::to_string(&cached).unwrap();
        let mut restored: TargetIndex = serde_json::from_str(&text).unwrap();
        restore_target_float_bits(&mut restored, bits);
        assert_eq!(restored.awr.to_bits(), awr.to_bits());
        assert_eq!(
            restored.evaluation_temperature_K.to_bits(),
            temperature.to_bits()
        );
    }

    #[test]
    fn charged_residual_arithmetic_includes_projectile() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let products = BTreeMap::from([(102, (0, 1))]);
        let built = build_evaluation(
            evaluation(Projectile::Proton),
            LibraryFormat::Tendl,
            "p-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &products,
        )
        .unwrap();
        assert_eq!(built.rows.len(), 2);
        assert_eq!(built.rows[0].zap, -1);
        assert_eq!(built.rows[1].zap, 27057);
        assert_eq!(built.rows[1].sigma, vec![2.0]);
    }

    #[test]
    fn resonance_sections_fail_closed_until_processed() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut evaluation = evaluation(Projectile::Neutron);
        evaluation.mf2_sections.insert(151);
        let error = build_evaluation(
            evaluation,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(
            error.contains("without parsed resonance parameters"),
            "{error}"
        );
    }

    #[test]
    fn unsupported_rml_cannot_fall_back_to_mf3() {
        use crate::resonance::{
            ParticlePair, RMatrixLimited, RangeData, ResonanceEvaluation, ResonanceIsotope,
            ResonanceRange, RmlChannel, RmlResonance, SpinGroup,
        };

        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf2_sections.insert(151);
        input.resonance = Some(ResonanceEvaluation {
            za: 26056,
            awr: 55.45,
            isotopes: vec![ResonanceIsotope {
                zai: 26056,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
                    energy_min: 1.0,
                    energy_max: 4.0,
                    lru: 1,
                    lrf: 7,
                    naps: 1,
                    scattering_radius: None,
                    data: RangeData::RMatrixLimited(RMatrixLimited {
                        reduced_widths: false,
                        krm: 3,
                        particle_pairs: vec![ParticlePair {
                            mass_a: 1.0,
                            mass_b: 55.0,
                            za: 0,
                            zb: 26,
                            spin_a: 0.5,
                            spin_b: 0.0,
                            q_value: 0.0,
                            penetrability: 1,
                            shift: 0,
                            mt: 2,
                            parity_a: 1,
                            parity_b: 1,
                        }],
                        spin_groups: vec![SpinGroup {
                            spin: 0.5,
                            parity: 1.0,
                            channels: vec![RmlChannel {
                                pair: 0,
                                l: 0,
                                spin: 0.5,
                                boundary: 0.0,
                                effective_radius: 0.5,
                                true_radius: 0.5,
                            }],
                            resonances: vec![RmlResonance {
                                energy: 2.0,
                                widths: vec![0.4],
                            }],
                            backgrounds: Vec::new(),
                            phase_shifts: Vec::new(),
                        }],
                    }),
                }],
            }],
        });
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(
            error.contains("needs exactly one eliminated MT=102 channel"),
            "{error}"
        );
    }

    #[test]
    fn resonance_only_capture_uses_an_explicit_zero_background() {
        use crate::resonance::{
            LegacyLGroup, LegacyResolved, LegacyResonance, RangeData, ResonanceEvaluation,
            ResonanceIsotope, ResonanceRange,
        };

        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.clear();
        input.mf2_sections.insert(151);
        input.resonance = Some(ResonanceEvaluation {
            za: 26056,
            awr: 55.45,
            isotopes: vec![ResonanceIsotope {
                zai: 26056,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
                    energy_min: 1.0,
                    energy_max: 4.0,
                    lru: 1,
                    lrf: 1,
                    naps: 1,
                    scattering_radius: None,
                    data: RangeData::BreitWigner(LegacyResolved {
                        spin: 0.0,
                        ap: 0.5,
                        groups: vec![LegacyLGroup {
                            awri: 55.45,
                            apl: 0.0,
                            qx: 0.0,
                            l: 0,
                            lrx: 0,
                            resonances: vec![LegacyResonance {
                                energy: 2.0,
                                spin: 0.5,
                                total: 0.2,
                                neutron: 0.1,
                                capture: 0.1,
                                fission_a: 0.0,
                                fission_b: 0.0,
                            }],
                        }],
                    }),
                }],
            }],
        });
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &BTreeMap::from([(102, (0, 1))]),
        )
        .unwrap();

        assert_eq!(built.rows.len(), 2);
        assert!(built.rows.iter().all(|row| row.mt == 102));
        assert!(built.rows.iter().all(|row| row.sigma[0] > 0.0));
        assert!(built
            .index
            .ledger
            .iter()
            .any(|entry| entry.contains("explicit zero background used")));
    }

    #[test]
    fn inelastic_descriptors_validate_ground_and_metastable_before_ground_omission() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.clear();
        input.mf8.insert(
            4,
            vec![
                ProductRef {
                    zap: 26056,
                    lfs: 0,
                    lmf: 10,
                },
                ProductRef {
                    zap: 26056,
                    lfs: 2,
                    lmf: 10,
                },
            ],
        );
        input.mf10.insert(
            4,
            vec![
                crate::activation::ProductTable {
                    zap: 26056,
                    lfs: 0,
                    table: table([3.0, 3.0]),
                },
                crate::activation::ProductTable {
                    zap: 26056,
                    lfs: 2,
                    table: table([0.25, 0.25]),
                },
            ],
        );
        let built = build_evaluation(
            input.clone(),
            LibraryFormat::Tendl,
            "n-Fe056m",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &BTreeMap::new(),
        )
        .unwrap();
        assert_eq!(built.rows.len(), 2);
        assert_eq!((built.rows[0].mt, built.rows[0].zap), (4, -1));
        assert_eq!(built.rows[0].sigma, vec![0.25]);
        assert_eq!(
            (
                built.rows[1].mt,
                built.rows[1].zap,
                built.rows[1].lfs,
                built.rows[1].lmf,
            ),
            (4, 26056, 1, 10)
        );
        assert_eq!(built.rows[1].sigma, vec![0.25]);

        input.mf8.get_mut(&4).unwrap()[0].zap = 25056;
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056m",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(error.contains("conflict with MF=10 products"), "{error}");
    }
}
