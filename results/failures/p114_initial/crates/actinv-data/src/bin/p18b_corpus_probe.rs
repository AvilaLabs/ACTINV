//! Bounded P18b source/runtime classifier.
//!
//! This diagnostic deliberately lives outside the production builder. It reads one evaluation at a time, retains
//! the printed ordinate quantum needed by the frozen P18b protocol, and emits a resumable JSONL checkpoint. The
//! predecessor P18 checkpoint remains immutable and is bound by hash in the new header.

use actinv_data::activation::{
    parse_state_audit_evaluations, Evaluation, ProductTable, Projectile,
};
use actinv_data::endf::{
    checked_fields, parse_endf_float, parse_endf_i32, parse_endf_usize, parse_sections, Section,
};
use actinv_data::groups::{GroupStructure, Tabulated};
use actinv_data::processing::{has_resonance_contribution, process_reaction, ProcessedReaction};
use actinv_data::resonance::parse_mf2;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsStr;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

const EPS_STANDARD: f64 = 0.001;
const POINTWISE_ABS_TOLERANCE_B: f64 = 1e-12;
const COLLAPSED_ABS_TOLERANCE_B: f64 = 1e-14;
const P18_REL_TOLERANCE: f64 = 5e-10;
const NEUTRON_TEMPERATURE_K: f64 = 293.6;
const PROCESSING_GRID_DENSITY: f64 = 1.0;

#[derive(Clone)]
struct PrintedValue {
    value: f64,
    quantum: f64,
}

#[derive(Clone)]
struct PrintedTable {
    value: Tabulated,
    lower: Tabulated,
    upper: Tabulated,
}

#[derive(Clone)]
struct PrintedProduct {
    zap: i32,
    lfs: i32,
    qm: PrintedValue,
    qi: PrintedValue,
    table: PrintedTable,
}

#[derive(Default)]
struct PrintedEvaluation {
    awr: Option<PrintedValue>,
    awi: Option<PrintedValue>,
    mf3: BTreeMap<i32, PrintedTable>,
    mf9: BTreeMap<i32, Vec<PrintedProduct>>,
    mf10: BTreeMap<i32, Vec<PrintedProduct>>,
}

#[derive(Serialize)]
struct ProbeHeader<'a> {
    kind: &'static str,
    schema: &'static str,
    projectile: &'a str,
    group_structure: &'a str,
    groups: usize,
    temperature_k: f64,
    processing_grid_density: f64,
    predecessor_checkpoint_sha256: String,
    probe_source_sha256: String,
    activation_source_sha256: String,
    endf_source_sha256: String,
    groups_source_sha256: String,
    processing_source_sha256: String,
    resonance_source_sha256: String,
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
    liso: i32,
    source: SourceReport,
    runtime: RuntimeReport,
    pass: bool,
}

#[derive(Default, Serialize)]
struct ContractReport {
    product_tables: usize,
    missing_totals: usize,
    non_linlin_tables: usize,
    grid_contract_tables: usize,
    threshold_contract_tables: usize,
    issues: Vec<ContractIssue>,
}

#[derive(Serialize)]
struct ContractIssue {
    mf: i32,
    mt: i32,
    zap: i32,
    raw_lfs: i32,
    decision: &'static str,
    qi_bits: String,
    first_energy_bits: String,
    first_value_bits: String,
    off_grid_energy_bits: Option<String>,
    expected_threshold_low_bits: Option<String>,
    expected_threshold_high_bits: Option<String>,
}

#[derive(Serialize)]
struct SourceBoundary {
    mf: i32,
    mt: i32,
    scope: &'static str,
    side: Option<&'static str>,
    energy_bits: Option<String>,
    group: Option<usize>,
    zap: i32,
    raw_lfs: Option<i32>,
    summed: bool,
    partial_bits: String,
    total_bits: String,
    partial_low_bits: String,
    total_high_bits: String,
    peak_total_bits: String,
    p18_tolerance_bits: String,
    binary64_primary_class: &'static str,
    p18_violation: bool,
    standard_compatible: bool,
}

#[derive(Serialize)]
struct SourceReport {
    comparisons: usize,
    pointwise_individual_comparisons: usize,
    pointwise_sum_comparisons: usize,
    collapsed_individual_comparisons: usize,
    collapsed_sum_comparisons: usize,
    mf9_comparisons: usize,
    mf10_comparisons: usize,
    binary64_primary_counts: BTreeMap<&'static str, usize>,
    p18_violations: usize,
    p18_violations_by_binary64_primary: BTreeMap<&'static str, usize>,
    p18_violation_bitmap_hex: String,
    binary64_excesses: usize,
    binary64_standard_compatible_excesses: usize,
    comparison_sha256: String,
    mf9_fractions: FractionReport,
    contracts: ContractReport,
    boundary_examples: Vec<SourceBoundary>,
}

#[derive(Serialize)]
struct FractionReport {
    comparisons: usize,
    individual_comparisons: usize,
    sum_comparisons: usize,
    binary64_primary_counts: BTreeMap<&'static str, usize>,
    p18_violations: usize,
    p18_violation_bitmap_hex: String,
    binary64_excesses: usize,
    binary64_standard_compatible_excesses: usize,
    comparison_sha256: String,
    boundary_examples: Vec<SourceBoundary>,
}

#[derive(Serialize)]
struct RuntimeBoundary {
    mf: i32,
    mt: i32,
    zap: i32,
    group: usize,
    sum_bits: String,
    total_bits: String,
    raw_total_bits: String,
    decision: &'static str,
}

#[derive(Serialize)]
struct RuntimeReport {
    vectors: usize,
    group_comparisons: usize,
    unchanged: usize,
    standard_compatible_excesses: usize,
    outside_standard_excesses: usize,
    raw_runtime_total_differences: usize,
    processed_reactions: usize,
    inelastic_constructed_loss_groups: usize,
    comparison_sha256: String,
    boundary_examples: Vec<RuntimeBoundary>,
}

#[derive(Clone, Copy)]
enum Location {
    Pointwise { side: &'static str, energy_ev: f64 },
    Collapsed { group: usize },
}

#[derive(Clone, Copy)]
enum ContractClass {
    MissingTotalOrGrid,
    Threshold,
}

struct SourceAccumulator {
    comparisons: usize,
    pointwise_individual_comparisons: usize,
    pointwise_sum_comparisons: usize,
    collapsed_individual_comparisons: usize,
    collapsed_sum_comparisons: usize,
    mf9_comparisons: usize,
    mf10_comparisons: usize,
    binary64_primary_counts: BTreeMap<&'static str, usize>,
    p18_violations: usize,
    p18_violations_by_binary64_primary: BTreeMap<&'static str, usize>,
    p18_violation_bitmap: BitFlags,
    binary64_excesses: usize,
    binary64_standard_compatible_excesses: usize,
    comparison_hash: Sha256,
    fractions: FractionAccumulator,
    contracts: ContractReport,
    boundaries: Vec<SourceBoundary>,
}

struct FractionAccumulator {
    comparisons: usize,
    individual_comparisons: usize,
    sum_comparisons: usize,
    binary64_primary_counts: BTreeMap<&'static str, usize>,
    p18_violations: usize,
    p18_violation_bitmap: BitFlags,
    binary64_excesses: usize,
    binary64_standard_compatible_excesses: usize,
    comparison_hash: Sha256,
    boundaries: Vec<SourceBoundary>,
}

#[derive(Default)]
struct BitFlags {
    bytes: Vec<u8>,
    len: usize,
}

impl BitFlags {
    fn push(&mut self, value: bool) {
        if self.len.is_multiple_of(8) {
            self.bytes.push(0);
        }
        if value {
            let final_byte = self
                .bytes
                .last_mut()
                .expect("a byte was added before setting its bit");
            *final_byte |= 1 << (self.len % 8);
        }
        self.len += 1;
    }

    fn into_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut output = String::with_capacity(self.bytes.len() * 2);
        for byte in self.bytes {
            output.push(char::from(HEX[usize::from(byte >> 4)]));
            output.push(char::from(HEX[usize::from(byte & 0x0f)]));
        }
        output
    }
}

