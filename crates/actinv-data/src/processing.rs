//! Pointwise neutron resonance processing shared by the library builder and scientific controls.

use crate::doppler;
use crate::groups::{GroupStructure, Tabulated};
use crate::resonance::{
    legacy_effective_total_width, reconstruct_legacy, reconstruct_rmatrix_limited,
    reconstruct_unresolved, LegacyResonance, RangeData, ResonanceEvaluation, ResonanceRange,
    K_WAVE,
};
use rayon::prelude::*;
use std::collections::BTreeSet;

const LINEARIZATION_TOLERANCE: f64 = 2e-4;
const MAX_LINEARIZATION_PASSES: usize = 16;
const MAX_GRID_POINTS: usize = 10_000_000;
const ULTRA_NARROW_RATIO: f64 = 1e-4;
const ULTRA_NARROW_ZERO_K_REFERENCE_K: f64 = 293.6;
const ULTRA_NARROW_ISOLATION_WIDTHS: f64 = 100.0;
const ULTRA_NARROW_CORE_OUTER_WIDTHS: f64 = 700_000.0;
const ULTRA_NARROW_CORE_TAPER_WIDTHS: f64 = 100_000.0;
const ULTRA_NARROW_RANGE_EDGE_MIN_WIDTHS: f64 = 1_000.0;
const ULTRA_NARROW_AREA_TOLERANCE: f64 = 1e-6;

#[derive(Clone, Debug, PartialEq)]
pub struct UltraNarrowLineCertificate {
    pub isotope_zai: i32,
    pub isotope_abundance: f64,
    pub range_min_ev: f64,
    pub range_max_ev: f64,
    pub energy_ev: f64,
    pub total_width_ev: f64,
    pub classification_temperature_k: f64,
    pub doppler_width_ev: f64,
    pub width_to_doppler_ratio: f64,
    pub direct_area_barn_ev: f64,
    pub closed_form_area_barn_ev: f64,
    pub weighted_area_barn_ev: f64,
    pub affected_group: usize,
    pub range_edge_decomposition: bool,
    pub core_low_ev: f64,
    pub core_high_ev: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct UltraNarrowCandidateDiagnostic {
    pub isotope_zai: i32,
    pub energy_ev: f64,
    pub total_width_ev: f64,
    pub width_to_doppler_ratio: f64,
    pub edge_distance_widths: f64,
    pub nearest_line_distance_widths: f64,
    pub area_relative_difference: Option<f64>,
    pub treated: bool,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq)]
enum WingRetention {
    SmoothSymmetric {
        inner_widths: f64,
        outer_widths: f64,
    },
    RangeEdge {
        low_ev: f64,
        high_ev: f64,
        edge_is_upper: bool,
    },
}

#[derive(Clone, Debug, PartialEq)]
struct AnalyticLine {
    energy_ev: f64,
    total_width_ev: f64,
    weighted_area_barn_ev: f64,
    isotope_abundance: f64,
    wing_range: ResonanceRange,
    wing_retention: WingRetention,
}

