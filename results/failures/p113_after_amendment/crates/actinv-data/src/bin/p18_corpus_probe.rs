//! Bounded, one-file-at-a-time P18 corpus probe for MF=8/9/10 identity and conservation evidence.

use actinv_data::activation::{
    parse_state_audit_evaluations, Evaluation, ProductRef, ProductTable, Projectile,
};
use actinv_data::groups::{GroupStructure, Tabulated};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsStr;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

const POINTWISE_ABS_TOLERANCE_B: f64 = 1e-12;
const COLLAPSED_ABS_TOLERANCE_B: f64 = 1e-14;
const REL_TOLERANCE: f64 = 5e-10;

#[derive(Serialize)]
struct ProbeHeader<'a> {
    kind: &'static str,
    schema: &'static str,
    projectile: &'a str,
    group_structure: &'a str,
    groups: usize,
    start_after: Option<&'a str>,
    probe_source_sha256: String,
    activation_source_sha256: String,
    groups_source_sha256: String,
}

#[derive(Serialize)]
struct FileAudit {
    kind: &'static str,
    file: String,
    bytes: u64,
    source_sha256: String,
    targets: Vec<TargetAudit>,
    pass: bool,
}

#[derive(Serialize)]
struct TargetAudit {
    mat: i32,
    za: i32,
    lis: i32,
    liso: i32,
    elis_ev: f64,
    declarations: Vec<Declaration>,
    exact_duplicate_declarations: usize,
    issues: Vec<String>,
    conservation: ConservationReport,
    pass: bool,
}

#[derive(Serialize)]
struct Declaration {
    mf: i32,
    mt: i32,
    ordinal: usize,
    zap: i32,
    lmf: i32,
    raw_lfs: i32,
    elfs_ev: Option<f64>,
    qm_ev: Option<f64>,
    qi_ev: Option<f64>,
    table_sha256: Option<String>,
    matching_mf8_elfs_ev: Vec<f64>,
}

#[derive(Serialize)]
struct MissingTotal {
    mf: i32,
    mt: i32,
    state_products: usize,
}

#[derive(Serialize)]
struct WorstComparison {
    mf: i32,
    mt: i32,
    scope: &'static str,
    side: Option<&'static str>,
    energy_ev: Option<f64>,
    group: Option<usize>,
    zap: i32,
    raw_lfs: Option<i32>,
    partial_b: f64,
    total_b: f64,
    peak_total_b: f64,
    tolerance_b: f64,
    excess_b: f64,
}

#[derive(Serialize)]
struct ConservationReport {
    mf9_declared_products: usize,
    mf9_unique_products: usize,
    mf10_declared_products: usize,
    mf10_unique_products: usize,
    pointwise_individual_comparisons: usize,
    pointwise_sum_comparisons: usize,
    collapsed_individual_comparisons: usize,
    collapsed_sum_comparisons: usize,
    mf9_comparisons: usize,
    mf10_comparisons: usize,
    missing_totals: Vec<MissingTotal>,
    pointwise_individual_violations: usize,
    pointwise_sum_violations: usize,
    collapsed_individual_violations: usize,
    collapsed_sum_violations: usize,
    mf9_violations: usize,
    mf10_violations: usize,
    violations: usize,
    worst: Option<WorstComparison>,
    comparison_sha256: String,
    pass: bool,
}

struct ConservationAccumulator {
    mf9_declared_products: usize,
    mf9_unique_products: usize,
    mf10_declared_products: usize,
    mf10_unique_products: usize,
    pointwise_individual_comparisons: usize,
    pointwise_sum_comparisons: usize,
    collapsed_individual_comparisons: usize,
    collapsed_sum_comparisons: usize,
    mf9_comparisons: usize,
    mf10_comparisons: usize,
    missing_totals: Vec<MissingTotal>,
    pointwise_individual_violations: usize,
    pointwise_sum_violations: usize,
    collapsed_individual_violations: usize,
    collapsed_sum_violations: usize,
    mf9_violations: usize,
    mf10_violations: usize,
    violations: usize,
    worst: Option<WorstComparison>,
    comparison_hash: Sha256,
}