impl FractionAccumulator {
    fn new() -> Self {
        Self {
            comparisons: 0,
            individual_comparisons: 0,
            sum_comparisons: 0,
            binary64_primary_counts: BTreeMap::new(),
            p18_violations: 0,
            p18_violation_bitmap: BitFlags::default(),
            binary64_excesses: 0,
            binary64_standard_compatible_excesses: 0,
            comparison_hash: Sha256::new(),
            boundaries: Vec::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn observe(
        &mut self,
        mt: i32,
        side: &'static str,
        energy_ev: f64,
        zap: i32,
        raw_lfs: Option<i32>,
        value: f64,
        lower: f64,
        summed: bool,
        contract: Option<ContractClass>,
    ) -> Result<(), String> {
        for (name, number) in [("multiplicity", value), ("lower multiplicity", lower)] {
            if !number.is_finite() || number < 0.0 {
                return Err(format!(
                    "MF=9/MT={mt} ZAP={zap} {name} is nonfinite or negative ({number:.17e})"
                ));
            }
        }
        let tolerance = POINTWISE_ABS_TOLERANCE_B.max(P18_REL_TOLERANCE);
        // Preserve P18's frozen binary64 operation order exactly. Reassociating this as
        // `value > 1.0 + tolerance` changes decisions at rounding boundaries.
        let p18_violation = value - 1.0 - tolerance > 0.0;
        let binary64_excess = value > 1.0;
        let standard = standard_compatible(1.0, value);
        let primary = primary_class(contract, value, 1.0, lower, 1.0, p18_violation);
        self.comparisons += 1;
        if summed {
            self.sum_comparisons += 1;
        } else {
            self.individual_comparisons += 1;
        }
        *self.binary64_primary_counts.entry(primary).or_default() += 1;
        self.p18_violations += usize::from(p18_violation);
        self.p18_violation_bitmap.push(p18_violation);
        self.binary64_excesses += usize::from(binary64_excess);
        self.binary64_standard_compatible_excesses += usize::from(binary64_excess && standard);
        self.comparison_hash.update(mt.to_le_bytes());
        self.comparison_hash.update(zap.to_le_bytes());
        self.comparison_hash
            .update(raw_lfs.unwrap_or(-1).to_le_bytes());
        self.comparison_hash.update([u8::from(summed)]);
        self.comparison_hash.update(side.as_bytes());
        self.comparison_hash
            .update(energy_ev.to_bits().to_le_bytes());
        self.comparison_hash.update(primary.as_bytes());
        self.comparison_hash.update([u8::from(standard)]);
        if binary64_excess || p18_violation {
            retain_source_boundary(
                &mut self.boundaries,
                SourceBoundary {
                    mf: 9,
                    mt,
                    scope: "multiplicity",
                    side: Some(side),
                    energy_bits: Some(bits(energy_ev)),
                    group: None,
                    zap,
                    raw_lfs,
                    summed,
                    partial_bits: bits(value),
                    total_bits: bits(1.0),
                    partial_low_bits: bits(lower),
                    total_high_bits: bits(1.0),
                    peak_total_bits: bits(1.0),
                    p18_tolerance_bits: bits(tolerance),
                    binary64_primary_class: primary,
                    p18_violation,
                    standard_compatible: standard,
                },
            );
        }
        Ok(())
    }

    fn finish(self) -> FractionReport {
        FractionReport {
            comparisons: self.comparisons,
            individual_comparisons: self.individual_comparisons,
            sum_comparisons: self.sum_comparisons,
            binary64_primary_counts: self.binary64_primary_counts,
            p18_violations: self.p18_violations,
            p18_violation_bitmap_hex: self.p18_violation_bitmap.into_hex(),
            binary64_excesses: self.binary64_excesses,
            binary64_standard_compatible_excesses: self.binary64_standard_compatible_excesses,
            comparison_sha256: format!("{:x}", self.comparison_hash.finalize()),
            boundary_examples: self.boundaries,
        }
    }
}

impl SourceAccumulator {
    fn new() -> Self {
        Self {
            comparisons: 0,
            pointwise_individual_comparisons: 0,
            pointwise_sum_comparisons: 0,
            collapsed_individual_comparisons: 0,
            collapsed_sum_comparisons: 0,
            mf9_comparisons: 0,
            mf10_comparisons: 0,
            binary64_primary_counts: BTreeMap::new(),
            p18_violations: 0,
            p18_violations_by_binary64_primary: BTreeMap::new(),
            p18_violation_bitmap: BitFlags::default(),
            binary64_excesses: 0,
            binary64_standard_compatible_excesses: 0,
            comparison_hash: Sha256::new(),
            fractions: FractionAccumulator::new(),
            contracts: ContractReport::default(),
            boundaries: Vec::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn observe(
        &mut self,
        mf: i32,
        mt: i32,
        location: Location,
        zap: i32,
        raw_lfs: Option<i32>,
        partial: f64,
        total: f64,
        partial_low: f64,
        total_high: f64,
        peak_total: f64,
        summed: bool,
        contract: Option<ContractClass>,
    ) -> Result<(), String> {
        for (name, value) in [
            ("partial", partial),
            ("total", total),
            ("partial lower bound", partial_low),
            ("total upper bound", total_high),
            ("peak total", peak_total),
        ] {
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "MF={mf}/MT={mt} ZAP={zap} {name} is nonfinite or negative ({value:.17e})"
                ));
            }
        }
        let absolute = match location {
            Location::Pointwise { .. } => POINTWISE_ABS_TOLERANCE_B,
            Location::Collapsed { .. } => COLLAPSED_ABS_TOLERANCE_B,
        };
        let p18_tolerance = absolute.max(P18_REL_TOLERANCE * total.max(peak_total));
        // Preserve P18's frozen binary64 operation order exactly. Reassociating this as
        // `partial > total + p18_tolerance` changes decisions at rounding boundaries.
        let p18_violation = partial - total - p18_tolerance > 0.0;
        let binary64_excess = partial > total;
        let standard = standard_compatible(total, partial);
        let primary = primary_class(
            contract,
            partial,
            total,
            partial_low,
            total_high,
            p18_violation,
        );

        self.comparisons += 1;
        match (location, summed) {
            (Location::Pointwise { .. }, false) => self.pointwise_individual_comparisons += 1,
            (Location::Pointwise { .. }, true) => self.pointwise_sum_comparisons += 1,
            (Location::Collapsed { .. }, false) => self.collapsed_individual_comparisons += 1,
            (Location::Collapsed { .. }, true) => self.collapsed_sum_comparisons += 1,
        }
        if mf == 9 {
            self.mf9_comparisons += 1;
        } else {
            self.mf10_comparisons += 1;
        }
        *self.binary64_primary_counts.entry(primary).or_default() += 1;
        self.p18_violation_bitmap.push(p18_violation);
        if p18_violation {
            self.p18_violations += 1;
            *self
                .p18_violations_by_binary64_primary
                .entry(primary)
                .or_default() += 1;
        }
        self.binary64_excesses += usize::from(binary64_excess);
        self.binary64_standard_compatible_excesses += usize::from(binary64_excess && standard);

        self.comparison_hash.update(mf.to_le_bytes());
        self.comparison_hash.update(mt.to_le_bytes());
        self.comparison_hash.update(zap.to_le_bytes());
        self.comparison_hash
            .update(raw_lfs.unwrap_or(-1).to_le_bytes());
        self.comparison_hash.update([u8::from(summed)]);
        match location {
            Location::Pointwise { side, energy_ev } => {
                self.comparison_hash.update(b"pointwise\0");
                self.comparison_hash.update(side.as_bytes());
                self.comparison_hash
                    .update(energy_ev.to_bits().to_le_bytes());
            }
            Location::Collapsed { group } => {
                self.comparison_hash.update(b"collapsed\0");
                self.comparison_hash.update((group as u64).to_le_bytes());
            }
        }
        self.comparison_hash.update(primary.as_bytes());
        self.comparison_hash.update([u8::from(p18_violation)]);
        self.comparison_hash.update([u8::from(standard)]);

        if binary64_excess || p18_violation {
            let (scope, side, energy_bits, group) = match location {
                Location::Pointwise { side, energy_ev } => {
                    ("pointwise", Some(side), Some(bits(energy_ev)), None)
                }
                Location::Collapsed { group } => ("collapsed", None, None, Some(group)),
            };
            retain_source_boundary(
                &mut self.boundaries,
                SourceBoundary {
                    mf,
                    mt,
                    scope,
                    side,
                    energy_bits,
                    group,
                    zap,
                    raw_lfs,
                    summed,
                    partial_bits: bits(partial),
                    total_bits: bits(total),
                    partial_low_bits: bits(partial_low),
                    total_high_bits: bits(total_high),
                    peak_total_bits: bits(peak_total),
                    p18_tolerance_bits: bits(p18_tolerance),
                    binary64_primary_class: primary,
                    p18_violation,
                    standard_compatible: standard,
                },
            );
        }
        Ok(())
    }

    fn finish(self) -> SourceReport {
        SourceReport {
            comparisons: self.comparisons,
            pointwise_individual_comparisons: self.pointwise_individual_comparisons,
            pointwise_sum_comparisons: self.pointwise_sum_comparisons,
            collapsed_individual_comparisons: self.collapsed_individual_comparisons,
            collapsed_sum_comparisons: self.collapsed_sum_comparisons,
            mf9_comparisons: self.mf9_comparisons,
            mf10_comparisons: self.mf10_comparisons,
            binary64_primary_counts: self.binary64_primary_counts,
            p18_violations: self.p18_violations,
            p18_violations_by_binary64_primary: self.p18_violations_by_binary64_primary,
            p18_violation_bitmap_hex: self.p18_violation_bitmap.into_hex(),
            binary64_excesses: self.binary64_excesses,
            binary64_standard_compatible_excesses: self.binary64_standard_compatible_excesses,
            comparison_sha256: format!("{:x}", self.comparison_hash.finalize()),
            mf9_fractions: self.fractions.finish(),
            contracts: self.contracts,
            boundary_examples: self.boundaries,
        }
    }
}

struct RuntimeAccumulator {
    vectors: usize,
    group_comparisons: usize,
    unchanged: usize,
    standard_compatible_excesses: usize,
    outside_standard_excesses: usize,
    raw_runtime_total_differences: usize,
    processed_reactions: usize,
    inelastic_constructed_loss_groups: usize,
    comparison_hash: Sha256,
    boundaries: Vec<RuntimeBoundary>,
}

impl RuntimeAccumulator {
    fn new(processed_reactions: usize) -> Self {
        Self {
            vectors: 0,
            group_comparisons: 0,
            unchanged: 0,
            standard_compatible_excesses: 0,
            outside_standard_excesses: 0,
            raw_runtime_total_differences: 0,
            processed_reactions,
            inelastic_constructed_loss_groups: 0,
            comparison_hash: Sha256::new(),
            boundaries: Vec::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn observe(
        &mut self,
        mf: i32,
        mt: i32,
        zap: i32,
        group: usize,
        sum: f64,
        total: f64,
        raw_total: f64,
        inelastic_constructed: bool,
    ) -> Result<(), String> {
        for (name, value) in [
            ("sum", sum),
            ("runtime total", total),
            ("raw total", raw_total),
        ] {
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "MF={mf}/MT={mt} ZAP={zap} group {group} {name} is nonfinite or negative ({value:.17e})"
                ));
            }
        }
        let decision = if sum <= total {
            self.unchanged += 1;
            "unchanged"
        } else if standard_compatible(total, sum) {
            self.standard_compatible_excesses += 1;
            "standard_compatible_excess"
        } else {
            self.outside_standard_excesses += 1;
            "outside_standard_excess"
        };
        self.group_comparisons += 1;
        self.raw_runtime_total_differences += usize::from(raw_total.to_bits() != total.to_bits());
        self.inelastic_constructed_loss_groups += usize::from(inelastic_constructed);
        self.comparison_hash.update(mf.to_le_bytes());
        self.comparison_hash.update(mt.to_le_bytes());
        self.comparison_hash.update(zap.to_le_bytes());
        self.comparison_hash.update((group as u64).to_le_bytes());
        self.comparison_hash.update(sum.to_bits().to_le_bytes());
        self.comparison_hash.update(total.to_bits().to_le_bytes());
        self.comparison_hash
            .update(raw_total.to_bits().to_le_bytes());
        self.comparison_hash.update(decision.as_bytes());
        if sum > total {
            retain_runtime_boundary(
                &mut self.boundaries,
                RuntimeBoundary {
                    mf,
                    mt,
                    zap,
                    group,
                    sum_bits: bits(sum),
                    total_bits: bits(total),
                    raw_total_bits: bits(raw_total),
                    decision,
                },
            );
        }
        Ok(())
    }

