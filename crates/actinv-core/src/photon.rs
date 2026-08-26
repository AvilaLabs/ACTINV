//! Decay-photon source terms and the P7 screening-dose response.
//!
//! Photon spectra come directly from ENDF-6 MF=8/MT=457. Evaluated intensities remain
//! visible, while the transport source is energy-normalized to the record's mean
//! electromagnetic energy. Any correction is returned in diagnostics for the run ledger.
#![allow(non_snake_case)] // names are the public JSON wire format and include physical units

use std::collections::BTreeMap;

use actinv_data::decay::{ContinuousRadiation, Nuclide};
use serde::{Deserialize, Serialize};

use crate::run::EV;

pub const FISPACT_24_BOUNDARIES_EV: [f64; 25] = [
    0.0, 1.0e4, 2.0e4, 5.0e4, 1.0e5, 2.0e5, 3.0e5, 4.0e5, 6.0e5, 8.0e5, 1.0e6, 1.22e6, 1.44e6,
    1.66e6, 2.0e6, 2.5e6, 3.0e6, 4.0e6, 5.0e6, 6.5e6, 8.0e6, 1.0e7, 1.2e7, 1.4e7, 2.0e7,
];

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResponseCurve {
    pub energy_eV: Vec<f64>,
    pub values_cm2_g: Vec<f64>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhotonResponse {
    pub schema: String,
    #[serde(default)]
    pub provenance: serde_json::Value,
    pub air_mass_energy_absorption: ResponseCurve,
    pub element_mass_attenuation: BTreeMap<String, ResponseCurve>,
}

impl PhotonResponse {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let r: Self = serde_json::from_str(text).map_err(|e| format!("photon response: {e}"))?;
        if r.schema != "actinv-photon-response-1" {
            return Err(format!("unsupported photon response schema '{}'", r.schema));
        }
        validate_curve("air_mass_energy_absorption", &r.air_mass_energy_absorption)?;
        for (element, curve) in &r.element_mass_attenuation {
            validate_curve(&format!("element_mass_attenuation.{element}"), curve)?;
        }
        Ok(r)
    }
}

fn validate_curve(name: &str, c: &ResponseCurve) -> Result<(), String> {
    if c.energy_eV.len() < 2 || c.energy_eV.len() != c.values_cm2_g.len() {
        return Err(format!(
            "{name}: energy/value arrays must have the same length >= 2"
        ));
    }
    if c.energy_eV.iter().any(|x| !x.is_finite() || *x <= 0.0)
        || c.values_cm2_g.iter().any(|x| !x.is_finite() || *x <= 0.0)
        || c.energy_eV.windows(2).any(|w| w[1] < w[0])
    {
        return Err(format!(
            "{name}: energies must be positive and nondecreasing; values must be positive"
        ));
    }
    Ok(())
}