#[derive(Clone, Copy)]
enum ComparisonLocation {
    Pointwise { side: &'static str, energy_ev: f64 },
    Collapsed { group: usize },
}

impl ConservationAccumulator {
    fn new() -> Self {
        Self {
            mf9_declared_products: 0,
            mf9_unique_products: 0,
            mf10_declared_products: 0,
            mf10_unique_products: 0,
            pointwise_individual_comparisons: 0,
            pointwise_sum_comparisons: 0,
            collapsed_individual_comparisons: 0,
            collapsed_sum_comparisons: 0,
            mf9_comparisons: 0,
            mf10_comparisons: 0,
            missing_totals: Vec::new(),
            pointwise_individual_violations: 0,
            pointwise_sum_violations: 0,
            collapsed_individual_violations: 0,
            collapsed_sum_violations: 0,
            mf9_violations: 0,
            mf10_violations: 0,
            violations: 0,
            worst: None,
            comparison_hash: Sha256::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn observe(
        &mut self,
        mf: i32,
        mt: i32,
        location: ComparisonLocation,
        zap: i32,
        raw_lfs: Option<i32>,
        partial_b: f64,
        total_b: f64,
        peak_total_b: f64,
        summed: bool,
    ) -> Result<(), String> {
        if !partial_b.is_finite() || partial_b < 0.0 {
            return Err(format!(
                "MF={mf}/MT={mt} ZAP={zap} state partial is nonfinite or negative ({partial_b:.17e} barn)"
            ));
        }
        if !total_b.is_finite() || total_b < 0.0 {
            return Err(format!(
                "MF=3/MT={mt} total is nonfinite or negative ({total_b:.17e} barn)"
            ));
        }
        let absolute = match location {
            ComparisonLocation::Pointwise { .. } => POINTWISE_ABS_TOLERANCE_B,
            ComparisonLocation::Collapsed { .. } => COLLAPSED_ABS_TOLERANCE_B,
        };
        let tolerance_b = absolute.max(REL_TOLERANCE * total_b.max(peak_total_b));
        let excess_b = partial_b - total_b - tolerance_b;
        match (location, summed) {
            (ComparisonLocation::Pointwise { .. }, false) => {
                self.pointwise_individual_comparisons += 1;
            }
            (ComparisonLocation::Pointwise { .. }, true) => {
                self.pointwise_sum_comparisons += 1;
            }
            (ComparisonLocation::Collapsed { .. }, false) => {
                self.collapsed_individual_comparisons += 1;
            }
            (ComparisonLocation::Collapsed { .. }, true) => {
                self.collapsed_sum_comparisons += 1;
            }
        }
        if mf == 9 {
            self.mf9_comparisons += 1;
        } else {
            self.mf10_comparisons += 1;
        }
        if excess_b > 0.0 {
            self.violations += 1;
            if mf == 9 {
                self.mf9_violations += 1;
            } else {
                self.mf10_violations += 1;
            }
            match (location, summed) {
                (ComparisonLocation::Pointwise { .. }, false) => {
                    self.pointwise_individual_violations += 1;
                }
                (ComparisonLocation::Pointwise { .. }, true) => {
                    self.pointwise_sum_violations += 1;
                }
                (ComparisonLocation::Collapsed { .. }, false) => {
                    self.collapsed_individual_violations += 1;
                }
                (ComparisonLocation::Collapsed { .. }, true) => {
                    self.collapsed_sum_violations += 1;
                }
            }
        }
        self.comparison_hash.update(mf.to_le_bytes());
        self.comparison_hash.update(mt.to_le_bytes());
        self.comparison_hash.update(zap.to_le_bytes());
        self.comparison_hash
            .update(raw_lfs.unwrap_or(-1).to_le_bytes());
        self.comparison_hash.update([u8::from(summed)]);
        match location {
            ComparisonLocation::Pointwise { side, energy_ev } => {
                self.comparison_hash.update(b"pointwise\0");
                self.comparison_hash.update(side.as_bytes());
                self.comparison_hash
                    .update(energy_ev.to_bits().to_le_bytes());
            }
            ComparisonLocation::Collapsed { group } => {
                self.comparison_hash.update(b"collapsed\0");
                self.comparison_hash.update((group as u64).to_le_bytes());
            }
        }
        for value in [partial_b, total_b, tolerance_b, excess_b] {
            self.comparison_hash.update(value.to_bits().to_le_bytes());
        }
        if self
            .worst
            .as_ref()
            .is_none_or(|worst| excess_b > worst.excess_b)
        {
            let (scope, side, energy_ev, group) = match location {
                ComparisonLocation::Pointwise { side, energy_ev } => {
                    ("pointwise", Some(side), Some(energy_ev), None)
                }
                ComparisonLocation::Collapsed { group } => ("collapsed", None, None, Some(group)),
            };
            self.worst = Some(WorstComparison {
                mf,
                mt,
                scope,
                side,
                energy_ev,
                group,
                zap,
                raw_lfs,
                partial_b,
                total_b,
                peak_total_b,
                tolerance_b,
                excess_b,
            });
        }
        Ok(())
    }

    fn finish(self) -> ConservationReport {
        let pass = self.missing_totals.is_empty() && self.violations == 0;
        ConservationReport {
            mf9_declared_products: self.mf9_declared_products,
            mf9_unique_products: self.mf9_unique_products,
            mf10_declared_products: self.mf10_declared_products,
            mf10_unique_products: self.mf10_unique_products,
            pointwise_individual_comparisons: self.pointwise_individual_comparisons,
            pointwise_sum_comparisons: self.pointwise_sum_comparisons,
            collapsed_individual_comparisons: self.collapsed_individual_comparisons,
            collapsed_sum_comparisons: self.collapsed_sum_comparisons,
            mf9_comparisons: self.mf9_comparisons,
            mf10_comparisons: self.mf10_comparisons,
            missing_totals: self.missing_totals,
            pointwise_individual_violations: self.pointwise_individual_violations,
            pointwise_sum_violations: self.pointwise_sum_violations,
            collapsed_individual_violations: self.collapsed_individual_violations,
            collapsed_sum_violations: self.collapsed_sum_violations,
            mf9_violations: self.mf9_violations,
            mf10_violations: self.mf10_violations,
            violations: self.violations,
            worst: self.worst,
            comparison_sha256: format!("{:x}", self.comparison_hash.finalize()),
            pass,
        }
    }
}

fn sha256_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn table_sha256(table: &Tabulated) -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-P18-TAB1-v1\0");
    hash.update((table.interpolation.len() as u64).to_le_bytes());
    for &(nbt, law) in &table.interpolation {
        hash.update((nbt as u64).to_le_bytes());
        hash.update(law.to_le_bytes());
    }
    hash.update((table.x.len() as u64).to_le_bytes());
    for (&x, &y) in table.x.iter().zip(&table.y) {
        hash.update(x.to_bits().to_le_bytes());
        hash.update(y.to_bits().to_le_bytes());
    }
    format!("{:x}", hash.finalize())
}

fn descriptor_issues(
    evaluation: &Evaluation,
    declarations: &mut Vec<Declaration>,
) -> (usize, Vec<String>) {
    let mut issues = Vec::new();
    let mut exact_duplicates = 0usize;
    for (&mt, descriptors) in &evaluation.mf8 {
        let mut unique: BTreeMap<(i32, i32, i32), &ProductRef> = BTreeMap::new();
        for (ordinal, descriptor) in descriptors.iter().enumerate() {
            declarations.push(Declaration {
                mf: 8,
                mt,
                ordinal,
                zap: descriptor.zap,
                lmf: descriptor.lmf,
                raw_lfs: descriptor.lfs,
                elfs_ev: Some(descriptor.elfs_ev),
                qm_ev: None,
                qi_ev: None,
                table_sha256: None,
                matching_mf8_elfs_ev: Vec::new(),
            });
            let identity = (descriptor.lmf, descriptor.zap, descriptor.lfs);
            if let Some(previous) = unique.get(&identity) {
                if **previous == *descriptor {
                    exact_duplicates += 1;
                } else {
                    issues.push(format!(
                        "MF=8/MT={mt} has conflicting duplicate LMF={}/ZAP={}/LFS={}",
                        descriptor.lmf, descriptor.zap, descriptor.lfs
                    ));
                }
            } else {
                unique.insert(identity, descriptor);
            }
        }
    }
    (exact_duplicates, issues)
}

fn unique_tables<'a>(
    mf: i32,
    mt: i32,
    products: &'a [ProductTable],
    issues: &mut Vec<String>,
    exact_duplicates: &mut usize,
) -> Vec<&'a ProductTable> {
    let mut unique: BTreeMap<(i32, i32), &ProductTable> = BTreeMap::new();
    for product in products {
        let identity = (product.zap, product.lfs);
        if let Some(previous) = unique.get(&identity) {
            if **previous == *product {
                *exact_duplicates += 1;
            } else {
                issues.push(format!(
                    "MF={mf}/MT={mt} has conflicting duplicate ZAP={}/LFS={}",
                    product.zap, product.lfs
                ));
            }
        } else {
            unique.insert(identity, product);
        }
    }
    unique.into_values().collect()
}