    fn finish(self) -> RuntimeReport {
        RuntimeReport {
            vectors: self.vectors,
            group_comparisons: self.group_comparisons,
            unchanged: self.unchanged,
            standard_compatible_excesses: self.standard_compatible_excesses,
            outside_standard_excesses: self.outside_standard_excesses,
            raw_runtime_total_differences: self.raw_runtime_total_differences,
            processed_reactions: self.processed_reactions,
            inelastic_constructed_loss_groups: self.inelastic_constructed_loss_groups,
            comparison_sha256: format!("{:x}", self.comparison_hash.finalize()),
            boundary_examples: self.boundaries,
        }
    }
}

fn bits(value: f64) -> String {
    format!("{:016x}", value.to_bits())
}

fn retain_source_boundary(examples: &mut Vec<SourceBoundary>, candidate: SourceBoundary) {
    let matching = examples
        .iter()
        .filter(|example| {
            example.binary64_primary_class == candidate.binary64_primary_class
                && example.scope == candidate.scope
                && example.summed == candidate.summed
        })
        .count();
    if matching < 2 {
        examples.push(candidate);
    }
}

fn retain_runtime_boundary(examples: &mut Vec<RuntimeBoundary>, candidate: RuntimeBoundary) {
    if examples
        .iter()
        .filter(|example| example.decision == candidate.decision)
        .count()
        < 8
    {
        examples.push(candidate);
    }
}

