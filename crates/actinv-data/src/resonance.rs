//! Strict ENDF-6 MF=2/MT=151 resonance parameters and reconstruction primitives.
//!
//! Parsing and physics live together here so a raw neutron evaluation cannot accidentally be treated as an already
//! processed MF=3 file. Every declared record is consumed. Optional R-matrix backgrounds and tabulated phase shifts
//! are retained explicitly; reconstruction rejects an unsupported extension instead of silently omitting it.

use crate::endf::{read_cont_checked, read_list_checked, read_tab1_checked, ContRecord, Section};
use crate::groups::Tabulated;
use num_complex::Complex64;
use std::collections::{BTreeMap, BTreeSet};

pub const K_WAVE: f64 = 2.196_771e-3;
const NEUTRON_MASS_AMU: f64 = 1.008_664_915_95;
const BREIT_WIGNER_FIELD_ROUNDING_RELATIVE: f64 = 5e-6;

#[derive(Clone, Debug, PartialEq)]
pub struct ResonanceEvaluation {
    pub za: i32,
    pub awr: f64,
    pub isotopes: Vec<ResonanceIsotope>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ResonanceIsotope {
    pub zai: i32,
    pub abundance: f64,
    pub fission_widths: bool,
    pub ranges: Vec<ResonanceRange>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ResonanceRange {
    pub energy_min: f64,
    pub energy_max: f64,
    pub lru: i32,
    pub lrf: i32,
    pub naps: i32,
    pub scattering_radius: Option<Tabulated>,
    pub data: RangeData,
}

#[derive(Clone, Debug, PartialEq)]
pub enum RangeData {
    ScatteringOnly { spin: f64, ap: f64 },
    BreitWigner(LegacyResolved),
    ReichMoore(LegacyResolved),
    RMatrixLimited(RMatrixLimited),
    Unresolved(Unresolved),
}

#[derive(Clone, Debug, PartialEq)]
pub struct LegacyResolved {
    pub spin: f64,
    pub ap: f64,
    pub groups: Vec<LegacyLGroup>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct LegacyLGroup {
    pub awri: f64,
    pub apl: f64,
    pub qx: f64,
    pub l: i32,
    pub lrx: i32,
    pub resonances: Vec<LegacyResonance>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct LegacyResonance {
    pub energy: f64,
    pub spin: f64,
    /// LRF=1/2 evaluator-reported total width. Zero for Reich-Moore.
    pub total: f64,
    pub neutron: f64,
    pub capture: f64,
    /// LRF=1/2 fission width or Reich-Moore signed first fission width.
    pub fission_a: f64,
    /// Reich-Moore signed second fission width.
    pub fission_b: f64,
}

fn width_rounding_tolerance(left: f64, right: f64) -> f64 {
    BREIT_WIGNER_FIELD_ROUNDING_RELATIVE * left.abs().max(right.abs()).max(f64::MIN_POSITIVE)
}

fn omitted_fission_total_fields(lrx: i32, resonance: &LegacyResonance) -> bool {
    if lrx != 0 || resonance.fission_a <= 0.0 {
        return false;
    }
    let without_fission = resonance.neutron + resonance.capture;
    let components = without_fission + resonance.fission_a;
    resonance.total + width_rounding_tolerance(resonance.total, components) < components
        && (resonance.total - without_fission).abs()
            <= width_rounding_tolerance(resonance.total, without_fission)
}

/// Whether this LRF=1/2 record has the narrowly recognized TENDL omitted-fission `GT` pattern.
pub fn omitted_fission_total_width(group: &LegacyLGroup, resonance: &LegacyResonance) -> bool {
    omitted_fission_total_fields(group.lrx, resonance)
}

/// Natural LRF=1/2 width at the resonance energy, following NJOY's component-width reconstruction.
/// `max(total, components)` for every LRX: declared-LRX files and LRX=0 files
/// whose GT omits a listed component (Amendment D) reconstruct to the component
/// sum, while LRX=0 files carrying an undeclared competitive width keep GT in
/// the denominator — the excess is the competitive width.
pub fn legacy_effective_total_width(_group: &LegacyLGroup, resonance: &LegacyResonance) -> f64 {
    let components = resonance.neutron + resonance.capture + resonance.fission_a;
    resonance.total.max(components)
}

/// Count of LRX=0 LRF=1/2 resonances carrying an undeclared competitive width.
pub fn undeclared_competitive_width_count(evaluation: &ResonanceEvaluation) -> usize {
    evaluation
        .isotopes
        .iter()
        .flat_map(|isotope| &isotope.ranges)
        .filter_map(|range| match &range.data {
            RangeData::BreitWigner(resolved) => Some(resolved),
            _ => None,
        })
        .flat_map(|resolved| &resolved.groups)
        .map(|group| {
            group
                .resonances
                .iter()
                .filter(|resonance| undeclared_competitive_width_fields(group.lrx, resonance))
                .count()
        })
        .sum()
}

pub fn omitted_fission_total_width_count(evaluation: &ResonanceEvaluation) -> usize {
    evaluation
        .isotopes
        .iter()
        .flat_map(|isotope| &isotope.ranges)
        .filter_map(|range| match &range.data {
            RangeData::BreitWigner(resolved) => Some(resolved),
            _ => None,
        })
        .flat_map(|resolved| &resolved.groups)
        .map(|group| {
            group
                .resonances
                .iter()
                .filter(|resonance| omitted_fission_total_width(group, resonance))
                .count()
        })
        .sum()
}

/// Whether this LRF=1/2 record carries a total width exceeding the listed
/// components without declaring LRX — undeclared competitive width, the
/// TENDL-2017-era convention the `max(total, components)` reconstruction
/// already implements for declared LRX.
fn undeclared_competitive_width_fields(lrx: i32, resonance: &LegacyResonance) -> bool {
    lrx == 0
        && resonance.total
            > resonance.neutron
                + resonance.capture
                + resonance.fission_a
                + width_rounding_tolerance(
                    resonance.total,
                    resonance.neutron + resonance.capture + resonance.fission_a,
                )
}

fn validate_breit_wigner_widths(lrx: i32, resonance: &LegacyResonance) -> Result<(), String> {
    let component_sum = resonance.neutron + resonance.capture + resonance.fission_a;
    let rounding_tolerance = width_rounding_tolerance(resonance.total, component_sum);
    if resonance.total + rounding_tolerance < component_sum
        && !omitted_fission_total_fields(lrx, resonance)
    {
        return Err(format!(
            "Breit-Wigner total width {} is below component sum {component_sum} for LRX={lrx} (GN={}, GG={}, GF={})",
            resonance.total, resonance.neutron, resonance.capture, resonance.fission_a
        ));
    }
    Ok(())
}

#[derive(Clone, Debug, PartialEq)]
pub struct RMatrixLimited {
    pub reduced_widths: bool,
    pub krm: i32,
    pub particle_pairs: Vec<ParticlePair>,
    pub spin_groups: Vec<SpinGroup>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ParticlePair {
    pub mass_a: f64,
    pub mass_b: f64,
    pub za: i32,
    pub zb: i32,
    pub spin_a: f64,
    pub spin_b: f64,
    pub q_value: f64,
    pub penetrability: i32,
    pub shift: i32,
    pub mt: i32,
    pub parity_a: i32,
    pub parity_b: i32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SpinGroup {
    pub spin: f64,
    pub parity: f64,
    pub channels: Vec<RmlChannel>,
    pub resonances: Vec<RmlResonance>,
    pub backgrounds: Vec<RmlExtension>,
    pub phase_shifts: Vec<RmlExtension>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RmlChannel {
    /// Zero-based particle-pair index.
    pub pair: usize,
    pub l: i32,
    pub spin: f64,
    pub boundary: f64,
    pub effective_radius: f64,
    pub true_radius: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RmlResonance {
    pub energy: f64,
    /// One signed width or reduced-width amplitude per declared channel, including eliminated channels.
    pub widths: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RmlExtension {
    pub channel: usize,
    pub law: i32,
    pub real: Option<Tabulated>,
    pub imaginary: Option<Tabulated>,
    pub parameters: Vec<f64>,
    /// [E_c^down, E_c^up] validity bounds from the law 2/3 LIST head.
    pub energy_min: f64,
    pub energy_max: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UnresolvedCase {
    A,
    B,
    C,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Unresolved {
    pub spin: f64,
    pub ap: f64,
    pub add_to_background: bool,
    pub case: UnresolvedCase,
    pub sequences: Vec<UnresolvedSequence>,
    pub interpolation_energies: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct UnresolvedSequence {
    pub awri: f64,
    pub l: i32,
    pub spin: f64,
    pub interpolation: i32,
    pub competitive_dof: i32,
    pub neutron_dof: i32,
    pub fission_dof: i32,
    pub points: Vec<UnresolvedPoint>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct UnresolvedPoint {
    /// `None` denotes the energy-independent case-A parameters.
    pub energy: Option<f64>,
    pub spacing: f64,
    pub competitive: f64,
    pub neutron: f64,
    pub capture: f64,
    pub fission: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct CrossSections {
    pub elastic: f64,
    pub capture: f64,
    pub fission: f64,
    pub competitive: f64,
}

impl CrossSections {
    pub fn total(self) -> f64 {
        self.elastic + self.capture + self.fission + self.competitive
    }

    fn checked(self, context: &str) -> Result<Self, String> {
        if [self.elastic, self.capture, self.fission, self.competitive]
            .into_iter()
            .any(|value| !value.is_finite() || value < -1e-10)
        {
            return Err(format!(
                "{context} produced a nonfinite or negative cross section"
            ));
        }
        Ok(Self {
            elastic: self.elastic.max(0.0),
            capture: self.capture.max(0.0),
            fission: self.fission.max(0.0),
            competitive: self.competitive.max(0.0),
        })
    }
}

fn integer(value: f64, label: &str) -> Result<i32, String> {
    if !value.is_finite() || value < f64::from(i32::MIN) || value > f64::from(i32::MAX) {
        return Err(format!("invalid {label} {value}"));
    }
    let rounded = value.round() as i32;
    if (value - f64::from(rounded)).abs() > 1e-7 {
        return Err(format!("nonintegral {label} {value}"));
    }
    Ok(rounded)
}

fn nonnegative(values: &[f64], label: &str) -> Result<(), String> {
    if values.iter().any(|value| *value < 0.0) {
        Err(format!("negative {label}"))
    } else {
        Ok(())
    }
}

fn table(record: crate::endf::CheckedTab1Record) -> Result<Tabulated, String> {
    Tabulated::try_from(record)
}

fn parse_legacy_resolved(
    lines: &[&str],
    mut index: usize,
    lrf: i32,
) -> Result<(RangeData, usize), String> {
    let (control, next) = read_cont_checked(lines, index)?;
    index = next;
    let mut groups = Vec::with_capacity(control.n1);
    for _ in 0..control.n1 {
        let (record, next) = read_list_checked(lines, index)?;
        index = next;
        if record.head.n1 != 6 * record.head.n2 {
            return Err(format!(
                "LRF={lrf} resonance LIST has NPL={}, expected 6*NRS={}",
                record.head.n1,
                6 * record.head.n2
            ));
        }
        let mut resonances = Vec::with_capacity(record.head.n2);
        for values in record.values.as_chunks::<6>().0 {
            if lrf <= 2 {
                nonnegative(&values[2..], "Breit-Wigner width")?;
                // ENDF's eleven-column fields normally retain only seven significant digits.  The separately
                // rounded total and component widths can therefore differ by a few last-place units even when the
                // evaluator formed a consistent total (Ag-107 and Rb-94 both exercise this).  Amendment D also
                // recognizes only the exact TENDL-2025 pattern where GT omitted a positive fission component.
                let resonance = LegacyResonance {
                    energy: values[0],
                    spin: values[1],
                    total: values[2],
                    neutron: values[3],
                    capture: values[4],
                    fission_a: values[5],
                    fission_b: 0.0,
                };
                validate_breit_wigner_widths(record.head.l2, &resonance)?;
                resonances.push(resonance);
            } else {
                nonnegative(&values[2..4], "Reich-Moore neutron/capture width")?;
                resonances.push(LegacyResonance {
                    energy: values[0],
                    spin: values[1],
                    total: 0.0,
                    neutron: values[2],
                    capture: values[3],
                    fission_a: values[4],
                    fission_b: values[5],
                });
            }
        }
        groups.push(LegacyLGroup {
            awri: record.head.c1,
            apl: if lrf == 3 { record.head.c2 } else { 0.0 },
            qx: if lrf <= 2 { record.head.c2 } else { 0.0 },
            l: record.head.l1,
            lrx: if lrf <= 2 { record.head.l2 } else { 0 },
            resonances,
        });
    }
    let resolved = LegacyResolved {
        spin: control.c1,
        ap: control.c2,
        groups,
    };
    Ok((
        if lrf == 3 {
            RangeData::ReichMoore(resolved)
        } else {
            RangeData::BreitWigner(resolved)
        },
        index,
    ))
}

fn parse_rml_extension(lines: &[&str], mut index: usize) -> Result<(RmlExtension, usize), String> {
    let (control, next) = read_cont_checked(lines, index)?;
    index = next;
    let channel = usize::try_from(control.l1)
        .map_err(|_| format!("negative RML extension channel {}", control.l1))?;
    let law = control.l2;
    let mut extension = RmlExtension {
        channel,
        law,
        real: None,
        imaginary: None,
        parameters: Vec::new(),
        energy_min: 0.0,
        energy_max: 0.0,
    };
    match law {
        0 => {}
        1 => {
            let (real, next) = read_tab1_checked(lines, index)?;
            let (imaginary, next2) = read_tab1_checked(lines, next)?;
            extension.real = Some(table(real)?);
            extension.imaginary = Some(table(imaginary)?);
            index = next2;
        }
        2 | 3 => {
            let (parameters, next) = read_list_checked(lines, index)?;
            extension.energy_min = parameters.head.c1;
            extension.energy_max = parameters.head.c2;
            extension.parameters = parameters.values;
            index = next;
        }
        _ => return Err(format!("unsupported RML extension law {law}")),
    }
    Ok((extension, index))
}

/// KPS phase-shift extension record: LIST head (0,0,0,0,LPS,1) with a
/// six-zero body, followed by two TAB1 records when LPS=1.
fn parse_rml_phase_shift(
    lines: &[&str],
    mut index: usize,
    channel: usize,
) -> Result<(RmlExtension, usize), String> {
    let (record, next) = read_list_checked(lines, index)?;
    index = next;
    if record.head.n1 != 1 || record.head.n2 != 0 || record.values.iter().any(|value| *value != 0.0)
    {
        return Err("RML phase-shift record is not the declared six-zero LIST".into());
    }
    let mut extension = RmlExtension {
        channel,
        law: record.head.l2,
        real: None,
        imaginary: None,
        parameters: Vec::new(),
        energy_min: 0.0,
        energy_max: 0.0,
    };
    match extension.law {
        0 => {}
        1 => {
            let (real, next) = read_tab1_checked(lines, index)?;
            let (imaginary, next2) = read_tab1_checked(lines, next)?;
            extension.real = Some(table(real)?);
            extension.imaginary = Some(table(imaginary)?);
            index = next2;
        }
        law => return Err(format!("unsupported RML phase-shift law {law}")),
    }
    Ok((extension, index))
}

fn parse_rmatrix_limited(lines: &[&str], mut index: usize) -> Result<(RangeData, usize), String> {
    let (control, next) = read_cont_checked(lines, index)?;
    index = next;
    if !matches!(control.l1, 0 | 1) {
        return Err(format!("invalid RML IFG={}", control.l1));
    }
    if control.l2 != 3 {
        return Err(format!(
            "unsupported RML KRM={}; only Reich-Moore KRM=3 is implemented",
            control.l2
        ));
    }
    let (pair_record, next) = read_list_checked(lines, index)?;
    index = next;
    let pair_count = usize::try_from(pair_record.head.l1)
        .map_err(|_| format!("negative RML NPP {}", pair_record.head.l1))?;
    if pair_record.head.n1 != 12 * pair_count || pair_record.head.n2 != 2 * pair_count {
        return Err(format!(
            "RML particle-pair LIST counts NPL={}/N2={} disagree with NPP={pair_count}",
            pair_record.head.n1, pair_record.head.n2
        ));
    }
    let mut particle_pairs = Vec::with_capacity(pair_count);
    for values in pair_record.values.as_chunks::<12>().0 {
        let pair = ParticlePair {
            mass_a: values[0],
            mass_b: values[1],
            za: integer(values[2], "RML particle ZA")?,
            zb: integer(values[3], "RML particle ZB")?,
            spin_a: values[4],
            spin_b: values[5],
            q_value: values[6],
            penetrability: integer(values[7], "RML PNT")?,
            shift: integer(values[8], "RML SHF")?,
            mt: integer(values[9], "RML MT")?,
            parity_a: integer(values[10], "RML parity A")?,
            parity_b: integer(values[11], "RML parity B")?,
        };
        // ENDF-6 encodes parity in the sign of the IA/IB spin fields, so
        // negative spins are legal and must not be rejected here; every
        // downstream consumer takes the magnitude (see the spin-statistical
        // factor below).
        if pair.mass_a < 0.0
            || pair.mass_b <= 0.0
            || !matches!(pair.penetrability, 0 | 1)
            || !matches!(pair.shift, 0 | 1)
            || pair.mt <= 0
        {
            return Err(format!("invalid RML particle pair for MT={}", pair.mt));
        }
        particle_pairs.push(pair);
    }

    let mut spin_groups = Vec::with_capacity(control.n1);
    for _ in 0..control.n1 {
        let (channel_record, next) = read_list_checked(lines, index)?;
        index = next;
        if channel_record.head.n1 != 6 * channel_record.head.n2 {
            return Err(format!(
                "RML channel LIST has NPL={}, expected {}",
                channel_record.head.n1,
                6 * channel_record.head.n2
            ));
        }
        let mut channels = Vec::with_capacity(channel_record.head.n2);
        for values in channel_record.values.as_chunks::<6>().0 {
            let pair = integer(values[0], "RML particle-pair index")?;
            let pair = usize::try_from(pair - 1)
                .map_err(|_| format!("RML particle-pair index {} is not one-based", pair))?;
            if pair >= particle_pairs.len() {
                return Err(format!(
                    "RML particle-pair index {} is out of range",
                    pair + 1
                ));
            }
            let l = integer(values[1], "RML channel L")?;
            if l < 0
                || values[4] < 0.0
                || values[5] < 0.0
                || !values[2..].iter().all(|value| value.is_finite())
            {
                return Err("invalid RML channel quantum number or radius".into());
            }
            channels.push(RmlChannel {
                pair,
                l,
                spin: values[2],
                boundary: values[3],
                effective_radius: values[4],
                true_radius: values[5],
            });
        }
        let (resonance_record, next) = read_list_checked(lines, index)?;
        index = next;
        let resonance_count = usize::try_from(resonance_record.head.l2)
            .map_err(|_| format!("negative RML NRS {}", resonance_record.head.l2))?;
        // SAMMY-written evaluations emit a 6-zero placeholder record for
        // zero-resonance spin groups (NRS=0 but one row present). Honor NRS
        // and consume the inert payload; the N2=NRS count check only binds
        // when resonances are actually declared.
        if resonance_count > 0 && resonance_record.head.n2 != resonance_count {
            return Err("RML resonance LIST N2 does not equal L2/NRS".into());
        }
        let values_per_resonance = 6 * (channels.len() + 1).div_ceil(6);
        let expected_values = values_per_resonance * resonance_count;
        if resonance_record.head.n1 != expected_values
            && !(resonance_count == 0
                && resonance_record.head.n1 % values_per_resonance == 0
                && resonance_record.values.iter().all(|value| *value == 0.0))
        {
            return Err(format!(
                "RML resonance LIST has NPL={}, expected {}",
                resonance_record.head.n1, expected_values
            ));
        }
        let mut resonances = Vec::with_capacity(resonance_count);
        for values in resonance_record
            .values
            .chunks_exact(values_per_resonance)
            .take(resonance_count)
        {
            resonances.push(RmlResonance {
                energy: values[0],
                widths: values[1..=channels.len()].to_vec(),
            });
        }
        let mut backgrounds = Vec::with_capacity(
            usize::try_from(channel_record.head.l1)
                .map_err(|_| format!("negative RML KBK {}", channel_record.head.l1))?,
        );
        for _ in 0..channel_record.head.l1 {
            let (extension, next) = parse_rml_extension(lines, index)?;
            backgrounds.push(extension);
            index = next;
        }
        let mut phase_shifts = Vec::with_capacity(
            usize::try_from(channel_record.head.l2)
                .map_err(|_| format!("negative RML KPS {}", channel_record.head.l2))?,
        );
        // ENDF-6 2.2.1.6: KPS>0 writes one LIST record per channel in order;
        // the channel index is positional, not a record field.
        for ordinal in 0..channel_record.head.l2 {
            let (extension, next) = parse_rml_phase_shift(lines, index, ordinal as usize + 1)?;
            phase_shifts.push(extension);
            index = next;
        }
        spin_groups.push(SpinGroup {
            spin: channel_record.head.c1,
            parity: channel_record.head.c2,
            channels,
            resonances,
            backgrounds,
            phase_shifts,
        });
    }
    Ok((
        RangeData::RMatrixLimited(RMatrixLimited {
            reduced_widths: control.l1 == 1,
            krm: control.l2,
            particle_pairs,
            spin_groups,
        }),
        index,
    ))
}

fn validate_unresolved_point(point: &UnresolvedPoint) -> Result<(), String> {
    if point.energy.is_some_and(|energy| energy <= 0.0)
        || point.spacing <= 0.0
        || [
            point.competitive,
            point.neutron,
            point.capture,
            point.fission,
        ]
        .into_iter()
        .any(|value| value < 0.0)
    {
        return Err("invalid unresolved energy, spacing or width".into());
    }
    Ok(())
}

const UNRESR_GRID_MANTISSAS: [f64; 13] = [
    1.0, 1.25, 1.5, 1.7, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.2, 8.5,
];
const UNRESR_GRID_DECADES: [f64; 6] = [10.0, 100.0, 1e3, 1e4, 1e5, 1e6];

fn unresolved_cross_section_mesh(
    energy_min: f64,
    energy_max: f64,
    case: UnresolvedCase,
    mut parameter_energies: Vec<f64>,
) -> Vec<f64> {
    parameter_energies.push(energy_min);
    parameter_energies.push(energy_max);
    parameter_energies.sort_by(f64::total_cmp);
    parameter_energies.dedup_by(|left, right| left.to_bits() == right.to_bits());

    let mut mesh = Vec::with_capacity(
        parameter_energies.len() + UNRESR_GRID_DECADES.len() * UNRESR_GRID_MANTISSAS.len(),
    );
    mesh.push(parameter_energies[0]);
    for &high in &parameter_energies[1..] {
        let low = *mesh.last().expect("unresolved mesh was seeded");
        if case == UnresolvedCase::A || high >= 1.26 * low {
            let mut cursor = low;
            for decade in UNRESR_GRID_DECADES {
                for mantissa in UNRESR_GRID_MANTISSAS {
                    let candidate = mantissa * decade;
                    if candidate > 1.01 * cursor && candidate < high {
                        mesh.push(candidate);
                        cursor = candidate;
                    }
                }
            }
        }
        mesh.push(high);
    }
    mesh
}

fn parse_unresolved(
    lines: &[&str],
    index: usize,
    lrf: i32,
    fission_widths: bool,
    energy_min: f64,
    energy_max: f64,
) -> Result<(RangeData, usize), String> {
    let case = if lrf == 2 {
        UnresolvedCase::C
    } else if fission_widths {
        UnresolvedCase::B
    } else {
        UnresolvedCase::A
    };
    let (control, mut index) = if case == UnresolvedCase::B {
        let (record, next) = read_list_checked(lines, index)?;
        (record, next)
    } else {
        let (head, next) = read_cont_checked(lines, index)?;
        (
            crate::endf::CheckedListRecord {
                head,
                values: Vec::new(),
            },
            next,
        )
    };
    let spin = control.head.c1;
    let ap = control.head.c2;
    let add_to_background = control.head.l1 == 0;
    if !matches!(control.head.l1, 0 | 1) {
        return Err(format!("invalid unresolved LSSF={}", control.head.l1));
    }
    let mut sequences = Vec::new();
    match case {
        UnresolvedCase::A => {
            for _ in 0..control.head.n1 {
                let (record, next) = read_list_checked(lines, index)?;
                index = next;
                if record.head.n1 != 6 * record.head.n2 {
                    return Err("case-A unresolved LIST count mismatch".into());
                }
                for values in record.values.as_chunks::<6>().0 {
                    let point = UnresolvedPoint {
                        energy: None,
                        spacing: values[0],
                        competitive: 0.0,
                        neutron: values[3],
                        capture: values[4],
                        fission: 0.0,
                    };
                    validate_unresolved_point(&point)?;
                    sequences.push(UnresolvedSequence {
                        awri: record.head.c1,
                        l: record.head.l1,
                        spin: values[1],
                        interpolation: 2,
                        competitive_dof: 0,
                        neutron_dof: integer(values[2], "unresolved AMUN")?,
                        fission_dof: 0,
                        points: vec![point],
                    });
                }
            }
        }
        UnresolvedCase::B => {
            let energies = control.values;
            if energies.len() != control.head.n1
                || energies.windows(2).any(|pair| pair[1] <= pair[0])
            {
                return Err("case-B unresolved energy grid is not strictly increasing".into());
            }
            for _ in 0..control.head.n2 {
                let (l_record, next) = read_cont_checked(lines, index)?;
                index = next;
                for _ in 0..l_record.n1 {
                    let (record, next) = read_list_checked(lines, index)?;
                    index = next;
                    if record.head.n1 != 6 + energies.len() || record.values.len() != record.head.n1
                    {
                        return Err("case-B unresolved fission-width LIST count mismatch".into());
                    }
                    let mut points = Vec::with_capacity(energies.len());
                    for (&energy, &fission) in energies.iter().zip(&record.values[6..]) {
                        let point = UnresolvedPoint {
                            energy: Some(energy),
                            spacing: record.values[1],
                            competitive: 0.0,
                            neutron: record.values[4],
                            capture: record.values[5],
                            fission,
                        };
                        validate_unresolved_point(&point)?;
                        points.push(point);
                    }
                    sequences.push(UnresolvedSequence {
                        awri: l_record.c1,
                        l: l_record.l1,
                        spin: record.values[2],
                        interpolation: 2,
                        competitive_dof: 0,
                        neutron_dof: integer(record.values[3], "unresolved AMUN")?,
                        fission_dof: integer(record.values[0], "unresolved AMUF")?,
                        points,
                    });
                }
            }
        }
        UnresolvedCase::C => {
            for _ in 0..control.head.n1 {
                let (l_record, next) = read_cont_checked(lines, index)?;
                index = next;
                for _ in 0..l_record.n1 {
                    let (record, next) = read_list_checked(lines, index)?;
                    index = next;
                    if record.head.n1 != 6 * (record.head.n2 + 1) {
                        return Err(format!(
                            "case-C unresolved LIST has NPL={}, expected {}",
                            record.head.n1,
                            6 * (record.head.n2 + 1)
                        ));
                    }
                    if !(1..=5).contains(&record.head.l1) {
                        return Err(format!(
                            "unsupported unresolved interpolation INT={}",
                            record.head.l1
                        ));
                    }
                    let dof = &record.values[..6];
                    let mut points = Vec::with_capacity(record.head.n2);
                    for values in record.values[6..].as_chunks::<6>().0 {
                        let point = UnresolvedPoint {
                            energy: Some(values[0]),
                            spacing: values[1],
                            competitive: values[2],
                            neutron: values[3],
                            capture: values[4],
                            fission: values[5],
                        };
                        validate_unresolved_point(&point)?;
                        points.push(point);
                    }
                    if points
                        .windows(2)
                        .any(|pair| pair[1].energy <= pair[0].energy)
                    {
                        return Err(
                            "case-C unresolved energy grid is not strictly increasing".into()
                        );
                    }
                    sequences.push(UnresolvedSequence {
                        awri: l_record.c1,
                        l: l_record.l1,
                        spin: record.head.c1,
                        interpolation: record.head.l1,
                        competitive_dof: integer(dof[2], "unresolved AMUX")?,
                        neutron_dof: integer(dof[3], "unresolved AMUN")?,
                        fission_dof: integer(dof[5], "unresolved AMUF")?,
                        points,
                    });
                }
            }
        }
    }
    let interpolation_energies: Vec<f64> = sequences
        .iter()
        .flat_map(|sequence| sequence.points.iter().filter_map(|point| point.energy))
        .collect();
    let interpolation_energies =
        unresolved_cross_section_mesh(energy_min, energy_max, case, interpolation_energies);
    Ok((
        RangeData::Unresolved(Unresolved {
            spin,
            ap,
            add_to_background,
            case,
            sequences,
            interpolation_energies,
        }),
        index,
    ))
}

pub fn parse_mf2(section: &Section<'_>) -> Result<ResonanceEvaluation, String> {
    if section.mf != 2 || section.mt != 151 {
        return Err(format!(
            "expected MF=2/MT=151, got MF={}/MT={}",
            section.mf, section.mt
        ));
    }
    let lines = section.lines.as_slice();
    let (head, mut index) = read_cont_checked(lines, 0)?;
    let za = integer(head.c1, "MF=2 ZA")?;
    if head.awr_invalid() {
        return Err(format!("invalid MF=2 AWR {}", head.c2));
    }
    let mut isotopes = Vec::with_capacity(head.n1);
    for _ in 0..head.n1 {
        let (isotope, next) = read_cont_checked(lines, index)?;
        index = next;
        let zai = integer(isotope.c1, "MF=2 ZAI")?;
        if isotope.c2 < 0.0 || !matches!(isotope.l2, 0 | 1) {
            return Err("invalid MF=2 isotope abundance or LFW".into());
        }
        let mut ranges = Vec::with_capacity(isotope.n1);
        for _ in 0..isotope.n1 {
            let (range, next) = read_cont_checked(lines, index)?;
            index = next;
            if range.c1 <= 0.0 || range.c2 <= range.c1 || !matches!(range.n1, 0 | 1) {
                return Err("invalid MF=2 energy range or NRO".into());
            }
            let scattering_radius = if range.n1 == 1 {
                let (record, next) = read_tab1_checked(lines, index)?;
                index = next;
                Some(table(record)?)
            } else {
                None
            };
            let (data, next) = match (range.l1, range.l2) {
                (0, 0) => {
                    let (control, next) = read_cont_checked(lines, index)?;
                    (
                        RangeData::ScatteringOnly {
                            spin: control.c1,
                            ap: control.c2,
                        },
                        next,
                    )
                }
                (1, 1..=3) => parse_legacy_resolved(lines, index, range.l2)?,
                (1, 7) => parse_rmatrix_limited(lines, index)?,
                (2, 1 | 2) => {
                    parse_unresolved(lines, index, range.l2, isotope.l2 == 1, range.c1, range.c2)?
                }
                (lru, lrf) => {
                    return Err(format!("unsupported MF=2 LRU={lru}/LRF={lrf}"));
                }
            };
            index = next;
            ranges.push(ResonanceRange {
                energy_min: range.c1,
                energy_max: range.c2,
                lru: range.l1,
                lrf: range.l2,
                naps: range.n2 as i32,
                scattering_radius,
                data,
            });
        }
        isotopes.push(ResonanceIsotope {
            zai,
            abundance: isotope.c2,
            fission_widths: isotope.l2 == 1,
            ranges,
        });
    }
    if index != lines.len() {
        return Err(format!(
            "MF=2/MT=151 has {} unconsumed record(s)",
            lines.len() - index
        ));
    }
    Ok(ResonanceEvaluation {
        za,
        awr: head.c2,
        isotopes,
    })
}

trait AwrRecord {
    fn awr_invalid(&self) -> bool;
}

impl AwrRecord for ContRecord {
    fn awr_invalid(&self) -> bool {
        !self.c2.is_finite() || self.c2 <= 0.0
    }
}

fn penetration_shift(l: i32, rho: f64) -> Result<(f64, f64), String> {
    let r2 = rho * rho;
    Ok(match l {
        0 => (rho, 0.0),
        1 => (rho * r2 / (1.0 + r2), -1.0 / (1.0 + r2)),
        2 => {
            let denominator = 9.0 + 3.0 * r2 + r2 * r2;
            (
                rho * r2 * r2 / denominator,
                -(18.0 + 3.0 * r2) / denominator,
            )
        }
        3 => {
            let denominator = 225.0 + 45.0 * r2 + 6.0 * r2 * r2 + r2.powi(3);
            (
                rho * r2.powi(3) / denominator,
                -(675.0 + 90.0 * r2 + 6.0 * r2 * r2) / denominator,
            )
        }
        4 => {
            let denominator =
                11_025.0 + 1_575.0 * r2 + 135.0 * r2 * r2 + 10.0 * r2.powi(3) + r2.powi(4);
            (
                rho * r2.powi(4) / denominator,
                -(44_100.0 + 4_725.0 * r2 + 270.0 * r2 * r2 + 10.0 * r2.powi(3)) / denominator,
            )
        }
        _ => {
            let (f, g, fp, gp) = riccati_bessel(l, rho)?;
            let d = f * f + g * g;
            (rho / d, rho * (f * fp + g * gp) / d)
        }
    })
}

fn phase_shift(l: i32, rho: f64) -> Result<f64, String> {
    Ok(match l {
        0 => rho,
        1 => rho - rho.atan(),
        2 => rho - (3.0 * rho).atan2(3.0 - rho * rho),
        3 => rho - (15.0 * rho - rho.powi(3)).atan2(15.0 - 6.0 * rho * rho),
        4 => rho - (105.0 * rho - 10.0 * rho.powi(3)).atan2(105.0 - 45.0 * rho * rho + rho.powi(4)),
        _ => riccati_bessel(l, rho).map(|(f, g, _, _)| f.atan2(g))?,
    })
}

/// Riccati–Bessel F_l = ρ·j_l (regular) and G_l = −ρ·n_l (irregular) with
/// their first derivatives, for arbitrary l ≥ 0 at ρ > 0. F is the minimal
/// solution of the three-term recurrence, so it is generated by downward
/// Miller recursion; G is dominant and stable upward. Used for l ≥ 5 where
/// the closed-form penetrability/shift/phase tables end — verified against
/// the closed forms for l ≤ 4 by `riccati_bessel_matches_closed_forms`.
fn riccati_bessel(l: i32, rho: f64) -> Result<(f64, f64, f64, f64), String> {
    if l < 0 {
        return Err(format!("negative orbital angular momentum L={l}"));
    }
    if rho <= 0.0 || !rho.is_finite() {
        return Err(format!("Riccati-Bessel needs finite rho>0, got {rho:.17e}"));
    }
    let l = l as usize;
    let (sin, cos) = rho.sin_cos();
    // Irregular (dominant) solution: stable upward.
    let mut g = vec![0.0_f64; l + 2];
    g[0] = cos;
    g[1] = cos / rho + sin;
    for k in 1..=l {
        g[k + 1] = (2 * k + 1) as f64 / rho * g[k] - g[k - 1];
    }
    // Regular (minimal) solution: downward Miller recursion seeded above
    // both l and ρ, normalized against the known F_0 = sin ρ.
    let hi = (l + 16).max(rho.ceil() as usize + 16);
    let mut f = vec![0.0_f64; hi + 2];
    f[hi] = 1e-35;
    for k in (1..=hi).rev() {
        f[k - 1] = (2 * k + 1) as f64 / rho * f[k] - f[k + 1];
    }
    if !(f[0].is_finite() && f[0] != 0.0) {
        return Err(format!(
            "Riccati-Bessel Miller seed failed at L={l}, rho={rho:.17e}"
        ));
    }
    let scale = sin / f[0];
    let fl = f[l] * scale;
    let fl1 = f[l + 1] * scale;
    let gl = g[l];
    let gl1 = g[l + 1];
    let w = l as f64 + 1.0;
    // F'_l = (l+1)/ρ·F_l − F_{l+1}; same identity for G.
    Ok((fl, gl, w / rho * fl - fl1, w / rho * gl - gl1))
}

// ---------------------------------------------------------------------------
// Coulomb wave functions for charged RML channels
// ---------------------------------------------------------------------------

/// z_a·z_b·μ[amu] / k[fm^-1] -> Sommerfeld parameter η.
/// e²/4πε0 = 1.4399764 MeV·fm, ℏc = 197.3269804 MeV·fm, amu = 931.49410242 MeV.
const COULOMB_ETA: f64 = 1.439_976_4 * 931.494_102_42 / 197.326_980_4;
/// Channels below this penetrability transmit < ~P·π/k² barns, far below
/// any resolvable sigma at any energy; keeping their rows in the collision
/// solve only worsens conditioning.
const RML_MIN_PENETRABILITY: f64 = 1e-40;

/// Complex ln Γ(z), Lanczos g=7 (valid for Re z >= 0.5).
fn log_gamma(z: Complex64) -> Complex64 {
    const C: [f64; 9] = [
        0.999_999_999_999_809_9,
        676.520_368_121_885_1,
        -1_259.139_216_722_402_8,
        771.323_428_777_653_1,
        -176.615_029_162_140_6,
        12.507_343_278_686_905,
        -0.138_571_095_265_720_12,
        9.984_369_578_019_572e-6,
        1.505_632_735_149_311_6e-7,
    ];
    let mut a = Complex64::new(C[0], 0.0);
    for (j, c) in C.iter().enumerate().skip(1) {
        a += Complex64::new(*c, 0.0) / (z + Complex64::new(j as f64, 0.0));
    }
    let t = z + Complex64::new(6.5, 0.0);
    Complex64::new(0.5 * (2.0 * std::f64::consts::PI).ln(), 0.0) + (z + 0.5) * t.ln() - t + a.ln()
}

/// Coulomb phase shift σ_l(η) = arg Γ(l+1+iη).
fn coulomb_phase(l: i32, eta: f64) -> f64 {
    let mut sigma = log_gamma(Complex64::new(1.0, eta)).im;
    for j in 1..=l.max(0) {
        sigma += (eta / j as f64).atan();
    }
    sigma
}

/// Series (Frobenius) start for the regular Coulomb function at small ρ:
/// F = Σ c_k ρ^{l+1+k}, c_k = (2η c_{k-1} − c_{k−2}) / (k(2l+k+1)).
/// Returns (F, F') at `rho`.
fn coulomb_series_f(l: i32, eta: f64, rho: f64) -> (f64, f64) {
    let lp1 = (l + 1) as f64;
    let ll = l as f64;
    let mut c = [0.0f64; 256];
    c[0] = 1.0;
    let mut f = rho.powi(l + 1);
    let mut fp = lp1 * rho.powi(l);
    for k in 1..256 {
        c[k] = (2.0 * eta * c[k - 1] - if k > 1 { c[k - 2] } else { 0.0 })
            / (k as f64 * (2.0 * ll + k as f64 + 1.0));
        let term = c[k] * rho.powi(l + 1 + k as i32);
        f += term;
        fp += c[k] * (lp1 + k as f64) * rho.powi(l + k as i32);
        if term.abs() < 1e-17 * f.abs().max(1e-300) {
            break;
        }
    }
    (f, fp)
}

/// RK4 propagator for y'' + K(ρ) y = 0 with K = 1 − 2η/ρ − l(l+1)/ρ².
/// Marches (y0, y0') from r0 through each ordered stop, returning the
/// (y, y') pair at every requested radius.
fn coulomb_march(l: i32, eta: f64, r0: f64, y0: f64, y0p: f64, stops: &[f64]) -> Vec<(f64, f64)> {
    let k2 = |r: f64| -> f64 {
        let rf = r.max(1e-8);
        1.0 - 2.0 * eta / rf - (l * (l + 1)) as f64 / (rf * rf)
    };
    let mut out = Vec::with_capacity(stops.len());
    let mut r = r0;
    let mut y = y0;
    let mut yp = y0p;
    for &target in stops {
        let n = ((target - r).abs() / 0.02).ceil().max(1.0) as usize;
        let h = (target - r) / n as f64;
        for _ in 0..n {
            let dy = |yy: f64, vp: f64, rr: f64| (vp, -k2(rr) * yy);
            let (k1y, k1p) = dy(y, yp, r);
            let (k2y, k2p) = dy(y + h / 2.0 * k1y, yp + h / 2.0 * k1p, r + h / 2.0);
            let (k3y, k3p) = dy(y + h / 2.0 * k2y, yp + h / 2.0 * k2p, r + h / 2.0);
            let (k4y, k4p) = dy(y + h * k3y, yp + h * k3p, r + h);
            y += h / 6.0 * (k1y + 2.0 * k2y + 2.0 * k3y + k4y);
            yp += h / 6.0 * (k1p + 2.0 * k2p + 2.0 * k3p + k4p);
            r += h;
        }
        out.push((y, yp));
    }
    out
}

fn coulomb_numerov(l: i32, eta: f64, r0: f64, y0: f64, y0p: f64, r1: f64) -> (f64, f64) {
    coulomb_march(l, eta, r0, y0, y0p, &[r1])[0]
}

/// Coulomb wave functions (F, F', G, G') via Steed's method (DLMF 33.8):
/// u = F'/F from the real continued fraction 33.8.1, p+iq = H'+/H+ from
/// the complex continued fraction 33.8.2, then 33.8.4–5 recover F, F', G,
/// G' with W[F,G]=−1 exact by construction.
fn coulomb_fg_steed(l: i32, eta: f64, rho: f64) -> Result<(f64, f64, f64, f64), String> {
    const TINY: f64 = 1e-300;
    const EPS: f64 = 1e-14;
    const MAX_ITER: usize = 50_000;
    // CF1 (33.8.1): u = S_{l+1} + a_1/(b_1 + a_2/(b_2 + ...)) with
    // b_0 = S_{l+1}, a_k = −R²_{l+k}, b_k = T_{l+k}.
    let lf = l as f64;
    let s = |k: f64| k / rho + eta / k;
    let r2 = |k: f64| 1.0 + eta * eta / (k * k);
    let mut f = s(lf + 1.0).max(TINY);
    let mut c = f;
    let mut d = 0.0;
    let mut delta_last = 0.0;
    let mut denom_sign = 1.0;
    let mut converged = false;
    for k in 1..MAX_ITER {
        let lk = lf + k as f64;
        let a = -r2(lk);
        let b = s(lk) + s(lk + 1.0);
        d = b + a * d;
        if d.abs() < TINY {
            d = TINY;
        }
        c = b + a / c;
        if c.abs() < TINY {
            c = TINY;
        }
        d = 1.0 / d;
        let delta = c * d;
        f *= delta;
        delta_last = delta;
        denom_sign = d.signum();
        if (delta - 1.0).abs() < EPS {
            converged = true;
            break;
        }
    }
    if !converged {
        return Err("Coulomb CF1 did not converge".into());
    }
    let _ = delta_last;
    let u = f;
    // CF2 (33.8.2): p + iq = c + (i/ρ)·[ab/(d_1 + (a+1)(b+1)/(d_2 + ...))],
    // a = 1+l+iη, b = −l+iη, c = i(1−η/ρ), d_j = 2(ρ−η+ji).
    let cf2 = {
        let i = Complex64::i();
        let a0 = Complex64::new(lf + 1.0, eta);
        let b0 = Complex64::new(-lf, eta);
        let dj = |j: f64| Complex64::new(2.0 * (rho - eta), 2.0 * j);
        // Lentz floor: 1e-150 keeps c.norm_sqr() above f64 underflow in n/c.
        let tiny = 1e-150_f64;
        let mut f = Complex64::new(tiny, 0.0);
        let mut c = f;
        let mut d = Complex64::new(0.0, 0.0);
        let mut value = None;
        for j in 1..MAX_ITER {
            let jf = j as f64;
            let n = (a0 + Complex64::new(jf - 1.0, 0.0)) * (b0 + Complex64::new(jf - 1.0, 0.0));
            let b = dj(jf);
            d = b + n * d;
            if d.norm_sqr() < 1e-250 {
                d = Complex64::new(tiny, 0.0);
            }
            c = b + n / c;
            if c.norm_sqr() < 1e-250 {
                c = Complex64::new(tiny, 0.0);
            }
            d = d.inv();
            let delta = c * d;
            f *= delta;
            if (delta - Complex64::new(1.0, 0.0)).norm_sqr() < EPS * EPS {
                value = Some(Complex64::new(0.0, 1.0 - eta / rho) + i / rho * f);
                break;
            }
        }
        value
    };
    let Some(cf2) = cf2 else {
        return Err("Coulomb CF2 did not converge".into());
    };
    let (p, q) = (cf2.re, cf2.im);
    if q <= 0.0 {
        return Err("Coulomb CF2 produced a nonpositive imaginary part".into());
    }
    // 33.8.4–5, sign from the last CF1 denominator (DLMF note).
    let f_mag = 1.0 / ((u - p) * (u - p) / q + q).sqrt();
    let fl = denom_sign * f_mag;
    let fpl = u * fl;
    let gl = (u - p) * fl / q;
    let gpl = (u * p - p * p - q * q) * fl / q;
    Ok((fl, fpl, gl, gpl))
}

/// Coulomb wave functions (F, F', G, G') for angular momentum l at (η, ρ).
///
/// Near or above the outer turning point Steed's method is fast and
/// accurate. Deep below it the continued fractions converge to values with
/// ~|G|² relative contamination (DLMF 33.23(v)) while the true
/// penetrability is ~e^(−2πη) ≪ any channel that could compete — so the
/// point is reported as a failure, which upstream code treats as a closed
/// channel. The propagation fallback only fills the narrow band where
/// Steed fails while penetrability could still matter.
fn coulomb_fg(l: i32, eta: f64, rho: f64) -> Result<(f64, f64, f64, f64), String> {
    if rho <= 0.0 || eta < 0.0 {
        return Err("Coulomb evaluation needs rho > 0 and eta >= 0".into());
    }
    let r_turn = eta + (eta * eta + (l * (l + 1)) as f64).sqrt();
    if rho >= 0.75 * r_turn {
        if let Ok(result) = coulomb_fg_steed(l, eta, rho) {
            return Ok(result);
        }
        return coulomb_fg_march(l, eta, rho);
    }
    // Deep sub-barrier only suppresses exponentially when η is appreciable;
    // for η ~ 0 the centrifugal barrier suppresses only algebraically, so
    // the propagation path still has to run.
    if eta > 1.0 {
        return Err("Coulomb evaluation is deep below the barrier".into());
    }
    coulomb_fg_march(l, eta, rho)
}

/// Propagation fallback: series-started regular solution marched outward,
/// WKBJ-started irregular solution marched inward, decontaminated against
/// the asymptotic basis above the outer turning point.
fn coulomb_fg_march(l: i32, eta: f64, rho: f64) -> Result<(f64, f64, f64, f64), String> {
    // G: inward from WKBJ asymptotic above the outer turning point.
    let r_turn = eta + (eta * eta + (l * (l + 1)) as f64).sqrt();
    let r_far = rho.max(r_turn) + 30.0;
    let sigma = coulomb_phase(l, eta);
    let theta = r_far - eta * (2.0 * r_far).ln() - (l as f64) * std::f64::consts::FRAC_PI_2 + sigma;
    let kw = (1.0 - 2.0 * eta / r_far - (l * (l + 1)) as f64 / (r_far * r_far)).max(1e-6);
    // WKBJ basis (F_t ~ sinθ, G_t ~ cosθ) and derivatives; θ'_w = √K.
    let kw4 = kw.powf(0.25);
    let ft = theta.sin() / kw4;
    let gt = theta.cos() / kw4;
    let ftp = kw4 * theta.cos();
    let gtp = -kw4 * theta.sin();
    let wt = ft * gtp - ftp * gt;
    if !wt.is_finite() || wt == 0.0 {
        return Err("Coulomb WKBJ basis is degenerate".into());
    }
    // F: series start, propagated outward to rho and r_far in one march.
    let r_start = (0.05 / (1.0 + eta / 4.0)).min(rho);
    let (fs, fps) = coulomb_series_f(l, eta, r_start);
    let f_stops = coulomb_march(l, eta, r_start, fs, fps, &[rho, r_far]);
    let (fr, frp) = f_stops[0];
    let (fr_far, frp_far) = f_stops[1];
    // G: inward from the WKBJ start.
    let (gr, grp) = coulomb_numerov(l, eta, r_far, gt, gtp, rho);
    if ![fr, frp, fr_far, frp_far, gr, grp]
        .iter()
        .all(|v| v.is_finite())
    {
        return Err("Coulomb propagation produced a nonfinite value".into());
    }
    // F_r = α F + β G in the true basis: α = (F_r G'_t − F'_r G_t)/W_t,
    // β = (F_t F'_r − F'_t F_r)/W_t.
    let alpha = (fr_far * gtp - frp_far * gt) / wt;
    let beta = (ft * frp_far - ftp * fr_far) / wt;
    if !alpha.is_finite() || alpha == 0.0 || !beta.is_finite() {
        return Err("Coulomb basis decomposition failed".into());
    }
    // Decontaminate F_r of its G admixture, then normalize so that
    // W[F, G_r] = F G'_r − F' G_r = −1 exactly (G_r is taken as û_G; the
    // WKBJ basis itself is not symplectic, W_t = −θ'(r_far) ≠ −1).
    let decon_f = fr - beta * gr;
    let decon_fp = frp - beta * grp;
    let w = decon_f * grp - decon_fp * gr;
    if !w.is_finite() || w == 0.0 {
        return Err("Coulomb Wronskian is zero or nonfinite".into());
    }
    // Consistency: W[F_r, G_r] must equal α·W[F_t, G_t] at r_far.
    let expected = alpha * wt;
    if ((w / expected) - 1.0).abs() > 0.1 {
        return Err("Coulomb decomposition/Wronskian inconsistency".into());
    }
    let f = -decon_f / w;
    let fp = -decon_fp / w;
    Ok((f, fp, gr, grp))
}

/// Coulomb penetrability and shift factor for a charged channel (ENDF-6
/// D.84): A_l² = F²+G², P_l = ρ/A², S_l = (ρ/A)·∂A/∂ρ.
fn coulomb_penetration_shift(l: i32, eta: f64, rho: f64) -> Result<(f64, f64), String> {
    let (f, fp, g, gp) = match coulomb_fg(l, eta, rho) {
        Ok(v) => v,
        Err(e) => {
            // Below the outer turning point the irregular solution overflows
            // f64 for deep barriers: the channel is closed.
            let r_turn = eta + (eta * eta + (l * (l + 1)) as f64).sqrt();
            if rho < r_turn {
                return Ok((0.0, 0.0));
            }
            return Err(e);
        }
    };
    let d = f * f + g * g;
    if d <= 0.0 || !d.is_finite() {
        // deeply sub-barrier: penetrability underflows to zero
        return Ok((0.0, 0.0));
    }
    Ok((rho / d, rho * (f * fp + g * gp) / d))
}

pub(crate) fn channel_radius(awri: f64) -> f64 {
    0.123 * (awri * NEUTRON_MASS_AMU).cbrt() + 0.08
}

pub(crate) fn scattering_radius(
    range: &ResonanceRange,
    fallback: f64,
    energy: f64,
) -> Result<f64, String> {
    match &range.scattering_radius {
        Some(table) => table.evaluate(energy),
        None => Ok(fallback),
    }
}

fn solve_complex_matrix(
    mut matrix: Vec<Vec<Complex64>>,
    mut right: Vec<Vec<Complex64>>,
) -> Result<Vec<Vec<Complex64>>, String> {
    let size = matrix.len();
    if size == 0
        || matrix.iter().any(|row| row.len() != size)
        || right.len() != size
        || right.iter().any(|row| row.len() != size)
    {
        return Err("RML matrix operands must be equally sized nonempty squares".into());
    }
    for column in 0..size {
        let pivot = (column..size)
            .max_by(|&left, &right| {
                matrix[left][column]
                    .norm_sqr()
                    .total_cmp(&matrix[right][column].norm_sqr())
            })
            .expect("nonempty pivot range");
        let pivot_norm = matrix[pivot][column].norm_sqr();
        if !pivot_norm.is_finite() || pivot_norm <= f64::MIN_POSITIVE {
            return Err("singular or nonfinite RML collision matrix".into());
        }
        matrix.swap(column, pivot);
        right.swap(column, pivot);
        let diagonal = matrix[column][column];
        for item in &mut matrix[column][column..] {
            *item /= diagonal;
        }
        for item in &mut right[column] {
            *item /= diagonal;
        }
        let pivot_matrix = matrix[column].clone();
        let pivot_right = right[column].clone();
        for row in 0..size {
            if row == column {
                continue;
            }
            let factor = matrix[row][column];
            if factor == Complex64::new(0.0, 0.0) {
                continue;
            }
            for item in column..size {
                matrix[row][item] -= factor * pivot_matrix[item];
            }
            for item in 0..size {
                right[row][item] -= factor * pivot_right[item];
            }
        }
    }
    if right
        .iter()
        .flatten()
        .any(|value| !value.re.is_finite() || !value.im.is_finite())
    {
        return Err("RML collision-matrix solution is nonfinite".into());
    }
    Ok(right)
}

#[derive(Clone, Copy)]
struct OpenRmlChannel {
    declared: usize,
    pair: usize,
    wave: f64,
    penetrability: f64,
    shift_minus_boundary: f64,
    phase: f64,
    /// Imaginary part of a tabulated phase shift (LPS=1); zero otherwise.
    absorption: f64,
}

fn rml_threshold(pair: &ParticlePair, entrance_lab_to_cm: f64) -> f64 {
    -pair.q_value / entrance_lab_to_cm
}

/// Every channel threshold strictly inside the range: a channel toggling on
/// (its cross-section contribution may not vanish at threshold — e.g. a
/// penetrability-flag-0 pair forced to P=1) makes the evaluated sigma
/// discontinuous there, so the caller must seed the edge as a boundary.
pub fn rml_channel_thresholds(range: &ResonanceRange) -> Vec<f64> {
    let RangeData::RMatrixLimited(rml) = &range.data else {
        return Vec::new();
    };
    let Some(entrance_pair) = rml.particle_pairs.iter().find(|pair| pair.mt == 2) else {
        return Vec::new();
    };
    let entrance_lab_to_cm = entrance_pair.mass_b / (entrance_pair.mass_a + entrance_pair.mass_b);
    let mut out = Vec::new();
    for spin_group in &rml.spin_groups {
        for channel in &spin_group.channels {
            let threshold = rml_threshold(&rml.particle_pairs[channel.pair], entrance_lab_to_cm);
            if threshold > range.energy_min && threshold < range.energy_max {
                out.push(threshold);
            }
        }
    }
    out.sort_by(f64::total_cmp);
    out.dedup_by(|left, right| left.to_bits() == right.to_bits());
    out
}

fn rml_wave_coefficient(
    pair: &ParticlePair,
    entrance_mass: f64,
    entrance_lab_to_cm: f64,
) -> Result<f64, String> {
    if pair.mass_a <= 0.0 || pair.mass_b <= 0.0 || entrance_mass <= 0.0 {
        return Err(format!(
            "RML MT={} has invalid particle-pair masses",
            pair.mt
        ));
    }
    let pair_lab_to_cm = pair.mass_b / (pair.mass_a + pair.mass_b);
    let reduced_mass = pair_lab_to_cm * pair.mass_a;
    Ok(K_WAVE * (reduced_mass * entrance_lab_to_cm / entrance_mass).sqrt())
}

/// Sommerfeld parameter for a charged particle pair at channel wave number k.
fn coulomb_eta(pair: &ParticlePair, wave: f64) -> Result<f64, String> {
    if pair.mass_a <= 0.0 || pair.mass_b <= 0.0 || wave <= 0.0 {
        return Err(format!(
            "RML MT={} has invalid charged-pair kinematics",
            pair.mt
        ));
    }
    let reduced = pair.mass_a * pair.mass_b / (pair.mass_a + pair.mass_b);
    Ok(COULOMB_ETA * (pair.za as f64) * (pair.zb as f64) * reduced / wave)
}

fn rml_channel_at(
    rml: &RMatrixLimited,
    channel: &RmlChannel,
    declared: usize,
    energy: f64,
    entrance_mass: f64,
    entrance_lab_to_cm: f64,
    phase_shifts: &[RmlExtension],
) -> Result<Option<OpenRmlChannel>, String> {
    let pair = &rml.particle_pairs[channel.pair];
    let threshold = rml_threshold(pair, entrance_lab_to_cm);
    if energy <= threshold {
        return Ok(None);
    }
    let charged = pair.za != 0 && pair.zb != 0;
    let (wave, penetrability, shift, phase) = if pair.penetrability == 0 {
        (1.0, 1.0, 0.0, 0.0)
    } else {
        if channel.true_radius <= 0.0 || channel.effective_radius <= 0.0 {
            return Err(format!(
                "RML MT={} needs positive true/effective radii",
                pair.mt
            ));
        }
        let coefficient = rml_wave_coefficient(pair, entrance_mass, entrance_lab_to_cm)?;
        let wave = coefficient * (energy - threshold).sqrt();
        let (penetrability, shift, phase) = if charged {
            let eta = coulomb_eta(pair, wave)?;
            let (p, s) = coulomb_penetration_shift(channel.l, eta, wave * channel.true_radius)?;
            // ENDF-6 D.84/D.85: φ_l = atan2(F, G) at the effective radius and
            // Ω_c = exp(i(w_c − φ_c)) with w_c = Σ_{n=1..l} atan(η/n).
            let phase = if p == 0.0 {
                0.0
            } else {
                let (f, _fp, g, _gp) = coulomb_fg(channel.l, eta, wave * channel.effective_radius)?;
                f.atan2(g) - (coulomb_phase(channel.l, eta) - coulomb_phase(0, eta))
            };
            (p, s, phase)
        } else {
            let (p, s) = penetration_shift(channel.l, wave * channel.true_radius)?;
            let phase = phase_shift(channel.l, wave * channel.effective_radius)?;
            (p, s, phase)
        };
        (wave, penetrability, shift, phase)
    };
    // LPS=1 replaces the hard-sphere/Coulomb phase with a tabulated complex
    // phase shift for this channel (PSR+i·PSI).
    let (phase, absorption) = match phase_shifts
        .iter()
        .find(|extension| extension.channel == declared + 1)
    {
        Some(extension) if extension.law == 1 => {
            let real = extension
                .real
                .as_ref()
                .ok_or("RML LPS=1 record is missing the real table")?
                .evaluate(energy)?;
            let imaginary = extension
                .imaginary
                .as_ref()
                .ok_or("RML LPS=1 record is missing the imaginary table")?
                .evaluate(energy)?;
            (real, imaginary)
        }
        _ => (phase, 0.0),
    };
    if penetrability < RML_MIN_PENETRABILITY {
        // Transmission is bounded by ~4P, so a channel with vanishing
        // penetrability contributes nothing physically — but its row/column
        // still enters the collision solve, where the |U|² < P-scale
        // answer is destroyed by conditioning noise from the huge dynamic
        // range. Drop it like a closed channel.
        return Ok(None);
    }
    if penetrability <= 0.0 || !penetrability.is_finite() {
        return Err(format!(
            "RML MT={} has nonpositive channel penetrability",
            pair.mt
        ));
    }
    Ok(Some(OpenRmlChannel {
        declared,
        pair: channel.pair,
        wave,
        penetrability,
        shift_minus_boundary: if pair.shift == 0 {
            0.0
        } else {
            shift - channel.boundary
        },
        phase,
        absorption,
    }))
}

/// Channel background R-matrix element at `energy` for a KBK extension
/// (ENDF-6 2.2.1.6 / D.1.7.5). Law 0 contributes nothing; law 1 is a
/// tabulated complex function; law 2 is SAMMY's logarithmic
/// parameterization (D.77). Law 3 (Fröhner) is not implemented.
fn rml_background(extension: &RmlExtension, energy: f64) -> Result<Complex64, String> {
    match extension.law {
        0 => Ok(Complex64::new(0.0, 0.0)),
        1 => {
            let real = extension
                .real
                .as_ref()
                .ok_or("RML LBK=1 record is missing the real table")?
                .evaluate(energy)?;
            let imaginary = extension
                .imaginary
                .as_ref()
                .ok_or("RML LBK=1 record is missing the imaginary table")?
                .evaluate(energy)?;
            Ok(Complex64::new(real, imaginary))
        }
        2 => {
            if extension.parameters.len() != 5 {
                return Err(format!(
                    "RML LBK=2 expects 5 parameters, found {}",
                    extension.parameters.len()
                ));
            }
            let (e_down, e_up) = (extension.energy_min, extension.energy_max);
            if !(e_down < energy && energy < e_up) || e_down >= e_up {
                return Err(format!(
                    "RML LBK=2 energy {energy} is outside ({} , {})",
                    e_down, e_up
                ));
            }
            let [r0, r1, r2, s0, s1]: [f64; 5] = extension.parameters[..5]
                .try_into()
                .map_err(|_| "RML LBK=2 parameter list is truncated".to_string())?;
            // D.77: Rcon + Rlin E + Rq E² − slin(Eup−Edown)
            //       − (scon + slin E) ln[(Eup−E)/(E−Edown)]
            let value = r0 + r1 * energy + r2 * energy * energy
                - s1 * (e_up - e_down)
                - (s0 + s1 * energy) * ((e_up - energy) / (energy - e_down)).ln();
            if !value.is_finite() {
                return Err("RML LBK=2 background is nonfinite".into());
            }
            Ok(Complex64::new(value, 0.0))
        }
        law => Err(format!("RML background law {law} is not implemented")),
    }
}

fn rml_reduced_amplitude(
    rml: &RMatrixLimited,
    channel: &RmlChannel,
    width: f64,
    resonance_energy: f64,
    entrance_mass: f64,
    entrance_lab_to_cm: f64,
) -> Result<f64, String> {
    if width == 0.0 {
        return Ok(0.0);
    }
    let pair = &rml.particle_pairs[channel.pair];
    let penetrability = if pair.penetrability == 0 {
        1.0
    } else {
        if channel.true_radius <= 0.0 {
            return Err(format!("RML MT={} needs a positive true radius", pair.mt));
        }
        let threshold = rml_threshold(pair, entrance_lab_to_cm);
        let coefficient = rml_wave_coefficient(pair, entrance_mass, entrance_lab_to_cm)?;
        let wave = coefficient * (resonance_energy - threshold).abs().sqrt();
        let rho = wave * channel.true_radius;
        if pair.za != 0 && pair.zb != 0 {
            coulomb_penetration_shift(channel.l, coulomb_eta(pair, wave)?, rho)?.0
        } else {
            penetration_shift(channel.l, rho)?.0
        }
    };
    if penetrability <= 0.0 || !penetrability.is_finite() {
        return Err(format!(
            "RML MT={} resonance has nonpositive penetrability",
            pair.mt
        ));
    }
    Ok(width.signum() * (0.5 * width.abs() / penetrability).sqrt())
}

/// Validate that an R-matrix-limited range uses only the subset implemented by this crate.
///
/// This check is intentionally independent of energy and reaction selection. Callers that might otherwise use an
/// MF=3 background must not silently skip an unsupported MF=2 representation merely because its malformed channel
/// declarations make the resonance contribution appear to be absent.
pub fn validate_rmatrix_limited(range: &ResonanceRange) -> Result<(), String> {
    let RangeData::RMatrixLimited(rml) = &range.data else {
        return Err("range is not R-matrix-limited".into());
    };
    if rml.krm != 3 {
        return Err(format!("unsupported RML KRM={}", rml.krm));
    }
    if rml.reduced_widths {
        return Err("RML reduced-width amplitudes (IFG=1) are not implemented".into());
    }
    let entrance_pair = rml
        .particle_pairs
        .iter()
        .find(|pair| pair.mt == 2)
        .ok_or("RML evaluation has no neutron entrance particle pair")?;
    if entrance_pair.mass_a <= 0.0 || entrance_pair.mass_b <= 0.0 {
        return Err("RML neutron entrance pair has invalid masses".into());
    }

    for pair in &rml.particle_pairs {
        if pair.za < 0 || pair.zb < 0 {
            return Err(format!(
                "RML particle pair for MT={} has negative charge",
                pair.mt
            ));
        }
    }
    for spin_group in &rml.spin_groups {
        for extension in &spin_group.backgrounds {
            if !matches!(extension.law, 0..=2) {
                return Err(format!(
                    "RML background law {} is not implemented",
                    extension.law
                ));
            }
            if extension.channel == 0 || extension.channel > spin_group.channels.len() {
                return Err(format!(
                    "RML background channel {} is out of range",
                    extension.channel
                ));
            }
        }
        for extension in &spin_group.phase_shifts {
            if !matches!(extension.law, 0 | 1) {
                return Err(format!(
                    "RML phase-shift law {} is not implemented",
                    extension.law
                ));
            }
            if extension.channel == 0 || extension.channel > spin_group.channels.len() {
                return Err(format!(
                    "RML phase-shift channel {} is out of range",
                    extension.channel
                ));
            }
        }
        let mut gamma_channels = 0usize;
        for channel in &spin_group.channels {
            let pair = rml.particle_pairs.get(channel.pair).ok_or_else(|| {
                format!(
                    "RML particle-pair index {} is out of range",
                    channel.pair + 1
                )
            })?;
            if pair.mt == 102 {
                gamma_channels += 1;
            }
            if pair.penetrability != 0 {
                if channel.l > 30 {
                    return Err(format!(
                        "unsupported orbital angular momentum L={}",
                        channel.l
                    ));
                }
                if channel.true_radius <= 0.0 || channel.effective_radius <= 0.0 {
                    return Err(format!(
                        "RML MT={} needs positive true/effective radii",
                        pair.mt
                    ));
                }
                if pair.mass_a <= 0.0 || pair.mass_b <= 0.0 {
                    return Err(format!(
                        "RML MT={} has invalid particle-pair masses",
                        pair.mt
                    ));
                }
            }
        }
        if gamma_channels != 1 {
            return Err(format!(
                "Reich-Moore RML spin group needs exactly one eliminated MT=102 channel, found {gamma_channels}"
            ));
        }
        if spin_group
            .resonances
            .iter()
            .any(|resonance| resonance.widths.len() != spin_group.channels.len())
        {
            return Err("RML resonance width count disagrees with its channels".into());
        }
    }
    Ok(())
}

/// Reconstruct one Reich-Moore R-matrix-limited range at zero Kelvin.
///
/// The returned capture term is the unitarity defect from the eliminated photon channel. Explicit fission and
/// other non-neutron particle pairs are accumulated separately. Coulomb channels, reduced-width input and RML
/// extensions fail closed until their declared physics is implemented.
pub fn reconstruct_rmatrix_limited(
    range: &ResonanceRange,
    energy: f64,
) -> Result<CrossSections, String> {
    if energy <= 0.0 || energy < range.energy_min || energy > range.energy_max {
        return Ok(CrossSections::default());
    }
    let RangeData::RMatrixLimited(rml) = &range.data else {
        return Err("range is not R-matrix-limited".into());
    };
    validate_rmatrix_limited(range)?;
    let entrance_pair = rml
        .particle_pairs
        .iter()
        .find(|pair| pair.mt == 2)
        .expect("validated RML neutron entrance pair");
    let entrance_lab_to_cm = entrance_pair.mass_b / (entrance_pair.mass_a + entrance_pair.mass_b);
    let mut result = CrossSections::default();

    for spin_group in &rml.spin_groups {
        let gamma_channels: Vec<usize> = spin_group
            .channels
            .iter()
            .enumerate()
            .filter_map(|(index, channel)| {
                (rml.particle_pairs[channel.pair].mt == 102).then_some(index)
            })
            .collect();
        let gamma_channel = gamma_channels[0];
        let mut open = Vec::new();
        for (declared, channel) in spin_group.channels.iter().enumerate() {
            if declared != gamma_channel {
                if let Some(channel) = rml_channel_at(
                    rml,
                    channel,
                    declared,
                    energy,
                    entrance_pair.mass_a,
                    entrance_lab_to_cm,
                    &spin_group.phase_shifts,
                )? {
                    open.push(channel);
                }
            }
        }
        let entrance: Vec<usize> = open
            .iter()
            .enumerate()
            .filter_map(|(index, channel)| {
                (rml.particle_pairs[channel.pair].mt == 2).then_some(index)
            })
            .collect();
        if entrance.is_empty() {
            continue;
        }
        let size = open.len();
        let mut r_matrix = vec![vec![Complex64::new(0.0, 0.0); size]; size];
        // Channel background R-matrix elements (KBK/LBK extensions, ENDF-6
        // D.77 family) contribute diagonally.
        for extension in &spin_group.backgrounds {
            if let Some(row) = open
                .iter()
                .position(|channel| channel.declared == extension.channel - 1)
            {
                r_matrix[row][row] += rml_background(extension, energy)?;
            }
        }
        for resonance in &spin_group.resonances {
            let half_capture = 0.5 * resonance.widths[gamma_channel].abs();
            let alpha = Complex64::new(resonance.energy - energy, -half_capture).inv();
            let mut amplitudes = Vec::with_capacity(size);
            for channel in &open {
                amplitudes.push(rml_reduced_amplitude(
                    rml,
                    &spin_group.channels[channel.declared],
                    resonance.widths[channel.declared],
                    resonance.energy,
                    entrance_pair.mass_a,
                    entrance_lab_to_cm,
                )?);
            }
            for row in 0..size {
                for column in 0..size {
                    r_matrix[row][column] += alpha * amplitudes[row] * amplitudes[column];
                }
            }
        }

        // X = sqrt(P) (I - R L)^-1 R sqrt(P), W = I + 2iX, U = Omega W Omega.
        let mut system = vec![vec![Complex64::new(0.0, 0.0); size]; size];
        for row in 0..size {
            for column in 0..size {
                let channel_log_derivative = Complex64::new(
                    open[column].shift_minus_boundary,
                    open[column].penetrability,
                );
                system[row][column] = -r_matrix[row][column] * channel_log_derivative;
            }
            system[row][row] += Complex64::new(1.0, 0.0);
        }
        let reduced = solve_complex_matrix(system, r_matrix)?;
        let mut collision = vec![vec![Complex64::new(0.0, 0.0); size]; size];
        for row in 0..size {
            for column in 0..size {
                let x = reduced[row][column]
                    * (open[row].penetrability * open[column].penetrability).sqrt();
                let w = if row == column {
                    Complex64::new(1.0, 0.0) + 2.0 * Complex64::i() * x
                } else {
                    2.0 * Complex64::i() * x
                };
                collision[row][column] = Complex64::from_polar(
                    (open[row].absorption + open[column].absorption).exp(),
                    -(open[row].phase + open[column].phase),
                ) * w;
            }
        }

        let statistical = (2.0 * spin_group.spin.abs() + 1.0)
            / ((2.0 * entrance_pair.spin_a.abs() + 1.0) * (2.0 * entrance_pair.spin_b.abs() + 1.0));
        if !statistical.is_finite() || statistical <= 0.0 {
            return Err("RML spin group has an invalid statistical factor".into());
        }
        for &incoming in &entrance {
            let wave = open[incoming].wave;
            if wave <= 0.0 {
                return Err("RML neutron entrance channel has a nonpositive wave number".into());
            }
            let scale = std::f64::consts::PI / (wave * wave) * statistical;
            let mut explicit_probability = 0.0;
            for outgoing in 0..size {
                let probability = collision[outgoing][incoming].norm_sqr();
                explicit_probability += probability;
                let mt = rml.particle_pairs[open[outgoing].pair].mt;
                if mt == 2 {
                    let delta = if outgoing == incoming { 1.0 } else { 0.0 };
                    result.elastic += scale
                        * (Complex64::new(delta, 0.0) - collision[outgoing][incoming]).norm_sqr();
                } else if mt == 18 {
                    result.fission += scale * probability;
                } else {
                    result.competitive += scale * probability;
                }
            }
            if !explicit_probability.is_finite() || explicit_probability > 1.0 + 5e-9 {
                return Err(format!(
                    "RML collision matrix violates unitarity with explicit probability {explicit_probability}"
                ));
            }
            result.capture += scale * (1.0 - explicit_probability).max(0.0);
        }
    }
    result.checked("R-matrix-limited reconstruction")
}

// Hwang ten-point chi-square quadrature used by NJOY UNRESR. The outer index is degrees of freedom minus one.
const HWANG_WEIGHTS: [[f64; 10]; 4] = [
    [
        1.112_041_3e-1,
        2.354_679_8e-1,
        2.844_098_7e-1,
        2.241_912_7e-1,
        1.096_766_8e-1,
        3.049_378_9e-2,
        4.293_087_4e-3,
        2.582_704_7e-4,
        4.903_196_5e-6,
        1.407_920_6e-8,
    ],
    [
        3.377_341_8e-2,
        7.993_217_1e-2,
        1.283_593_7e-1,
        1.765_261_6e-1,
        2.134_704_3e-1,
        2.115_496_5e-1,
        1.336_518_6e-1,
        2.263_065_9e-2,
        1.631_363_8e-5,
        2.745_383e-31,
    ],
    [
        3.337_621_4e-4,
        1.850_610_8e-2,
        1.230_994_6e-1,
        2.991_892_3e-1,
        3.343_147_5e-1,
        1.776_665_7e-1,
        4.269_589_4e-2,
        4.076_057_5e-3,
        1.176_611_5e-4,
        5.098_954_6e-7,
    ],
    [
        1.762_378_8e-3,
        2.151_774_9e-2,
        8.097_984_9e-2,
        1.879_799_8e-1,
        3.015_633_5e-1,
        2.961_609_1e-1,
        1.077_564_9e-1,
        2.517_191_4e-3,
        8.963_038_8e-10,
        0.0,
    ],
];

const HWANG_POINTS: [[f64; 10]; 4] = [
    [
        3.001_346_5e-3,
        7.859_288_6e-2,
        4.328_241_5e-1,
        1.334_526_7,
        3.048_184_6,
        5.826_319_8,
        9.945_265_6,
        1.578_212_8e1,
        2.399_682_4e1,
        3.621_620_8e1,
    ],
    [
        1.321_920_3e-2,
        7.234_962_4e-2,
        1.908_947_3e-1,
        3.952_884_2e-1,
        7.408_344_3e-1,
        1.349_829_3,
        2.529_798_3,
        5.238_489_4,
        1.382_177_2e1,
        7.564_752_5e1,
    ],
    [
        1.000_448_8e-3,
        2.619_762_9e-2,
        1.442_747_2e-1,
        4.448_422_3e-1,
        1.016_061_5,
        1.942_106_6,
        3.315_088_5,
        5.260_709_2,
        7.998_941_4,
        1.207_206_9e1,
    ],
    [
        1.321_920_3e-2,
        7.234_962_4e-2,
        1.908_947_3e-1,
        3.952_884_2e-1,
        7.408_344_3e-1,
        1.349_829_3,
        2.529_798_3,
        5.238_489_4,
        1.382_177_2e1,
        7.564_752_5e1,
    ],
];

fn interpolate_parameter(
    law: i32,
    x1: f64,
    y1: f64,
    x2: f64,
    y2: f64,
    x: f64,
) -> Result<f64, String> {
    if x2 <= x1 {
        return Err("unresolved interpolation interval is not increasing".into());
    }
    let fraction = (x - x1) / (x2 - x1);
    Ok(match law {
        1 => y1,
        2 => y1 + fraction * (y2 - y1),
        3 => {
            if x1 <= 0.0 || x2 <= 0.0 || x <= 0.0 {
                return Err("unresolved log-energy interpolation needs positive energies".into());
            }
            y1 + (x / x1).ln() / (x2 / x1).ln() * (y2 - y1)
        }
        4 => {
            if y1 <= 0.0 || y2 <= 0.0 {
                return Err("unresolved log-width interpolation needs positive values".into());
            }
            y1 * (y2 / y1).powf(fraction)
        }
        5 => {
            if x1 <= 0.0 || x2 <= 0.0 || x <= 0.0 || y1 <= 0.0 || y2 <= 0.0 {
                return Err("unresolved log-log interpolation needs positive coordinates".into());
            }
            y1 * (y2 / y1).powf((x / x1).ln() / (x2 / x1).ln())
        }
        _ => return Err(format!("unsupported unresolved interpolation INT={law}")),
    })
}

pub(crate) fn unresolved_point_at(
    sequence: &UnresolvedSequence,
    energy: f64,
) -> Result<UnresolvedPoint, String> {
    if sequence.points.is_empty() {
        return Err("unresolved sequence has no parameter points".into());
    }
    if sequence.points[0].energy.is_none() {
        if sequence.points.len() != 1 {
            return Err("energy-independent unresolved sequence has multiple points".into());
        }
        return Ok(sequence.points[0].clone());
    }
    let first_energy = sequence.points[0]
        .energy
        .ok_or("mixed unresolved energy representation")?;
    let last_energy = sequence.points[sequence.points.len() - 1]
        .energy
        .ok_or("mixed unresolved energy representation")?;
    if energy <= first_energy {
        return Ok(sequence.points[0].clone());
    }
    if energy >= last_energy {
        return Ok(sequence.points[sequence.points.len() - 1].clone());
    }
    let upper = sequence
        .points
        .partition_point(|point| point.energy.is_some_and(|value| value <= energy));
    let lower = upper - 1;
    let (left, right) = (&sequence.points[lower], &sequence.points[upper]);
    let (x1, x2) = (
        left.energy
            .ok_or("mixed unresolved energy representation")?,
        right
            .energy
            .ok_or("mixed unresolved energy representation")?,
    );
    let interpolate = |left: f64, right: f64| {
        interpolate_parameter(sequence.interpolation, x1, left, x2, right, energy)
    };
    let point = UnresolvedPoint {
        energy: Some(energy),
        spacing: interpolate(left.spacing, right.spacing)?,
        competitive: interpolate(left.competitive, right.competitive)?,
        neutron: interpolate(left.neutron, right.neutron)?,
        capture: interpolate(left.capture, right.capture)?,
        fission: interpolate(left.fission, right.fission)?,
    };
    validate_unresolved_point(&point)?;
    Ok(point)
}

fn width_samples(width: f64, degrees: i32, label: &str) -> Result<Vec<(f64, f64)>, String> {
    if width == 0.0 || degrees == 0 {
        return Ok(vec![(width, 1.0)]);
    }
    let index = usize::try_from(degrees - 1)
        .ok()
        .filter(|index| *index < HWANG_WEIGHTS.len())
        .ok_or_else(|| {
            format!("unsupported {label} degrees of freedom {degrees}; expected 0..4")
        })?;
    let weight_sum: f64 = HWANG_WEIGHTS[index].iter().sum();
    let sampled_mean: f64 = HWANG_POINTS[index]
        .iter()
        .zip(HWANG_WEIGHTS[index])
        .map(|(point, weight)| point * weight)
        .sum::<f64>()
        / weight_sum;
    if !weight_sum.is_finite()
        || weight_sum <= 0.0
        || !sampled_mean.is_finite()
        || sampled_mean <= 0.0
    {
        return Err(format!("invalid {label} Hwang quadrature normalization"));
    }
    // NJOY publishes the Hwang tables at limited decimal precision. Preserve their discrete shape while restoring
    // the probability and declared-width first moments that the truncated constants otherwise miss by up to 1e-3.
    Ok(HWANG_POINTS[index]
        .iter()
        .copied()
        .zip(HWANG_WEIGHTS[index].iter().copied())
        .map(|(point, weight)| (point * width / sampled_mean, weight / weight_sum))
        .collect())
}

fn reconstruct_unresolved_node(
    range: &ResonanceRange,
    energy: f64,
) -> Result<CrossSections, String> {
    if energy <= 0.0 || energy < range.energy_min || energy > range.energy_max {
        return Ok(CrossSections::default());
    }
    let RangeData::Unresolved(unresolved) = &range.data else {
        return Err("range is not unresolved".into());
    };
    if !unresolved.add_to_background {
        return Ok(CrossSections::default());
    }
    let phase_radius = scattering_radius(range, unresolved.ap, energy)?;
    if phase_radius <= 0.0 {
        return Err("unresolved range has a nonpositive scattering radius".into());
    }
    let mut result = CrossSections::default();
    let mut potential_l = BTreeSet::new();
    for sequence in &unresolved.sequences {
        if sequence.awri <= 0.0 || sequence.l < 0 {
            return Err("unresolved sequence has invalid AWRI or L".into());
        }
        if !(0..=4).contains(&sequence.neutron_dof)
            || !(0..=4).contains(&sequence.fission_dof)
            || !(0..=4).contains(&sequence.competitive_dof)
        {
            return Err("unresolved degrees of freedom must be in 0..4".into());
        }
        let point = unresolved_point_at(sequence, energy)?;
        let wave = K_WAVE * sequence.awri / (sequence.awri + 1.0) * energy.sqrt();
        let channel_radius = match range.naps {
            0 => channel_radius(sequence.awri),
            1 => phase_radius,
            value => {
                return Err(format!(
                    "unsupported unresolved NAPS={value}; expected calculated or scattering radius"
                ));
            }
        };
        let rho = wave * channel_radius;
        if rho <= 0.0 {
            return Err("unresolved channel radius produced zero rho".into());
        }
        let penetrability_factor = penetration_shift(sequence.l, rho)?.0 / rho;
        let phase = phase_shift(sequence.l, wave * phase_radius)?;
        let neutron =
            point.neutron * penetrability_factor * energy.sqrt() * f64::from(sequence.neutron_dof);
        let statistical =
            (2.0 * sequence.spin.abs() + 1.0) / (2.0 * (2.0 * unresolved.spin.abs() + 1.0));
        if !neutron.is_finite() || !statistical.is_finite() || statistical <= 0.0 {
            return Err("unresolved sequence produced invalid neutron width or spin factor".into());
        }
        let scale =
            2.0 * std::f64::consts::PI.powi(2) / (wave * wave) * statistical / point.spacing;
        let neutron_samples = width_samples(neutron, sequence.neutron_dof, "neutron")?;
        let fission_samples = width_samples(point.fission, sequence.fission_dof, "fission")?;
        let competitive_samples =
            width_samples(point.competitive, sequence.competitive_dof, "competitive")?;
        let mut elastic_average = 0.0;
        let mut capture_average = 0.0;
        let mut fission_average = 0.0;
        let mut competitive_average = 0.0;
        for &(fission, fission_weight) in &fission_samples {
            for &(sampled_neutron, neutron_weight) in &neutron_samples {
                for &(competitive, competitive_weight) in &competitive_samples {
                    let weight = fission_weight * neutron_weight * competitive_weight;
                    let total = sampled_neutron + point.capture + fission + competitive;
                    if total <= 0.0 || !total.is_finite() {
                        return Err("unresolved sampled total width is nonpositive".into());
                    }
                    elastic_average += weight * sampled_neutron * sampled_neutron / total;
                    capture_average += weight * sampled_neutron * point.capture / total;
                    fission_average += weight * sampled_neutron * fission / total;
                    competitive_average += weight * sampled_neutron * competitive / total;
                }
            }
        }
        result.elastic += scale * elastic_average;
        result.capture += scale * capture_average;
        result.fission += scale * fission_average;
        result.competitive += scale * competitive_average;

        let sin_squared = phase.sin().powi(2);
        if potential_l.insert((sequence.awri.to_bits(), sequence.l)) {
            result.elastic += 4.0 * std::f64::consts::PI / (wave * wave)
                * f64::from(2 * sequence.l + 1)
                * sin_squared;
        }
        result.elastic -= 4.0 * std::f64::consts::PI.powi(2) / (wave * wave)
            * statistical
            * neutron
            * sin_squared
            / point.spacing;
    }
    result.checked("unresolved resonance reconstruction")
}

/// Infinite-dilution average cross sections for one unresolved range.
///
/// `LSSF=0` returns the resonance contribution to add to MF=3. `LSSF=1` returns exact zeros because MF=3 already
/// contains the evaluated average. Width-fluctuation averages use NJOY's published ten-point Hwang quadrature.
/// Energy-dependent cases are evaluated on their declared and UNRESR-refined energy mesh and the resulting cross
/// sections are linearly interpolated, matching UNRESR's ENDF output contract; interpolating widths through the
/// nonlinear fluctuation integral would agree at parameter nodes but produce different multigroup averages.
pub fn reconstruct_unresolved(
    range: &ResonanceRange,
    energy: f64,
) -> Result<CrossSections, String> {
    if energy <= 0.0 || energy < range.energy_min || energy > range.energy_max {
        return Ok(CrossSections::default());
    }
    let RangeData::Unresolved(unresolved) = &range.data else {
        return Err("range is not unresolved".into());
    };
    let nodes = &unresolved.interpolation_energies;
    if nodes.len() < 2 || energy <= nodes[0] || energy >= nodes[nodes.len() - 1] {
        return reconstruct_unresolved_node(range, energy);
    }
    match nodes.binary_search_by(|node| node.total_cmp(&energy)) {
        Ok(_) => reconstruct_unresolved_node(range, energy),
        Err(upper) => {
            let low = nodes[upper - 1];
            let high = nodes[upper];
            let left = reconstruct_unresolved_node(range, low)?;
            let right = reconstruct_unresolved_node(range, high)?;
            let fraction = (energy - low) / (high - low);
            CrossSections {
                elastic: left.elastic + fraction * (right.elastic - left.elastic),
                capture: left.capture + fraction * (right.capture - left.capture),
                fission: left.fission + fraction * (right.fission - left.fission),
                competitive: left.competitive + fraction * (right.competitive - left.competitive),
            }
            .checked("interpolated unresolved resonance reconstruction")
        }
    }
}

/// Reconstruct one legacy LRF=1/2/3 range at zero Kelvin.
pub fn reconstruct_legacy(range: &ResonanceRange, energy: f64) -> Result<CrossSections, String> {
    if energy <= 0.0 || energy < range.energy_min || energy > range.energy_max {
        return Ok(CrossSections::default());
    }
    let (resolved, reich_moore) = match &range.data {
        RangeData::BreitWigner(value) => (value, false),
        RangeData::ReichMoore(value) => (value, true),
        _ => return Err("range is not a legacy resolved formalism".into()),
    };
    let mut result = CrossSections::default();
    for group in &resolved.groups {
        if group.awri <= 0.0 || group.l < 0 {
            return Err("invalid legacy resolved AWRI or L".into());
        }
        let wave = K_WAVE * group.awri / (group.awri + 1.0) * energy.sqrt();
        let calculated = channel_radius(group.awri);
        let apl = if group.apl != 0.0 {
            group.apl
        } else {
            resolved.ap
        };
        let tabulated_radius = if reich_moore { apl } else { resolved.ap };
        let penetration_radius = match range.naps {
            0 => calculated,
            1 => tabulated_radius,
            // NAPS=2 is defined only with an energy-dependent radius (NRO=1): the
            // constant CONT radius drives penetrability and shift while AP(E) drives
            // the phase shift — the choice the unresolved shielding path makes.
            2 if range.scattering_radius.is_some() => tabulated_radius,
            naps => {
                return Err(format!(
                    "unsupported resolved NAPS={naps}{}",
                    if naps == 2 {
                        " without an energy-dependent scattering radius (NRO=1)"
                    } else {
                        ""
                    }
                ))
            }
        };
        let phase_radius = scattering_radius(range, apl, energy)?;
        let (penetrability, shift) = penetration_shift(group.l, wave * penetration_radius)?;
        let phase = phase_shift(group.l, wave * phase_radius)?;
        let pi_over_k2 = std::f64::consts::PI / (wave * wave);
        let mut by_spin: BTreeMap<u64, Vec<&LegacyResonance>> = BTreeMap::new();
        for resonance in &group.resonances {
            by_spin
                .entry(resonance.spin.abs().to_bits())
                .or_default()
                .push(resonance);
        }
        for resonances in by_spin.values() {
            let spin = resonances[0].spin.abs();
            let statistical = (2.0 * spin + 1.0) / (2.0 * (2.0 * resolved.spin + 1.0));
            if reich_moore {
                let mut has_fission = false;
                for resonance in resonances {
                    has_fission |= resonance.fission_a != 0.0 || resonance.fission_b != 0.0;
                }
                if has_fission {
                    let mut k_matrix = vec![vec![Complex64::new(0.0, 0.0); 3]; 3];
                    for resonance in resonances {
                        let resonance_wave = K_WAVE * group.awri / (group.awri + 1.0)
                            * resonance.energy.abs().sqrt();
                        let (resonance_penetrability, _) =
                            penetration_shift(group.l, resonance_wave * penetration_radius)?;
                        if resonance_penetrability <= 0.0 {
                            return Err("zero resonance penetrability".into());
                        }
                        let neutron = resonance.neutron * penetrability / resonance_penetrability;
                        let amplitudes = [
                            neutron.sqrt(),
                            resonance.fission_a.signum() * resonance.fission_a.abs().sqrt(),
                            resonance.fission_b.signum() * resonance.fission_b.abs().sqrt(),
                        ];
                        let inverse =
                            Complex64::new(resonance.energy - energy, -0.5 * resonance.capture)
                                .inv();
                        for row in 0..3 {
                            for column in 0..3 {
                                k_matrix[row][column] +=
                                    0.5 * amplitudes[row] * amplitudes[column] * inverse;
                            }
                        }
                    }
                    let mut system = vec![vec![Complex64::new(0.0, 0.0); 3]; 3];
                    let identity = (0..3)
                        .map(|row| {
                            (0..3)
                                .map(|column| {
                                    if row == column {
                                        Complex64::new(1.0, 0.0)
                                    } else {
                                        Complex64::new(0.0, 0.0)
                                    }
                                })
                                .collect::<Vec<_>>()
                        })
                        .collect::<Vec<_>>();
                    for row in 0..3 {
                        for column in 0..3 {
                            system[row][column] =
                                identity[row][column] - Complex64::i() * k_matrix[row][column];
                        }
                    }
                    let w = solve_complex_matrix(system, identity)?;
                    let phase_factor = Complex64::from_polar(1.0, -phase);
                    let collision_00 =
                        phase_factor * (2.0 * w[0][0] - Complex64::new(1.0, 0.0)) * phase_factor;
                    let collision_01 = phase_factor * (2.0 * w[0][1]);
                    let collision_02 = phase_factor * (2.0 * w[0][2]);
                    let total = 2.0 * pi_over_k2 * statistical * (1.0 - collision_00.re);
                    let elastic = pi_over_k2
                        * statistical
                        * (Complex64::new(1.0, 0.0) - collision_00).norm_sqr();
                    let fission = pi_over_k2
                        * statistical
                        * (collision_01.norm_sqr() + collision_02.norm_sqr());
                    let capture = total - elastic - fission;
                    if capture < -1e-9 * total.abs().max(1.0) {
                        return Err("Reich-Moore collision matrix produced negative capture".into());
                    }
                    result.elastic +=
                        elastic - 4.0 * pi_over_k2 * statistical * phase.sin().powi(2);
                    result.fission += fission;
                    result.capture += capture.max(0.0);
                } else {
                    let mut k_matrix = Complex64::new(0.0, 0.0);
                    for resonance in resonances {
                        let resonance_wave = K_WAVE * group.awri / (group.awri + 1.0)
                            * resonance.energy.abs().sqrt();
                        let (resonance_penetrability, _) =
                            penetration_shift(group.l, resonance_wave * penetration_radius)?;
                        if resonance_penetrability <= 0.0 {
                            return Err("zero resonance penetrability".into());
                        }
                        let neutron = resonance.neutron * penetrability / resonance_penetrability;
                        let denominator =
                            Complex64::new(resonance.energy - energy, -0.5 * resonance.capture);
                        k_matrix += 0.5 * neutron / denominator;
                    }
                    let collision_denominator =
                        Complex64::new(1.0, 0.0) - Complex64::i() * k_matrix;
                    let w = Complex64::new(1.0, 0.0) / collision_denominator;
                    let collision = Complex64::from_polar(1.0, -2.0 * phase)
                        * (2.0 * w - Complex64::new(1.0, 0.0));
                    let elastic = pi_over_k2
                        * statistical
                        * (Complex64::new(1.0, 0.0) - collision).norm_sqr();
                    // P10 Amendment E: this is algebraically total-elastic, but it evaluates the small absorption
                    // term directly instead of cancelling two potential-scattering-dominated cross sections.
                    let capture = 4.0 * pi_over_k2 * statistical * k_matrix.im
                        / collision_denominator.norm_sqr();
                    if !capture.is_finite() || capture < 0.0 {
                        return Err(
                            "Reich-Moore scalar collision matrix produced invalid capture".into(),
                        );
                    }
                    result.elastic +=
                        elastic - 4.0 * pi_over_k2 * statistical * phase.sin().powi(2);
                    result.capture += capture;
                }
            } else {
                let mut amplitude = Complex64::new(0.0, 0.0);
                for resonance in resonances {
                    let resonance_wave =
                        K_WAVE * group.awri / (group.awri + 1.0) * resonance.energy.abs().sqrt();
                    let (resonance_penetrability, resonance_shift) =
                        penetration_shift(group.l, resonance_wave * penetration_radius)?;
                    if resonance_penetrability <= 0.0 {
                        return Err("zero resonance penetrability".into());
                    }
                    let neutron = resonance.neutron * penetrability / resonance_penetrability;
                    // GT exceeding the listed components is the competitive
                    // width, whether declared (LRX) or not (the TENDL-2017-era
                    // LRX=0 convention validated above). Field-level rounding
                    // is not a competitive width.
                    let competitive = (resonance.total
                        - resonance.neutron
                        - resonance.capture
                        - resonance.fission_a
                        - width_rounding_tolerance(
                            resonance.total,
                            resonance.neutron + resonance.capture + resonance.fission_a,
                        ))
                    .max(0.0);
                    let total = neutron + resonance.capture + resonance.fission_a + competitive;
                    let shifted_energy = resonance.energy
                        + resonance.neutron * (resonance_shift - shift)
                            / (2.0 * resonance_penetrability);
                    let denominator = (energy - shifted_energy).powi(2) + 0.25 * total * total;
                    result.capture +=
                        pi_over_k2 * statistical * neutron * resonance.capture / denominator;
                    result.fission +=
                        pi_over_k2 * statistical * neutron * resonance.fission_a / denominator;
                    result.competitive +=
                        pi_over_k2 * statistical * neutron * competitive / denominator;
                    if range.lrf == 1 {
                        result.elastic += pi_over_k2
                            * statistical
                            * (neutron * neutron - 2.0 * neutron * total * phase.sin().powi(2)
                                + 2.0 * (energy - shifted_energy) * neutron * (2.0 * phase).sin())
                            / denominator;
                    } else {
                        amplitude +=
                            neutron / Complex64::new(shifted_energy - energy, -0.5 * total);
                    }
                }
                if range.lrf == 2 {
                    let collision = Complex64::from_polar(1.0, -2.0 * phase)
                        * (Complex64::new(1.0, 0.0) + Complex64::i() * amplitude);
                    result.elastic += pi_over_k2
                        * statistical
                        * ((Complex64::new(1.0, 0.0) - collision).norm_sqr()
                            - 4.0 * phase.sin().powi(2));
                }
            }
        }
        result.elastic += pi_over_k2 * f64::from(2 * group.l + 1) * 4.0 * phase.sin().powi(2);
    }
    result.checked("legacy resonance reconstruction")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn breit_wigner_range(reported_total: f64) -> ResonanceRange {
        ResonanceRange {
            energy_min: 1.0,
            energy_max: 100.0,
            lru: 1,
            lrf: 2,
            naps: 1,
            scattering_radius: None,
            data: RangeData::BreitWigner(LegacyResolved {
                spin: 0.5,
                ap: 0.5,
                groups: vec![LegacyLGroup {
                    awri: 100.0,
                    apl: 0.0,
                    qx: 0.0,
                    l: 0,
                    lrx: 0,
                    resonances: vec![LegacyResonance {
                        energy: 10.0,
                        spin: 0.5,
                        total: reported_total,
                        neutron: 0.1,
                        capture: 0.5,
                        fission_a: 0.01,
                        fission_b: 0.0,
                    }],
                }],
            }),
        }
    }

    #[test]
    fn resolved_naps_outside_the_defined_set_fails_closed() {
        let mut range = breit_wigner_range(0.61);
        // p-wave: for l = 0 the channel radius cancels in the penetrability ratio.
        if let RangeData::BreitWigner(resolved) = &mut range.data {
            resolved.groups[0].l = 1;
        }
        let naps1 = reconstruct_legacy(&range, 10.0).unwrap();
        // Any other value used to fall through to the NAPS=0 computed radius.
        range.naps = 3;
        assert!(reconstruct_legacy(&range, 10.0)
            .unwrap_err()
            .contains("NAPS=3"));
        // NAPS=2 is defined only with an energy-dependent radius.
        range.naps = 2;
        assert!(reconstruct_legacy(&range, 10.0)
            .unwrap_err()
            .contains("NRO=1"));
        // With AP(E) equal to the CONT radius, NAPS=2 reproduces NAPS=1 exactly.
        range.scattering_radius = Some(Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![1.0, 100.0],
            y: vec![0.5, 0.5],
        });
        assert_eq!(reconstruct_legacy(&range, 10.0).unwrap(), naps1);
        // NAPS=0 takes the computed channel radius instead of AP.
        range.naps = 0;
        range.scattering_radius = None;
        assert_ne!(reconstruct_legacy(&range, 10.0).unwrap(), naps1);
    }

    #[test]
    fn omitted_fission_total_is_narrowly_accepted_and_reconstructed_from_components() {
        let tendl_record = LegacyResonance {
            energy: -38.85695,
            spin: 2.5,
            total: 0.01489947,
            neutron: 0.00001556771,
            capture: 0.01488390,
            fission_a: 0.00001,
            fission_b: 0.0,
        };
        assert!(omitted_fission_total_fields(0, &tendl_record));
        validate_breit_wigner_widths(0, &tendl_record).unwrap();

        let omitted = breit_wigner_range(0.6);
        let RangeData::BreitWigner(resolved) = &omitted.data else {
            unreachable!();
        };
        let group = &resolved.groups[0];
        let resonance = &group.resonances[0];
        assert!(omitted_fission_total_width(group, resonance));
        assert_eq!(legacy_effective_total_width(group, resonance), 0.61);
        validate_breit_wigner_widths(group.lrx, resonance).unwrap();

        let corrected = breit_wigner_range(0.61);
        assert_eq!(
            reconstruct_legacy(&omitted, 10.0).unwrap(),
            reconstruct_legacy(&corrected, 10.0).unwrap()
        );
    }

    #[test]
    fn unrelated_breit_wigner_total_mismatches_still_fail_closed() {
        let range = breit_wigner_range(0.59);
        let RangeData::BreitWigner(resolved) = &range.data else {
            unreachable!();
        };
        let resonance = &resolved.groups[0].resonances[0];
        assert!(!omitted_fission_total_width(&resolved.groups[0], resonance));
        assert!(validate_breit_wigner_widths(0, resonance).is_err());
    }

    #[test]
    fn undeclared_competitive_width_is_accepted_and_reconstructed() {
        // TENDL-2017-era convention: LRX=0 with GT exceeding GN+GG+GF. The
        // excess is the competitive width — it must appear in the denominator
        // and the competitive channel, exactly as declared LRX behaves.
        let range = breit_wigner_range(0.7);
        let RangeData::BreitWigner(resolved) = &range.data else {
            unreachable!();
        };
        let group = &resolved.groups[0];
        let resonance = &group.resonances[0];
        assert!(undeclared_competitive_width_fields(0, resonance));
        validate_breit_wigner_widths(group.lrx, resonance).unwrap();
        assert_eq!(legacy_effective_total_width(group, resonance), 0.7);
        // Declared-LRX and undeclared records with identical widths must
        // reconstruct identically.
        let mut declared = breit_wigner_range(0.7);
        if let RangeData::BreitWigner(resolved) = &mut declared.data {
            resolved.groups[0].lrx = 1;
        }
        assert_eq!(
            reconstruct_legacy(&range, 10.0).unwrap(),
            reconstruct_legacy(&declared, 10.0).unwrap()
        );
    }

    #[test]
    fn reich_moore_far_tail_capture_avoids_total_elastic_cancellation() {
        let energy = 1e-5;
        let group = LegacyLGroup {
            awri: 100.0,
            apl: 0.5,
            qx: 0.0,
            l: 0,
            lrx: 0,
            resonances: vec![LegacyResonance {
                energy: -1e6,
                spin: 0.5,
                total: 0.0,
                neutron: 1e6,
                capture: 1e-10,
                fission_a: 0.0,
                fission_b: 0.0,
            }],
        };
        let range = ResonanceRange {
            energy_min: energy,
            energy_max: 1.0,
            lru: 1,
            lrf: 3,
            naps: 1,
            scattering_radius: None,
            data: RangeData::ReichMoore(LegacyResolved {
                spin: 0.5,
                ap: 0.5,
                groups: vec![group.clone()],
            }),
        };
        let actual = reconstruct_legacy(&range, energy).unwrap().capture;

        let wave = K_WAVE * group.awri / (group.awri + 1.0) * energy.sqrt();
        let resonance_wave = K_WAVE * group.awri / (group.awri + 1.0) * 1e6_f64.sqrt();
        let neutron = group.resonances[0].neutron * wave / resonance_wave;
        let denominator = Complex64::new(-1e6 - energy, -0.5e-10);
        let k_matrix = 0.5 * neutron / denominator;
        let collision_denominator = Complex64::new(1.0, 0.0) - Complex64::i() * k_matrix;
        let pi_over_k2 = std::f64::consts::PI / wave.powi(2);
        let expected = 4.0 * pi_over_k2 * 0.5 * k_matrix.im / collision_denominator.norm_sqr();
        assert!(actual > 0.0);
        assert!((actual - expected).abs() <= 2e-14 * expected);

        let w = Complex64::new(1.0, 0.0) / collision_denominator;
        let collision =
            Complex64::from_polar(1.0, -2.0 * wave * 0.5) * (2.0 * w - Complex64::new(1.0, 0.0));
        let total = 2.0 * pi_over_k2 * 0.5 * (1.0 - collision.re);
        let elastic = pi_over_k2 * 0.5 * (Complex64::new(1.0, 0.0) - collision).norm_sqr();
        let cancelled = total - elastic;
        assert!((cancelled - expected).abs() > 1e-3 * expected);
    }

    fn energy_dependent_unresolved_range() -> ResonanceRange {
        ResonanceRange {
            energy_min: 10.0,
            energy_max: 20.0,
            lru: 2,
            lrf: 2,
            naps: 1,
            scattering_radius: None,
            data: RangeData::Unresolved(Unresolved {
                spin: 0.5,
                ap: 0.5,
                add_to_background: true,
                case: UnresolvedCase::C,
                sequences: vec![UnresolvedSequence {
                    awri: 100.0,
                    l: 0,
                    spin: 0.5,
                    interpolation: 2,
                    competitive_dof: 1,
                    neutron_dof: 1,
                    fission_dof: 1,
                    points: vec![
                        UnresolvedPoint {
                            energy: Some(10.0),
                            spacing: 1.0,
                            competitive: 0.2,
                            neutron: 0.02,
                            capture: 0.1,
                            fission: 0.3,
                        },
                        UnresolvedPoint {
                            energy: Some(20.0),
                            spacing: 5.0,
                            competitive: 2.0,
                            neutron: 0.5,
                            capture: 3.0,
                            fission: 1.0,
                        },
                    ],
                }],
                interpolation_energies: vec![10.0, 20.0],
            }),
        }
    }

    fn scalar_rml_range() -> ResonanceRange {
        ResonanceRange {
            energy_min: 1e-5,
            energy_max: 1e4,
            lru: 1,
            lrf: 7,
            naps: 1,
            scattering_radius: None,
            data: RangeData::RMatrixLimited(RMatrixLimited {
                reduced_widths: false,
                krm: 3,
                particle_pairs: vec![
                    ParticlePair {
                        mass_a: 0.0,
                        mass_b: 56.0,
                        za: 0,
                        zb: 26,
                        spin_a: 1.0,
                        spin_b: 0.0,
                        q_value: 0.0,
                        penetrability: 0,
                        shift: 0,
                        mt: 102,
                        parity_a: 0,
                        parity_b: 1,
                    },
                    ParticlePair {
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
                    },
                ],
                spin_groups: vec![SpinGroup {
                    spin: 0.5,
                    parity: 1.0,
                    channels: vec![
                        RmlChannel {
                            pair: 0,
                            l: 0,
                            spin: 0.0,
                            boundary: 0.0,
                            effective_radius: 0.0,
                            true_radius: 0.0,
                        },
                        RmlChannel {
                            pair: 1,
                            l: 0,
                            spin: 0.5,
                            boundary: 0.0,
                            effective_radius: 0.5,
                            true_radius: 0.5,
                        },
                    ],
                    resonances: vec![RmlResonance {
                        energy: 1e3,
                        widths: vec![0.2, 0.4],
                    }],
                    backgrounds: Vec::new(),
                    phase_shifts: Vec::new(),
                }],
            }),
        }
    }

    #[test]
    fn scalar_rml_capture_matches_breit_wigner_peak() {
        let range = scalar_rml_range();
        let actual = reconstruct_rmatrix_limited(&range, 1e3).unwrap();
        let awri = 55.0;
        let wave = K_WAVE * awri / (awri + 1.0) * 1e3f64.sqrt();
        let expected = std::f64::consts::PI / (wave * wave) * (4.0 * 0.4 * 0.2 / 0.6f64.powi(2));
        assert!((actual.capture - expected).abs() <= 2e-13 * expected);
        assert_eq!(actual.fission, 0.0);
        assert_eq!(actual.competitive, 0.0);
        assert!(actual.elastic.is_finite() && actual.elastic >= 0.0);
    }

    #[test]
    fn unresolved_interpolates_averaged_cross_sections_between_parameter_nodes() {
        let range = energy_dependent_unresolved_range();
        let left = reconstruct_unresolved_node(&range, 10.0).unwrap();
        let right = reconstruct_unresolved_node(&range, 20.0).unwrap();
        let midpoint = reconstruct_unresolved(&range, 15.0).unwrap();
        for (actual, expected) in [
            (midpoint.elastic, (left.elastic + right.elastic) / 2.0),
            (midpoint.capture, (left.capture + right.capture) / 2.0),
            (midpoint.fission, (left.fission + right.fission) / 2.0),
            (
                midpoint.competitive,
                (left.competitive + right.competitive) / 2.0,
            ),
        ] {
            assert!((actual - expected).abs() <= 2e-14 * expected.abs().max(1.0));
        }

        let width_interpolated = reconstruct_unresolved_node(&range, 15.0).unwrap();
        assert!(
            (midpoint.capture - width_interpolated.capture).abs() > 1e-4 * midpoint.capture.abs()
        );
    }

    #[test]
    fn unresolved_cross_section_mesh_matches_unresr_refinement_rule() {
        let mesh = unresolved_cross_section_mesh(
            6_500.0,
            31_810.0,
            UnresolvedCase::C,
            vec![6_500.0, 7_400.0, 10_670.0, 13_940.0, 17_210.0, 31_810.0],
        );
        assert_eq!(
            mesh,
            vec![
                6_500.0, 7_400.0, 8_500.0, 10_000.0, 10_670.0, 12_500.0, 13_940.0, 17_210.0,
                20_000.0, 25_000.0, 30_000.0, 31_810.0,
            ]
        );
    }

    #[test]
    fn hwang_samples_preserve_probability_and_declared_mean() {
        for degrees in 1..=4 {
            let samples = width_samples(0.125, degrees, "test").unwrap();
            let probability: f64 = samples.iter().map(|(_, weight)| weight).sum();
            let mean: f64 = samples.iter().map(|(width, weight)| width * weight).sum();
            assert!((probability - 1.0).abs() <= 4e-16);
            assert!((mean - 0.125).abs() <= 4e-16);
        }
    }

    #[test]
    fn rml_extensions_and_reduced_widths_fail_closed() {
        let mut range = scalar_rml_range();
        if let RangeData::RMatrixLimited(rml) = &mut range.data {
            rml.reduced_widths = true;
        }
        assert!(reconstruct_rmatrix_limited(&range, 1e3)
            .unwrap_err()
            .contains("IFG=1"));
        if let RangeData::RMatrixLimited(rml) = &mut range.data {
            rml.reduced_widths = false;
            rml.spin_groups[0].backgrounds.push(RmlExtension {
                channel: 2,
                law: 3,
                real: None,
                imaginary: None,
                parameters: Vec::new(),
                energy_min: 0.0,
                energy_max: 0.0,
            });
        }
        assert!(reconstruct_rmatrix_limited(&range, 1e3)
            .unwrap_err()
            .contains("law 3"));
    }

    #[test]
    fn rml_negative_spin_parity_encoding_is_admitted() {
        // ENDF-6 LRF=7 pair LIST carrying a negative spin (parity encoding),
        // as FENDL/TENDL Fe-57 and W-183 do for the elastic channel.
        let lines = [
            " 0.000000+0 0.000000+0          0          3          0          0",
            " 0.000000+0 0.000000+0          2          0         24          4",
            " 0.000000+0 5.743560+1 0.000000+0 2.600000+1 1.000000+0 0.000000+0",
            " 0.000000+0 0.000000+0 0.000000+0 1.020000+2 0.000000+0 1.000000+0",
            " 1.000000+0 5.644629+1 0.000000+0 2.600000+1 5.000000-1-5.000000-1",
            " 0.000000+0 1.000000+0 0.000000+0 2.000000+0 0.000000+0 0.000000+0",
        ];
        let (data, _next) = parse_rmatrix_limited(&lines, 0).unwrap();
        let RangeData::RMatrixLimited(rml) = data else {
            panic!("expected RML data");
        };
        assert_eq!(rml.particle_pairs.len(), 2);
        assert_eq!(rml.particle_pairs[1].spin_b, -0.5);
        assert_eq!(rml.particle_pairs[1].mt, 2);
    }

    #[test]
    fn coulomb_neutral_limit() {
        // eta -> 0 must recover the neutral penetrabilities/shifts.
        for l in 0..=4 {
            for rho in [0.5, 2.0, 6.0, 15.0] {
                let (f, _fp, g, _gp) = coulomb_fg(l, 1e-6, rho).unwrap();
                let (p_neutral, s_neutral) = penetration_shift(l, rho).unwrap();
                let d = f * f + g * g;
                let (p, s) = (rho / d, rho * (f * _fp + g * _gp) / d);
                assert!(
                    ((p - p_neutral) / p_neutral).abs() < 2e-3,
                    "l={l} rho={rho}: coulomb P={p} neutral P={p_neutral}"
                );
                assert!(
                    (s - s_neutral).abs() < 5e-2,
                    "l={l} rho={rho}: coulomb S={s} neutral S={s_neutral}"
                );
            }
        }
    }

    #[test]
    fn coulomb_reference_values() {
        // Reference (P, S) computed with mpmath coulombf/coulombg at 50 digits.
        let cases: &[(i32, f64, f64, f64, f64)] = &[
            (0, 0.5, 3.0, 2.490436, -0.0974152),
            (0, 1.0, 4.0, 2.909817, -0.1909323),
            (0, 1.0, 5.0, 3.917612, -0.1439151),
            (0, 2.0, 8.0, 5.713564, -0.2255358),
            (0, 5.0, 20.0, 14.16937, -0.2446878),
            (1, 2.0, 10.0, 7.649057, -0.1792963),
            (2, 0.5, 3.0, 1.516693, -0.6517423),
            (3, 4.0, 30.0, 25.46100, -0.1014268),
        ];
        for &(l, eta, rho, p_ref, s_ref) in cases {
            let (p, s) = coulomb_penetration_shift(l, eta, rho).unwrap();
            assert!(
                ((p - p_ref) / p_ref).abs() < 5e-3,
                "l={l} eta={eta} rho={rho}: P={p} ref={p_ref}"
            );
            assert!(
                (s - s_ref).abs() < 5e-2,
                "l={l} eta={eta} rho={rho}: S={s} ref={s_ref}"
            );
        }
    }

    #[test]
    fn coulomb_wronskian_is_one() {
        for (l, eta, rho) in [(0, 1.0, 4.0), (1, 2.0, 10.0), (2, 0.5, 3.0), (3, 4.0, 30.0)] {
            let (f, fp, g, gp) = coulomb_fg(l, eta, rho).unwrap();
            let w = f * gp - fp * g;
            assert!((w + 1.0).abs() < 2e-3, "l={l} eta={eta} rho={rho}: W={w}");
        }
    }

    #[test]
    fn coulomb_barrier_suppresses() {
        // deep sub-barrier: eta >> rho/2 suppresses P toward zero
        let (p, _s) = coulomb_penetration_shift(0, 30.0, 10.0).unwrap_or((0.0, 0.0));
        assert!(p < 1e-20, "expected suppressed P, got {p}");
    }

    #[test]
    fn coulomb_region_dispatch() {
        // Deep sub-barrier with appreciable η: the raw solver must fail
        // fast (no expensive propagation), while the penetrability API
        // reports a closed channel.
        assert!(coulomb_fg(0, 30.0, 10.0).is_err());
        assert_eq!(
            coulomb_penetration_shift(0, 30.0, 10.0).unwrap(),
            (0.0, 0.0)
        );
        // At and above the turning point the evaluation must succeed:
        // r_turn = η + sqrt(η² + l(l+1)) = 30 for (l=0, η=15).
        assert!(coulomb_fg(0, 15.0, 30.0).is_ok());
        assert!(coulomb_fg(0, 15.0, 45.0).is_ok());
        // η ~ 0 below the centrifugal turning point still has to evaluate
        // (algebraic suppression, not exponential).
        assert!(coulomb_fg(2, 1e-6, 0.5).is_ok());
    }

    #[test]
    fn riccati_bessel_matches_closed_forms() {
        // The generic recurrence must reproduce the l <= 4 closed forms
        // exactly, then extend smoothly through l = 5..8 (TENDL O-16 RML
        // declares an L=5 channel).
        for l in 0..=4 {
            for rho in [0.01_f64, 0.1, 0.5, 1.0, 3.0, 8.0, 25.0] {
                let (p_closed, s_closed) = match l {
                    0 => (rho, 0.0),
                    1 => (
                        rho * rho * rho / (1.0 + rho * rho),
                        -1.0 / (1.0 + rho * rho),
                    ),
                    2 => {
                        let d = 9.0 + 3.0 * rho * rho + rho.powi(4);
                        (rho.powi(5) / d, -(18.0 + 3.0 * rho * rho) / d)
                    }
                    _ => continue,
                };
                let (f, g, fp, gp) = riccati_bessel(l, rho).unwrap();
                let d = f * f + g * g;
                let p = rho / d;
                let s = rho * (f * fp + g * gp) / d;
                assert!(
                    (p - p_closed).abs() < 1e-9 * p_closed.abs().max(1.0),
                    "P l={l} rho={rho}: {p} vs {p_closed}"
                );
                assert!(
                    (s - s_closed).abs() < 1e-6,
                    "S l={l} rho={rho}: {s} vs {s_closed}"
                );
            }
        }
        // Wronskian F G' - F' G = -1 and monotonic suppression in l.
        for (l, rho) in [(5, 0.5), (5, 3.0), (6, 1.0), (7, 10.0), (8, 0.2)] {
            let (f, g, fp, gp) = riccati_bessel(l, rho).unwrap();
            let w = f * gp - fp * g;
            assert!((w + 1.0).abs() < 1e-6, "Wronskian l={l} rho={rho}: {w}");
            let (p, _) = penetration_shift(l, rho).unwrap();
            let phi = phase_shift(l, rho).unwrap();
            assert!(p.is_finite() && p > 0.0, "P l={l} rho={rho}: {p}");
            assert!(phi.is_finite(), "phi l={l} rho={rho}: {phi}");
        }
        // l=5 closed-form check (Bessel-polynomial denominator):
        // D5 = 893025 + 99225ρ² + 6300ρ⁴ + 315ρ⁶ + 15ρ⁸ + ρ¹⁰.
        let rho: f64 = 2.0;
        let r2 = rho * rho;
        let d5 = 893_025.0
            + 99_225.0 * r2
            + 6_300.0 * r2.powi(2)
            + 315.0 * r2.powi(3)
            + 15.0 * r2.powi(4)
            + r2.powi(5);
        let (p5, _s5) = penetration_shift(5, rho).unwrap();
        assert!((p5 - rho * r2.powi(5) / d5).abs() < 1e-12 * p5, "P5: {p5}");
    }
}