fn descriptor_matches(evaluation: &Evaluation, mf: i32, mt: i32, zap: i32, lfs: i32) -> Vec<f64> {
    evaluation
        .mf8
        .get(&mt)
        .into_iter()
        .flatten()
        .filter(|descriptor| descriptor.lmf == mf && descriptor.zap == zap && descriptor.lfs == lfs)
        .map(|descriptor| descriptor.elfs_ev)
        .collect()
}

fn check_descriptor_set(
    evaluation: &Evaluation,
    mf: i32,
    mt: i32,
    products: &[&ProductTable],
    issues: &mut Vec<String>,
) {
    let declared: BTreeSet<_> = evaluation
        .mf8
        .get(&mt)
        .into_iter()
        .flatten()
        .filter(|descriptor| descriptor.lmf == mf)
        .map(|descriptor| (descriptor.zap, descriptor.lfs))
        .collect();
    let actual: BTreeSet<_> = products
        .iter()
        .map(|product| (product.zap, product.lfs))
        .collect();
    if !declared.is_empty() && declared != actual {
        issues.push(format!(
            "MF=8/MT={mt}/LMF={mf} identities {declared:?} differ from MF={mf} identities {actual:?}"
        ));
    }
}

fn evaluate_product(
    mf: i32,
    total_value: f64,
    product: &ProductTable,
    energy_ev: f64,
    left_limit: bool,
) -> Result<f64, String> {
    let value = if left_limit {
        product.table.evaluate_left_limit(energy_ev)?
    } else {
        product.table.evaluate(energy_ev)?
    };
    Ok(if mf == 9 { total_value * value } else { value })
}