/// Log-log interpolation. Duplicate energies at absorption edges are retained: values below
/// an edge approach its first entry and values above it leave from its last entry.
fn curve_value(c: &ResponseCurve, energy: f64) -> Option<f64> {
    if energy < c.energy_eV[0] || energy > *c.energy_eV.last()? {
        return None;
    }
    let upper = c.energy_eV.partition_point(|x| *x <= energy);
    if upper > 0 && c.energy_eV[upper - 1] == energy {
        return Some(c.values_cm2_g[upper - 1]);
    }
    if upper == 0 || upper == c.energy_eV.len() {
        return None;
    }
    let (x0, x1) = (c.energy_eV[upper - 1], c.energy_eV[upper]);
    let (y0, y1) = (c.values_cm2_g[upper - 1], c.values_cm2_g[upper]);
    if x1 == x0 {
        return Some(y1);
    }
    let t = (energy / x0).ln() / (x1 / x0).ln();
    Some((y0.ln() + t * (y1 / y0).ln()).exp())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PhotonLineOut {
    pub energy_eV: f64,
    pub photons_s_g: f64,
    pub photons_s: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PhotonGroupOut {
    pub low_eV: f64,
    pub high_eV: f64,
    pub centroid_eV: f64,
    pub photons_s_g: f64,
    pub photons_s: f64,
    pub power_W_g: f64,
    pub power_W: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NuclideLineOut {
    pub energy_eV: f64,
    pub evaluated_yield_per_decay: f64,
    pub source_yield_per_decay: f64,
    pub photons_s_g: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NuclidePhotonOut {
    pub nuclide: String,
    pub activity_Bq_g: f64,
    pub mean_em_energy_eV_per_decay: f64,
    pub raw_spectrum_energy_eV_per_decay: f64,
    pub energy_normalization: f64,
    pub raw_photons_per_decay: f64,
    pub source_photons_per_decay: f64,
    pub source_power_W_g: f64,
    pub gamma_constant_Gy_m2_Bq_s: Option<f64>,
    pub gamma_constant_mGy_m2_GBq_h: Option<f64>,
    pub contact_gamma_air_dose_proxy_Gy_h: Option<f64>,
    pub lines: Vec<NuclideLineOut>,
    pub groups: Vec<PhotonGroupOut>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PhotonSourceOut {
    pub group_structure: String,
    pub boundaries_eV: Vec<f64>,
    pub lines: Vec<PhotonLineOut>,
    pub groups: Vec<PhotonGroupOut>,
    pub by_nuclide: Vec<NuclidePhotonOut>,
    pub grouped_photons_s_g: f64,
    pub grouped_photons_s: f64,
    pub total_photons_s_g: f64,
    pub total_photons_s: f64,
    pub source_power_W_g: f64,
    pub source_power_W: f64,
    pub ungrouped_power_W_g: f64,
    pub unrepresented_gamma_power_W_g: f64,
    pub represented_gamma_power_fraction: f64,
    pub contact_gamma_air_dose_proxy_Gy_h: Option<f64>,
    pub dose_response_power_coverage: Option<f64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct NormalizationDiagnostic {
    pub nuclide: String,
    pub raw_energy_eV_per_decay: f64,
    pub mean_em_energy_eV_per_decay: f64,
    pub relative_raw_closure: f64,
    pub source_scale: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct MissingSpectrumDiagnostic {
    pub nuclide: String,
    pub activity_Bq_g: f64,
    pub unrepresented_power_W_g: f64,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct PhotonDiagnostics {
    pub energy_normalized_spectra: Vec<NormalizationDiagnostic>,
    pub nuclides_with_em_energy_but_no_photon_spectrum: Vec<MissingSpectrumDiagnostic>,
    pub group_underflow_photons_s_g: f64,
    pub group_overflow_photons_s_g: f64,
    pub group_underflow_power_W_g: f64,
    pub group_overflow_power_W_g: f64,
    pub response_excluded_power_W_g: f64,
    pub response_missing_elements: Vec<String>,
}

#[derive(Clone, Debug)]
struct ShapeLine {
    energy: f64,
    evaluated: f64,
    source: f64,
}

#[derive(Clone, Debug)]
struct Shape {
    lines: Vec<ShapeLine>,
    group_count: Vec<f64>,
    group_moment: Vec<f64>,
    raw_count: f64,
    raw_energy: f64,
    source_count: f64,
    source_energy: f64,
    scale: f64,
    under_count: f64,
    over_count: f64,
    under_energy: f64,
    over_energy: f64,
}

fn law_for_interval(ranges: &[(usize, i32)], interval: usize) -> Result<i32, String> {
    let end_point = interval + 2; // ENDF NBT is a one-based point number.
    ranges
        .iter()
        .find(|(nbt, _)| end_point <= *nbt)
        .map(|(_, law)| *law)
        .ok_or_else(|| format!("no ENDF interpolation range covers point {end_point}"))
}

/// Interpolate and integrate y and E*y over `[a,b]` within one ENDF TAB1 interval.
fn segment_moments(
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    law: i32,
    a: f64,
    b: f64,
) -> Result<(f64, f64), String> {
    if !(x1 > x0 && b >= a && a >= x0 && b <= x1) {
        return Err("invalid TAB1 integration interval".into());
    }
    if b == a {
        return Ok((0.0, 0.0));
    }
    let (i0, i1) = match law {
        1 => (y0 * (b - a), 0.5 * y0 * (b * b - a * a)),
        2 => {
            let m = (y1 - y0) / (x1 - x0);
            let c = y0 - m * x0;
            (
                0.5 * m * (b * b - a * a) + c * (b - a),
                (m / 3.0) * (b.powi(3) - a.powi(3)) + 0.5 * c * (b * b - a * a),
            )
        }
        3 => {
            if x0 <= 0.0 || a <= 0.0 {
                return Err("lin-log TAB1 interpolation needs positive energy".into());
            }
            let q = (y1 - y0) / (x1 / x0).ln();
            let p = y0 - q * x0.ln();
            let f0 = |x: f64| p * x + q * (x * x.ln() - x);
            let f1 = |x: f64| 0.5 * p * x * x + q * (0.5 * x * x * x.ln() - 0.25 * x * x);
            (f0(b) - f0(a), f1(b) - f1(a))
        }
        4 => {
            if y0 <= 0.0 || y1 <= 0.0 {
                return Err("log-lin TAB1 interpolation needs positive probability".into());
            }
            let k = (y1 / y0).ln() / (x1 - x0);
            let ya = y0 * (k * (a - x0)).exp();
            let d = b - a;
            if k.abs() < 1e-14 {
                (ya * d, 0.5 * ya * (b * b - a * a))
            } else {
                let ek = (k * d).exp();
                let j0 = ya * (ek - 1.0) / k;
                let jt = ya * (ek * (k * d - 1.0) + 1.0) / (k * k);
                (j0, a * j0 + jt)
            }
        }
        5 => {
            if x0 <= 0.0 || a <= 0.0 || y0 <= 0.0 || y1 <= 0.0 {
                return Err(
                    "log-log TAB1 interpolation needs positive energy and probability".into(),
                );
            }
            let p = (y1 / y0).ln() / (x1 / x0).ln();
            let ya = y0 * (a / x0).powf(p);
            let r = b / a;
            let power_integral = |n: f64| {
                if n.abs() < 1e-12 {
                    r.ln()
                } else {
                    (r.powf(n) - 1.0) / n
                }
            };
            (
                ya * a * power_integral(p + 1.0),
                ya * a * a * power_integral(p + 2.0),
            )
        }
        _ => return Err(format!("unsupported ENDF TAB1 interpolation law {law}")),
    };
    Ok((i0, i1))
}

fn add_to_groups(
    boundaries: &[f64],
    count: &mut [f64],
    moment: &mut [f64],
    a: f64,
    b: f64,
    integral: impl Fn(f64, f64) -> Result<(f64, f64), String>,
) -> Result<(f64, f64, f64, f64), String> {
    let (mut under_count, mut under_energy) = (0.0, 0.0);
    let (mut over_count, mut over_energy) = (0.0, 0.0);
    if a < boundaries[0] {
        let z = b.min(boundaries[0]);
        if z > a {
            let (count, energy) = integral(a, z)?;
            under_count += count;
            under_energy += energy;
        }
    }
    for g in 0..count.len() {
        let lo = a.max(boundaries[g]);
        let hi = b.min(boundaries[g + 1]);
        if hi > lo {
            let (n, e) = integral(lo, hi)?;
            count[g] += n;
            moment[g] += e;
        }
    }
    if b > *boundaries.last().unwrap() {
        let z = a.max(*boundaries.last().unwrap());
        if b > z {
            let (count, energy) = integral(z, b)?;
            over_count += count;
            over_energy += energy;
        }
    }
    Ok((under_count, under_energy, over_count, over_energy))
}

fn continuous_moments(
    c: &ContinuousRadiation,
    boundaries: &[f64],
    count: &mut [f64],
    moment: &mut [f64],
    factor: f64,
) -> Result<(f64, f64, f64, f64, f64, f64), String> {
    if c.points.len() < 2 {
        return Err("continuous spectrum has fewer than two points".into());
    }
    let mut total_n = 0.0;
    let mut total_e = 0.0;
    let (mut under_count, mut under_energy) = (0.0, 0.0);
    let (mut over_count, mut over_energy) = (0.0, 0.0);
    for k in 0..c.points.len() - 1 {
        let ((x0, y0), (x1, y1)) = (c.points[k], c.points[k + 1]);
        let law = law_for_interval(&c.interpolation, k)?;
        let integrate = |a, b| {
            segment_moments(x0, y0, x1, y1, law, a, b).map(|(n, e)| (factor * n, factor * e))
        };
        let (n, e) = integrate(x0, x1)?;
        total_n += n;
        total_e += e;
        let (un, ue, on, oe) = add_to_groups(boundaries, count, moment, x0, x1, integrate)?;
        under_count += un;
        under_energy += ue;
        over_count += on;
        over_energy += oe;
    }
    Ok((
        total_n,
        total_e,
        under_count,
        under_energy,
        over_count,
        over_energy,
    ))
}

fn line_group(energy: f64, boundaries: &[f64]) -> Option<usize> {
    if energy < boundaries[0] || energy > *boundaries.last()? {
        return None;
    }
    if energy == *boundaries.last()? {
        return Some(boundaries.len() - 2);
    }
    let upper = boundaries.partition_point(|x| *x <= energy);
    upper.checked_sub(1).filter(|g| *g + 1 < boundaries.len())
}

fn shape(nu: &Nuclide, boundaries: &[f64]) -> Result<Option<Shape>, String> {
    let ng = boundaries.len() - 1;
    let mut group_count = vec![0.0; ng];
    let mut group_moment = vec![0.0; ng];
    let mut lines = Vec::new();
    let mut raw_count = 0.0;
    let mut raw_energy = 0.0;
    let mut under_count = 0.0;
    let mut over_count = 0.0;
    let mut under_energy = 0.0;
    let mut over_energy = 0.0;
    for spectrum in nu
        .spectra
        .iter()
        .filter(|s| s.styp.round() as i32 == 0 || s.styp.round() as i32 == 9)
    {
        for d in &spectrum.discrete {
            let y = spectrum.fd * d.intensity;
            if y <= 0.0 || d.energy < 0.0 {
                continue;
            }
            raw_count += y;
            raw_energy += y * d.energy;
            if let Some(g) = line_group(d.energy, boundaries) {
                group_count[g] += y;
                group_moment[g] += y * d.energy;
            } else if d.energy < boundaries[0] {
                under_count += y;
                under_energy += y * d.energy;
            } else {
                over_count += y;
                over_energy += y * d.energy;
            }
            lines.push(ShapeLine {
                energy: d.energy,
                evaluated: y,
                source: 0.0,
            });
        }
        if let Some(c) = &spectrum.continuous {
            let (n, e, un, ue, on, oe) = continuous_moments(
                c,
                boundaries,
                &mut group_count,
                &mut group_moment,
                spectrum.fc,
            )?;
            raw_count += n;
            raw_energy += e;
            under_count += un;
            under_energy += ue;
            over_count += on;
            over_energy += oe;
        }
    }
    if raw_count <= 0.0 || raw_energy <= 0.0 {
        return Ok(None);
    }
    let target = nu.e_em();
    let scale = if target > 0.0 {
        target / raw_energy
    } else {
        1.0
    };
    for line in &mut lines {
        line.source = line.evaluated * scale;
    }
    for v in &mut group_count {
        *v *= scale;
    }
    for v in &mut group_moment {
        *v *= scale;
    }
    Ok(Some(Shape {
        lines,
        group_count,
        group_moment,
        raw_count,
        raw_energy,
        source_count: raw_count * scale,
        source_energy: raw_energy * scale,
        scale,
        under_count: under_count * scale,
        over_count: over_count * scale,
        under_energy: under_energy * scale,
        over_energy: over_energy * scale,
    }))
}

fn material_mu(
    response: &PhotonResponse,
    fractions: &BTreeMap<String, f64>,
    energy: f64,
) -> Option<f64> {
    let mut out = 0.0;
    for (element, fraction) in fractions {
        let curve = response.element_mass_attenuation.get(element)?;
        out += fraction * curve_value(curve, energy)?;
    }
    Some(out).filter(|v| *v > 0.0)
}

fn gamma_constant(
    moments_eV: &[f64],
    counts: &[f64],
    response: &PhotonResponse,
    cutoff_eV: f64,
) -> (f64, f64) {
    let mut weighted_eV_cm2_g = 0.0;
    let mut included_energy = 0.0;
    for (&moment, &count) in moments_eV.iter().zip(counts) {
        if count <= 0.0 {
            continue;
        }
        let e = moment / count;
        if e < cutoff_eV {
            continue;
        }
        if let Some(mu) = curve_value(&response.air_mass_energy_absorption, e) {
            weighted_eV_cm2_g += moment * mu;
            included_energy += moment;
        }
    }
    // E[eV] * 1.602e-19 J/eV * (cm2/g -> 0.1 m2/kg) / 4pi.
    (
        weighted_eV_cm2_g * EV * 0.1 / (4.0 * std::f64::consts::PI),
        included_energy,
    )
}

/// Build one step's source from `(name, decay record, activity Bq/g)` entries.
#[allow(clippy::too_many_arguments)]
pub fn source_for_step(
    active: &[(&str, &Nuclide, f64)],
    boundaries: &[f64],
    group_structure: &str,
    mass_g: f64,
    response: Option<&PhotonResponse>,
    material_mass_fractions: &BTreeMap<String, f64>,
    material_response_complete: bool,
    build_up: f64,
    gamma_cutoff_eV: f64,
) -> Result<(PhotonSourceOut, PhotonDiagnostics), String> {
    if boundaries.len() < 2 || boundaries.windows(2).any(|w| w[1] <= w[0]) {
        return Err("photon group boundaries must be strictly increasing".into());
    }
    let ng = boundaries.len() - 1;
    let mut aggregate_count = vec![0.0; ng];
    let mut aggregate_moment = vec![0.0; ng];
    let mut aggregate_lines: Vec<(f64, f64)> = Vec::new();
    let mut by_nuclide = Vec::new();
    let mut diag = PhotonDiagnostics::default();
    let mut missing_power = 0.0;
    let mut contact_total = 0.0;
    let mut contact_available = response.is_some() && material_response_complete;
    let mut response_included_power = 0.0;
    let mut response_total_power = 0.0;

    if response.is_some() && !material_response_complete {
        diag.response_missing_elements = material_mass_fractions
            .keys()
            .filter(|e| !response.unwrap().element_mass_attenuation.contains_key(*e))
            .cloned()
            .collect();
        contact_available = false;
    }

    for &(name, nu, activity) in active {
        if activity <= 0.0 || nu.lambda() <= 0.0 {
            continue;
        }
        let expected_power = activity * nu.e_em().max(0.0) * EV;
        let Some(sh) = shape(nu, boundaries)? else {
            if expected_power > 0.0 {
                missing_power += expected_power;
                diag.nuclides_with_em_energy_but_no_photon_spectrum.push(
                    MissingSpectrumDiagnostic {
                        nuclide: name.into(),
                        activity_Bq_g: activity,
                        unrepresented_power_W_g: expected_power,
                    },
                );
            }
            continue;
        };
        let raw_rel = if nu.e_em() > 0.0 {
            (sh.raw_energy - nu.e_em()).abs() / nu.e_em()
        } else {
            0.0
        };
        if (sh.scale - 1.0).abs() > 1e-12 {
            diag.energy_normalized_spectra
                .push(NormalizationDiagnostic {
                    nuclide: name.into(),
                    raw_energy_eV_per_decay: sh.raw_energy,
                    mean_em_energy_eV_per_decay: nu.e_em(),
                    relative_raw_closure: raw_rel,
                    source_scale: sh.scale,
                });
        }
        diag.group_underflow_photons_s_g += activity * sh.under_count;
        diag.group_overflow_photons_s_g += activity * sh.over_count;
        diag.group_underflow_power_W_g += activity * sh.under_energy * EV;
        diag.group_overflow_power_W_g += activity * sh.over_energy * EV;

        let mut n_groups = Vec::with_capacity(ng);
        for g in 0..ng {
            let rate = activity * sh.group_count[g];
            let moment_rate = activity * sh.group_moment[g];
            aggregate_count[g] += rate;
            aggregate_moment[g] += moment_rate;
            n_groups.push(PhotonGroupOut {
                low_eV: boundaries[g],
                high_eV: boundaries[g + 1],
                centroid_eV: if rate > 0.0 { moment_rate / rate } else { 0.0 },
                photons_s_g: rate,
                photons_s: rate * mass_g,
                power_W_g: moment_rate * EV,
                power_W: moment_rate * EV * mass_g,
            });
        }
        let mut n_lines = Vec::with_capacity(sh.lines.len());
        for line in &sh.lines {
            let rate = activity * line.source;
            aggregate_lines.push((line.energy, rate));
            n_lines.push(NuclideLineOut {
                energy_eV: line.energy,
                evaluated_yield_per_decay: line.evaluated,
                source_yield_per_decay: line.source,
                photons_s_g: rate,
            });
        }

        let gc = match response {
            Some(r) => gamma_constant(&sh.group_moment, &sh.group_count, r, gamma_cutoff_eV).0,
            None => 0.0,
        };
        let mut contact_nuclide = 0.0;
        let mut contact_ok = contact_available;
        if let Some(r) = response {
            response_total_power += activity * sh.source_energy;
            if sh.under_energy > 0.0 || sh.over_energy > 0.0 {
                diag.response_excluded_power_W_g +=
                    activity * (sh.under_energy + sh.over_energy) * EV;
                contact_ok = false;
            }
            for g in 0..ng {
                let moment_rate = activity * sh.group_moment[g];
                if moment_rate <= 0.0 {
                    continue;
                }
                let e = sh.group_moment[g] / sh.group_count[g];
                match (
                    curve_value(&r.air_mass_energy_absorption, e),
                    material_mu(r, material_mass_fractions, e),
                ) {
                    (Some(mu_air), Some(mu_mat)) => {
                        // eV/s/g -> Gy/h, with the FISPACT B/2 slab factor.
                        contact_nuclide += (build_up / 2.0)
                            * (mu_air / mu_mat)
                            * moment_rate
                            * EV
                            * 1000.0
                            * 3600.0;
                        response_included_power += moment_rate;
                    }
                    _ => {
                        diag.response_excluded_power_W_g += moment_rate * EV;
                        contact_ok = false;
                    }
                }
            }
        }
        if contact_ok {
            contact_total += contact_nuclide;
        }
        by_nuclide.push(NuclidePhotonOut {
            nuclide: name.into(),
            activity_Bq_g: activity,
            mean_em_energy_eV_per_decay: nu.e_em(),
            raw_spectrum_energy_eV_per_decay: sh.raw_energy,
            energy_normalization: sh.scale,
            raw_photons_per_decay: sh.raw_count,
            source_photons_per_decay: sh.source_count,
            source_power_W_g: activity * sh.source_energy * EV,
            gamma_constant_Gy_m2_Bq_s: response.map(|_| gc),
            gamma_constant_mGy_m2_GBq_h: response.map(|_| gc * 3.6e15),
            contact_gamma_air_dose_proxy_Gy_h: if contact_ok {
                Some(contact_nuclide)
            } else {
                None
            },
            lines: n_lines,
            groups: n_groups,
        });
    }

    aggregate_lines.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut merged_lines: Vec<PhotonLineOut> = Vec::new();
    for (energy, rate) in aggregate_lines {
        if let Some(last) = merged_lines
            .last_mut()
            .filter(|x| x.energy_eV.to_bits() == energy.to_bits())
        {
            last.photons_s_g += rate;
            last.photons_s += rate * mass_g;
        } else {
            merged_lines.push(PhotonLineOut {
                energy_eV: energy,
                photons_s_g: rate,
                photons_s: rate * mass_g,
            });
        }
    }
    let groups: Vec<_> = (0..ng)
        .map(|g| PhotonGroupOut {
            low_eV: boundaries[g],
            high_eV: boundaries[g + 1],
            centroid_eV: if aggregate_count[g] > 0.0 {
                aggregate_moment[g] / aggregate_count[g]
            } else {
                0.0
            },
            photons_s_g: aggregate_count[g],
            photons_s: aggregate_count[g] * mass_g,
            power_W_g: aggregate_moment[g] * EV,
            power_W: aggregate_moment[g] * EV * mass_g,
        })
        .collect();
    let grouped_count: f64 = aggregate_count.iter().sum();
    let total_count =
        grouped_count + diag.group_underflow_photons_s_g + diag.group_overflow_photons_s_g;
    let grouped_energy: f64 = aggregate_moment.iter().sum();
    let ungrouped_power = diag.group_underflow_power_W_g + diag.group_overflow_power_W_g;
    let source_power = grouped_energy * EV + ungrouped_power;
    let represented = (source_power + ungrouped_power).max(0.0);
    let denom = represented + missing_power;
    let coverage = if response_total_power > 0.0 {
        Some(response_included_power / response_total_power)
    } else {
        response.map(|_| 1.0)
    };
    Ok((
        PhotonSourceOut {
            group_structure: group_structure.into(),
            boundaries_eV: boundaries.to_vec(),
            lines: merged_lines,
            groups,
            by_nuclide,
            grouped_photons_s_g: grouped_count,
            grouped_photons_s: grouped_count * mass_g,
            total_photons_s_g: total_count,
            total_photons_s: total_count * mass_g,
            source_power_W_g: source_power,
            source_power_W: source_power * mass_g,
            ungrouped_power_W_g: ungrouped_power,
            unrepresented_gamma_power_W_g: missing_power,
            represented_gamma_power_fraction: if denom > 0.0 {
                represented / denom
            } else {
                1.0
            },
            contact_gamma_air_dose_proxy_Gy_h: if contact_available
                && diag.response_excluded_power_W_g == 0.0
            {
                Some(contact_total)
            } else {
                None
            },
            dose_response_power_coverage: coverage,
        },
        diag,
    ))
}

fn nonzero_distribution(source: &PhotonSourceOut) -> Result<(Vec<f64>, Vec<f64>), String> {
    let mut energy = Vec::new();
    let mut weight = Vec::new();
    for g in &source.groups {
        if g.photons_s > 0.0 && g.centroid_eV > 0.0 {
            energy.push(g.centroid_eV);
            weight.push(g.photons_s);
        }
    }
    let total: f64 = weight.iter().sum();
    if total <= 0.0 {
        return Err("selected step has no decay photons inside the export group structure".into());
    }
    let omitted = (source.total_photons_s - total).abs();
    if omitted > 1e-12 * source.total_photons_s.abs().max(1.0) {
        return Err(format!(
            "selected group structure omits {:.17e} photons/s; export would not conserve source strength",
            source.total_photons_s - total
        ));
    }
    for p in &mut weight {
        *p /= total;
    }
    Ok((energy, weight))
}

pub fn export_openmc(source: &PhotonSourceOut) -> Result<String, String> {
    let (energy, probability) = nonzero_distribution(source)?;
    let es = energy
        .iter()
        .map(|v| format!("{v:.17e}"))
        .collect::<Vec<_>>()
        .join(", ");
    let ps = probability
        .iter()
        .map(|v| format!("{v:.17e}"))
        .collect::<Vec<_>>()
        .join(", ");
    Ok(format!(
        "# ACTINV decay-photon source; replace the point with the P8/user spatial distribution.\n\
import openmc\n\n\
energy = openmc.stats.Discrete([{es}], [{ps}])\n\
source = openmc.IndependentSource(\n\
    space=openmc.stats.Point((0.0, 0.0, 0.0)),\n\
    energy=energy, particle=\"photon\", strength={:.17e})\n",
        source.total_photons_s
    ))
}

fn wrap_mcnp(prefix: &str, values: &[String]) -> String {
    let mut out = String::new();
    let mut line = prefix.to_string();
    for value in values {
        if line.len() + 1 + value.len() > 78 {
            out.push_str(&line);
            out.push('\n');
            line = "     ".into();
        }
        line.push(' ');
        line.push_str(value);
    }
    out.push_str(&line);
    out.push('\n');
    out
}

pub fn export_mcnp(source: &PhotonSourceOut) -> Result<String, String> {
    let (energy, probability) = nonzero_distribution(source)?;
    let mev = energy
        .iter()
        .map(|v| format!("{:.17e}", v * 1e-6))
        .collect::<Vec<_>>();
    let p = probability
        .iter()
        .map(|v| format!("{v:.17e}"))
        .collect::<Vec<_>>();
    let mut out = format!(
        "c ACTINV source; replace POS with the P8/user spatial distribution.\n\
MODE P\nSDEF PAR=P POS=0 0 0 ERG=D1 WGT={:.17e}\n",
        source.total_photons_s
    );
    out.push_str(&wrap_mcnp("SI1 L", &mev));
    out.push_str(&wrap_mcnp("SP1", &p));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::segment_moments;

    #[test]
    fn interpolation_moments_match_dense_reference() {
        for law in 1..=5 {
            let (x0, x1, y0, y1) = (1.0_f64, 4.0_f64, 2.0_f64, 7.0_f64);
            let (n, e) = segment_moments(x0, y0, x1, y1, law, 1.3, 3.7).unwrap();
            let m = 200_000;
            let dx = (3.7 - 1.3) / m as f64;
            let y = |x: f64| match law {
                1 => y0,
                2 => y0 + (y1 - y0) * (x - x0) / (x1 - x0),
                3 => y0 + (y1 - y0) * (x / x0).ln() / (x1 / x0).ln(),
                4 => y0 * ((y1 / y0).ln() * (x - x0) / (x1 - x0)).exp(),
                _ => y0 * (x / x0).powf((y1 / y0).ln() / (x1 / x0).ln()),
            };
            let mut nr = 0.0;
            let mut er = 0.0;
            for i in 0..m {
                let x = 1.3 + (i as f64 + 0.5) * dx;
                nr += y(x) * dx;
                er += x * y(x) * dx;
            }
            assert!((n - nr).abs() / nr < 2e-11, "law {law}: {n} {nr}");
            assert!((e - er).abs() / er < 2e-11, "law {law}: {e} {er}");
        }
    }
}