fn sha256_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let file =
        File::open(path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    let mut reader = BufReader::new(file);
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = std::io::Read::read(&mut reader, &mut buffer)
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn quantum_power(field: &str) -> Result<Option<i32>, String> {
    if field.len() != 11 || !field.is_ascii() {
        return Err("ENDF real field must be exactly 11 ASCII bytes".into());
    }
    let text = field.trim();
    if text.is_empty() {
        return Ok(None);
    }
    let bytes = text.as_bytes();
    let explicit = bytes.iter().position(|byte| matches!(byte, b'e' | b'E'));
    let implicit = (1..bytes.len()).find(|&index| {
        matches!(bytes[index], b'+' | b'-') && !matches!(bytes[index - 1], b'e' | b'E')
    });
    let split = explicit.or(implicit);
    let (mantissa, exponent) = match split {
        Some(index) => {
            let exponent_text = if matches!(bytes[index], b'e' | b'E') {
                &text[index + 1..]
            } else {
                &text[index..]
            };
            let exponent = exponent_text
                .parse::<i32>()
                .map_err(|_| format!("invalid ENDF exponent '{exponent_text}'"))?;
            (&text[..index], exponent)
        }
        None => (text, 0),
    };
    let decimals = mantissa
        .find('.')
        .map_or(0, |point| mantissa[point + 1..].len());
    let decimals = i32::try_from(decimals).map_err(|_| "decimal count overflows i32")?;
    Ok(Some(exponent - decimals))
}

fn printed_value(field: &str) -> Result<PrintedValue, String> {
    let value = parse_endf_float(field)?;
    let quantum = quantum_power(field)?.map_or(0.0, |power| 10_f64.powi(power));
    if !quantum.is_finite() || quantum < 0.0 {
        return Err(format!("invalid printed quantum for '{field}'"));
    }
    Ok(PrintedValue { value, quantum })
}

fn read_printed_tab1(
    lines: &[&str],
    index: usize,
) -> Result<(PrintedValue, PrintedValue, i32, i32, PrintedTable, usize), String> {
    let head = checked_fields(
        lines
            .get(index)
            .copied()
            .ok_or_else(|| format!("truncated TAB1 head at record {}", index + 1))?,
    )?;
    let c1 = printed_value(head[0])?;
    let c2 = printed_value(head[1])?;
    let l1 = parse_endf_i32(head[2])?;
    let l2 = parse_endf_i32(head[3])?;
    let nr = parse_endf_usize(head[4])?;
    let np = parse_endf_usize(head[5])?;
    let mut next = index + 1;
    let mut raw_interpolation = Vec::with_capacity(nr * 2);
    while raw_interpolation.len() < nr * 2 {
        let fields = checked_fields(
            lines
                .get(next)
                .copied()
                .ok_or_else(|| "truncated TAB1 interpolation payload".to_string())?,
        )?;
        for field in fields {
            if raw_interpolation.len() == nr * 2 {
                break;
            }
            raw_interpolation.push(parse_endf_i32(field)?);
        }
        next += 1;
    }
    let mut interpolation = Vec::with_capacity(nr);
    for pair in raw_interpolation.as_chunks::<2>().0 {
        interpolation.push((
            usize::try_from(pair[0]).map_err(|_| format!("negative NBT {}", pair[0]))?,
            pair[1],
        ));
    }
    let mut values = Vec::with_capacity(np * 2);
    while values.len() < np * 2 {
        let fields = checked_fields(
            lines
                .get(next)
                .copied()
                .ok_or_else(|| "truncated TAB1 point payload".to_string())?,
        )?;
        for field in fields {
            if values.len() == np * 2 {
                break;
            }
            values.push(printed_value(field)?);
        }
        next += 1;
    }
    let mut x = Vec::with_capacity(np);
    let mut y = Vec::with_capacity(np);
    let mut lower = Vec::with_capacity(np);
    let mut upper = Vec::with_capacity(np);
    for pair in values.as_chunks::<2>().0 {
        x.push(pair[0].value);
        y.push(pair[1].value);
        lower.push((pair[1].value - 0.5 * pair[1].quantum).max(0.0));
        upper.push(pair[1].value + 0.5 * pair[1].quantum);
    }
    let make = |ordinates: Vec<f64>| Tabulated {
        interpolation: interpolation.clone(),
        x: x.clone(),
        y: ordinates,
    };
    let table = PrintedTable {
        value: make(y),
        lower: make(lower),
        upper: make(upper),
    };
    table.value.validate()?;
    table.lower.validate()?;
    table.upper.validate()?;
    Ok((c1, c2, l1, l2, table, next))
}

fn printed_evaluations(
    sections: &[Section<'_>],
) -> Result<BTreeMap<i32, PrintedEvaluation>, String> {
    let mut output: BTreeMap<i32, PrintedEvaluation> = BTreeMap::new();
    for section in sections {
        let evaluation = output.entry(section.mat).or_default();
        if section.mf == 1 && section.mt == 451 {
            let head = checked_fields(section.lines[0])?;
            let incident = checked_fields(
                section
                    .lines
                    .get(2)
                    .copied()
                    .ok_or_else(|| "truncated MF=1/MT=451 metadata".to_string())?,
            )?;
            if evaluation.awr.replace(printed_value(head[1])?).is_some()
                || evaluation
                    .awi
                    .replace(printed_value(incident[0])?)
                    .is_some()
            {
                return Err(format!("duplicate MAT={}/MF=1/MT=451", section.mat));
            }
            continue;
        }
        match section.mf {
            3 => {
                let (_, _, _, _, table, next) = read_printed_tab1(&section.lines, 1)?;
                if next != section.lines.len() {
                    return Err(format!(
                        "MAT={}/MF=3/MT={} has {} unconsumed records",
                        section.mat,
                        section.mt,
                        section.lines.len() - next
                    ));
                }
                if evaluation.mf3.insert(section.mt, table).is_some() {
                    return Err(format!(
                        "duplicate MAT={}/MF=3/MT={}",
                        section.mat, section.mt
                    ));
                }
            }
            9 | 10 => {
                let head = checked_fields(section.lines[0])?;
                let count = parse_endf_usize(head[4])?;
                let mut next = 1;
                let mut products = Vec::with_capacity(count);
                for _ in 0..count {
                    let (qm, qi, zap, lfs, table, after) = read_printed_tab1(&section.lines, next)?;
                    next = after;
                    products.push(PrintedProduct {
                        zap,
                        lfs,
                        qm,
                        qi,
                        table,
                    });
                }
                if next != section.lines.len() {
                    return Err(format!(
                        "MAT={}/MF={}/MT={} has {} unconsumed records",
                        section.mat,
                        section.mf,
                        section.mt,
                        section.lines.len() - next
                    ));
                }
                let destination = if section.mf == 9 {
                    &mut evaluation.mf9
                } else {
                    &mut evaluation.mf10
                };
                if destination.insert(section.mt, products).is_some() {
                    return Err(format!(
                        "duplicate MAT={}/MF={}/MT={}",
                        section.mat, section.mf, section.mt
                    ));
                }
            }
            _ => {}
        }
    }
    Ok(output)
}

fn standard_compatible(total: f64, sum: f64) -> bool {
    if sum <= total {
        true
    } else if total > 0.0 {
        sum - total <= EPS_STANDARD * total
    } else {
        sum <= EPS_STANDARD
    }
}

fn primary_class(
    contract: Option<ContractClass>,
    partial: f64,
    total: f64,
    partial_low: f64,
    total_high: f64,
    p18_violation: bool,
) -> &'static str {
    match contract {
        Some(ContractClass::MissingTotalOrGrid) => "missing_total_or_grid_contract",
        Some(ContractClass::Threshold) => "threshold_contract",
        None if partial > total && partial_low > total_high => "definite_source_excess",
        None if partial > total => "printing_envelope_excess",
        None if p18_violation => "binary_only_excess",
        None => "source_conformant",
    }
}

fn evaluate(table: &Tabulated, energy: f64, left: bool) -> Result<f64, String> {
    if left {
        table.evaluate_left_limit(energy)
    } else {
        table.evaluate(energy)
    }
}

fn compensated_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let next = sum + value;
        correction += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    sum + correction
}

fn compensated_sum_results(
    values: impl IntoIterator<Item = Result<f64, String>>,
) -> Result<f64, String> {
    let mut parsed = Vec::new();
    for value in values {
        parsed.push(value?);
    }
    Ok(compensated_sum(parsed))
}

fn unique_products<'a>(
    actual: &'a [ProductTable],
    printed: &'a [PrintedProduct],
) -> Result<Vec<(&'a ProductTable, &'a PrintedProduct)>, String> {
    if actual.len() != printed.len() {
        return Err("parsed and printed product counts differ".into());
    }
    let mut unique: BTreeMap<(i32, i32), (&ProductTable, &PrintedProduct)> = BTreeMap::new();
    for (product, source) in actual.iter().zip(printed) {
        if product.zap != source.zap
            || product.lfs != source.lfs
            || product.qm_ev.to_bits() != source.qm.value.to_bits()
            || product.qi_ev.to_bits() != source.qi.value.to_bits()
            || product.table != source.table.value
        {
            return Err(format!(
                "parsed product ZAP={}/LFS={} does not match printed source",
                product.zap, product.lfs
            ));
        }
        let key = (product.zap, product.lfs);
        if let Some((previous, previous_source)) = unique.get(&key) {
            if **previous != *product || previous_source.table.value != source.table.value {
                return Err(format!(
                    "conflicting duplicate product ZAP={}/LFS={}",
                    product.zap, product.lfs
                ));
            }
        } else {
            unique.insert(key, (product, source));
        }
    }
    Ok(unique.into_values().collect())
}