fn audit_products(
    mf: i32,
    mt: i32,
    total: Option<&Tabulated>,
    products: &[&ProductTable],
    groups: &GroupStructure,
    audit: &mut ConservationAccumulator,
) -> Result<(), String> {
    let state_products: Vec<_> = products
        .iter()
        .copied()
        .filter(|product| product.zap >= 0)
        .collect();
    if state_products.is_empty() {
        return Ok(());
    }
    let Some(total) = total else {
        audit.missing_totals.push(MissingTotal {
            mf,
            mt,
            state_products: state_products.len(),
        });
        return Ok(());
    };
    if total
        .y
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!(
            "MF=3/MT={mt} contains a nonfinite or negative total"
        ));
    }
    let peak_total = total.y.iter().copied().fold(0.0, f64::max);
    let mut energies = Vec::with_capacity(
        total.x.len()
            + state_products
                .iter()
                .map(|product| product.table.x.len())
                .sum::<usize>(),
    );
    energies.extend(total.x.iter().copied());
    for product in &state_products {
        energies.extend(product.table.x.iter().copied());
    }
    energies.sort_by(f64::total_cmp);
    energies.dedup_by(|left, right| *left == *right);
    let zaps: BTreeSet<_> = state_products.iter().map(|product| product.zap).collect();
    for energy_ev in energies {
        for (side, left_limit) in [("right", false), ("left", true)] {
            let total_value = if left_limit {
                total.evaluate_left_limit(energy_ev)?
            } else {
                total.evaluate(energy_ev)?
            };
            for product in &state_products {
                audit.observe(
                    mf,
                    mt,
                    ComparisonLocation::Pointwise { side, energy_ev },
                    product.zap,
                    Some(product.lfs),
                    evaluate_product(mf, total_value, product, energy_ev, left_limit)?,
                    total_value,
                    peak_total,
                    false,
                )?;
            }
            for zap in &zaps {
                let mut sum = 0.0;
                let mut correction = 0.0;
                for product in state_products.iter().filter(|product| product.zap == *zap) {
                    let value = evaluate_product(mf, total_value, product, energy_ev, left_limit)?;
                    let next = sum + value;
                    correction += if sum.abs() >= value.abs() {
                        (sum - next) + value
                    } else {
                        (value - next) + sum
                    };
                    sum = next;
                }
                audit.observe(
                    mf,
                    mt,
                    ComparisonLocation::Pointwise { side, energy_ev },
                    *zap,
                    None,
                    sum + correction,
                    total_value,
                    peak_total,
                    true,
                )?;
            }
        }
    }

    let collapsed_total = groups.collapse(total)?;
    if collapsed_total
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!(
            "MF=3/MT={mt} collapsed to a nonfinite or negative total"
        ));
    }
    let peak_collapsed = collapsed_total.iter().copied().fold(0.0, f64::max);
    let collapsed_products: Vec<_> = state_products
        .iter()
        .map(|product| {
            let values = if mf == 9 {
                groups.collapse_product(&[total, &product.table])?
            } else {
                groups.collapse(&product.table)?
            };
            Ok::<_, String>((*product, values))
        })
        .collect::<Result<_, _>>()?;
    for (product, partial) in &collapsed_products {
        for (group, (&partial_b, &total_b)) in partial.iter().zip(&collapsed_total).enumerate() {
            audit.observe(
                mf,
                mt,
                ComparisonLocation::Collapsed { group },
                product.zap,
                Some(product.lfs),
                partial_b,
                total_b,
                peak_collapsed,
                false,
            )?;
        }
    }
    for zap in zaps {
        for (group, &total_b) in collapsed_total.iter().enumerate() {
            let mut sum = 0.0;
            let mut correction = 0.0;
            for (_, partial) in collapsed_products
                .iter()
                .filter(|(product, _)| product.zap == zap)
            {
                let value = partial[group];
                let next = sum + value;
                correction += if sum.abs() >= value.abs() {
                    (sum - next) + value
                } else {
                    (value - next) + sum
                };
                sum = next;
            }
            audit.observe(
                mf,
                mt,
                ComparisonLocation::Collapsed { group },
                zap,
                None,
                sum + correction,
                total_b,
                peak_collapsed,
                true,
            )?;
        }
    }
    Ok(())
}