struct AnalyticLineSeparation {
    smooth_evaluation: ResonanceEvaluation,
    analytic_lines: Vec<AnalyticLine>,
    certificates: Vec<UltraNarrowLineCertificate>,
    diagnostics: Vec<UltraNarrowCandidateDiagnostic>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProcessingCertificate {
    pub mt: i32,
    pub zero_k_points: usize,
    pub output_points: usize,
    pub zero_k_refinement_passes: usize,
    pub output_refinement_passes: usize,
    pub ultra_narrow_lines: Vec<UltraNarrowLineCertificate>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProcessedReaction {
    pub table: Tabulated,
    pub certificate: ProcessingCertificate,
    zero_k_lines: Vec<AnalyticLine>,
}

impl ProcessedReaction {
    pub fn collapse(&self, groups: &GroupStructure) -> Result<Vec<f64>, String> {
        let mut result = groups.collapse(&self.table)?;
        for line in &self.zero_k_lines {
            let group = containing_group(groups, line.energy_ev).ok_or_else(|| {
                format!(
                    "0 K analytic line at {} eV is outside or exactly on a group boundary",
                    line.energy_ev
                )
            })?;
            let low = groups.boundaries_ev[group];
            let high = groups.boundaries_ev[group + 1];
            result[group] += line.weighted_area_barn_ev / (line.energy_ev * (high / low).ln());
        }
        Ok(result)
    }

    pub fn collapse_product(
        &self,
        groups: &GroupStructure,
        factors: &[&Tabulated],
    ) -> Result<Vec<f64>, String> {
        let mut tables = Vec::with_capacity(factors.len() + 1);
        tables.push(&self.table);
        tables.extend_from_slice(factors);
        let mut result = groups.collapse_product(&tables)?;
        for line in &self.zero_k_lines {
            let group = containing_group(groups, line.energy_ev).ok_or_else(|| {
                format!(
                    "0 K analytic line at {} eV is outside or exactly on a group boundary",
                    line.energy_ev
                )
            })?;
            let factor = factors.iter().try_fold(1.0, |product, table| {
                Ok::<_, String>(product * table.evaluate(line.energy_ev)?)
            })?;
            let low = groups.boundaries_ev[group];
            let high = groups.boundaries_ev[group + 1];
            result[group] +=
                line.weighted_area_barn_ev * factor / (line.energy_ev * (high / low).ln());
        }
        Ok(result)
    }
}

fn containing_group(groups: &GroupStructure, energy: f64) -> Option<usize> {
    if energy <= groups.boundaries_ev[0]
        || energy >= groups.boundaries_ev[groups.boundaries_ev.len() - 1]
        || groups
            .boundaries_ev
            .binary_search_by(|value| value.total_cmp(&energy))
            .is_ok()
    {
        return None;
    }
    Some(
        groups
            .boundaries_ev
            .partition_point(|boundary| *boundary < energy)
            - 1,
    )
}

/// Whether MF=2 can add a nonzero contribution for a reaction. This lets the builder retain exact MF=3 processing
/// for channels (notably sub-actinide fission) that are present in the evaluation but absent from its resonances.
pub fn has_resonance_contribution(evaluation: &ResonanceEvaluation, mt: i32) -> bool {
    evaluation.isotopes.iter().any(|isotope| {
        isotope.abundance > 0.0
            && isotope.ranges.iter().any(|range| match &range.data {
                RangeData::BreitWigner(data) | RangeData::ReichMoore(data) => data
                    .groups
                    .iter()
                    .flat_map(|group| &group.resonances)
                    .any(|resonance| match mt {
                        2 => resonance.neutron != 0.0,
                        18 => resonance.fission_a != 0.0 || resonance.fission_b != 0.0,
                        102 => resonance.capture != 0.0,
                        _ => false,
                    }),
                RangeData::RMatrixLimited(data) => data.spin_groups.iter().any(|group| {
                    group.resonances.iter().any(|resonance| {
                        group.channels.iter().enumerate().any(|(index, channel)| {
                            data.particle_pairs[channel.pair].mt == mt
                                && resonance.widths[index] != 0.0
                        })
                    })
                }),
                RangeData::Unresolved(data) => {
                    data.add_to_background
                        && data.sequences.iter().any(|sequence| {
                            sequence.points.iter().any(|point| match mt {
                                2 => point.neutron != 0.0,
                                18 => point.fission != 0.0,
                                102 => point.capture != 0.0,
                                _ => false,
                            })
                        })
                }
                RangeData::ScatteringOnly { .. } => false,
            })
    })
}

fn push_log_grid(grid: &mut Vec<f64>, low: f64, high: f64, density: f64) {
    if high <= low || low <= 0.0 {
        return;
    }
    let count = ((high / low).ln() * 900.0 * density).ceil().max(32.0) as usize;
    let logarithmic_width = (high / low).ln();
    for index in 0..=count {
        grid.push(low * (logarithmic_width * index as f64 / count as f64).exp());
    }
}

fn push_table_grid(grid: &mut Vec<f64>, table: &Tabulated) {
    let below = table.x[0].next_down();
    if below > 0.0 {
        grid.push(below);
    }
    for (index, &energy) in table.x.iter().enumerate() {
        if index > 0 && table.x[index - 1].to_bits() == energy.to_bits() {
            let left = energy.next_down();
            if left > 0.0 {
                grid.push(left);
            }
        }
        grid.push(energy);
    }
    grid.push(table.x[table.x.len() - 1].next_up());
}

fn approximate_resonances(range: &ResonanceRange) -> Vec<(f64, f64)> {
    match &range.data {
        RangeData::BreitWigner(data) | RangeData::ReichMoore(data) => data
            .groups
            .iter()
            .flat_map(|group| {
                group.resonances.iter().map(|resonance| {
                    let width = if range.lrf <= 2 {
                        legacy_effective_total_width(group, resonance).abs()
                    } else {
                        resonance.neutron.abs()
                            + resonance.capture.abs()
                            + resonance.fission_a.abs()
                            + resonance.fission_b.abs()
                    };
                    (resonance.energy, width)
                })
            })
            .collect(),
        RangeData::RMatrixLimited(data) => data
            .spin_groups
            .iter()
            .flat_map(|group| {
                group.resonances.iter().map(|resonance| {
                    (
                        resonance.energy,
                        resonance.widths.iter().map(|width| width.abs()).sum(),
                    )
                })
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn push_resolved_grid(
    grid: &mut Vec<f64>,
    range: &ResonanceRange,
    temperature_k: f64,
    awr: f64,
    density: f64,
) {
    push_log_grid(grid, range.energy_min, range.energy_max, density);
    let points = (161.0 * density).ceil().max(41.0) as usize | 1;
    let theta_limit = 200.0f64.atan();
    for (energy, declared_width) in approximate_resonances(range) {
        if energy <= 0.0 {
            continue;
        }
        let natural_width = declared_width.max(1e-12 * energy.max(1.0));
        let doppler_width = if temperature_k > 0.0 {
            (4.0 * doppler::KB_EV_PER_K * temperature_k * energy / awr).sqrt()
        } else {
            0.0
        };
        let mut widths = vec![natural_width];
        if doppler_width > 3.0 * natural_width {
            widths.push(doppler_width);
        }
        for width in widths {
            if energy + 200.0 * width <= range.energy_min
                || energy - 200.0 * width >= range.energy_max
            {
                continue;
            }
            for index in 0..points {
                let theta = -theta_limit + 2.0 * theta_limit * index as f64 / (points - 1) as f64;
                let point = energy + 0.5 * width * theta.tan();
                if point > range.energy_min && point < range.energy_max {
                    grid.push(point);
                }
            }
        }
    }
}

fn push_broadened_resolved_grid(
    grid: &mut Vec<f64>,
    range: &ResonanceRange,
    temperature_k: f64,
    awr: f64,
    density: f64,
) {
    push_log_grid(grid, range.energy_min, range.energy_max, 0.2 * density);
    let points = (81.0 * density).ceil().max(31.0) as usize | 1;
    let theta_limit = 20.0f64.atan();
    for (energy, natural_width) in approximate_resonances(range) {
        if energy <= 0.0 {
            continue;
        }
        let doppler_width = (4.0 * doppler::KB_EV_PER_K * temperature_k * energy / awr).sqrt();
        let width = natural_width
            .max(doppler_width)
            .max(1e-12 * energy.max(1.0));
        for index in 0..points {
            let theta = -theta_limit + 2.0 * theta_limit * index as f64 / (points - 1) as f64;
            let point = energy + 0.5 * width * theta.tan();
            if point > range.energy_min && point < range.energy_max {
                grid.push(point);
            }
        }
    }
}

fn push_thermal_transition(grid: &mut Vec<f64>, energy: f64, temperature_k: f64, awr: f64) {
    if energy <= 0.0 || temperature_k <= 0.0 {
        return;
    }
    let width = (4.0 * doppler::KB_EV_PER_K * temperature_k * energy / awr).sqrt();
    for index in -64..=64 {
        let point = energy + width * f64::from(index) / 8.0;
        if point > 0.0 {
            grid.push(point);
        }
    }
}

fn push_background_thermal_features(
    grid: &mut Vec<f64>,
    background: &Tabulated,
    temperature_k: f64,
    awr: f64,
) {
    for index in 1..background.x.len() - 1 {
        let left_width = background.x[index] - background.x[index - 1];
        let right_width = background.x[index + 1] - background.x[index];
        if left_width <= 0.0 || right_width <= 0.0 {
            continue;
        }
        let left_slope = (background.y[index] - background.y[index - 1]) / left_width;
        let right_slope = (background.y[index + 1] - background.y[index]) / right_width;
        let thermal_width =
            (4.0 * doppler::KB_EV_PER_K * temperature_k * background.x[index] / awr).sqrt();
        let center = background.y[index];
        let local_scale = center
            .abs()
            .max((center - left_slope * thermal_width.min(left_width)).abs())
            .max((center + right_slope * thermal_width.min(right_width)).abs())
            .max(1e-12);
        if (right_slope - left_slope).abs() * thermal_width > LINEARIZATION_TOLERANCE * local_scale
        {
            push_thermal_transition(grid, background.x[index], temperature_k, awr);
        }
    }
}

fn push_unresolved_grid(grid: &mut Vec<f64>, range: &ResonanceRange) {
    let RangeData::Unresolved(data) = &range.data else {
        return;
    };
    grid.push(range.energy_min);
    grid.push(range.energy_max);
    grid.extend(data.interpolation_energies.iter().copied());
}

fn sorted_grid(mut grid: Vec<f64>, low: f64, high: f64) -> Result<Vec<f64>, String> {
    grid.retain(|energy| energy.is_finite() && *energy >= low && *energy <= high && *energy > 0.0);
    grid.sort_by(f64::total_cmp);
    grid.dedup_by(|left, right| left.to_bits() == right.to_bits());
    if grid.len() < 2 {
        return Err("resonance processing grid has fewer than two points".into());
    }
    if grid.len() > MAX_GRID_POINTS {
        return Err(format!(
            "resonance processing grid has {} points, exceeding the {}-point safety cap",
            grid.len(),
            MAX_GRID_POINTS
        ));
    }
    Ok(grid)
}

fn range_contribution(range: &ResonanceRange, energy: f64, mt: i32) -> Result<f64, String> {
    let cross_sections = match &range.data {
        RangeData::BreitWigner(_) | RangeData::ReichMoore(_) => reconstruct_legacy(range, energy)?,
        RangeData::RMatrixLimited(_) => reconstruct_rmatrix_limited(range, energy)?,
        RangeData::Unresolved(_) => reconstruct_unresolved(range, energy)?,
        RangeData::ScatteringOnly { .. } => return Ok(0.0),
    };
    Ok(match mt {
        2 => cross_sections.elastic,
        18 => cross_sections.fission,
        102 => cross_sections.capture,
        _ => return Err(format!("MT={mt} is not a resonance-processed reaction")),
    })
}

fn reaction_width(resonance: &LegacyResonance, mt: i32) -> f64 {
    match mt {
        18 => resonance.fission_a,
        102 => resonance.capture,
        _ => 0.0,
    }
}

fn retained_wing_fraction(distance_in_widths: f64, inner_widths: f64, outer_widths: f64) -> f64 {
    if distance_in_widths <= inner_widths {
        0.0
    } else if distance_in_widths >= outer_widths {
        1.0
    } else {
        let fraction = (distance_in_widths - inner_widths) / (outer_widths - inner_widths);
        fraction * fraction * (3.0 - 2.0 * fraction)
    }
}

fn simpson_theta<F>(function: &mut F, low: f64, high: f64) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    let middle = low + 0.5 * (high - low);
    Ok((high - low) * (function(low)? + 4.0 * function(middle)? + function(high)?) / 6.0)
}

fn adaptive_theta<F>(
    function: &mut F,
    low: f64,
    high: f64,
    whole: f64,
    tolerance: f64,
    depth: usize,
) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    let middle = low + 0.5 * (high - low);
    let left = simpson_theta(function, low, middle)?;
    let right = simpson_theta(function, middle, high)?;
    let difference = left + right - whole;
    if depth == 0 || difference.abs() <= 15.0 * tolerance {
        return Ok(left + right + difference / 15.0);
    }
    Ok(
        adaptive_theta(function, low, middle, left, 0.5 * tolerance, depth - 1)?
            + adaptive_theta(function, middle, high, right, 0.5 * tolerance, depth - 1)?,
    )
}

fn isolated_line_areas(
    range: &ResonanceRange,
    group_index: usize,
    resonance_index: usize,
    mt: i32,
) -> Result<(f64, f64, ResonanceRange, WingRetention, f64, f64), String> {
    let RangeData::BreitWigner(resolved) = &range.data else {
        return Err("analytic line area requires LRF=1/2 parameters".into());
    };
    let group = &resolved.groups[group_index];
    let resonance = &group.resonances[resonance_index];
    let total_width = legacy_effective_total_width(group, resonance);
    let reaction = reaction_width(resonance, mt);
    let wave = K_WAVE * group.awri / (group.awri + 1.0) * resonance.energy.sqrt();
    if wave <= 0.0 || total_width <= 0.0 || resonance.neutron <= 0.0 || reaction <= 0.0 {
        return Err("analytic line has nonpositive energy or width".into());
    }
    let statistical = (2.0 * resonance.spin.abs() + 1.0) / (2.0 * (2.0 * resolved.spin + 1.0));
    let full_closed_form = 2.0 * std::f64::consts::PI.powi(2) / (wave * wave)
        * statistical
        * resonance.neutron
        * reaction
        / total_width;

    let mut isolated_range = range.clone();
    let RangeData::BreitWigner(isolated) = &mut isolated_range.data else {
        unreachable!("range variant was checked above");
    };
    isolated.groups = vec![group.clone()];
    isolated.groups[0].resonances = vec![resonance.clone()];

    let available_left = (resonance.energy - range.energy_min) / total_width;
    let available_right = (range.energy_max - resonance.energy) / total_width;
    let integrate = |wing_retention: &WingRetention,
                     integration_low: f64,
                     integration_high: f64,
                     theta_low: f64,
                     theta_high: f64,
                     closed_form: f64|
     -> Result<f64, String> {
        if theta_high <= theta_low {
            return Err("analytic line has an empty transformed integration interval".into());
        }
        let mut integrand = |theta: f64| {
            let tangent = theta.tan();
            let energy = (resonance.energy + 0.5 * total_width * tangent)
                .clamp(integration_low, integration_high);
            let jacobian = 0.5 * total_width * (1.0 + tangent * tangent);
            let removed_fraction = match wing_retention {
                WingRetention::SmoothSymmetric {
                    inner_widths,
                    outer_widths,
                } => {
                    let distance = (energy - resonance.energy).abs() / total_width;
                    1.0 - retained_wing_fraction(distance, *inner_widths, *outer_widths)
                }
                WingRetention::RangeEdge { edge_is_upper, .. } => {
                    let far_side = if *edge_is_upper {
                        energy < resonance.energy
                    } else {
                        energy > resonance.energy
                    };
                    if far_side {
                        let distance = (energy - resonance.energy).abs() / total_width;
                        1.0 - retained_wing_fraction(
                            distance,
                            ULTRA_NARROW_CORE_OUTER_WIDTHS - ULTRA_NARROW_CORE_TAPER_WIDTHS,
                            ULTRA_NARROW_CORE_OUTER_WIDTHS,
                        )
                    } else {
                        1.0
                    }
                }
            };
            Ok(range_contribution(&isolated_range, energy, mt)? * removed_fraction * jacobian)
        };
        let whole = simpson_theta(&mut integrand, theta_low, theta_high)?;
        let direct = adaptive_theta(
            &mut integrand,
            theta_low,
            theta_high,
            whole,
            1e-8 * closed_form.abs().max(1e-300),
            20,
        )?;
        if !direct.is_finite() || direct <= 0.0 || !closed_form.is_finite() || closed_form <= 0.0 {
            return Err(
                "analytic line integration produced a nonfinite or nonpositive area".into(),
            );
        }
        Ok(direct)
    };

    if available_left >= ULTRA_NARROW_CORE_OUTER_WIDTHS
        && available_right >= ULTRA_NARROW_CORE_OUTER_WIDTHS
    {
        // Start with the smallest independently bounded core. Expand only a line whose energy-dependent core misses
        // the frozen-width area gate, leaving all already-certified lines unchanged.
        let mut last = None;
        for outer_widths in [700_000.0, 1_000_000.0, 1_500_000.0, 2_000_000.0] {
            if available_left < outer_widths || available_right < outer_widths {
                continue;
            }
            let inner_widths = outer_widths - ULTRA_NARROW_CORE_TAPER_WIDTHS;
            let low = resonance.energy - outer_widths * total_width;
            let high = resonance.energy + outer_widths * total_width;
            let theta = (2.0 * outer_widths).atan();
            let retention = WingRetention::SmoothSymmetric {
                inner_widths,
                outer_widths,
            };
            let direct = integrate(&retention, low, high, -theta, theta, full_closed_form)?;
            let relative =
                (direct - full_closed_form).abs() / direct.abs().max(full_closed_form.abs());
            last = Some((direct, retention, low, high));
            if relative <= ULTRA_NARROW_AREA_TOLERANCE {
                break;
            }
        }
        let (direct, retention, low, high) =
            last.ok_or("analytic line has no symmetric core inside its resonance range")?;
        return Ok((
            direct,
            full_closed_form,
            isolated_range,
            retention,
            low,
            high,
        ));
    }

    // A nearby resolved-range edge already truncates the evaluated line. Remove its complete in-range core and
    // compare against the exact arctangent integral of the same frozen-width truncated Lorentzian.
    let left = available_left.min(ULTRA_NARROW_CORE_OUTER_WIDTHS) * (1.0 - 1e-12);
    let right = available_right.min(ULTRA_NARROW_CORE_OUTER_WIDTHS) * (1.0 - 1e-12);
    let low = resonance.energy - left * total_width;
    let high = resonance.energy + right * total_width;
    let theta_low = (-2.0 * left).atan();
    let theta_high = (2.0 * right).atan();
    let closed_form = full_closed_form * (theta_high - theta_low) / std::f64::consts::PI;
    let retention = WingRetention::RangeEdge {
        low_ev: low,
        high_ev: high,
        edge_is_upper: available_right <= available_left,
    };
    let direct = integrate(&retention, low, high, theta_low, theta_high, closed_form)?;
    Ok((direct, closed_form, isolated_range, retention, low, high))
}

fn separate_ultra_narrow_lines(
    evaluation: &ResonanceEvaluation,
    groups: &GroupStructure,
    mt: i32,
    temperature_k: f64,
) -> Result<AnalyticLineSeparation, String> {
    let classification_temperature = if temperature_k == 0.0 {
        // A 0 K delta has no Doppler width of its own. Use the canonical room-temperature build solely to classify
        // the same lines, then retain their exact areas for the flat-lethargy 0 K collapse.
        ULTRA_NARROW_ZERO_K_REFERENCE_K
    } else {
        temperature_k
    };
    let mut removed = BTreeSet::new();
    let mut lines = Vec::new();
    let mut certificates = Vec::new();
    let mut diagnostics = Vec::new();

    for (isotope_index, isotope) in evaluation.isotopes.iter().enumerate() {
        if isotope.abundance <= 0.0 {
            continue;
        }
        for (range_index, range) in isotope.ranges.iter().enumerate() {
            if !matches!(range.lrf, 1 | 2) {
                continue;
            }
            let RangeData::BreitWigner(resolved) = &range.data else {
                continue;
            };
            for (group_index, group) in resolved.groups.iter().enumerate() {
                for (resonance_index, resonance) in group.resonances.iter().enumerate() {
                    let total_width = legacy_effective_total_width(group, resonance);
                    let reaction = reaction_width(resonance, mt);
                    if resonance.energy <= range.energy_min
                        || resonance.energy >= range.energy_max
                        || total_width <= 0.0
                        || resonance.neutron <= 0.0
                        || reaction <= 0.0
                    {
                        continue;
                    }
                    let doppler_width = (4.0
                        * doppler::KB_EV_PER_K
                        * classification_temperature
                        * resonance.energy
                        / evaluation.awr)
                        .sqrt();
                    let ratio = total_width / doppler_width;
                    if !ratio.is_finite() || ratio > ULTRA_NARROW_RATIO {
                        continue;
                    }
                    let edge_distance_widths = ((resonance.energy - range.energy_min)
                        .min(range.energy_max - resonance.energy))
                        / total_width;
                    let nearest_line_distance_widths = resolved
                        .groups
                        .iter()
                        .enumerate()
                        .flat_map(|(other_group, data)| {
                            data.resonances.iter().enumerate().filter_map(
                                move |(other_index, other)| {
                                    (other_group != group_index || other_index != resonance_index)
                                        .then_some(
                                            (other.energy - resonance.energy).abs() / total_width,
                                        )
                                },
                            )
                        })
                        .min_by(f64::total_cmp)
                        .unwrap_or(f64::INFINITY);
                    if edge_distance_widths < ULTRA_NARROW_RANGE_EDGE_MIN_WIDTHS {
                        diagnostics.push(UltraNarrowCandidateDiagnostic {
                            isotope_zai: isotope.zai,
                            energy_ev: resonance.energy,
                            total_width_ev: total_width,
                            width_to_doppler_ratio: ratio,
                            edge_distance_widths,
                            nearest_line_distance_widths,
                            area_relative_difference: None,
                            treated: false,
                            reason: "too close to resolved-range edge for bounded decomposition"
                                .into(),
                        });
                        continue;
                    }
                    if nearest_line_distance_widths < ULTRA_NARROW_ISOLATION_WIDTHS {
                        diagnostics.push(UltraNarrowCandidateDiagnostic {
                            isotope_zai: isotope.zai,
                            energy_ev: resonance.energy,
                            total_width_ev: total_width,
                            width_to_doppler_ratio: ratio,
                            edge_distance_widths,
                            nearest_line_distance_widths,
                            area_relative_difference: None,
                            treated: false,
                            reason: "not isolated by 100 natural widths".into(),
                        });
                        continue;
                    }
                    let Some(affected_group) = containing_group(groups, resonance.energy) else {
                        diagnostics.push(UltraNarrowCandidateDiagnostic {
                            isotope_zai: isotope.zai,
                            energy_ev: resonance.energy,
                            total_width_ev: total_width,
                            width_to_doppler_ratio: ratio,
                            edge_distance_widths,
                            nearest_line_distance_widths,
                            area_relative_difference: None,
                            treated: false,
                            reason: "outside or exactly on a group boundary".into(),
                        });
                        continue;
                    };
                    let (
                        direct_area,
                        closed_form_area,
                        wing_range,
                        wing_retention,
                        core_low_ev,
                        core_high_ev,
                    ) = isolated_line_areas(range, group_index, resonance_index, mt)?;
                    let area_relative_difference = (direct_area - closed_form_area).abs()
                        / direct_area.abs().max(closed_form_area.abs());
                    let area_clears = area_relative_difference <= ULTRA_NARROW_AREA_TOLERANCE;
                    diagnostics.push(UltraNarrowCandidateDiagnostic {
                        isotope_zai: isotope.zai,
                        energy_ev: resonance.energy,
                        total_width_ev: total_width,
                        width_to_doppler_ratio: ratio,
                        edge_distance_widths,
                        nearest_line_distance_widths,
                        area_relative_difference: Some(area_relative_difference),
                        treated: area_clears,
                        reason: if area_clears {
                            "treated"
                        } else {
                            "direct/closed-form area difference exceeds 1e-6"
                        }
                        .into(),
                    });
                    if !area_clears {
                        // The core is not independently bounded by the frozen-width area. Keep the complete line on
                        // the ordinary adaptive path; only actually treated lines are certified below.
                        continue;
                    }
                    let weighted_area = isotope.abundance * direct_area;
                    let range_edge_decomposition =
                        matches!(&wing_retention, WingRetention::RangeEdge { .. });
                    removed.insert((isotope_index, range_index, group_index, resonance_index));
                    lines.push(AnalyticLine {
                        energy_ev: resonance.energy,
                        total_width_ev: total_width,
                        weighted_area_barn_ev: weighted_area,
                        isotope_abundance: isotope.abundance,
                        wing_range,
                        wing_retention,
                    });
                    certificates.push(UltraNarrowLineCertificate {
                        isotope_zai: isotope.zai,
                        isotope_abundance: isotope.abundance,
                        range_min_ev: range.energy_min,
                        range_max_ev: range.energy_max,
                        energy_ev: resonance.energy,
                        total_width_ev: total_width,
                        classification_temperature_k: classification_temperature,
                        doppler_width_ev: doppler_width,
                        width_to_doppler_ratio: ratio,
                        direct_area_barn_ev: direct_area,
                        closed_form_area_barn_ev: closed_form_area,
                        weighted_area_barn_ev: weighted_area,
                        affected_group,
                        range_edge_decomposition,
                        core_low_ev,
                        core_high_ev,
                    });
                }
            }
        }
    }

    let mut smooth = evaluation.clone();
    for (isotope_index, isotope) in smooth.isotopes.iter_mut().enumerate() {
        for (range_index, range) in isotope.ranges.iter_mut().enumerate() {
            let RangeData::BreitWigner(resolved) = &mut range.data else {
                continue;
            };
            for (group_index, group) in resolved.groups.iter_mut().enumerate() {
                let mut resonance_index = 0usize;
                group.resonances.retain(|_| {
                    let keep = !removed.contains(&(
                        isotope_index,
                        range_index,
                        group_index,
                        resonance_index,
                    ));
                    resonance_index += 1;
                    keep
                });
            }
        }
    }
    Ok(AnalyticLineSeparation {
        smooth_evaluation: smooth,
        analytic_lines: lines,
        certificates,
        diagnostics,
    })
}

fn push_delta_grid(grid: &mut Vec<f64>, lines: &[AnalyticLine], temperature_k: f64, awr: f64) {
    for line in lines {
        let width = (4.0 * doppler::KB_EV_PER_K * temperature_k * line.energy_ev / awr).sqrt();
        for index in -96..=96 {
            let energy = line.energy_ev + width * f64::from(index) / 8.0;
            if energy > 0.0 {
                grid.push(energy);
            }
        }
    }
}

fn push_analytic_core_grid(grid: &mut Vec<f64>, lines: &[AnalyticLine]) {
    for line in lines {
        grid.push(line.energy_ev);
        match &line.wing_retention {
            WingRetention::SmoothSymmetric {
                inner_widths,
                outer_widths,
            } => {
                for widths in [-*outer_widths, -*inner_widths, *inner_widths, *outer_widths] {
                    grid.push(line.energy_ev + widths * line.total_width_ev);
                }
            }
            WingRetention::RangeEdge {
                low_ev,
                high_ev,
                edge_is_upper,
            } => {
                let inner = ULTRA_NARROW_CORE_OUTER_WIDTHS - ULTRA_NARROW_CORE_TAPER_WIDTHS;
                if *edge_is_upper {
                    grid.push(
                        line.energy_ev - ULTRA_NARROW_CORE_OUTER_WIDTHS * line.total_width_ev,
                    );
                    grid.push(line.energy_ev - inner * line.total_width_ev);
                    grid.push(*high_ev);
                } else {
                    grid.push(*low_ev);
                    grid.push(line.energy_ev + inner * line.total_width_ev);
                    grid.push(
                        line.energy_ev + ULTRA_NARROW_CORE_OUTER_WIDTHS * line.total_width_ev,
                    );
                }
            }
        }
    }
}

fn broaden_with_lines(
    zero_k_energy: &[f64],
    zero_k_sigma: &[f64],
    lines: &[AnalyticLine],
    temperature_k: f64,
    awr: f64,
    output_energy: &[f64],
) -> Result<Vec<f64>, String> {
    let mut result = doppler::broaden(
        zero_k_energy,
        zero_k_sigma,
        temperature_k,
        awr,
        output_energy,
    )?;
    for line in lines {
        output_energy
            .par_iter()
            .zip(result.par_iter_mut())
            .try_for_each(|(&energy, sigma)| {
                *sigma += doppler::delta_line(
                    line.weighted_area_barn_ev,
                    line.energy_ev,
                    temperature_k,
                    awr,
                    energy,
                )?;
                Ok::<_, String>(())
            })?;
    }
    Ok(result)
}

fn resonance_at(evaluation: &ResonanceEvaluation, energy: f64, mt: i32) -> Result<f64, String> {
    let mut total = 0.0;
    for isotope in &evaluation.isotopes {
        // Reverse order makes a shared boundary right-continuous and prevents double counting adjacent ranges.
        if let Some(range) = isotope
            .ranges
            .iter()
            .rev()
            .find(|range| energy >= range.energy_min && energy <= range.energy_max)
        {
            total += isotope.abundance * range_contribution(range, energy, mt)?;
        }
    }
    if !total.is_finite() || total < -1e-10 {
        return Err(format!(
            "MT={mt} resonance reconstruction is nonfinite or negative at {energy} eV"
        ));
    }
    Ok(total.max(0.0))
}

fn analytic_wings_at(lines: &[AnalyticLine], energy: f64, mt: i32) -> Result<f64, String> {
    lines.iter().try_fold(0.0, |total, line| {
        let retained = match &line.wing_retention {
            WingRetention::SmoothSymmetric {
                inner_widths,
                outer_widths,
            } => {
                let distance = (energy - line.energy_ev).abs() / line.total_width_ev;
                retained_wing_fraction(distance, *inner_widths, *outer_widths)
            }
            WingRetention::RangeEdge {
                low_ev,
                high_ev,
                edge_is_upper,
            } => {
                if energy < *low_ev || energy > *high_ev {
                    1.0
                } else {
                    let far_side = if *edge_is_upper {
                        energy < line.energy_ev
                    } else {
                        energy > line.energy_ev
                    };
                    if far_side {
                        let distance = (energy - line.energy_ev).abs() / line.total_width_ev;
                        retained_wing_fraction(
                            distance,
                            ULTRA_NARROW_CORE_OUTER_WIDTHS - ULTRA_NARROW_CORE_TAPER_WIDTHS,
                            ULTRA_NARROW_CORE_OUTER_WIDTHS,
                        )
                    } else {
                        0.0
                    }
                }
            }
        };
        if retained <= 0.0 {
            Ok(total)
        } else {
            Ok(total
                + retained
                    * line.isotope_abundance
                    * range_contribution(&line.wing_range, energy, mt)?)
        }
    })
}

fn evaluate_zero_k(
    evaluation: &ResonanceEvaluation,
    background: &Tabulated,
    analytic_lines: &[AnalyticLine],
    energy: f64,
    mt: i32,
) -> Result<f64, String> {
    let value = background.evaluate(energy)?
        + resonance_at(evaluation, energy, mt)?
        + analytic_wings_at(analytic_lines, energy, mt)?;
    if !value.is_finite() || value < -1e-10 {
        Err(format!(
            "MT={mt} zero-K reconstruction is nonfinite or negative at {energy} eV"
        ))
    } else {
        Ok(value.max(0.0))
    }
}

fn discontinuity_boundaries(
    evaluation: &ResonanceEvaluation,
    background: &Tabulated,
    low: f64,
    high: f64,
) -> Vec<f64> {
    let mut boundaries = vec![background.x[0], background.x[background.x.len() - 1]];
    for pair in background.x.windows(2) {
        if pair[0].to_bits() == pair[1].to_bits() {
            boundaries.push(pair[0]);
        }
    }
    for isotope in &evaluation.isotopes {
        for range in &isotope.ranges {
            boundaries.push(range.energy_min);
            boundaries.push(range.energy_max);
        }
    }
    boundaries.retain(|energy| *energy > low && *energy < high);
    boundaries.sort_by(f64::total_cmp);
    boundaries.dedup_by(|left, right| left.to_bits() == right.to_bits());
    boundaries
}

fn insert_exact_discontinuities<F>(
    mut energy: Vec<f64>,
    mut sigma: Vec<f64>,
    boundaries: &[f64],
    mut evaluate: F,
) -> Result<(Vec<f64>, Vec<f64>), String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    if energy.len() != sigma.len() {
        return Err("discontinuity insertion needs matching energy/sigma arrays".into());
    }
    if energy.len() + boundaries.len() > MAX_GRID_POINTS {
        return Err(format!(
            "exact discontinuities would exceed the {MAX_GRID_POINTS}-point safety cap"
        ));
    }
    for &boundary in boundaries {
        let first = energy.partition_point(|value| *value < boundary);
        let last = energy.partition_point(|value| *value <= boundary);
        let left_value = evaluate(boundary.next_down())?;
        let right_value = evaluate(boundary.next_up())?;
        // A terminal TAB1/range edge is left-continuous while a starting or shared edge is right-continuous.
        // Replacing the point with both one-sided limits makes either case an exact zero-width step for SIGMA1;
        // the value at the measure-zero boundary itself cannot affect broadening or group collapse.
        energy.splice(first..last, [boundary, boundary]);
        sigma.splice(first..last, [left_value, right_value]);
    }
    Ok((energy, sigma))
}

fn linearize<F>(
    energy: Vec<f64>,
    sigma: Vec<f64>,
    mut evaluate: F,
) -> Result<(Vec<f64>, Vec<f64>, usize), String>
where
    F: FnMut(&[f64]) -> Result<Vec<f64>, String>,
{
    if energy.len() != sigma.len() || energy.len() < 2 {
        return Err("linearization needs matching energy/sigma arrays of length >= 2".into());
    }
    let mut points: Vec<(f64, f64)> = energy.iter().copied().zip(sigma.iter().copied()).collect();
    let mut frontier: Vec<(f64, f64, f64, f64)> = energy
        .windows(2)
        .zip(sigma.windows(2))
        .map(|(energies, values)| (energies[0], values[0], energies[1], values[1]))
        .collect();
    let mut last_diagnostic = String::new();
    for pass in 0..MAX_LINEARIZATION_PASSES {
        let candidates: Vec<((f64, f64, f64, f64), f64)> = frontier
            .iter()
            .filter_map(|&segment| {
                let midpoint = segment.0 + 0.5 * (segment.2 - segment.0);
                (midpoint > segment.0 && midpoint < segment.2).then_some((segment, midpoint))
            })
            .collect();
        let midpoints: Vec<f64> = candidates.iter().map(|candidate| candidate.1).collect();
        let midpoint_sigma = evaluate(&midpoints)?;
        let mut additions = Vec::new();
        let mut next_frontier = Vec::new();
        let mut worst = (0.0f64, 0.0f64);
        for (candidate, &(segment, midpoint)) in candidates.iter().enumerate() {
            let linear = 0.5 * (segment.1 + segment.3);
            let scale = midpoint_sigma[candidate].abs().max(linear.abs()).max(1e-6);
            let relative = (midpoint_sigma[candidate] - linear).abs() / scale;
            if relative > worst.0 {
                worst = (relative, midpoint);
            }
            if relative > LINEARIZATION_TOLERANCE {
                let middle_sigma = midpoint_sigma[candidate];
                additions.push((midpoint, middle_sigma));
                next_frontier.push((segment.0, segment.1, midpoint, middle_sigma));
                next_frontier.push((midpoint, middle_sigma, segment.2, segment.3));
            }
        }
        if additions.is_empty() {
            points.sort_by(|left, right| left.0.total_cmp(&right.0));
            let (energy, sigma) = points.into_iter().unzip();
            return Ok((energy, sigma, pass));
        }
        if points.len() + additions.len() > MAX_GRID_POINTS {
            return Err(format!(
                "resonance linearization would exceed the {MAX_GRID_POINTS}-point safety cap"
            ));
        }
        last_diagnostic = format!(
            "{} remaining segment(s), worst relative error {:.6e} at {:.17e} eV",
            additions.len(),
            worst.0,
            worst.1
        );
        points.extend(additions);
        frontier = next_frontier;
    }
    Err(format!(
        "resonance linearization did not converge in {MAX_LINEARIZATION_PASSES} passes: {last_diagnostic}"
    ))
}

/// Classify and certify analytic LRF=1/2 lines without running pointwise reconstruction or broadening.
pub fn ultra_narrow_certificates(
    evaluation: &ResonanceEvaluation,
    groups: &GroupStructure,
    mt: i32,
    temperature_k: f64,
) -> Result<Vec<UltraNarrowLineCertificate>, String> {
    if !matches!(mt, 18 | 102) {
        return Err(format!("MT={mt} has no analytic capture/fission line path"));
    }
    if !temperature_k.is_finite() || temperature_k < 0.0 {
        return Err("analytic-line temperature must be finite and nonnegative".into());
    }
    groups.validate()?;
    Ok(separate_ultra_narrow_lines(evaluation, groups, mt, temperature_k)?.certificates)
}

/// Report every width-ratio candidate and the deterministic reason it was treated or retained on the sampled path.
pub fn ultra_narrow_diagnostics(
    evaluation: &ResonanceEvaluation,
    groups: &GroupStructure,
    mt: i32,
    temperature_k: f64,
) -> Result<Vec<UltraNarrowCandidateDiagnostic>, String> {
    if !matches!(mt, 18 | 102) {
        return Err(format!("MT={mt} has no analytic capture/fission line path"));
    }
    if !temperature_k.is_finite() || temperature_k < 0.0 {
        return Err("analytic-line temperature must be finite and nonnegative".into());
    }
    groups.validate()?;
    Ok(separate_ultra_narrow_lines(evaluation, groups, mt, temperature_k)?.diagnostics)
}

/// Reconstruct, Doppler broaden and linearize MT=2/18/102 from raw MF=2 plus its MF=3 background.
pub fn process_reaction(
    evaluation: &ResonanceEvaluation,
    background: &Tabulated,
    groups: &GroupStructure,
    mt: i32,
    temperature_k: f64,
    grid_density: f64,
) -> Result<ProcessedReaction, String> {
    if !matches!(mt, 2 | 18 | 102) {
        return Err(format!("MT={mt} is not resonance-processable"));
    }
    if !temperature_k.is_finite() || temperature_k < 0.0 {
        return Err("resonance-processing temperature must be finite and nonnegative".into());
    }
    if !grid_density.is_finite() || grid_density <= 0.0 {
        return Err("resonance grid density must be finite and positive".into());
    }
    groups.validate()?;
    background.validate()?;
    let AnalyticLineSeparation {
        smooth_evaluation,
        analytic_lines,
        certificates: ultra_narrow_lines,
        ..
    } = separate_ultra_narrow_lines(evaluation, groups, mt, temperature_k)?;
    let low = groups.boundaries_ev[0];
    let high = groups.boundaries_ev[groups.boundaries_ev.len() - 1];
    let mut initial = groups.boundaries_ev.clone();
    push_table_grid(&mut initial, background);
    for isotope in &smooth_evaluation.isotopes {
        for range in &isotope.ranges {
            initial.push(range.energy_min);
            initial.push(range.energy_max);
            let left = range.energy_min.next_down();
            let right = range.energy_max.next_up();
            if left > 0.0 {
                initial.push(left);
            }
            initial.push(right);
            match range.data {
                RangeData::BreitWigner(_)
                | RangeData::ReichMoore(_)
                | RangeData::RMatrixLimited(_) => push_resolved_grid(
                    &mut initial,
                    range,
                    temperature_k,
                    smooth_evaluation.awr,
                    grid_density,
                ),
                RangeData::Unresolved(_) => push_unresolved_grid(&mut initial, range),
                RangeData::ScatteringOnly { .. } => {}
            }
        }
    }
    push_analytic_core_grid(&mut initial, &analytic_lines);
    let initial = sorted_grid(initial, low, high)?;
    let initial_sigma: Vec<f64> = initial
        .par_iter()
        .map(|&energy| evaluate_zero_k(&smooth_evaluation, background, &analytic_lines, energy, mt))
        .collect::<Result<_, _>>()?;
    let (zero_k_energy, zero_k_sigma, zero_k_passes) =
        linearize(initial, initial_sigma, |energies| {
            energies
                .par_iter()
                .map(|&energy| {
                    evaluate_zero_k(&smooth_evaluation, background, &analytic_lines, energy, mt)
                })
                .collect()
        })
        .map_err(|error| format!("zero-K {error}"))?;
    let discontinuities = discontinuity_boundaries(&smooth_evaluation, background, low, high);
    let (zero_k_energy, zero_k_sigma) =
        insert_exact_discontinuities(zero_k_energy, zero_k_sigma, &discontinuities, |energy| {
            evaluate_zero_k(&smooth_evaluation, background, &analytic_lines, energy, mt)
        })?;
    let zero_k_points = zero_k_energy.len();

    let (output_energy, output_sigma, output_passes) = if temperature_k == 0.0 {
        (zero_k_energy, zero_k_sigma, 0)
    } else {
        let mut output_seed = groups.boundaries_ev.clone();
        push_table_grid(&mut output_seed, background);
        for &boundary in &discontinuities {
            push_thermal_transition(
                &mut output_seed,
                boundary,
                temperature_k,
                smooth_evaluation.awr,
            );
        }
        push_background_thermal_features(
            &mut output_seed,
            background,
            temperature_k,
            smooth_evaluation.awr,
        );
        for isotope in &smooth_evaluation.isotopes {
            for range in &isotope.ranges {
                output_seed.push(range.energy_min);
                output_seed.push(range.energy_max);
                match range.data {
                    RangeData::BreitWigner(_)
                    | RangeData::ReichMoore(_)
                    | RangeData::RMatrixLimited(_) => push_broadened_resolved_grid(
                        &mut output_seed,
                        range,
                        temperature_k,
                        smooth_evaluation.awr,
                        grid_density,
                    ),
                    RangeData::Unresolved(_) => push_unresolved_grid(&mut output_seed, range),
                    RangeData::ScatteringOnly { .. } => {}
                }
            }
        }
        push_delta_grid(
            &mut output_seed,
            &analytic_lines,
            temperature_k,
            smooth_evaluation.awr,
        );
        for line in &analytic_lines {
            match &line.wing_retention {
                WingRetention::SmoothSymmetric {
                    inner_widths,
                    outer_widths,
                } => {
                    for widths in [-*outer_widths, -*inner_widths, *inner_widths, *outer_widths] {
                        push_thermal_transition(
                            &mut output_seed,
                            line.energy_ev + widths * line.total_width_ev,
                            temperature_k,
                            smooth_evaluation.awr,
                        );
                    }
                }
                WingRetention::RangeEdge {
                    low_ev,
                    high_ev,
                    edge_is_upper,
                } => {
                    let inner = ULTRA_NARROW_CORE_OUTER_WIDTHS - ULTRA_NARROW_CORE_TAPER_WIDTHS;
                    let breaks = if *edge_is_upper {
                        [
                            line.energy_ev - ULTRA_NARROW_CORE_OUTER_WIDTHS * line.total_width_ev,
                            line.energy_ev - inner * line.total_width_ev,
                            *high_ev,
                        ]
                    } else {
                        [
                            *low_ev,
                            line.energy_ev + inner * line.total_width_ev,
                            line.energy_ev + ULTRA_NARROW_CORE_OUTER_WIDTHS * line.total_width_ev,
                        ]
                    };
                    for energy in breaks {
                        push_thermal_transition(
                            &mut output_seed,
                            energy,
                            temperature_k,
                            smooth_evaluation.awr,
                        );
                    }
                }
            }
        }
        let output_seed = sorted_grid(output_seed, low, high)?;
        let broadened = broaden_with_lines(
            &zero_k_energy,
            &zero_k_sigma,
            &analytic_lines,
            temperature_k,
            smooth_evaluation.awr,
            &output_seed,
        )?;
        linearize(output_seed, broadened, |energies| {
            broaden_with_lines(
                &zero_k_energy,
                &zero_k_sigma,
                &analytic_lines,
                temperature_k,
                smooth_evaluation.awr,
                energies,
            )
        })
        .map_err(|error| format!("Doppler {error}"))?
    };
    if output_sigma
        .iter()
        .any(|value| !value.is_finite() || *value < -1e-10)
    {
        return Err(format!(
            "MT={mt} Doppler processing produced a nonfinite or negative value"
        ));
    }
    let table = Tabulated {
        interpolation: vec![(output_energy.len(), 2)],
        x: output_energy,
        y: output_sigma
            .into_iter()
            .map(|value| value.max(0.0))
            .collect(),
    };
    table.validate()?;
    Ok(ProcessedReaction {
        certificate: ProcessingCertificate {
            mt,
            zero_k_points,
            output_points: table.x.len(),
            zero_k_refinement_passes: zero_k_passes,
            output_refinement_passes: output_passes,
            ultra_narrow_lines,
        },
        table,
        zero_k_lines: if temperature_k == 0.0 {
            analytic_lines
        } else {
            Vec::new()
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resonance::{LegacyLGroup, LegacyResolved, ResonanceIsotope, ResonanceRange};

    fn ultra_narrow_evaluation() -> ResonanceEvaluation {
        ResonanceEvaluation {
            za: 1001,
            awr: 100.0,
            isotopes: vec![ResonanceIsotope {
                zai: 1001,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
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
                                total: 1e-8,
                                neutron: 4e-9,
                                capture: 6e-9,
                                fission_a: 0.0,
                                fission_b: 0.0,
                            }],
                        }],
                    }),
                }],
            }],
        }
    }

    fn upper_range_edge_evaluation() -> ResonanceEvaluation {
        let mut evaluation = ultra_narrow_evaluation();
        evaluation.isotopes[0].ranges[0].energy_max = 10.002;
        evaluation
    }

    fn zero_background() -> Tabulated {
        Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![1.0, 100.0],
            y: vec![0.0, 0.0],
        }
    }

    fn test_groups() -> GroupStructure {
        GroupStructure {
            name: "analytic-line-test".into(),
            boundaries_ev: vec![1.0, 9.0, 11.0, 100.0],
        }
    }

    #[test]
    fn ultra_narrow_zero_k_collapse_preserves_area_and_product_factor() {
        let groups = test_groups();
        let processed = process_reaction(
            &ultra_narrow_evaluation(),
            &zero_background(),
            &groups,
            102,
            0.0,
            0.01,
        )
        .unwrap();
        assert_eq!(processed.certificate.ultra_narrow_lines.len(), 1);
        let line = &processed.certificate.ultra_narrow_lines[0];
        let relative_area = (line.direct_area_barn_ev - line.closed_form_area_barn_ev).abs()
            / line.closed_form_area_barn_ev;
        assert!(relative_area <= ULTRA_NARROW_AREA_TOLERANCE);
        assert_eq!(line.affected_group, 1);
        assert!(processed.table.evaluate(line.energy_ev).unwrap() <= 1e-30);

        let delta = line.weighted_area_barn_ev / (line.energy_ev * (11.0f64 / 9.0).ln());
        let smooth = groups.collapse(&processed.table).unwrap();
        let expected = smooth[1] + delta;
        let collapsed = processed.collapse(&groups).unwrap();
        assert!((collapsed[1] - expected).abs() <= 2e-14 * expected);

        let factor = Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![1.0, 100.0],
            y: vec![0.25, 0.25],
        };
        let product = processed.collapse_product(&groups, &[&factor]).unwrap();
        for (actual, reference) in product.iter().zip(&collapsed) {
            assert!((actual - 0.25 * reference).abs() <= 2e-14 * reference.abs().max(1e-30));
        }
    }

    #[test]
    fn ultra_narrow_finite_temperature_uses_exact_delta_kernel() {
        let groups = test_groups();
        let temperature = 293.6;
        let evaluation = ultra_narrow_evaluation();
        let processed = process_reaction(
            &evaluation,
            &zero_background(),
            &groups,
            102,
            temperature,
            0.01,
        )
        .unwrap();
        let line = &processed.certificate.ultra_narrow_lines[0];
        let expected = doppler::delta_line(
            line.weighted_area_barn_ev,
            line.energy_ev,
            temperature,
            evaluation.awr,
            line.energy_ev,
        )
        .unwrap();
        let actual = processed.table.evaluate(line.energy_ev).unwrap();
        assert!(actual >= expected);
        assert!((actual - expected).abs() <= 1e-6 * expected);
        assert!(processed.zero_k_lines.is_empty());
    }

    #[test]
    fn exact_discontinuity_records_both_one_sided_limits() {
        let (energy, sigma) = insert_exact_discontinuities(
            vec![1.0, 2.0, 4.0],
            vec![1.0, 1.0, 0.0],
            &[2.0],
            |value| Ok(if value <= 2.0 { 1.0 } else { 0.0 }),
        )
        .unwrap();
        assert_eq!(energy, vec![1.0, 2.0, 2.0, 4.0]);
        assert_eq!(sigma, vec![1.0, 1.0, 0.0, 0.0]);
    }

    #[test]
    fn sharp_background_kink_gets_a_thermal_transition_grid() {
        let background = Tabulated {
            interpolation: vec![(4, 2)],
            x: vec![1.95e7, 2.0e7, 2.000_001e7, 2.0e8],
            y: vec![2.427e-5, 2.041e-5, 0.0, 0.0],
        };
        let mut grid = Vec::new();
        push_background_thermal_features(&mut grid, &background, 293.6, 106.0);
        let width = (4.0 * doppler::KB_EV_PER_K * 293.6 * 2.0e7 / 106.0).sqrt();
        assert!(grid
            .iter()
            .any(|energy| (*energy - (2.0e7 + width / 8.0)).abs() <= 1e-8));
    }

    #[test]
    fn distant_threshold_ordinate_does_not_hide_local_thermal_kink() {
        let background = Tabulated {
            interpolation: vec![(3, 2)],
            x: vec![1.0e5, 2.0e5, 3.0e5],
            y: vec![1.226274e-9, 1.502061e-9, 2.747336],
        };
        let mut grid = Vec::new();
        push_background_thermal_features(&mut grid, &background, 293.6, 275.7643);
        let width = (4.0 * doppler::KB_EV_PER_K * 293.6 * 2.0e5 / 275.7643).sqrt();
        for expected in [2.0e5 - width / 8.0, 2.0e5 + width / 8.0] {
            assert!(grid
                .iter()
                .any(|energy| (*energy - expected).abs() <= 1e-10));
        }
    }

    #[test]
    fn ultra_narrow_range_edge_decomposition_is_finite() {
        let groups = test_groups();
        let evaluation = upper_range_edge_evaluation();
        let processed =
            process_reaction(&evaluation, &zero_background(), &groups, 102, 293.6, 0.01).unwrap();

        let [line] = processed.certificate.ultra_narrow_lines.as_slice() else {
            panic!("expected exactly one analytic line")
        };
        assert!(line.range_edge_decomposition);
        let relative_area = (line.direct_area_barn_ev - line.closed_form_area_barn_ev).abs()
            / line.closed_form_area_barn_ev;
        assert!(relative_area <= ULTRA_NARROW_AREA_TOLERANCE);
        assert!(processed
            .table
            .y
            .iter()
            .all(|value| value.is_finite() && *value >= 0.0));
    }
}