fn threshold_interval(qi: &PrintedValue, awr: &PrintedValue, awi: &PrintedValue) -> (f64, f64) {
    let awr_low = awr.value - 0.5 * awr.quantum;
    let awr_high = awr.value + 0.5 * awr.quantum;
    let awi_low = awi.value - 0.5 * awi.quantum;
    let awi_high = awi.value + 0.5 * awi.quantum;
    let q_low = (-qi.value - 0.5 * qi.quantum).max(0.0);
    let q_high = -qi.value + 0.5 * qi.quantum;
    (
        q_low * (1.0 + awi_low / awr_high),
        q_high * (1.0 + awi_high / awr_low),
    )
}

fn contract_class(
    mf: i32,
    total: &PrintedTable,
    product: &PrintedProduct,
    awr: &PrintedValue,
    awi: &PrintedValue,
) -> Option<ContractClass> {
    let total_grid: BTreeSet<u64> = total.value.x.iter().map(|value| value.to_bits()).collect();
    if product
        .table
        .value
        .x
        .iter()
        .any(|value| !total_grid.contains(&value.to_bits()))
    {
        return Some(ContractClass::MissingTotalOrGrid);
    }
    if mf == 10 && product.qi.value < 0.0 {
        let awr_low = awr.value - 0.5 * awr.quantum;
        let awi_low = awi.value - 0.5 * awi.quantum;
        let (threshold_low, threshold_high) = threshold_interval(&product.qi, awr, awi);
        let first_energy = product.table.value.x[0];
        if awr_low <= 0.0
            || awi_low <= 0.0
            || !threshold_low.is_finite()
            || !threshold_high.is_finite()
            || first_energy < threshold_low
            || first_energy > threshold_high
            || product.table.value.y[0] != 0.0
        {
            return Some(ContractClass::Threshold);
        }
    }
    None
}

fn merge_contracts(
    values: impl IntoIterator<Item = Option<ContractClass>>,
) -> Option<ContractClass> {
    let mut threshold = false;
    for value in values {
        match value {
            Some(ContractClass::MissingTotalOrGrid) => {
                return Some(ContractClass::MissingTotalOrGrid)
            }
            Some(ContractClass::Threshold) => threshold = true,
            None => {}
        }
    }
    threshold.then_some(ContractClass::Threshold)
}

fn collapse_bounds(
    groups: &GroupStructure,
    mf: i32,
    total: &PrintedTable,
    product: &PrintedProduct,
) -> Result<(Vec<f64>, Vec<f64>), String> {
    if mf == 9 {
        Ok((
            groups.collapse_product(&[&total.value, &product.table.value])?,
            groups.collapse_product(&[&total.lower, &product.table.lower])?,
        ))
    } else {
        Ok((
            groups.collapse(&product.table.value)?,
            groups.collapse(&product.table.lower)?,
        ))
    }
}

#[allow(clippy::too_many_arguments)]
fn audit_source_products(
    mf: i32,
    mt: i32,
    total: Option<&PrintedTable>,
    actual: &[ProductTable],
    printed: &[PrintedProduct],
    awr: &PrintedValue,
    awi: &PrintedValue,
    groups: &GroupStructure,
    audit: &mut SourceAccumulator,
) -> Result<(), String> {
    let products = unique_products(actual, printed)?;
    let state_products: Vec<_> = products
        .into_iter()
        .filter(|(product, _)| product.zap >= 0)
        .collect();
    if state_products.is_empty() {
        return Ok(());
    }
    audit.contracts.product_tables += state_products.len();
    audit.contracts.non_linlin_tables += state_products
        .iter()
        .filter(|(_, source)| {
            source
                .table
                .value
                .interpolation
                .iter()
                .any(|(_, law)| *law != 2)
        })
        .count();
    let Some(total) = total else {
        audit.contracts.missing_totals += state_products.len();
        return Ok(());
    };
    if total
        .value
        .y
        .iter()
        .chain(&total.lower.y)
        .chain(&total.upper.y)
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!("MF=3/MT={mt} total is invalid"));
    }
    let contracts: Vec<_> = state_products
        .iter()
        .map(|(_, source)| contract_class(mf, total, source, awr, awi))
        .collect();
    audit.contracts.grid_contract_tables += contracts
        .iter()
        .filter(|value| matches!(value, Some(ContractClass::MissingTotalOrGrid)))
        .count();
    audit.contracts.threshold_contract_tables += contracts
        .iter()
        .filter(|value| matches!(value, Some(ContractClass::Threshold)))
        .count();
    let total_grid: BTreeSet<u64> = total.value.x.iter().map(|value| value.to_bits()).collect();
    for ((product, source), contract) in state_products.iter().zip(&contracts) {
        let Some(contract) = contract else {
            continue;
        };
        let off_grid_energy = source
            .table
            .value
            .x
            .iter()
            .find(|value| !total_grid.contains(&value.to_bits()))
            .copied();
        let threshold =
            (mf == 10 && source.qi.value < 0.0).then(|| threshold_interval(&source.qi, awr, awi));
        audit.contracts.issues.push(ContractIssue {
            mf,
            mt,
            zap: product.zap,
            raw_lfs: product.lfs,
            decision: match contract {
                ContractClass::MissingTotalOrGrid => "missing_total_or_grid_contract",
                ContractClass::Threshold => "threshold_contract",
            },
            qi_bits: bits(source.qi.value),
            first_energy_bits: bits(source.table.value.x[0]),
            first_value_bits: bits(source.table.value.y[0]),
            off_grid_energy_bits: off_grid_energy.map(bits),
            expected_threshold_low_bits: threshold.map(|(low, _)| bits(low)),
            expected_threshold_high_bits: threshold.map(|(_, high)| bits(high)),
        });
    }

    let total_collapsed = groups.collapse(&total.value)?;
    let total_upper_collapsed = groups.collapse(&total.upper)?;
    let peak_total = total.value.y.iter().copied().fold(0.0, f64::max);
    let peak_collapsed = total_collapsed.iter().copied().fold(0.0, f64::max);
    let collapsed_products: Vec<_> = state_products
        .iter()
        .map(|(product, source)| {
            let (value, lower) = collapse_bounds(groups, mf, total, source)?;
            Ok::<_, String>((*product, *source, value, lower))
        })
        .collect::<Result<_, _>>()?;

    let mut energies = Vec::with_capacity(
        total.value.x.len()
            + state_products
                .iter()
                .map(|(_, source)| source.table.value.x.len())
                .sum::<usize>(),
    );
    energies.extend(total.value.x.iter().copied());
    for (_, source) in &state_products {
        energies.extend(source.table.value.x.iter().copied());
    }
    energies.sort_by(f64::total_cmp);
    energies.dedup_by(|left, right| left.to_bits() == right.to_bits());
    let zaps: BTreeSet<i32> = state_products
        .iter()
        .map(|(product, _)| product.zap)
        .collect();
    for energy in energies {
        for (side, left) in [("right", false), ("left", true)] {
            let total_value = evaluate(&total.value, energy, left)?;
            let total_high = evaluate(&total.upper, energy, left)?;
            for ((product, source), contract) in state_products.iter().zip(&contracts) {
                let factor = if mf == 9 { total_value } else { 1.0 };
                let lower_factor = if mf == 9 {
                    evaluate(&total.lower, energy, left)?
                } else {
                    1.0
                };
                audit.observe(
                    mf,
                    mt,
                    Location::Pointwise {
                        side,
                        energy_ev: energy,
                    },
                    product.zap,
                    Some(product.lfs),
                    factor * evaluate(&source.table.value, energy, left)?,
                    total_value,
                    lower_factor * evaluate(&source.table.lower, energy, left)?,
                    total_high,
                    peak_total,
                    false,
                    *contract,
                )?;
            }
            for zap in &zaps {
                let selected: Vec<_> = state_products
                    .iter()
                    .zip(&contracts)
                    .filter(|((product, _), _)| product.zap == *zap)
                    .collect();
                let factor = if mf == 9 { total_value } else { 1.0 };
                let lower_factor = if mf == 9 {
                    evaluate(&total.lower, energy, left)?
                } else {
                    1.0
                };
                audit.observe(
                    mf,
                    mt,
                    Location::Pointwise {
                        side,
                        energy_ev: energy,
                    },
                    *zap,
                    None,
                    compensated_sum_results(selected.iter().map(|((_, source), _)| {
                        evaluate(&source.table.value, energy, left).map(|value| factor * value)
                    }))?,
                    total_value,
                    compensated_sum_results(selected.iter().map(|((_, source), _)| {
                        evaluate(&source.table.lower, energy, left)
                            .map(|value| lower_factor * value)
                    }))?,
                    total_high,
                    peak_total,
                    true,
                    merge_contracts(selected.iter().map(|(_, contract)| **contract)),
                )?;
            }
        }
    }

    for (product, _, value, lower) in &collapsed_products {
        let contract = state_products
            .iter()
            .position(|(candidate, _)| candidate.zap == product.zap && candidate.lfs == product.lfs)
            .and_then(|index| contracts[index]);
        for group in 0..groups.groups() {
            audit.observe(
                mf,
                mt,
                Location::Collapsed { group },
                product.zap,
                Some(product.lfs),
                value[group],
                total_collapsed[group],
                lower[group],
                total_upper_collapsed[group],
                peak_collapsed,
                false,
                contract,
            )?;
        }
    }
    for zap in zaps {
        let selected: Vec<_> = collapsed_products
            .iter()
            .enumerate()
            .filter(|(_, (product, _, _, _))| product.zap == zap)
            .collect();
        for group in 0..groups.groups() {
            audit.observe(
                mf,
                mt,
                Location::Collapsed { group },
                zap,
                None,
                compensated_sum(selected.iter().map(|(_, (_, _, values, _))| values[group])),
                total_collapsed[group],
                compensated_sum(selected.iter().map(|(_, (_, _, _, lower))| lower[group])),
                total_upper_collapsed[group],
                peak_collapsed,
                true,
                merge_contracts(selected.iter().map(|(index, _)| contracts[*index])),
            )?;
        }
    }
    Ok(())
}