fn audit_evaluation(
    evaluation: &Evaluation,
    groups: &GroupStructure,
) -> Result<TargetAudit, String> {
    let mut declarations = Vec::new();
    let (mut exact_duplicates, mut issues) = descriptor_issues(evaluation, &mut declarations);
    let mut conservation = ConservationAccumulator::new();
    for (mf, sections) in [(9, &evaluation.mf9), (10, &evaluation.mf10)] {
        for (&mt, products) in sections {
            if mf == 9 {
                conservation.mf9_declared_products += products.len();
            } else {
                conservation.mf10_declared_products += products.len();
            }
            for (ordinal, product) in products.iter().enumerate() {
                declarations.push(Declaration {
                    mf,
                    mt,
                    ordinal,
                    zap: product.zap,
                    lmf: mf,
                    raw_lfs: product.lfs,
                    elfs_ev: None,
                    qm_ev: Some(product.qm_ev),
                    qi_ev: Some(product.qi_ev),
                    table_sha256: Some(table_sha256(&product.table)),
                    matching_mf8_elfs_ev: descriptor_matches(
                        evaluation,
                        mf,
                        mt,
                        product.zap,
                        product.lfs,
                    ),
                });
            }
            let unique = unique_tables(mf, mt, products, &mut issues, &mut exact_duplicates);
            if mf == 9 {
                conservation.mf9_unique_products += unique.len();
            } else {
                conservation.mf10_unique_products += unique.len();
            }
            check_descriptor_set(evaluation, mf, mt, &unique, &mut issues);
            audit_products(
                mf,
                mt,
                evaluation.mf3.get(&mt),
                &unique,
                groups,
                &mut conservation,
            )?;
        }
    }
    declarations.sort_by_key(|row| (row.mf, row.mt, row.ordinal));
    let conservation = conservation.finish();
    let pass = issues.is_empty() && conservation.pass;
    Ok(TargetAudit {
        mat: evaluation.metadata.mat,
        za: evaluation.metadata.za,
        lis: evaluation.metadata.lis,
        liso: evaluation.metadata.liso,
        elis_ev: evaluation.metadata.elis_ev,
        declarations,
        exact_duplicate_declarations: exact_duplicates,
        issues,
        conservation,
        pass,
    })
}

fn audit_file(
    path: &Path,
    name: String,
    projectile: Projectile,
    groups: &GroupStructure,
) -> Result<FileAudit, String> {
    let bytes =
        std::fs::read(path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    let source_sha256 = sha256_bytes(&bytes);
    let text = std::str::from_utf8(&bytes)
        .map_err(|error| format!("{} is not UTF-8: {error}", path.display()))?;
    let evaluations = parse_state_audit_evaluations(text, Some(projectile))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let targets = evaluations
        .iter()
        .map(|evaluation| audit_evaluation(evaluation, groups))
        .collect::<Result<Vec<_>, _>>()?;
    let pass = !targets.is_empty() && targets.iter().all(|target| target.pass);
    Ok(FileAudit {
        kind: "file",
        file: name,
        bytes: bytes.len() as u64,
        source_sha256,
        targets,
        pass,
    })
}

fn source_files(
    directory: &Path,
    start_after: Option<&str>,
) -> Result<Vec<(String, PathBuf)>, String> {
    let mut files = Vec::new();
    for entry in std::fs::read_dir(directory)
        .map_err(|error| format!("cannot read {}: {error}", directory.display()))?
    {
        let entry = entry.map_err(|error| format!("cannot read directory entry: {error}"))?;
        let file_type = entry
            .file_type()
            .map_err(|error| format!("cannot inspect {}: {error}", entry.path().display()))?;
        if !file_type.is_file() || entry.path().extension() != Some(OsStr::new("tendl")) {
            continue;
        }
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "TENDL source filename is not UTF-8".to_string())?;
        if start_after.is_some_and(|start| name.as_str() <= start) {
            continue;
        }
        files.push((name, entry.path()));
    }
    files.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(files)
}

fn write_json_line(output: &mut impl Write, value: &impl Serialize) -> Result<(), String> {
    serde_json::to_writer(&mut *output, value)
        .map_err(|error| format!("cannot serialize audit row: {error}"))?;
    output
        .write_all(b"\n")
        .map_err(|error| format!("cannot write audit row: {error}"))?;
    output
        .flush()
        .map_err(|error| format!("cannot flush audit row: {error}"))
}

fn run() -> Result<(), String> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if !(2..=3).contains(&arguments.len()) {
        return Err(
            "usage: p18_corpus_probe DIRECTORY neutron|proton|deuteron|alpha [START_AFTER]".into(),
        );
    }
    let directory = Path::new(&arguments[0]);
    let projectile = Projectile::parse(&arguments[1])?;
    let start_after = arguments.get(2).map(String::as_str);
    let groups = if projectile == Projectile::Neutron {
        GroupStructure::fispact_709()?
    } else {
        GroupStructure::fispact_162()?
    };
    let files = source_files(directory, start_after)?;
    let stdout = std::io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    write_json_line(
        &mut output,
        &ProbeHeader {
            kind: "header",
            schema: "actinv-p18-corpus-probe-1",
            projectile: projectile.name(),
            group_structure: &groups.name,
            groups: groups.groups(),
            start_after,
            probe_source_sha256: sha256_bytes(include_bytes!("p18_corpus_probe.rs")),
            activation_source_sha256: sha256_bytes(include_bytes!("../activation.rs")),
            groups_source_sha256: sha256_bytes(include_bytes!("../groups.rs")),
        },
    )?;
    for (name, path) in files {
        let audit = audit_file(&path, name, projectile, &groups)?;
        write_json_line(&mut output, &audit)?;
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("p18 corpus probe: {error}");
        std::process::exit(1);
    }
}