fn audit_mf9_fractions(
    mt: i32,
    total: Option<&PrintedTable>,
    actual: &[ProductTable],
    printed: &[PrintedProduct],
    awr: &PrintedValue,
    awi: &PrintedValue,
    audit: &mut SourceAccumulator,
) -> Result<(), String> {
    let Some(total) = total else {
        return Ok(());
    };
    let products = unique_products(actual, printed)?;
    let state_products: Vec<_> = products
        .into_iter()
        .filter(|(product, _)| product.zap >= 0)
        .collect();
    if state_products.is_empty() {
        return Ok(());
    }
    let contracts: Vec<_> = state_products
        .iter()
        .map(|(_, source)| contract_class(9, total, source, awr, awi))
        .collect();
    let mut energies = Vec::with_capacity(
        total.value.x.len()
            + state_products
                .iter()
                .map(|(_, source)| source.table.value.x.len())
                .sum::<usize>(),
    );
    energies.extend(total.value.x.iter().copied());
    for (_, source) in &state_products {
        energies.extend(source.table.value.x.iter().copied());
    }
    energies.sort_by(f64::total_cmp);
    energies.dedup_by(|left, right| left.to_bits() == right.to_bits());
    let zaps: BTreeSet<_> = state_products
        .iter()
        .map(|(product, _)| product.zap)
        .collect();
    for energy in energies {
        for (side, left) in [("right", false), ("left", true)] {
            for ((product, source), contract) in state_products.iter().zip(&contracts) {
                audit.fractions.observe(
                    mt,
                    side,
                    energy,
                    product.zap,
                    Some(product.lfs),
                    evaluate(&source.table.value, energy, left)?,
                    evaluate(&source.table.lower, energy, left)?,
                    false,
                    *contract,
                )?;
            }
            for zap in &zaps {
                let selected: Vec<_> = state_products
                    .iter()
                    .zip(&contracts)
                    .filter(|((product, _), _)| product.zap == *zap)
                    .collect();
                audit.fractions.observe(
                    mt,
                    side,
                    energy,
                    *zap,
                    None,
                    compensated_sum_results(
                        selected
                            .iter()
                            .map(|((_, source), _)| evaluate(&source.table.value, energy, left)),
                    )?,
                    compensated_sum_results(
                        selected
                            .iter()
                            .map(|((_, source), _)| evaluate(&source.table.lower, energy, left)),
                    )?,
                    true,
                    merge_contracts(selected.iter().map(|(_, contract)| **contract)),
                )?;
            }
        }
    }
    Ok(())
}

fn processed_reactions(
    evaluation: &Evaluation,
    sections: &[Section<'_>],
    groups: &GroupStructure,
) -> Result<BTreeMap<i32, ProcessedReaction>, String> {
    if evaluation.metadata.projectile != Projectile::Neutron {
        return Ok(BTreeMap::new());
    }
    let resonance_section = sections.iter().find(|section| {
        section.mat == evaluation.metadata.mat && section.mf == 2 && section.mt == 151
    });
    let Some(section) = resonance_section else {
        if evaluation.metadata.evaluation_temperature_k.to_bits() != NEUTRON_TEMPERATURE_K.to_bits()
        {
            return Err(format!(
                "MAT={} has no MF=2/MT=151 and is at {} K, not {} K",
                evaluation.metadata.mat,
                evaluation.metadata.evaluation_temperature_k,
                NEUTRON_TEMPERATURE_K
            ));
        }
        return Ok(BTreeMap::new());
    };
    let resonance = parse_mf2(section)?;
    if resonance.za != evaluation.metadata.za
        || (resonance.awr - evaluation.metadata.awr).abs()
            > 1e-7 * evaluation.metadata.awr.abs().max(1.0)
    {
        return Err("MF=2 metadata disagrees with MF=1".into());
    }
    let requested: BTreeSet<i32> = evaluation
        .mf9
        .iter()
        .chain(&evaluation.mf10)
        .filter(|(_, products)| products.iter().any(|product| product.zap >= 0))
        .map(|(mt, _)| *mt)
        .filter(|mt| matches!(mt, 18 | 102))
        .collect();
    let mut output = BTreeMap::new();
    for mt in requested {
        if !has_resonance_contribution(&resonance, mt) {
            continue;
        }
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
            .expect("an MF=3 or explicit zero background is available");
        output.insert(
            mt,
            process_reaction(
                &resonance,
                background,
                groups,
                mt,
                NEUTRON_TEMPERATURE_K,
                PROCESSING_GRID_DENSITY,
            )?,
        );
    }
    Ok(output)
}

fn inelastic(mt: i32) -> bool {
    mt == 4 || (51..=91).contains(&mt)
}

fn audit_runtime_products(
    mf: i32,
    mt: i32,
    total: Option<&Tabulated>,
    actual: &[ProductTable],
    groups: &GroupStructure,
    processed: Option<&ProcessedReaction>,
    audit: &mut RuntimeAccumulator,
) -> Result<(), String> {
    let state_products: Vec<_> = actual.iter().filter(|product| product.zap >= 0).collect();
    if state_products.is_empty() {
        return Ok(());
    }
    let Some(raw_total_table) = total else {
        return Ok(());
    };
    let raw_total = groups.collapse(raw_total_table)?;
    let runtime_total = if let Some(reaction) = processed {
        reaction.collapse(groups)?
    } else {
        raw_total.clone()
    };
    let collapsed: Vec<_> = state_products
        .iter()
        .map(|product| {
            let values = if mf == 9 {
                if let Some(reaction) = processed {
                    reaction.collapse_product(groups, &[&product.table])?
                } else {
                    groups.collapse_product(&[raw_total_table, &product.table])?
                }
            } else {
                groups.collapse(&product.table)?
            };
            Ok::<_, String>((*product, values))
        })
        .collect::<Result<_, _>>()?;
    let zaps: BTreeSet<_> = collapsed.iter().map(|(product, _)| product.zap).collect();
    for zap in zaps {
        let retained: Vec<_> = collapsed
            .iter()
            .filter(|(product, _)| product.zap == zap && !inelastic_ground(mt, product.lfs))
            .collect();
        if retained.is_empty() {
            continue;
        }
        audit.vectors += 1;
        for group in 0..groups.groups() {
            let sum = compensated_sum(retained.iter().map(|(_, values)| values[group]));
            let constructed = mf == 10 && inelastic(mt);
            let comparator = if constructed {
                sum
            } else {
                runtime_total[group]
            };
            audit.observe(
                mf,
                mt,
                zap,
                group,
                sum,
                comparator,
                raw_total[group],
                constructed,
            )?;
        }
    }
    Ok(())
}

fn inelastic_ground(mt: i32, lfs: i32) -> bool {
    inelastic(mt) && lfs == 0
}

fn audit_evaluation(
    evaluation: &Evaluation,
    printed: &PrintedEvaluation,
    sections: &[Section<'_>],
    groups: &GroupStructure,
) -> Result<TargetAudit, String> {
    let awr = printed
        .awr
        .as_ref()
        .ok_or_else(|| "printed MF=1 AWR is missing".to_string())?;
    let awi = printed
        .awi
        .as_ref()
        .ok_or_else(|| "printed MF=1 AWI is missing".to_string())?;
    if awr.value.to_bits() != evaluation.metadata.awr.to_bits()
        || awi.value.to_bits() != evaluation.metadata.awi.to_bits()
    {
        return Err("MF=1 printed mass metadata differs from checked parse".into());
    }
    for (&mt, table) in &evaluation.mf3 {
        let source = printed
            .mf3
            .get(&mt)
            .ok_or_else(|| format!("MF=3/MT={mt} is absent from printed parse"))?;
        if &source.value != table {
            return Err(format!("MF=3/MT={mt} printed parse differs"));
        }
    }
    let processed = processed_reactions(evaluation, sections, groups)?;
    let mut source_audit = SourceAccumulator::new();
    let mut runtime_audit = RuntimeAccumulator::new(processed.len());
    for (mf, actual_sections, printed_sections) in [
        (9, &evaluation.mf9, &printed.mf9),
        (10, &evaluation.mf10, &printed.mf10),
    ] {
        for (&mt, products) in actual_sections {
            let source_products = printed_sections
                .get(&mt)
                .ok_or_else(|| format!("MF={mf}/MT={mt} is absent from printed parse"))?;
            audit_source_products(
                mf,
                mt,
                printed.mf3.get(&mt),
                products,
                source_products,
                awr,
                awi,
                groups,
                &mut source_audit,
            )?;
            if mf == 9 {
                audit_mf9_fractions(
                    mt,
                    printed.mf3.get(&mt),
                    products,
                    source_products,
                    awr,
                    awi,
                    &mut source_audit,
                )?;
            }
            audit_runtime_products(
                mf,
                mt,
                evaluation.mf3.get(&mt),
                products,
                groups,
                processed.get(&mt),
                &mut runtime_audit,
            )?;
        }
    }
    let source = source_audit.finish();
    let runtime = runtime_audit.finish();
    // Source nonconformance is an observed result at G2, not an audit-execution failure. Fail only when comparison
    // accounting is incomplete; malformed inputs already return an error before this point.
    let pass = source
        .p18_violations_by_binary64_primary
        .values()
        .sum::<usize>()
        == source.p18_violations
        && runtime.unchanged
            + runtime.standard_compatible_excesses
            + runtime.outside_standard_excesses
            == runtime.group_comparisons;
    Ok(TargetAudit {
        mat: evaluation.metadata.mat,
        za: evaluation.metadata.za,
        liso: evaluation.metadata.liso,
        source,
        runtime,
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
    let sections = parse_sections(text)?;
    let printed = printed_evaluations(&sections)?;
    let evaluations = parse_state_audit_evaluations(text, Some(projectile))?;
    let targets = evaluations
        .iter()
        .map(|evaluation| {
            let source = printed.get(&evaluation.metadata.mat).ok_or_else(|| {
                format!(
                    "MAT={} is absent from printed parse",
                    evaluation.metadata.mat
                )
            })?;
            audit_evaluation(evaluation, source, &sections, groups)
        })
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

fn resume_after(output: &Path) -> Result<Option<String>, String> {
    if !output.is_file() {
        return Ok(None);
    }
    let reader = BufReader::new(
        File::open(output).map_err(|error| format!("cannot read {}: {error}", output.display()))?,
    );
    let mut last = None;
    for line in reader.lines() {
        let line = line.map_err(|error| format!("cannot read {}: {error}", output.display()))?;
        if !line.trim().is_empty() {
            last = Some(line);
        }
    }
    let Some(line) = last else {
        return Ok(None);
    };
    let value: serde_json::Value = serde_json::from_str(&line)
        .map_err(|error| format!("invalid final checkpoint row: {error}"))?;
    if value.get("kind").and_then(serde_json::Value::as_str) == Some("header") {
        Ok(None)
    } else {
        value
            .get("file")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
            .map(Some)
            .ok_or_else(|| "final checkpoint row has no file identity".to_string())
    }
}

fn validate_resume_header(
    output: &Path,
    projectile: Projectile,
    groups: &GroupStructure,
    predecessor_sha256: &str,
) -> Result<(), String> {
    let mut reader = BufReader::new(
        File::open(output).map_err(|error| format!("cannot read {}: {error}", output.display()))?,
    );
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|error| format!("cannot read {} header: {error}", output.display()))?;
    let header: serde_json::Value = serde_json::from_str(&line)
        .map_err(|error| format!("invalid checkpoint header: {error}"))?;
    validate_resume_header_value(&header, projectile, groups, predecessor_sha256)
}

fn validate_resume_header_value(
    header: &serde_json::Value,
    projectile: Projectile,
    groups: &GroupStructure,
    predecessor_sha256: &str,
) -> Result<(), String> {
    let probe_source_sha256 = sha256_bytes(include_bytes!("p18b_corpus_probe.rs"));
    let activation_source_sha256 = sha256_bytes(include_bytes!("../activation.rs"));
    let endf_source_sha256 = sha256_bytes(include_bytes!("../endf.rs"));
    let groups_source_sha256 = sha256_bytes(include_bytes!("../groups.rs"));
    let processing_source_sha256 = sha256_bytes(include_bytes!("../processing.rs"));
    let resonance_source_sha256 = sha256_bytes(include_bytes!("../resonance.rs"));
    let expected_strings = [
        ("kind", "header"),
        ("schema", "actinv-p18b-corpus-probe-2"),
        ("projectile", projectile.name()),
        ("group_structure", groups.name.as_str()),
        ("predecessor_checkpoint_sha256", predecessor_sha256),
        ("probe_source_sha256", probe_source_sha256.as_str()),
        (
            "activation_source_sha256",
            activation_source_sha256.as_str(),
        ),
        ("endf_source_sha256", endf_source_sha256.as_str()),
        ("groups_source_sha256", groups_source_sha256.as_str()),
        (
            "processing_source_sha256",
            processing_source_sha256.as_str(),
        ),
        ("resonance_source_sha256", resonance_source_sha256.as_str()),
    ];
    for (field, expected) in expected_strings {
        if header.get(field).and_then(serde_json::Value::as_str) != Some(expected) {
            return Err(format!("checkpoint header has stale {field}"));
        }
    }
    let temperature = if projectile == Projectile::Neutron {
        NEUTRON_TEMPERATURE_K
    } else {
        0.0
    };
    if header.get("groups").and_then(serde_json::Value::as_u64) != Some(groups.groups() as u64)
        || header
            .get("temperature_k")
            .and_then(serde_json::Value::as_f64)
            .map(f64::to_bits)
            != Some(temperature.to_bits())
        || header
            .get("processing_grid_density")
            .and_then(serde_json::Value::as_f64)
            .map(f64::to_bits)
            != Some(PROCESSING_GRID_DENSITY.to_bits())
    {
        return Err("checkpoint header has stale numerical configuration".into());
    }
    Ok(())
}

fn write_json_line(output: &mut impl Write, value: &impl Serialize) -> Result<(), String> {
    serde_json::to_writer(&mut *output, value)
        .map_err(|error| format!("cannot serialize checkpoint row: {error}"))?;
    output
        .write_all(b"\n")
        .map_err(|error| format!("cannot write checkpoint row: {error}"))?;
    output
        .flush()
        .map_err(|error| format!("cannot flush checkpoint row: {error}"))
}

fn run() -> Result<(), String> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments.len() != 4 {
        return Err(
            "usage: p18b_corpus_probe DIRECTORY neutron|proton|deuteron|alpha PREDECESSOR.jsonl OUTPUT.jsonl"
                .into(),
        );
    }
    let directory = Path::new(&arguments[0]);
    let projectile = Projectile::parse(&arguments[1])?;
    let predecessor = Path::new(&arguments[2]);
    let output_path = Path::new(&arguments[3]);
    let predecessor_sha256 = sha256_file(predecessor)?;
    let groups = if projectile == Projectile::Neutron {
        GroupStructure::fispact_709()?
    } else {
        GroupStructure::fispact_162()?
    };
    let exists = output_path.is_file()
        && output_path
            .metadata()
            .map(|value| value.len() > 0)
            .unwrap_or(false);
    if exists {
        validate_resume_header(output_path, projectile, &groups, &predecessor_sha256)?;
    }
    let start_after = resume_after(output_path)?;
    let output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(output_path)
        .map_err(|error| format!("cannot open {}: {error}", output_path.display()))?;
    let mut writer = BufWriter::new(output);
    if !exists {
        write_json_line(
            &mut writer,
            &ProbeHeader {
                kind: "header",
                schema: "actinv-p18b-corpus-probe-2",
                projectile: projectile.name(),
                group_structure: &groups.name,
                groups: groups.groups(),
                temperature_k: if projectile == Projectile::Neutron {
                    NEUTRON_TEMPERATURE_K
                } else {
                    0.0
                },
                processing_grid_density: PROCESSING_GRID_DENSITY,
                predecessor_checkpoint_sha256: predecessor_sha256,
                probe_source_sha256: sha256_bytes(include_bytes!("p18b_corpus_probe.rs")),
                activation_source_sha256: sha256_bytes(include_bytes!("../activation.rs")),
                endf_source_sha256: sha256_bytes(include_bytes!("../endf.rs")),
                groups_source_sha256: sha256_bytes(include_bytes!("../groups.rs")),
                processing_source_sha256: sha256_bytes(include_bytes!("../processing.rs")),
                resonance_source_sha256: sha256_bytes(include_bytes!("../resonance.rs")),
            },
        )?;
        writer
            .get_ref()
            .sync_data()
            .map_err(|error| format!("cannot sync {}: {error}", output_path.display()))?;
    }
    for (name, path) in source_files(directory, start_after.as_deref())? {
        let audit = audit_file(&path, name, projectile, &groups)?;
        write_json_line(&mut writer, &audit)?;
        writer
            .get_ref()
            .sync_data()
            .map_err(|error| format!("cannot sync {}: {error}", output_path.display()))?;
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("p18b corpus probe: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn printed_quantum_covers_all_endf_real_forms() {
        assert_eq!(quantum_power("+1.23456789").unwrap(), Some(-8));
        assert_eq!(quantum_power(" 1.234567+9").unwrap(), Some(3));
        assert_eq!(quantum_power(" 1.23456-38").unwrap(), Some(-43));
    }

    #[test]
    fn primary_class_obeys_frozen_precedence() {
        assert_eq!(
            primary_class(
                Some(ContractClass::MissingTotalOrGrid),
                2.0,
                1.0,
                2.0,
                1.0,
                true,
            ),
            "missing_total_or_grid_contract"
        );
        assert_eq!(
            primary_class(None, 1.001, 1.0, 0.999, 1.000_1, true),
            "printing_envelope_excess"
        );
        assert_eq!(
            primary_class(None, 1.001, 1.0, 1.000_2, 1.000_1, true),
            "definite_source_excess"
        );
    }

    #[test]
    fn standard_ceiling_is_closed_at_positive_and_zero_totals() {
        assert!(standard_compatible(1000.0, 1001.0));
        assert!(!standard_compatible(
            1000.0,
            f64::from_bits(1001.0_f64.to_bits() + 1)
        ));
        assert!(standard_compatible(0.0, 0.001));
        assert!(!standard_compatible(
            0.0,
            f64::from_bits(0.001_f64.to_bits() + 1)
        ));
    }

    #[test]
    fn p18_violation_preserves_the_frozen_operation_order() {
        let partial = f64::from_bits(0x3ed8_51c7_d42d_a9de);
        let total = f64::from_bits(0x3ed8_51c7_8dcf_43d9);
        let tolerance = f64::from_bits(0x3d71_9799_812d_ea11);
        assert!(partial - total - tolerance > 0.0);
        assert!(partial <= total + tolerance);
    }

    #[test]
    fn negative_q_threshold_contract_is_specific_to_mf10() {
        let table = |x: Vec<f64>, y: Vec<f64>| {
            let points = x.len();
            let value = Tabulated {
                interpolation: vec![(points, 2)],
                x,
                y,
            };
            PrintedTable {
                lower: value.clone(),
                upper: value.clone(),
                value,
            }
        };
        let total = table(vec![1.0, 2.0, 3.0], vec![0.0, 1.0, 1.0]);
        let mut product = PrintedProduct {
            zap: 1,
            lfs: 1,
            qm: PrintedValue {
                value: 0.0,
                quantum: 0.0,
            },
            qi: PrintedValue {
                value: -1.0,
                quantum: 0.0,
            },
            table: table(vec![2.0, 3.0], vec![0.0, 1.0]),
        };
        let mass = PrintedValue {
            value: 1.0,
            quantum: 0.0,
        };
        assert!(contract_class(9, &total, &product, &mass, &mass).is_none());
        assert!(contract_class(10, &total, &product, &mass, &mass).is_none());
        product.table.value.y[0] = 1.0;
        assert!(matches!(
            contract_class(10, &total, &product, &mass, &mass),
            Some(ContractClass::Threshold)
        ));
    }

    #[test]
    fn inelastic_ground_is_excluded_from_constructed_loss() {
        assert!(inelastic_ground(4, 0));
        assert!(inelastic_ground(51, 0));
        assert!(!inelastic_ground(51, 1));
        assert!(!inelastic_ground(102, 0));
    }

    #[test]
    fn p18_bitmap_uses_stable_little_endian_bit_order() {
        let mut flags = BitFlags::default();
        for value in [true, false, true, false, false, false, false, true, true] {
            flags.push(value);
        }
        assert_eq!(flags.len, 9);
        assert_eq!(flags.into_hex(), "8501");
    }

    #[test]
    fn resume_header_rejects_stale_probe_source() {
        let groups = GroupStructure::fispact_709().unwrap();
        let predecessor_sha256 = "predecessor";
        let mut header = serde_json::to_value(ProbeHeader {
            kind: "header",
            schema: "actinv-p18b-corpus-probe-2",
            projectile: Projectile::Neutron.name(),
            group_structure: &groups.name,
            groups: groups.groups(),
            temperature_k: NEUTRON_TEMPERATURE_K,
            processing_grid_density: PROCESSING_GRID_DENSITY,
            predecessor_checkpoint_sha256: predecessor_sha256.to_owned(),
            probe_source_sha256: sha256_bytes(include_bytes!("p18b_corpus_probe.rs")),
            activation_source_sha256: sha256_bytes(include_bytes!("../activation.rs")),
            endf_source_sha256: sha256_bytes(include_bytes!("../endf.rs")),
            groups_source_sha256: sha256_bytes(include_bytes!("../groups.rs")),
            processing_source_sha256: sha256_bytes(include_bytes!("../processing.rs")),
            resonance_source_sha256: sha256_bytes(include_bytes!("../resonance.rs")),
        })
        .unwrap();
        assert!(validate_resume_header_value(
            &header,
            Projectile::Neutron,
            &groups,
            predecessor_sha256,
        )
        .is_ok());

        header["probe_source_sha256"] = serde_json::Value::String("stale".into());
        assert_eq!(
            validate_resume_header_value(&header, Projectile::Neutron, &groups, predecessor_sha256,),
            Err("checkpoint header has stale probe_source_sha256".into())
        );
    }
}
