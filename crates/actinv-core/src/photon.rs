//! Decay-photon source terms and the P7 screening-dose response.
//!
//! Photon spectra come directly from ENDF-6 MF=8/MT=457. Evaluated intensities remain
//! visible, while the transport source is energy-normalized to the record's mean
//! electromagnetic energy. Any correction is returned in diagnostics for the run ledger.
#![allow(non_snake_case)] // names are the public JSON wire format and include physical units

use std::collections::BTreeMap;

use actinv_data::decay::{ContinuousRadiation, Nuclide};
use serde::{Deserialize, Serialize};

use crate::chain::rtyp_digits;
use crate::run::EV;
use crate::spec::{BremsstrahlungOptions, PointSourceOptions, POINT_SOURCE_MIN_DISTANCE_M};

pub const FISPACT_24_BOUNDARIES_EV: [f64; 25] = [
    0.0, 1.0e4, 2.0e4, 5.0e4, 1.0e5, 2.0e5, 3.0e5, 4.0e5, 6.0e5, 8.0e5, 1.0e6, 1.22e6, 1.44e6,
    1.66e6, 2.0e6, 2.5e6, 3.0e6, 4.0e6, 5.0e6, 6.5e6, 8.0e6, 1.0e7, 1.2e7, 1.4e7, 2.0e7,
];

/// The 22-group "Steiner" gamma structure (`GROUP 1`): FISPACT-II User Manual, UKAEA-R(18)001
/// Table 6 (Table 5 of UKAEA-CCFE-RE(21)02), "22 Group" column, MeV converted to eV. The table
/// ends at 14 MeV, so photons above it fall in the overflow diagnostics.
pub const STEINER_22_BOUNDARIES_EV: [f64; 23] = [
    0.0, 1.0e4, 1.0e5, 2.0e5, 4.0e5, 1.0e6, 1.5e6, 2.0e6, 2.5e6, 3.0e6, 3.5e6, 4.0e6, 4.5e6, 5.0e6,
    5.5e6, 6.0e6, 6.5e6, 7.0e6, 7.5e6, 8.0e6, 1.0e7, 1.2e7, 1.4e7,
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
    /// Dry-air mass attenuation (mu/rho, cm2/g), NIST table 4. Optional: only the point-source
    /// dose's air transmission uses it, and a response built without it still loads.
    #[serde(default)]
    pub air_mass_attenuation: Option<ResponseCurve>,
    pub element_mass_attenuation: BTreeMap<String, ResponseCurve>,
}

impl PhotonResponse {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let r: Self = serde_json::from_str(text).map_err(|e| format!("photon response: {e}"))?;
        if r.schema != "actinv-photon-response-1" {
            return Err(format!("unsupported photon response schema '{}'", r.schema));
        }
        validate_curve("air_mass_energy_absorption", &r.air_mass_energy_absorption)?;
        if let Some(curve) = &r.air_mass_attenuation {
            validate_curve("air_mass_attenuation", curve)?;
        }
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
    /// This nuclide's share of the point-source dose; present only with `photon.point_source`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_gamma_air_dose_Sv_h: Option<f64>,
    /// Power (W/g) of the nuclide's X-ray spectra (STYP 9) removed from the source; present
    /// only with `photon.exclude_xrays`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub excluded_xray_power_W_g: Option<f64>,
    pub lines: Vec<NuclideLineOut>,
    pub groups: Vec<PhotonGroupOut>,
}

/// Point-source dose (FISPACT-II `DOSE 2`, manual Eq. 63): the grouped photon source of the
/// step's `mass_g` of material as a point source, seen through `distance_m` of air.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PointSourceDoseOut {
    pub distance_m: f64,
    pub mass_g: f64,
    pub air_attenuation: bool,
    /// Present only when `air_attenuation` is applied.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub air_density_g_cm3: Option<f64>,
    /// Photon dose in air at the detector; numerically Gy/h and Sv/h coincide at unit quality factor.
    pub gamma_air_dose_Sv_h: Option<f64>,
    /// `gamma_air_dose_Sv_h / mass_g`, the FISPACT-II 1 g convention.
    pub gamma_air_dose_Sv_h_per_g: Option<f64>,
    /// Power (W) in groups whose centroid is below the response's lowest tabulated energy.
    pub subthreshold_power_W: f64,
    /// Power (W) outside the group structure or above the response's highest energy.
    pub excluded_power_W: f64,
    /// Why the dose is `null`, when it is.
    pub unavailable_reason: Option<String>,
}

/// One nuclide's bremsstrahlung photon source.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BremsstrahlungNuclideOut {
    pub nuclide: String,
    pub activity_Bq_g: f64,
    /// Beta-minus electrons per decay: the summed branching ratio of the decay modes whose
    /// RTYP carries a beta-minus electron.
    #[serde(default)]
    pub electrons_per_decay: f64,
    /// Mean energy of one beta-minus electron: the STYP=1 average energy per decay divided by
    /// `electrons_per_decay`.
    pub mean_beta_energy_eV: f64,
    pub photons_s_g: f64,
    pub power_W_g: f64,
    pub contact_gamma_air_dose_proxy_Gy_h: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_gamma_air_dose_Sv_h: Option<f64>,
    pub groups: Vec<PhotonGroupOut>,
}

/// A requested bremsstrahlung nuclide that could not be evaluated, and the input it lacks.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BremsstrahlungMissingInput {
    pub nuclide: String,
    pub missing: String,
}

/// FISPACT-II `BREMSSTRAHLUNG` analog (manual appendix C.7.4). Always a separate component:
/// nothing here is added into the step's decay-photon groups, powers or dose proxies.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BremsstrahlungOut {
    pub model: String,
    pub z_eff: Option<f64>,
    pub z_eff_unavailable_reason: Option<String>,
    pub groups: Vec<PhotonGroupOut>,
    pub by_nuclide: Vec<BremsstrahlungNuclideOut>,
    pub photons_s_g: f64,
    pub photons_s: f64,
    pub power_W_g: f64,
    pub power_W: f64,
    /// Energy the model emits into a group whose lower edge is 0 eV; Eq. (66) diverges there,
    /// so that group is left empty and its exact energy is reported instead.
    pub zero_lower_edge_group_power_W_g: f64,
    /// Share of the analytic emitted energy `a Z E0^2 / 2` per electron that the zero-lower-edge
    /// group holds and the grouped photons therefore lack; `None` when nothing was emitted.
    #[serde(default)]
    pub zero_lower_edge_energy_fraction: Option<f64>,
    /// Photon energy emitted into the groups plus the zero-lower-edge energy, over the analytic
    /// total `a Z E0^2 / 2` per electron. Eq. (67) gives each group's photons its bin midpoint,
    /// so this is not 1 in general: above 1 where the midpoint exceeds the photons' mean
    /// energy, below 1 where the spectrum extends past the last group. `None` when nothing was
    /// emitted.
    #[serde(default)]
    pub energy_closure: Option<f64>,
    pub contact_gamma_air_dose_proxy_Gy_h: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_gamma_air_dose_Sv_h: Option<f64>,
    /// False when a requested nuclide with activity lacks an input; its dose is then absent
    /// from the totals above.
    pub inputs_complete: bool,
    pub missing_inputs: Vec<BremsstrahlungMissingInput>,
    /// Requested nuclides that have no positive activity at this step.
    pub requested_nuclides_without_activity: Vec<String>,
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
    /// Power (W/g) in groups whose centroid lies below the response's lowest tabulated energy.
    /// It is omitted from the proxy; present only when non-zero.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub dose_response_subthreshold_power_W_g: f64,
    /// FISPACT-II `DOSE 2` analog; present only with `photon.point_source`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_dose: Option<PointSourceDoseOut>,
    /// FISPACT-II `BREMSSTRAHLUNG` analog; present only with `photon.bremsstrahlung`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bremsstrahlung: Option<BremsstrahlungOut>,
    /// Power (W/g) of nuclides without a photon spectrum that `photon.missing_spectra: "estimate"`
    /// placed in the groups; included in `source_power_W_g`, not in `unrepresented_gamma_power_W_g`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_gamma_power_W_g: Option<f64>,
    /// Power (W/g) of the X-ray spectra removed by `photon.exclude_xrays`; not counted as
    /// unrepresented and not in `source_power_W_g`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub excluded_xray_power_W_g: Option<f64>,
    /// The estimated nuclides and the range of each dose; present only with
    /// `photon.missing_spectra: "estimate"`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_spectra: Option<EstimatedSpectraOut>,
}

/// One linear dose quantity of an estimated nuclide: the estimate and the bounds that hold for
/// any spectrum with the nuclide's power below its energy cap.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DoseRange {
    pub estimate: f64,
    pub lower: f64,
    pub upper: f64,
    /// This nuclide's share of the step's estimated dose; set when the step estimate exists.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub share_of_step_estimate: Option<f64>,
}

/// A nuclide whose photon spectrum was estimated from its mean electromagnetic energy.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EstimatedNuclideOut {
    pub nuclide: String,
    pub activity_Bq_g: f64,
    pub mean_em_energy_eV_per_decay: f64,
    /// Class of the decay mode with the largest summed branching ratio.
    pub decay_mode: String,
    /// E_max before the Q-value cap.
    pub e_max_manual_eV: f64,
    pub e_max_eV: f64,
    /// Largest Q-value among the modes with positive branching, when one is positive.
    pub q_cap_eV: Option<f64>,
    /// Upper energy of the dose bounds: `q_cap_eV`, else the top of the group structure.
    pub e_cap_eV: f64,
    pub photons_per_decay: f64,
    pub photons_s_g: f64,
    pub power_W_g: f64,
    /// Power (W/g) of the estimate below or above the group structure.
    pub ungrouped_power_W_g: f64,
    pub groups: Vec<PhotonGroupOut>,
    pub contact_gamma_air_dose_proxy_Gy_h: Option<DoseRange>,
    /// Present only with `photon.point_source`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_gamma_air_dose_Sv_h: Option<DoseRange>,
}

/// One dose quantity of a step: the value from evaluated spectra alone, the estimate that adds
/// the estimated nuclides, and bounds that add each nuclide's lower or upper bound.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct DoseBoundsOut {
    pub represented_only: Option<f64>,
    pub estimate: Option<f64>,
    pub lower: Option<f64>,
    pub upper: Option<f64>,
    /// Estimated nuclides' part of `estimate`.
    pub estimated_share: Option<f64>,
    pub unavailable_reason: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EstimatedSpectraOut {
    pub model: String,
    pub exponent_a: f64,
    pub contact_gamma_air_dose_proxy_Gy_h: DoseBoundsOut,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source_gamma_air_dose_Sv_h: Option<DoseBoundsOut>,
    pub by_nuclide: Vec<EstimatedNuclideOut>,
}

fn is_zero(v: &f64) -> bool {
    *v == 0.0
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

/// A nuclide whose missing spectrum `photon.missing_spectra: "estimate"` replaced by an estimate.
#[derive(Clone, Debug, Serialize)]
pub struct EstimatedSpectrumDiagnostic {
    pub nuclide: String,
    pub activity_Bq_g: f64,
    pub estimated_power_W_g: f64,
    pub decay_mode: String,
    pub e_max_manual_eV: f64,
    pub e_max_eV: f64,
    pub q_cap_eV: Option<f64>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct PhotonDiagnostics {
    pub energy_normalized_spectra: Vec<NormalizationDiagnostic>,
    pub nuclides_with_em_energy_but_no_photon_spectrum: Vec<MissingSpectrumDiagnostic>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub nuclides_with_estimated_photon_spectrum: Vec<EstimatedSpectrumDiagnostic>,
    #[serde(skip_serializing_if = "is_zero")]
    pub estimated_gamma_power_W_g: f64,
    #[serde(skip_serializing_if = "is_zero")]
    pub excluded_xray_power_W_g: f64,
    pub group_underflow_photons_s_g: f64,
    pub group_overflow_photons_s_g: f64,
    pub group_underflow_power_W_g: f64,
    pub group_overflow_power_W_g: f64,
    pub response_excluded_power_W_g: f64,
    #[serde(skip_serializing_if = "is_zero")]
    pub response_subthreshold_power_W_g: f64,
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
    /// Scaled energy (eV per decay) of the X-ray spectra removed by `exclude_xrays`.
    excluded_xray_energy: f64,
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
    // A repeated abscissa (ENDF's discontinuity idiom) is a legal zero-width segment.
    if x1 == x0 && a == x0 && b == x0 {
        return Ok((0.0, 0.0));
    }
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

/// The nuclide's grouped photon spectrum scaled to its mean electromagnetic energy. With
/// `exclude_xrays` the STYP 9 content counts toward the evaluated total, and so toward the
/// scale, but is left out of the groups, lines and source totals.
fn shape(nu: &Nuclide, boundaries: &[f64], exclude_xrays: bool) -> Result<Option<Shape>, String> {
    let ng = boundaries.len() - 1;
    let mut group_count = vec![0.0; ng];
    let mut group_moment = vec![0.0; ng];
    let mut lines = Vec::new();
    let mut raw_count = 0.0;
    let mut raw_energy = 0.0;
    let mut kept_count = 0.0;
    let mut kept_energy = 0.0;
    let mut xray_energy = 0.0;
    let mut under_count = 0.0;
    let mut over_count = 0.0;
    let mut under_energy = 0.0;
    let mut over_energy = 0.0;
    for spectrum in nu
        .spectra
        .iter()
        .filter(|s| s.styp.round() as i32 == 0 || s.styp.round() as i32 == 9)
    {
        let removed = exclude_xrays && spectrum.styp.round() as i32 == 9;
        for d in &spectrum.discrete {
            let y = spectrum.fd * d.intensity;
            if y <= 0.0 || d.energy < 0.0 {
                continue;
            }
            raw_count += y;
            raw_energy += y * d.energy;
            if removed {
                xray_energy += y * d.energy;
                continue;
            }
            kept_count += y;
            kept_energy += y * d.energy;
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
            if removed {
                let mut discarded_count = vec![0.0; ng];
                let mut discarded_moment = vec![0.0; ng];
                let (n, e, ..) = continuous_moments(
                    c,
                    boundaries,
                    &mut discarded_count,
                    &mut discarded_moment,
                    spectrum.fc,
                )?;
                raw_count += n;
                raw_energy += e;
                xray_energy += e;
                continue;
            }
            let (n, e, un, ue, on, oe) = continuous_moments(
                c,
                boundaries,
                &mut group_count,
                &mut group_moment,
                spectrum.fc,
            )?;
            raw_count += n;
            raw_energy += e;
            kept_count += n;
            kept_energy += e;
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
        source_count: kept_count * scale,
        source_energy: kept_energy * scale,
        scale,
        under_count: under_count * scale,
        over_count: over_count * scale,
        under_energy: under_energy * scale,
        over_energy: over_energy * scale,
        excluded_xray_energy: xray_energy * scale,
    }))
}

/// Exponent `a` of the estimated photon-number spectrum `n(E) ~ exp(-a E / E_max)`
/// (FISPACT-II `SPEK`).
pub const ESTIMATE_EXPONENT: f64 = 14.0;

/// `E_max` (eV) for beta-plus, electron-capture and every other decay mode, and the fallback
/// for a beta-minus record without a positive mean light-particle energy.
pub const ESTIMATE_DEFAULT_E_MAX_EV: f64 = 5.0e6;

/// Description of the estimator, written to the certificate and each step's block.
pub const ESTIMATE_MODEL_DESCRIPTION: &str = "photon-number density n(E) = C exp(-a E / E_max) on (0, E_max], a = 14, integrated exactly into each group and normalised to the nuclide's mean electromagnetic energy; E_max by the decay mode with the largest summed branching ratio (beta-minus 2 x mean light-particle energy, beta-plus/EC 5 MeV, IT mean EM energy, other 5 MeV; ties in that order), capped by the largest Q-value of the modes with positive branching when one is positive; dose bounds from the per-group dose per unit power over the groups below the energy cap";

/// Decay-mode class that sets `E_max`: the first RTYP digit, with RTYP 10 (unknown origin) as other.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModeClass {
    BetaMinus,
    BetaPlusEc,
    IsomericTransition,
    Other,
}

impl ModeClass {
    pub fn label(self) -> &'static str {
        match self {
            Self::BetaMinus => "beta-minus",
            Self::BetaPlusEc => "beta-plus/EC",
            Self::IsomericTransition => "isomeric-transition",
            Self::Other => "other",
        }
    }
}

fn mode_class(rtyp: f64) -> ModeClass {
    if rtyp.trunc() == 10.0 {
        return ModeClass::Other;
    }
    match rtyp_digits(rtyp).first() {
        Some(1) => ModeClass::BetaMinus,
        Some(2) => ModeClass::BetaPlusEc,
        Some(3) => ModeClass::IsomericTransition,
        _ => ModeClass::Other,
    }
}

/// How `E_max` of one nuclide's estimated spectrum was chosen.
#[derive(Clone, Debug)]
pub struct EstimateModel {
    pub mode: ModeClass,
    /// `E_max` by the mode rule, before the Q-value cap.
    pub e_max_manual_eV: f64,
    pub e_max_eV: f64,
    /// Largest Q-value among the modes with positive branching, when one is positive.
    pub q_cap_eV: Option<f64>,
}

/// The estimator's `E_max` rule for a nuclide with a positive mean electromagnetic energy;
/// `None` otherwise. Modes count only with a finite positive branching ratio.
pub fn estimate_model(nu: &Nuclide) -> Option<EstimateModel> {
    let e_em = nu.e_em();
    if !(e_em.is_finite() && e_em > 0.0) {
        return None;
    }
    let mut sums = [0.0_f64; 4];
    let mut q_max = 0.0_f64;
    for m in &nu.modes {
        if !(m.br.is_finite() && m.br > 0.0) {
            continue;
        }
        let slot = match mode_class(m.rtyp) {
            ModeClass::BetaMinus => 0,
            ModeClass::BetaPlusEc => 1,
            ModeClass::IsomericTransition => 2,
            ModeClass::Other => 3,
        };
        sums[slot] += m.br;
        if m.q.is_finite() && m.q > q_max {
            q_max = m.q;
        }
    }
    let classes = [
        ModeClass::BetaMinus,
        ModeClass::BetaPlusEc,
        ModeClass::IsomericTransition,
        ModeClass::Other,
    ];
    let mut best = 3;
    let mut best_sum = 0.0;
    for (slot, &sum) in sums.iter().enumerate() {
        if sum > best_sum {
            best = slot;
            best_sum = sum;
        }
    }
    let mode = classes[best];
    let manual = match mode {
        ModeClass::BetaMinus => {
            let twice_light = 2.0 * nu.e_light();
            if twice_light.is_finite() && twice_light > 0.0 {
                twice_light
            } else {
                ESTIMATE_DEFAULT_E_MAX_EV
            }
        }
        ModeClass::IsomericTransition => e_em,
        ModeClass::BetaPlusEc | ModeClass::Other => ESTIMATE_DEFAULT_E_MAX_EV,
    };
    let q_cap_eV = (q_max > 0.0).then_some(q_max);
    Some(EstimateModel {
        mode,
        e_max_manual_eV: manual,
        e_max_eV: q_cap_eV.map_or(manual, |q| manual.min(q)),
        q_cap_eV,
    })
}

/// Photons (per unit `c`) and energy (eV per unit `c`) of `c exp(-k E)` between `lo` and `hi`,
/// from the closed form of the exponential integral. `expm1` keeps narrow groups exact.
fn exp_moments(c: f64, k: f64, lo: f64, hi: f64) -> (f64, f64) {
    let d = hi - lo;
    let decay = (-k * d).exp();
    let u = -(-k * d).exp_m1();
    let w = c * (-k * lo).exp();
    (w * u / k, w * ((lo + 1.0 / k) * u - d * decay) / k)
}

/// The estimated spectrum of one nuclide as a `Shape` of the given group structure: photons per
/// decay of `C exp(-a E / E_max)` on (0, E_max], with `C` set so the energy is the nuclide's
/// mean electromagnetic energy. Photons outside the structure are counted as under or overflow.
fn estimate_shape(
    nu: &Nuclide,
    model: &EstimateModel,
    boundaries: &[f64],
) -> Result<Shape, String> {
    let ng = boundaries.len() - 1;
    let k = ESTIMATE_EXPONENT / model.e_max_eV;
    let c = nu.e_em() / exp_moments(1.0, k, 0.0, model.e_max_eV).1;
    let mut group_count = vec![0.0; ng];
    let mut group_moment = vec![0.0; ng];
    let (under_count, under_energy, over_count, over_energy) = add_to_groups(
        boundaries,
        &mut group_count,
        &mut group_moment,
        0.0,
        model.e_max_eV,
        |lo, hi| Ok(exp_moments(c, k, lo, hi)),
    )?;
    let (count, energy) = exp_moments(c, k, 0.0, model.e_max_eV);
    Ok(Shape {
        lines: Vec::new(),
        group_count,
        group_moment,
        raw_count: count,
        raw_energy: energy,
        source_count: count,
        source_energy: energy,
        scale: 1.0,
        under_count,
        over_count,
        under_energy,
        over_energy,
        excluded_xray_energy: 0.0,
    })
}

/// One group of a per-decay grouped spectrum.
#[derive(Clone, Debug, Serialize)]
pub struct SpectrumGroup {
    pub low_eV: f64,
    pub high_eV: f64,
    pub centroid_eV: f64,
    pub photons_per_decay: f64,
    pub energy_eV_per_decay: f64,
}

/// A nuclide's grouped photon spectrum per decay, evaluated or estimated.
#[derive(Clone, Debug, Serialize)]
pub struct GroupedSpectrum {
    pub groups: Vec<SpectrumGroup>,
    pub photons_per_decay: f64,
    pub energy_eV_per_decay: f64,
    pub below_structure_photons_per_decay: f64,
    pub below_structure_energy_eV_per_decay: f64,
    pub above_structure_photons_per_decay: f64,
    pub above_structure_energy_eV_per_decay: f64,
}

fn grouped_spectrum(sh: &Shape, boundaries: &[f64]) -> GroupedSpectrum {
    let groups = (0..sh.group_count.len())
        .map(|g| SpectrumGroup {
            low_eV: boundaries[g],
            high_eV: boundaries[g + 1],
            centroid_eV: if sh.group_count[g] > 0.0 {
                sh.group_moment[g] / sh.group_count[g]
            } else {
                0.0
            },
            photons_per_decay: sh.group_count[g],
            energy_eV_per_decay: sh.group_moment[g],
        })
        .collect();
    GroupedSpectrum {
        groups,
        photons_per_decay: sh.source_count,
        energy_eV_per_decay: sh.source_energy,
        below_structure_photons_per_decay: sh.under_count,
        below_structure_energy_eV_per_decay: sh.under_energy,
        above_structure_photons_per_decay: sh.over_count,
        above_structure_energy_eV_per_decay: sh.over_energy,
    }
}

/// The estimator's grouped spectrum for `nu`, with the `E_max` choice; `None` without a
/// positive mean electromagnetic energy. For `actinv inspect decay --estimated-spectrum`.
pub fn estimated_grouped_spectrum(
    nu: &Nuclide,
    boundaries: &[f64],
) -> Result<Option<(EstimateModel, GroupedSpectrum)>, String> {
    if boundaries.len() < 2 || boundaries.windows(2).any(|w| w[1] <= w[0]) {
        return Err("photon group boundaries must be strictly increasing".into());
    }
    let Some(model) = estimate_model(nu) else {
        return Ok(None);
    };
    let sh = estimate_shape(nu, &model, boundaries)?;
    Ok(Some((model, grouped_spectrum(&sh, boundaries))))
}

/// The evaluated STYP 0 and 9 spectrum of `nu`, grouped and scaled to its mean electromagnetic
/// energy as the photon source does; `None` when the record has no such content.
pub fn evaluated_grouped_spectrum(
    nu: &Nuclide,
    boundaries: &[f64],
) -> Result<Option<GroupedSpectrum>, String> {
    if boundaries.len() < 2 || boundaries.windows(2).any(|w| w[1] <= w[0]) {
        return Err("photon group boundaries must be strictly increasing".into());
    }
    Ok(shape(nu, boundaries, false)?.map(|sh| grouped_spectrum(&sh, boundaries)))
}

/// The energy intervals a nuclide's photons can occupy for the dose bounds: each group with its
/// lower edge below `e_cap`, clipped to `e_cap`.
pub fn allowed_intervals(boundaries: &[f64], e_cap: f64) -> Vec<[f64; 2]> {
    boundaries
        .windows(2)
        .filter(|w| w[0] < e_cap)
        .map(|w| [w[0], w[1].min(e_cap)])
        .collect()
}

/// Ascending knots of `curves` strictly inside `(a, b)`, with `a` and `b` as the ends.
fn split_points(curves: &[&ResponseCurve], a: f64, b: f64) -> Vec<f64> {
    let mut points = vec![a, b];
    for curve in curves {
        points.extend(curve.energy_eV.iter().copied().filter(|x| *x > a && *x < b));
    }
    points.sort_by(f64::total_cmp);
    points.dedup();
    points
}

/// The log-log power law `y = c E^p` that `curve_value` follows on the sub-interval `[a, b]`,
/// which must lie between two knots of the curve. `None` outside the tabulated range. At an
/// absorption edge (a repeated energy) this is the one-sided law of that sub-interval.
fn power_law_on(curve: &ResponseCurve, a: f64, b: f64) -> Option<(f64, f64)> {
    let mid = 0.5 * (a + b);
    let energies = &curve.energy_eV;
    if mid < energies[0] || mid > *energies.last()? {
        return None;
    }
    let upper = energies.partition_point(|x| *x <= mid);
    if upper == 0 || upper == energies.len() {
        return None;
    }
    let (x0, x1) = (energies[upper - 1], energies[upper]);
    let (y0, y1) = (curve.values_cm2_g[upper - 1], curve.values_cm2_g[upper]);
    let p = (y1 / y0).ln() / (x1 / x0).ln();
    Some((y0 / x0.powf(p), p))
}

/// Smallest and largest value of `f` over `candidates`.
fn extent(candidates: &[f64]) -> (f64, f64) {
    candidates
        .iter()
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| {
            (lo.min(v), hi.max(v))
        })
}

/// Candidate energies for the extrema of `ln(num) - ln(den)` with `num = c_n E^p_n` and
/// `den = sum_j m_j E^p_j` on `[s, t]`: the ends and, where the slope changes sign, the
/// stationary point. With `w(E)` the `m_j E^p_j`-weighted mean of the `p_j`, the slope in `ln E`
/// is `p_n - w(E)`; `w` rises with `E` (its slope is the weighted variance of the `p_j`), so
/// `ln rho` is concave and has at most one interior maximum, found by bisection.
fn power_ratio_candidates(pn: f64, den: &[(f64, f64)], s: f64, t: f64) -> Vec<f64> {
    let slope = |e: f64| {
        let (mut total, mut weighted) = (0.0, 0.0);
        for &(m, p) in den {
            let term = m * e.powf(p);
            total += term;
            weighted += term * p;
        }
        pn - weighted / total
    };
    let mut candidates = vec![s, t];
    if slope(s) > 0.0 && slope(t) < 0.0 {
        let (mut lo, mut hi) = (s, t);
        for _ in 0..200 {
            let mid = (lo * hi).sqrt();
            if slope(mid) > 0.0 {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        candidates.push((lo * hi).sqrt());
    }
    candidates
}

/// Exact smallest and largest contact-proxy dose per unit power (Gy/h per W/g) of a photon at
/// any energy in `[a, b]`, `a` at or above the response's lowest energy: `(B/2)(mu_air/mu_mat)`
/// with the same log-log curves as the proxy. The interval is split at every knot of the air
/// and element curves; on each piece `mu_air` is a power law and `mu_mat` a sum of power laws
/// (see `power_ratio_candidates`). `None` when a curve or element is missing or the interval
/// leaves the tabulated range.
fn contact_extrema(
    response: &PhotonResponse,
    fractions: &BTreeMap<String, f64>,
    build_up: f64,
    a: f64,
    b: f64,
) -> Option<(f64, f64)> {
    let mut curves = vec![&response.air_mass_energy_absorption];
    for element in fractions.keys() {
        curves.push(response.element_mass_attenuation.get(element)?);
    }
    let constant = (build_up / 2.0) * 1000.0 * 3600.0;
    let mut values = Vec::new();
    let points = split_points(&curves, a, b);
    for w in points.windows(2) {
        let (s, t) = (w[0], w[1]);
        let (c_air, p_air) = power_law_on(curves[0], s, t)?;
        let mut den = Vec::with_capacity(fractions.len());
        for (i, fraction) in fractions.values().enumerate() {
            let (c, p) = power_law_on(curves[i + 1], s, t)?;
            den.push((fraction * c, p));
        }
        for e in power_ratio_candidates(p_air, &den, s, t) {
            let mu_mat: f64 = den.iter().map(|&(m, p)| m * e.powf(p)).sum();
            values.push(constant * c_air * e.powf(p_air) / mu_mat);
        }
    }
    (!values.is_empty() && values.iter().all(|v| v.is_finite())).then(|| extent(&values))
}

/// Candidate values of `f(E) = scale c_q E^q exp(-c E^p)` on `[s, t]`: the ends and the
/// stationary point `E* = (q / (c p))^(1/p)` when it is real and inside. `attenuation` is
/// `Some((c, p))` for the air transmission `exp(-c E^p)`, or `None` without it.
fn point_candidates(
    scale: f64,
    c_q: f64,
    q: f64,
    attenuation: Option<(f64, f64)>,
    s: f64,
    t: f64,
) -> Vec<f64> {
    let f = |e: f64| {
        let transmission = match attenuation {
            Some((c, p)) => (-c * e.powf(p)).exp(),
            None => 1.0,
        };
        scale * c_q * e.powf(q) * transmission
    };
    let mut values = vec![f(s), f(t)];
    if let Some((c, p)) = attenuation {
        if p != 0.0 && c > 0.0 {
            let ratio = q / (c * p);
            if ratio > 0.0 {
                let stationary = ratio.powf(1.0 / p);
                if stationary.is_finite() && stationary > s && stationary < t {
                    values.push(f(stationary));
                }
            }
        }
    }
    values
}

/// Exact smallest and largest point-source dose (Sv/h) of the step's mass per unit power
/// (W/g) of a photon at any energy in `[a, b]`, `a` at or above the lowest tabulated energy of
/// the curves involved: `mu_en(E) T(E) / (4 pi r^2)`, with `T` the air transmission
/// `exp(-mu_rho(E) rho_air 100 r)` when `attenuation` is given. The interval is split at every
/// knot of both curves; on each piece `f = A E^q exp(-c E^p)`, whose extrema lie at the ends or
/// at the stationary point of `point_candidates`.
fn point_extrema(
    response: &PhotonResponse,
    attenuation: Option<&ResponseCurve>,
    options: &PointSourceOptions,
    mass_g: f64,
    a: f64,
    b: f64,
) -> Option<(f64, f64)> {
    let r = options.distance_m;
    let scale = 0.1 / (4.0 * std::f64::consts::PI * r * r) * 3600.0 * mass_g;
    let mut curves = vec![&response.air_mass_energy_absorption];
    curves.extend(attenuation);
    let mut values = Vec::new();
    for w in split_points(&curves, a, b).windows(2) {
        let (s, t) = (w[0], w[1]);
        let (c_q, q) = power_law_on(curves[0], s, t)?;
        let air = match attenuation {
            Some(curve) => {
                let (c_m, p) = power_law_on(curve, s, t)?;
                Some((c_m * options.air_density_g_cm3 * 100.0 * r, p))
            }
            None => None,
        };
        values.extend(point_candidates(scale, c_q, q, air, s, t));
    }
    (!values.is_empty() && values.iter().all(|v| v.is_finite())).then(|| extent(&values))
}

/// A linear dose model: the value per unit power at one energy, and its exact extrema over an
/// interval at or above the response's lowest energy. Both are `None` outside the response.
struct DoseModel<'a> {
    value: &'a dyn Fn(f64) -> Option<f64>,
    extrema: &'a dyn Fn(f64, f64) -> Option<(f64, f64)>,
}

/// The estimated nuclides' dose per unit power, summed over their groups, and its bounds.
struct RangeTerms {
    range: Option<DoseRange>,
    /// Power (W/g) in groups whose centroid is below the response's lowest energy.
    subthreshold_power_W_g: f64,
}

/// Dose of `groups` and its bounds for any spectrum of the same power below `e_cap`.
///
/// The dose model puts each group's power at the group's centroid energy. Whatever the spectrum
/// inside a group, that centroid lies in the group's interval clipped to `e_cap`
/// (`allowed_intervals`), so the dose per unit power is the model's value at some energy in the
/// union of those intervals. The bounds are the power times the exact extrema of that value
/// over the union, with zero below `e_lo` (the response's lowest energy), where the model
/// gives no dose. They hold for every energy-conserving spectrum, and contain the estimate by
/// construction. The estimate takes each group of the estimated spectrum at its centroid.
fn dose_range(groups: &[PhotonGroupOut], e_cap: f64, e_lo: f64, model: &DoseModel) -> RangeTerms {
    let (mut estimate, mut power, mut subthreshold) = (0.0, 0.0, 0.0);
    let (mut lowest, mut highest) = (f64::INFINITY, f64::NEG_INFINITY);
    let mut available = true;
    for g in groups {
        power += g.power_W_g;
        if g.power_W_g > 0.0 {
            if g.centroid_eV < e_lo {
                subthreshold += g.power_W_g;
            } else {
                match (model.value)(g.centroid_eV) {
                    Some(r) => estimate += g.power_W_g * r,
                    None => available = false,
                }
            }
        }
        if g.low_eV < e_cap {
            let (lo, hi) = (g.low_eV, g.high_eV.min(e_cap));
            if lo < e_lo {
                lowest = lowest.min(0.0);
                highest = highest.max(0.0);
            }
            if hi > e_lo {
                match (model.extrema)(lo.max(e_lo), hi) {
                    Some((mn, mx)) => {
                        lowest = lowest.min(mn);
                        highest = highest.max(mx);
                    }
                    None => available = false,
                }
            }
        }
    }
    let range = (available && lowest.is_finite()).then(|| {
        let (lower, upper) = (power * lowest, power * highest);
        debug_assert!(
            lower <= estimate * (1.0 + 1e-9) + 1e-300 && estimate <= upper * (1.0 + 1e-9) + 1e-300,
            "dose estimate {estimate} outside its bounds [{lower}, {upper}]"
        );
        DoseRange {
            estimate,
            lower,
            upper,
            share_of_step_estimate: None,
        }
    });
    RangeTerms {
        range,
        subthreshold_power_W_g: subthreshold,
    }
}

/// An estimated nuclide's dose range, withheld when power lies outside the group structure or
/// the sub-threshold share exceeds the tolerance, like the dose of an evaluated nuclide.
/// Returns the range and the nuclide's sub-threshold power (W/g).
fn nuclide_dose_range(
    n: &EstimatedNuclideOut,
    e_lo: f64,
    model: &DoseModel,
) -> (Option<DoseRange>, f64) {
    let terms = dose_range(&n.groups, n.e_cap_eV, e_lo, model);
    let withheld = n.ungrouped_power_W_g > 1e-9 * n.power_W_g
        || terms.subthreshold_power_W_g > SUBTHRESHOLD_POWER_TOLERANCE * n.power_W_g;
    (
        terms.range.filter(|_| !withheld),
        terms.subthreshold_power_W_g,
    )
}

/// Combine the value from evaluated spectra with the estimated nuclides' ranges. The step's
/// estimate and bounds need every part; `subthreshold` and `total` (same power unit) apply the
/// sub-threshold tolerance to the step as a whole. Fills each nuclide's share of the estimate.
fn combine_dose_bounds(
    represented: Option<f64>,
    ranges: &mut [Option<DoseRange>],
    subthreshold: f64,
    total: f64,
) -> DoseBoundsOut {
    let mut out = DoseBoundsOut {
        represented_only: represented,
        ..DoseBoundsOut::default()
    };
    let mut reasons = Vec::new();
    if represented.is_none() {
        reasons.push("the dose from evaluated spectra is unavailable");
    }
    if ranges.iter().any(|r| r.is_none()) {
        reasons.push("an estimated nuclide's dose range is unavailable");
    }
    if subthreshold > SUBTHRESHOLD_POWER_TOLERANCE * total {
        reasons.push("sub-threshold power exceeds the tolerance");
    }
    let Some(rep) = represented.filter(|_| reasons.is_empty()) else {
        out.unavailable_reason = Some(reasons.join("; "));
        return out;
    };
    let (mut est, mut lower, mut upper) = (0.0, 0.0, 0.0);
    for r in ranges.iter().flatten() {
        est += r.estimate;
        lower += r.lower;
        upper += r.upper;
    }
    let estimate = rep + est;
    out.estimate = Some(estimate);
    out.lower = Some(rep + lower);
    out.upper = Some(rep + upper);
    out.estimated_share = (estimate > 0.0).then(|| est / estimate);
    if estimate > 0.0 {
        for r in ranges.iter_mut().flatten() {
            r.share_of_step_estimate = Some(r.estimate / estimate);
        }
    }
    out
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

/// Largest sub-threshold power share (of the response's total photon power) for which the
/// contact proxy is still reported. Below the response's lowest energy the air/material
/// ratio is not tabulated; in low-Z materials it moves by up to about an order of magnitude,
/// so a share of 1e-4 leaves the proxy low by at most about 1e-3.
const SUBTHRESHOLD_POWER_TOLERANCE: f64 = 1e-4;

/// Lowest energy at which the air curve and every material element curve are all tabulated.
/// Zero (no sub-threshold groups) when an element curve is missing.
fn response_lowest_energy(response: &PhotonResponse, fractions: &BTreeMap<String, f64>) -> f64 {
    let mut e_lo = response.air_mass_energy_absorption.energy_eV[0];
    for element in fractions.keys() {
        match response.element_mass_attenuation.get(element) {
            Some(curve) => e_lo = e_lo.max(curve.energy_eV[0]),
            None => return 0.0,
        }
    }
    e_lo
}

/// One group's share of the contact proxy: `moment_rate` (eV/s/g) at energy `e` through the
/// material, in Gy/h, with the FISPACT B/2 slab factor. `None` where the air or the material
/// curve does not reach `e`.
fn contact_term(
    response: &PhotonResponse,
    fractions: &BTreeMap<String, f64>,
    build_up: f64,
    e: f64,
    moment_rate: f64,
) -> Option<f64> {
    let mu_air = curve_value(&response.air_mass_energy_absorption, e)?;
    let mu_mat = material_mu(response, fractions, e)?;
    // eV/s/g -> Gy/h, with the FISPACT B/2 slab factor.
    Some((build_up / 2.0) * (mu_air / mu_mat) * moment_rate * EV * 1000.0 * 3600.0)
}

/// One nuclide's photon source in the step's groups: photons/s/g and power W/g per group, and
/// the nuclide's total source power W/g (the group sum plus anything outside the structure).
#[derive(Clone, Debug)]
pub struct NuclideGroupSource {
    pub photons_s_g: Vec<f64>,
    pub power_W_g: Vec<f64>,
    pub source_power_W_g: f64,
}

/// The contact proxy of a photon source built from grouped nuclide sources, and of each nuclide.
#[derive(Clone, Debug, Default)]
pub struct GroupedContactProxy {
    pub proxy_Gy_h: Option<f64>,
    /// Each nuclide's proxy; a nuclide with a group the response cannot evaluate is absent.
    pub by_nuclide_Gy_h: BTreeMap<String, f64>,
    /// Why `proxy_Gy_h` is `None`.
    pub unavailable_reason: Option<String>,
}

/// The contact proxy `source_for_step` reports, for nuclide sources that were summed outside a
/// run (a composition's photon source is linear in the pure elements' sources) and evaluated at
/// `fractions`, the material's mass fractions. The terms, the response's lowest energy, the
/// sub-threshold share and the exclusions are those of `source_for_step_with`.
/// `ungrouped_power_W_g` is the power outside the group structure, which withholds the proxy.
pub fn contact_proxy_of_groups(
    response: &PhotonResponse,
    fractions: &BTreeMap<String, f64>,
    build_up: f64,
    nuclides: &BTreeMap<String, NuclideGroupSource>,
    ungrouped_power_W_g: f64,
) -> GroupedContactProxy {
    let mut out = GroupedContactProxy::default();
    let missing: Vec<&str> = fractions
        .keys()
        .filter(|e| !response.element_mass_attenuation.contains_key(*e))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        out.unavailable_reason = Some(format!(
            "the photon response has no attenuation curve for {}",
            missing.join(", ")
        ));
        return out;
    }
    let response_e_lo = response_lowest_energy(response, fractions);
    let mut contact_total = 0.0;
    let mut excluded = ungrouped_power_W_g > 0.0;
    let mut total_subthreshold = 0.0;
    let mut response_total_power = 0.0;
    for (name, source) in nuclides {
        response_total_power += source.source_power_W_g / EV;
        let mut contact_nuclide = 0.0;
        let mut contact_ok = true;
        for (count, power) in source.photons_s_g.iter().zip(&source.power_W_g) {
            let moment_rate = power / EV;
            if moment_rate <= 0.0 {
                continue;
            }
            let e = moment_rate / count;
            if e < response_e_lo {
                total_subthreshold += moment_rate;
                continue;
            }
            match contact_term(response, fractions, build_up, e, moment_rate) {
                Some(term) => contact_nuclide += term,
                None => {
                    excluded = true;
                    contact_ok = false;
                }
            }
        }
        if contact_ok {
            contact_total += contact_nuclide;
            out.by_nuclide_Gy_h.insert(name.clone(), contact_nuclide);
        }
    }
    if excluded {
        out.unavailable_reason = Some(
            "photon power lies outside the group structure or above the response's highest energy"
                .into(),
        );
    } else if total_subthreshold > SUBTHRESHOLD_POWER_TOLERANCE * response_total_power {
        out.unavailable_reason = Some(format!(
            "photon power below the response's lowest energy exceeds {SUBTHRESHOLD_POWER_TOLERANCE:e} of the total"
        ));
    } else {
        out.proxy_Gy_h = Some(contact_total);
    }
    out
}

/// Opt-in treatments of the decay records' photon content. The default changes nothing.
#[derive(Clone, Copy, Debug, Default)]
pub struct SourceModel {
    /// Estimate the spectrum of a nuclide that has a mean electromagnetic energy and no photon
    /// spectrum (`photon.missing_spectra: "estimate"`) instead of booking its power unrepresented.
    pub estimate_missing: bool,
    /// Remove STYP 9 (X-ray) content after scaling to the mean electromagnetic energy.
    pub exclude_xrays: bool,
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
    source_for_step_with(
        active,
        boundaries,
        group_structure,
        mass_g,
        response,
        material_mass_fractions,
        material_response_complete,
        build_up,
        gamma_cutoff_eV,
        &SourceModel::default(),
    )
}

/// `source_for_step` with the opt-in treatments of `model`.
#[allow(clippy::too_many_arguments)]
pub fn source_for_step_with(
    active: &[(&str, &Nuclide, f64)],
    boundaries: &[f64],
    group_structure: &str,
    mass_g: f64,
    response: Option<&PhotonResponse>,
    material_mass_fractions: &BTreeMap<String, f64>,
    material_response_complete: bool,
    build_up: f64,
    gamma_cutoff_eV: f64,
    model: &SourceModel,
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
    let mut estimated_power = 0.0;
    let mut excluded_xray_power = 0.0;
    let mut estimated_nuclides: Vec<EstimatedNuclideOut> = Vec::new();
    let mut contact_total = 0.0;
    let mut contact_available = response.is_some() && material_response_complete;
    let mut response_included_power = 0.0;
    let mut response_total_power = 0.0;
    let mut total_subthreshold = 0.0;
    let response_e_lo = match response {
        Some(r) if material_response_complete => response_lowest_energy(r, material_mass_fractions),
        _ => 0.0,
    };

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
        let Some(sh) = shape(nu, boundaries, model.exclude_xrays)? else {
            let estimate = if model.estimate_missing && expected_power > 0.0 {
                estimate_model(nu)
            } else {
                None
            };
            if let Some(em) = estimate {
                // The estimate joins the step source like an evaluated spectrum; its dose
                // range is reported apart, never inside the evaluated nuclides' totals.
                let est = estimate_shape(nu, &em, boundaries)?;
                diag.group_underflow_photons_s_g += activity * est.under_count;
                diag.group_overflow_photons_s_g += activity * est.over_count;
                diag.group_underflow_power_W_g += activity * est.under_energy * EV;
                diag.group_overflow_power_W_g += activity * est.over_energy * EV;
                let mut est_groups = Vec::with_capacity(ng);
                for g in 0..ng {
                    let rate = activity * est.group_count[g];
                    let moment_rate = activity * est.group_moment[g];
                    aggregate_count[g] += rate;
                    aggregate_moment[g] += moment_rate;
                    est_groups.push(group_out(
                        boundaries[g],
                        boundaries[g + 1],
                        rate,
                        moment_rate,
                        mass_g,
                    ));
                }
                estimated_power += expected_power;
                diag.nuclides_with_estimated_photon_spectrum
                    .push(EstimatedSpectrumDiagnostic {
                        nuclide: name.into(),
                        activity_Bq_g: activity,
                        estimated_power_W_g: expected_power,
                        decay_mode: em.mode.label().into(),
                        e_max_manual_eV: em.e_max_manual_eV,
                        e_max_eV: em.e_max_eV,
                        q_cap_eV: em.q_cap_eV,
                    });
                estimated_nuclides.push(EstimatedNuclideOut {
                    nuclide: name.into(),
                    activity_Bq_g: activity,
                    mean_em_energy_eV_per_decay: nu.e_em(),
                    decay_mode: em.mode.label().into(),
                    e_max_manual_eV: em.e_max_manual_eV,
                    e_max_eV: em.e_max_eV,
                    q_cap_eV: em.q_cap_eV,
                    e_cap_eV: em.q_cap_eV.unwrap_or_else(|| *boundaries.last().unwrap()),
                    photons_per_decay: est.source_count,
                    photons_s_g: activity * est.source_count,
                    power_W_g: expected_power,
                    ungrouped_power_W_g: activity * (est.under_energy + est.over_energy) * EV,
                    groups: est_groups,
                    contact_gamma_air_dose_proxy_Gy_h: None,
                    point_source_gamma_air_dose_Sv_h: None,
                });
            } else if expected_power > 0.0 {
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
        let mut nuclide_subthreshold = 0.0;
        let mut nuclide_response_power = 0.0;
        if let Some(r) = response {
            response_total_power += activity * sh.source_energy;
            nuclide_response_power = activity * sh.source_energy;
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
                if e < response_e_lo {
                    nuclide_subthreshold += moment_rate;
                    diag.response_subthreshold_power_W_g += moment_rate * EV;
                    continue;
                }
                match contact_term(r, material_mass_fractions, build_up, e, moment_rate) {
                    Some(term) => {
                        contact_nuclide += term;
                        response_included_power += moment_rate;
                    }
                    None => {
                        diag.response_excluded_power_W_g += moment_rate * EV;
                        contact_ok = false;
                    }
                }
            }
        }
        total_subthreshold += nuclide_subthreshold;
        if contact_ok {
            contact_total += contact_nuclide;
        }
        // A nuclide's own share above the tolerance withholds its proxy, not its covered
        // contribution to the total (which is governed by the total share).
        let nuclide_share_ok =
            nuclide_subthreshold <= SUBTHRESHOLD_POWER_TOLERANCE * nuclide_response_power;
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
            contact_gamma_air_dose_proxy_Gy_h: if contact_ok && nuclide_share_ok {
                Some(contact_nuclide)
            } else {
                None
            },
            point_source_gamma_air_dose_Sv_h: None,
            excluded_xray_power_W_g: model
                .exclude_xrays
                .then_some(activity * sh.excluded_xray_energy * EV),
            lines: n_lines,
            groups: n_groups,
        });
        excluded_xray_power += activity * sh.excluded_xray_energy * EV;
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
    // `source_power` already includes the under/overflow power once.
    let represented = source_power.max(0.0);
    let denom = represented + missing_power;
    let coverage = if response_total_power > 0.0 {
        Some(response_included_power / response_total_power)
    } else {
        response.map(|_| 1.0)
    };
    let contact_proxy = if contact_available
        && diag.response_excluded_power_W_g == 0.0
        && total_subthreshold <= SUBTHRESHOLD_POWER_TOLERANCE * response_total_power
    {
        Some(contact_total)
    } else {
        None
    };
    let estimated_spectra = model.estimate_missing.then(|| {
        // Contact proxy per unit power at one energy (Gy/h per W/g), the proxy's own formula.
        let contact_rho = |e: f64| -> Option<f64> {
            let r = response.filter(|_| material_response_complete)?;
            let mu_air = curve_value(&r.air_mass_energy_absorption, e)?;
            let mu_mat = material_mu(r, material_mass_fractions, e)?;
            Some((build_up / 2.0) * (mu_air / mu_mat) * 1000.0 * 3600.0)
        };
        let contact_extrema_fn = |a: f64, b: f64| -> Option<(f64, f64)> {
            let r = response.filter(|_| material_response_complete)?;
            contact_extrema(r, material_mass_fractions, build_up, a, b)
        };
        let dose_model = DoseModel {
            value: &contact_rho,
            extrema: &contact_extrema_fn,
        };
        let mut ranges = Vec::with_capacity(estimated_nuclides.len());
        let mut subthreshold = total_subthreshold * EV;
        let mut total_power = response_total_power * EV;
        for n in &estimated_nuclides {
            let (range, sub) = nuclide_dose_range(n, response_e_lo, &dose_model);
            ranges.push(range);
            subthreshold += sub;
            total_power += n.power_W_g;
        }
        let contact = combine_dose_bounds(contact_proxy, &mut ranges, subthreshold, total_power);
        for (n, range) in estimated_nuclides.iter_mut().zip(ranges) {
            n.contact_gamma_air_dose_proxy_Gy_h = range;
        }
        EstimatedSpectraOut {
            model: ESTIMATE_MODEL_DESCRIPTION.into(),
            exponent_a: ESTIMATE_EXPONENT,
            contact_gamma_air_dose_proxy_Gy_h: contact,
            point_source_gamma_air_dose_Sv_h: None,
            by_nuclide: std::mem::take(&mut estimated_nuclides),
        }
    });
    if model.estimate_missing {
        diag.estimated_gamma_power_W_g = estimated_power;
    }
    diag.excluded_xray_power_W_g = excluded_xray_power;
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
            contact_gamma_air_dose_proxy_Gy_h: contact_proxy,
            dose_response_power_coverage: coverage,
            dose_response_subthreshold_power_W_g: diag.response_subthreshold_power_W_g,
            point_source_dose: None,
            bremsstrahlung: None,
            estimated_gamma_power_W_g: model.estimate_missing.then_some(estimated_power),
            excluded_xray_power_W_g: model.exclude_xrays.then_some(excluded_xray_power),
            estimated_spectra,
        },
        diag,
    ))
}

/// The point-source dose of one set of groups, with the power it could not evaluate.
struct PointDoseTerms {
    dose_Sv_h: Option<f64>,
    subthreshold_W: f64,
    excluded_W: f64,
}

/// FISPACT-II manual Eq. (63) over `groups`, each taken at its centroid energy:
/// `D = C sum_i mu_a(E_i) exp(-mu(E_i) r) m_s S(E_i) / (4 pi r^2)`. There is no build-up factor.
/// `reference_power_W_g` is everything the groups' owner emits per gram, including power
/// outside the group structure, which can never reach the sum and withholds the dose.
fn point_source_dose(
    groups: &[PhotonGroupOut],
    reference_power_W_g: f64,
    mass_g: f64,
    response: &PhotonResponse,
    options: &PointSourceOptions,
) -> Result<PointDoseTerms, String> {
    let attenuation = if options.air_attenuation {
        Some(response.air_mass_attenuation.as_ref().ok_or(
            "photon.point_source.air_attenuation needs an air_mass_attenuation curve in the \
             photon response (build it with scripts/build_photon_response.py --air-attenuation) \
             or air_attenuation: false",
        )?)
    } else {
        None
    };
    let mut e_lo = response.air_mass_energy_absorption.energy_eV[0];
    if let Some(curve) = attenuation {
        e_lo = e_lo.max(curve.energy_eV[0]);
    }
    let r = options.distance_m;
    let (mut dose, mut subthreshold, mut excluded) = (0.0, 0.0, 0.0);
    let mut grouped_power_W_g = 0.0;
    for g in groups {
        grouped_power_W_g += g.power_W_g;
        let power_W = g.power_W_g * mass_g;
        if power_W <= 0.0 {
            continue;
        }
        let e = g.centroid_eV;
        if e < e_lo {
            subthreshold += power_W;
            continue;
        }
        let Some(mu_en) = curve_value(&response.air_mass_energy_absorption, e) else {
            excluded += power_W;
            continue;
        };
        let transmission = match attenuation {
            None => 1.0,
            Some(curve) => match curve_value(curve, e) {
                // mu/rho [cm2/g] * rho [g/cm3] = mu [1/cm]; 100 cm per m.
                Some(mu_rho) => (-mu_rho * options.air_density_g_cm3 * 100.0 * r).exp(),
                None => {
                    excluded += power_W;
                    continue;
                }
            },
        };
        // mu_en: cm2/g -> 0.1 m2/kg; power in W = J/s, so the sum is Gy/s; x 3600 for Gy/h.
        dose +=
            mu_en * 0.1 * transmission * power_W / (4.0 * std::f64::consts::PI * r * r) * 3600.0;
    }
    let ungrouped_W_g = (reference_power_W_g - grouped_power_W_g).max(0.0);
    if ungrouped_W_g > 1e-9 * reference_power_W_g {
        excluded += ungrouped_W_g * mass_g;
    }
    let total_W = reference_power_W_g * mass_g;
    let complete = excluded == 0.0
        && subthreshold <= SUBTHRESHOLD_POWER_TOLERANCE * total_W
        && dose.is_finite();
    Ok(PointDoseTerms {
        dose_Sv_h: complete.then_some(dose),
        subthreshold_W: subthreshold,
        excluded_W: excluded,
    })
}

/// With `photon.missing_spectra: "estimate"`, the point-source dose range of each estimated
/// nuclide and of the step: the dose from evaluated spectra alone (rebuilt from the evaluated
/// nuclides' groups), the estimate and the bounds. The dose model is `point_source_dose`'s:
/// group energies at their centroids, response and air transmission by log-log interpolation.
/// Returns the step's estimate (the evaluated nuclides' dose plus each estimated nuclide's own
/// sum over its groups) and, when it is `None`, the reason; `None` without an estimate request.
/// That estimate is the step's point-source dose in estimate mode.
fn add_estimated_point_source_bounds(
    source: &mut PhotonSourceOut,
    response: &PhotonResponse,
    options: &PointSourceOptions,
    mass_g: f64,
) -> Option<(Option<f64>, Option<String>)> {
    if source.estimated_spectra.is_none() || source.boundaries_eV.len() < 2 {
        return None;
    }
    let attenuation = if options.air_attenuation {
        response.air_mass_attenuation.as_ref()
    } else {
        None
    };
    let mut e_lo = response.air_mass_energy_absorption.energy_eV[0];
    if let Some(curve) = attenuation {
        e_lo = e_lo.max(curve.energy_eV[0]);
    }
    let r = options.distance_m;
    // Dose (Sv/h) of the step's mass per unit power (W/g) placed at one energy.
    let rho = |e: f64| -> Option<f64> {
        let mu_en = curve_value(&response.air_mass_energy_absorption, e)?;
        let transmission = match attenuation {
            None => 1.0,
            Some(curve) => (-curve_value(curve, e)? * options.air_density_g_cm3 * 100.0 * r).exp(),
        };
        Some(mu_en * 0.1 * transmission / (4.0 * std::f64::consts::PI * r * r) * 3600.0 * mass_g)
    };
    let extrema = |a: f64, b: f64| point_extrema(response, attenuation, options, mass_g, a, b);
    let dose_model = DoseModel {
        value: &rho,
        extrema: &extrema,
    };
    let ng = source.boundaries_eV.len() - 1;
    let mut rate = vec![0.0; ng];
    let mut moment = vec![0.0; ng];
    let mut reference_W_g = 0.0;
    for n in &source.by_nuclide {
        reference_W_g += n.source_power_W_g;
        for (g, group) in n.groups.iter().enumerate() {
            rate[g] += group.photons_s_g;
            moment[g] += group.power_W_g / EV;
        }
    }
    let evaluated_groups: Vec<_> = (0..ng)
        .map(|g| {
            group_out(
                source.boundaries_eV[g],
                source.boundaries_eV[g + 1],
                rate[g],
                moment[g],
                mass_g,
            )
        })
        .collect();
    let evaluated =
        point_source_dose(&evaluated_groups, reference_W_g, mass_g, response, options).ok();
    let represented = evaluated.as_ref().and_then(|t| t.dose_Sv_h);
    let mut subthreshold_W = evaluated.as_ref().map_or(0.0, |t| t.subthreshold_W);
    let mut total_W = reference_W_g * mass_g;
    let estimated = source.estimated_spectra.as_mut()?;
    let mut ranges = Vec::with_capacity(estimated.by_nuclide.len());
    for n in &estimated.by_nuclide {
        let (range, sub_W_g) = nuclide_dose_range(n, e_lo, &dose_model);
        ranges.push(range);
        subthreshold_W += sub_W_g * mass_g;
        total_W += n.power_W_g * mass_g;
    }
    let bounds = combine_dose_bounds(represented, &mut ranges, subthreshold_W, total_W);
    let outcome = (bounds.estimate, bounds.unavailable_reason.clone());
    estimated.point_source_gamma_air_dose_Sv_h = Some(bounds);
    for (n, range) in estimated.by_nuclide.iter_mut().zip(ranges) {
        n.point_source_gamma_air_dose_Sv_h = range;
    }
    Some(outcome)
}

/// Add the FISPACT-II `DOSE 2` point-source dose to a built step source. `mass_g` is the
/// material mass the source rates were scaled by (`material.mass_g`, 1 g by default). When
/// `bremsstrahlung` has been added to `source` first, its point-source dose is filled too.
/// A distance below `POINT_SOURCE_MIN_DISTANCE_M` or a non-finite or non-positive distance,
/// mass or (with air attenuation) air density gives no dose and says why. The dose is also
/// withheld while decay-record gamma power has no photon spectrum to place it in the groups.
pub fn add_point_source_dose(
    source: &mut PhotonSourceOut,
    response: Option<&PhotonResponse>,
    options: &PointSourceOptions,
    mass_g: f64,
) {
    let mut out = PointSourceDoseOut {
        distance_m: options.distance_m,
        mass_g,
        air_attenuation: options.air_attenuation,
        air_density_g_cm3: options.air_attenuation.then_some(options.air_density_g_cm3),
        gamma_air_dose_Sv_h: None,
        gamma_air_dose_Sv_h_per_g: None,
        subthreshold_power_W: 0.0,
        excluded_power_W: 0.0,
        unavailable_reason: None,
    };
    let unusable =
        if !(options.distance_m.is_finite() && options.distance_m >= POINT_SOURCE_MIN_DISTANCE_M) {
            Some(format!(
                "photon.point_source distance {} m must be finite and at least \
             {POINT_SOURCE_MIN_DISTANCE_M} m",
                options.distance_m
            ))
        } else if !(mass_g.is_finite() && mass_g > 0.0) {
            Some(format!(
                "the source mass {mass_g} g must be finite and positive"
            ))
        } else if options.air_attenuation
            && !(options.air_density_g_cm3.is_finite() && options.air_density_g_cm3 > 0.0)
        {
            Some(format!(
                "the air density {} g/cm3 must be finite and positive",
                options.air_density_g_cm3
            ))
        } else {
            None
        };
    if let Some(reason) = unusable {
        out.unavailable_reason = Some(reason);
        for n in &mut source.by_nuclide {
            n.point_source_gamma_air_dose_Sv_h = None;
        }
        if let Some(b) = source.bremsstrahlung.as_mut() {
            b.point_source_gamma_air_dose_Sv_h = None;
            for n in &mut b.by_nuclide {
                n.point_source_gamma_air_dose_Sv_h = None;
            }
        }
        source.point_source_dose = Some(out);
        return;
    }
    match response {
        None => out.unavailable_reason = Some("no photon.response was supplied".into()),
        Some(r) => {
            match point_source_dose(&source.groups, source.source_power_W_g, mass_g, r, options) {
                Err(reason) => out.unavailable_reason = Some(reason),
                Ok(terms) => {
                    // Gamma power the decay record carries but no spectrum places in a group
                    // never reaches the sum, exactly like power outside the response.
                    let unrepresented_W = source.unrepresented_gamma_power_W_g * mass_g;
                    out.subthreshold_power_W = terms.subthreshold_W;
                    out.excluded_power_W = terms.excluded_W + unrepresented_W;
                    let dose = terms.dose_Sv_h.filter(|_| unrepresented_W <= 0.0);
                    out.gamma_air_dose_Sv_h = dose;
                    out.gamma_air_dose_Sv_h_per_g = dose.map(|d| d / mass_g);
                    let mut reasons = Vec::new();
                    if terms.dose_Sv_h.is_none() {
                        reasons.push(
                            "photon power outside the response's energy range or the group \
                             structure exceeds the tolerance",
                        );
                    }
                    if unrepresented_W > 0.0 {
                        reasons.push(
                            "decay-record gamma power without a photon spectrum is not \
                             represented in the groups",
                        );
                    }
                    if !reasons.is_empty() {
                        out.unavailable_reason = Some(reasons.join("; "));
                    }
                    for n in &mut source.by_nuclide {
                        if let Ok(t) =
                            point_source_dose(&n.groups, n.source_power_W_g, mass_g, r, options)
                        {
                            n.point_source_gamma_air_dose_Sv_h = t.dose_Sv_h;
                        }
                    }
                    // In estimate mode the step's dose is the estimate by definition: the
                    // evaluated groups' dose plus each estimated nuclide at its own centroids,
                    // not one dose of the combined groups. Unavailable together with it.
                    if let Some((estimate, reason)) =
                        add_estimated_point_source_bounds(source, r, options, mass_g)
                    {
                        out.gamma_air_dose_Sv_h = estimate;
                        out.gamma_air_dose_Sv_h_per_g = estimate.map(|d| d / mass_g);
                        if estimate.is_some() {
                            out.unavailable_reason = None;
                        } else if out.unavailable_reason.is_none() {
                            out.unavailable_reason = reason;
                        }
                    }
                    if let Some(b) = source.bremsstrahlung.as_mut() {
                        for n in &mut b.by_nuclide {
                            if let Ok(t) =
                                point_source_dose(&n.groups, n.power_W_g, mass_g, r, options)
                            {
                                n.point_source_gamma_air_dose_Sv_h = t.dose_Sv_h;
                            }
                        }
                        if let Ok(t) = point_source_dose(&b.groups, b.power_W_g, mass_g, r, options)
                        {
                            b.point_source_gamma_air_dose_Sv_h = t.dose_Sv_h;
                        }
                    }
                }
            }
        }
    }
    source.point_source_dose = Some(out);
}

/// `a` of FISPACT-II manual Eq. (65), the bremsstrahlung yield constant, 2.76e-6 per keV.
const BREMSSTRAHLUNG_A_PER_KEV: f64 = 2.76e-6;

/// Photons per electron emitted into `[lo, hi]` keV by a mono-energetic electron of `e0` keV in
/// a matrix of mean atomic number `z`: manual Eq. (66). `lo` must be positive.
fn bremsstrahlung_photons_per_electron(z: f64, e0_keV: f64, lo_keV: f64, hi_keV: f64) -> f64 {
    if e0_keV <= lo_keV {
        0.0
    } else if e0_keV >= hi_keV {
        BREMSSTRAHLUNG_A_PER_KEV * z * (e0_keV * (hi_keV / lo_keV).ln() - (hi_keV - lo_keV))
    } else {
        BREMSSTRAHLUNG_A_PER_KEV * z * (e0_keV * (e0_keV / lo_keV).ln() - (e0_keV - lo_keV))
    }
}

/// Exact energy (keV per electron) the Eq. (65) spectrum puts below `hi` keV, i.e. in a group
/// whose lower edge is 0, where Eq. (66)'s photon number diverges.
fn bremsstrahlung_energy_below_keV(z: f64, e0_keV: f64, hi_keV: f64) -> f64 {
    let top = e0_keV.min(hi_keV);
    BREMSSTRAHLUNG_A_PER_KEV * z * (e0_keV * top - 0.5 * top * top)
}

/// Whether a decay mode's RTYP carries a beta-minus electron: the digit 1 in its decimal
/// spelling (1.0 beta-minus; 1.5 beta-minus then neutron). RTYP 10 (unknown origin) does not.
fn rtyp_has_beta_minus(rtyp: f64) -> bool {
    let spelled = format!("{rtyp}");
    let integer_part = spelled.split('.').next().unwrap_or("");
    integer_part != "10" && spelled.contains('1')
}

/// Beta-minus electrons per decay of the parent: the summed branching ratio of the decay modes
/// whose RTYP carries one. Zero when the record has no such mode.
fn beta_minus_electrons_per_decay(nu: &Nuclide) -> f64 {
    nu.modes
        .iter()
        .filter(|m| rtyp_has_beta_minus(m.rtyp) && m.br.is_finite() && m.br > 0.0)
        .map(|m| m.br)
        .sum()
}

/// The decay record's STYP=1 spectrum average energy (eV): the beta-minus energy per decay of
/// the parent (ENDF list item ER), already weighted by the branching ratio. Divide by
/// `beta_minus_electrons_per_decay` for the mean energy of one electron. `None` when the record
/// carries no such spectrum; the electron/light-particle mean energy is not substituted because
/// it also counts conversion and Auger electrons.
fn mean_beta_energy_eV(nu: &Nuclide) -> Option<f64> {
    let total: f64 = nu
        .spectra
        .iter()
        .filter(|s| s.styp.round() as i32 == 1)
        .map(|s| s.average_energy)
        .sum();
    (total.is_finite() && total > 0.0).then_some(total)
}

/// Mean atomic number of the material, FISPACT-II manual Eq. (68): `Z = sum_j Z_j f_j`
/// with `f_j` the atom fraction of element j. `atoms_per_g` is the composition-resolved
/// material inventory keyed by `(ZA, LISO)`; `unresolved_elements` are material elements
/// without natural-isotope data, which make the mean unavailable rather than approximate.
pub fn material_z_eff(
    atoms_per_g: &BTreeMap<(i32, i32), f64>,
    unresolved_elements: &[String],
) -> Result<f64, String> {
    if !unresolved_elements.is_empty() {
        return Err(format!(
            "material elements without natural-isotope data: {}",
            unresolved_elements.join(", ")
        ));
    }
    let total: f64 = atoms_per_g.values().sum();
    if !(total.is_finite() && total > 0.0) {
        return Err("material has no atoms".into());
    }
    let z_sum: f64 = atoms_per_g
        .iter()
        .map(|(&(za, _), atoms)| f64::from(za / 1000) * atoms)
        .sum();
    Ok(z_sum / total)
}

fn group_out(lo: f64, hi: f64, rate: f64, moment_rate: f64, mass_g: f64) -> PhotonGroupOut {
    PhotonGroupOut {
        low_eV: lo,
        high_eV: hi,
        centroid_eV: if rate > 0.0 { moment_rate / rate } else { 0.0 },
        photons_s_g: rate,
        photons_s: rate * mass_g,
        power_W_g: moment_rate * EV,
        power_W: moment_rate * EV * mass_g,
    }
}

/// Contact (semi-infinite slab) dose of bremsstrahlung `groups` in Gy/h, by the same
/// `B/2 * mu_a/mu_m` formula and sub-threshold rule as the decay-photon proxy.
fn contact_dose_from_groups(
    groups: &[PhotonGroupOut],
    response: Option<&PhotonResponse>,
    material_mass_fractions: &BTreeMap<String, f64>,
    material_response_complete: bool,
    build_up: f64,
) -> Option<f64> {
    let r = response.filter(|_| material_response_complete)?;
    let e_lo = response_lowest_energy(r, material_mass_fractions);
    let total: f64 = groups.iter().map(|g| g.power_W_g).sum();
    let (mut dose, mut subthreshold) = (0.0, 0.0);
    for g in groups {
        if g.power_W_g <= 0.0 {
            continue;
        }
        if g.centroid_eV < e_lo {
            subthreshold += g.power_W_g;
            continue;
        }
        let mu_air = curve_value(&r.air_mass_energy_absorption, g.centroid_eV)?;
        let mu_mat = material_mu(r, material_mass_fractions, g.centroid_eV)?;
        dose += (build_up / 2.0) * (mu_air / mu_mat) * g.power_W_g * 1000.0 * 3600.0;
    }
    (subthreshold <= SUBTHRESHOLD_POWER_TOLERANCE * total).then_some(dose)
}

/// Add the FISPACT-II `BREMSSTRAHLUNG` correction (manual appendix C.7.4) to a built step source
/// as the separate `bremsstrahlung` component. Each requested nuclide with positive activity
/// emits `f` beta-minus electrons per decay (the summed branching ratio of its beta-minus
/// modes), each at the mean beta energy `E0 = ER / f` where ER is the decay record's STYP=1
/// average energy per decay of the parent; Eqs. (66)-(67) give the photons and mean-energy
/// intensity in every group of the source's structure for the material's `z_eff`. A nuclide
/// lacking ER or `f`, or every nuclide when `z_eff` is unavailable, is listed in
/// `missing_inputs` and contributes nothing. The decay-photon fields of `source` are untouched.
#[allow(clippy::too_many_arguments)]
pub fn add_bremsstrahlung(
    source: &mut PhotonSourceOut,
    active: &[(&str, &Nuclide, f64)],
    options: &BremsstrahlungOptions,
    z_eff: &Result<f64, String>,
    mass_g: f64,
    response: Option<&PhotonResponse>,
    material_mass_fractions: &BTreeMap<String, f64>,
    material_response_complete: bool,
    build_up: f64,
) {
    let boundaries = source.boundaries_eV.clone();
    let ng = boundaries.len().saturating_sub(1);
    let mut aggregate_count = vec![0.0; ng];
    let mut aggregate_moment = vec![0.0; ng];
    let mut zero_edge_power = 0.0;
    let mut analytic_power = 0.0;
    let mut by_nuclide = Vec::new();
    let mut missing_inputs = Vec::new();
    for &(name, nu, activity) in active {
        if activity <= 0.0 || nu.lambda() <= 0.0 || !options.nuclides.iter().any(|n| n == name) {
            continue;
        }
        let per_decay_eV = mean_beta_energy_eV(nu);
        let electrons = beta_minus_electrons_per_decay(nu);
        let mut reasons = Vec::new();
        if per_decay_eV.is_none() {
            reasons.push(
                "mean beta-minus energy (the decay record has no STYP=1 spectrum average energy)"
                    .to_string(),
            );
        }
        if electrons <= 0.0 {
            reasons.push(
                "beta-minus electrons per decay (the decay record has no beta-minus decay mode)"
                    .to_string(),
            );
        }
        if let Err(reason) = z_eff {
            reasons.push(format!("material Z_eff: {reason}"));
        }
        let (Some(per_decay_eV), true, Ok(z)) = (per_decay_eV, electrons > 0.0, z_eff) else {
            missing_inputs.push(BremsstrahlungMissingInput {
                nuclide: name.into(),
                missing: reasons.join("; "),
            });
            continue;
        };
        // ER is the beta-minus energy per decay of the parent, already multiplied by the
        // branching ratio: f electrons per decay of E0 = ER / f each.
        let e0_eV = per_decay_eV / electrons;
        let e0_keV = e0_eV / 1000.0;
        // The Eq. (65) total, a Z E0^2 / 2 per electron, that the groups should add up to.
        let analytic_keV = bremsstrahlung_energy_below_keV(*z, e0_keV, e0_keV);
        analytic_power += activity * electrons * analytic_keV * 1000.0 * EV;
        let mut groups = Vec::with_capacity(ng);
        let (mut photons, mut moment_total) = (0.0, 0.0);
        for g in 0..ng {
            let (lo, hi) = (boundaries[g], boundaries[g + 1]);
            if lo <= 0.0 {
                zero_edge_power += activity
                    * electrons
                    * bremsstrahlung_energy_below_keV(*z, e0_keV, hi / 1000.0)
                    * 1000.0
                    * EV;
                groups.push(group_out(lo, hi, 0.0, 0.0, mass_g));
                continue;
            }
            let per_electron =
                bremsstrahlung_photons_per_electron(*z, e0_keV, lo / 1000.0, hi / 1000.0);
            let rate = activity * electrons * per_electron;
            // Eq. (67): the group's energy is its photon number times the bin midpoint.
            let moment_rate = rate * 0.5 * (lo + hi);
            aggregate_count[g] += rate;
            aggregate_moment[g] += moment_rate;
            photons += rate;
            moment_total += moment_rate;
            groups.push(group_out(lo, hi, rate, moment_rate, mass_g));
        }
        let contact = contact_dose_from_groups(
            &groups,
            response,
            material_mass_fractions,
            material_response_complete,
            build_up,
        );
        by_nuclide.push(BremsstrahlungNuclideOut {
            nuclide: name.into(),
            activity_Bq_g: activity,
            electrons_per_decay: electrons,
            mean_beta_energy_eV: e0_eV,
            photons_s_g: photons,
            power_W_g: moment_total * EV,
            contact_gamma_air_dose_proxy_Gy_h: contact,
            point_source_gamma_air_dose_Sv_h: None,
            groups,
        });
    }
    let requested_nuclides_without_activity: Vec<String> = options
        .nuclides
        .iter()
        .filter(|n| {
            !active.iter().any(|&(name, nu, activity)| {
                name == n.as_str() && activity > 0.0 && nu.lambda() > 0.0
            })
        })
        .cloned()
        .collect();
    let groups: Vec<_> = (0..ng)
        .map(|g| {
            group_out(
                boundaries[g],
                boundaries[g + 1],
                aggregate_count[g],
                aggregate_moment[g],
                mass_g,
            )
        })
        .collect();
    let photons: f64 = aggregate_count.iter().sum();
    let power: f64 = aggregate_moment.iter().sum::<f64>() * EV;
    let contact = contact_dose_from_groups(
        &groups,
        response,
        material_mass_fractions,
        material_response_complete,
        build_up,
    );
    source.bremsstrahlung = Some(BremsstrahlungOut {
        model: "fispact-ii-c74-jarvis-mean-beta-energy".into(),
        z_eff: z_eff.as_ref().ok().copied(),
        z_eff_unavailable_reason: z_eff.as_ref().err().cloned(),
        groups,
        by_nuclide,
        photons_s_g: photons,
        photons_s: photons * mass_g,
        power_W_g: power,
        power_W: power * mass_g,
        zero_lower_edge_group_power_W_g: zero_edge_power,
        zero_lower_edge_energy_fraction: (analytic_power > 0.0)
            .then(|| zero_edge_power / analytic_power),
        energy_closure: (analytic_power > 0.0).then(|| (power + zero_edge_power) / analytic_power),
        contact_gamma_air_dose_proxy_Gy_h: contact,
        point_source_gamma_air_dose_Sv_h: None,
        inputs_complete: missing_inputs.is_empty(),
        missing_inputs,
        requested_nuclides_without_activity,
    });
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

/// One mesh cell's contribution to a spatial photon source.
#[derive(Clone, Debug, Serialize)]
pub struct MeshPhotonCell {
    pub id: String,
    pub bounds_cm: [[f64; 2]; 3],
    pub volume_cm3: f64,
    pub strength_photons_s: f64,
    pub energy_eV: Vec<f64>,
    pub weight: Vec<f64>,
}

/// Build one cell's spatial-source fragment from its step photon source.
/// `Ok(None)` means the cell emits no photons in the export group
/// structure and contributes no source entry; other failures are hard
/// errors — the contract never silently drops a non-empty cell.
pub fn mesh_cell_from_source(
    id: String,
    bounds_cm: [[f64; 2]; 3],
    volume_cm3: f64,
    source: &PhotonSourceOut,
) -> Result<Option<MeshPhotonCell>, String> {
    if !(volume_cm3.is_finite() && volume_cm3 > 0.0) {
        return Err(format!("cell '{id}': non-positive volume"));
    }
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
        return Ok(None);
    }
    let omitted = (source.total_photons_s - total).abs();
    if omitted > 1e-12 * source.total_photons_s.abs().max(1.0) {
        return Err(format!(
            "cell '{id}': group structure omits {:.17e} photons/s; export would not conserve source strength",
            source.total_photons_s - total
        ));
    }
    for p in &mut weight {
        *p /= total;
    }
    Ok(Some(MeshPhotonCell {
        id,
        bounds_cm,
        volume_cm3,
        strength_photons_s: total,
        energy_eV: energy,
        weight,
    }))
}

/// Serialize one cell's source fragment: uniform-in-bounds spatial
/// distribution, line/groups energy distribution, strength in photons/s.
fn mesh_cell_fragment(cell: &MeshPhotonCell) -> Result<String, String> {
    let [x, y, z] = cell.bounds_cm;
    if !(x[0] < x[1] && y[0] < y[1] && z[0] < z[1]) {
        return Err(format!("cell '{}': degenerate bounds", cell.id));
    }
    if cell.energy_eV.is_empty() || cell.weight.is_empty() {
        return Err(format!("cell '{}': empty energy distribution", cell.id));
    }
    if !(cell.strength_photons_s.is_finite() && cell.strength_photons_s > 0.0) {
        return Err(format!("cell '{}': non-positive strength", cell.id));
    }
    let es = cell
        .energy_eV
        .iter()
        .map(|v| format!("{v:.17e}"))
        .collect::<Vec<_>>()
        .join(", ");
    let ps = cell
        .weight
        .iter()
        .map(|v| format!("{v:.17e}"))
        .collect::<Vec<_>>()
        .join(", ");
    Ok(format!(
        "sources.append(openmc.IndependentSource(\n    \
         space=openmc.stats.Box([{x0:.17e}, {y0:.17e}, {z0:.17e}], \
         [{x1:.17e}, {y1:.17e}, {z1:.17e}]),\n    \
         energy=openmc.stats.Discrete([{es}], [{ps}]),\n    \
         particle=\"photon\", strength={s:.17e}))  # cell {id}\n",
        x0 = x[0],
        y0 = y[0],
        z0 = z[0],
        x1 = x[1],
        y1 = y[1],
        z1 = z[1],
        s = cell.strength_photons_s,
        id = cell.id,
    ))
}

/// Emit a complete OpenMC spatial-source module: `sources` is a list of
/// per-cell IndependentSources whose `strength` fields carry absolute
/// photons/s (OpenMC normalizes the mixture); the absolute total is
/// recorded as TOTAL_PHOTONS_S.
pub fn export_openmc_mesh(cells: &[MeshPhotonCell]) -> Result<String, String> {
    if cells.is_empty() {
        return Err("mesh result has no photon-bearing cells to export".into());
    }
    let total: f64 = cells.iter().map(|c| c.strength_photons_s).sum();
    if !(total.is_finite() && total > 0.0) {
        return Err("mesh photon source total is zero".into());
    }
    let mut out = String::from(
        "# ACTINV spatial decay-photon source (actinv-export-openmc-mesh-1)\n\
         import openmc\n\nsources = []\n",
    );
    for cell in cells {
        out.push_str(&mesh_cell_fragment(cell)?);
    }
    out.push_str(&format!("TOTAL_PHOTONS_S = {total:.17e}\n"));
    Ok(out)
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
    fn repeated_abscissa_is_a_zero_width_segment() {
        // ENDF writes a discontinuity as two points at one energy; that used to abort
        // the whole step's photon source.
        assert_eq!(
            segment_moments(1.0, 2.0, 1.0, 3.0, 2, 1.0, 1.0),
            Ok((0.0, 0.0))
        );
        assert!(segment_moments(2.0, 1.0, 1.0, 1.0, 2, 1.0, 2.0).is_err());
    }

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

    mod subthreshold {
        use super::super::{
            contact_proxy_of_groups, source_for_step, NuclideGroupSource, PhotonResponse,
            ResponseCurve,
        };
        use crate::run::EV;
        use actinv_data::decay::{DiscreteRadiation, Nuclide, Spectrum};
        use std::collections::BTreeMap;

        // Group 0 holds the sub-keV lines, group 3 lies above the response's top energy.
        const BOUNDS: [f64; 5] = [100.0, 1.0e3, 1.0e5, 1.0e7, 1.0e8];

        fn curve(values: [f64; 4]) -> ResponseCurve {
            ResponseCurve {
                energy_eV: vec![1.0e3, 1.0e4, 1.0e6, 1.0e7],
                values_cm2_g: values.to_vec(),
            }
        }

        /// Air and element "H" are tabulated at 1 keV, 10 keV, 1 MeV and 10 MeV, so a line
        /// at a tabulated energy has an exact response: air/H = 2 at 10 keV, 0.5 at 1 MeV.
        fn response() -> PhotonResponse {
            let mut elements = BTreeMap::new();
            elements.insert("H".to_string(), curve([4500.0, 5.0, 0.06, 0.03]));
            PhotonResponse {
                schema: "actinv-photon-response-1".into(),
                provenance: serde_json::Value::Null,
                air_mass_energy_absorption: curve([3000.0, 10.0, 0.03, 0.02]),
                air_mass_attenuation: None,
                element_mass_attenuation: elements,
            }
        }

        fn fractions(elements: &[&str]) -> BTreeMap<String, f64> {
            let w = 1.0 / elements.len() as f64;
            elements.iter().map(|e| (e.to_string(), w)).collect()
        }

        /// Discrete lines `(energy_eV, intensity)`; the mean EM energy equals the line sum,
        /// so the source scale is one.
        fn nuclide(lines: &[(f64, f64)]) -> Nuclide {
            let discrete = lines
                .iter()
                .map(|&(energy, intensity)| DiscreteRadiation {
                    energy,
                    d_energy: 0.0,
                    rtyp: 0.0,
                    transition_type: 0.0,
                    intensity,
                    d_intensity: 0.0,
                    pair_intensity: 0.0,
                    d_pair_intensity: 0.0,
                    conversion_total: 0.0,
                    d_conversion_total: 0.0,
                    conversion_k: 0.0,
                    d_conversion_k: 0.0,
                    conversion_l: 0.0,
                    d_conversion_l: 0.0,
                })
                .collect();
            Nuclide {
                mat: 0,
                za: 1001,
                awr: 1.0,
                liso: 0,
                nst: 0,
                half_life: 1.0,
                d_half_life: 0.0,
                energies: vec![0.0, 0.0, lines.iter().map(|&(e, i)| e * i).sum()],
                modes: Vec::new(),
                spectra: vec![Spectrum {
                    styp: 0.0,
                    lcon: 0,
                    lcov: 0,
                    fd: 1.0,
                    d_fd: 0.0,
                    average_energy: 0.0,
                    d_average_energy: 0.0,
                    fc: 0.0,
                    d_fc: 0.0,
                    discrete,
                    continuous: None,
                }],
            }
        }

        fn run(
            active: &[(&str, &Nuclide, f64)],
            elements: &[&str],
            complete: bool,
        ) -> (
            super::super::PhotonSourceOut,
            super::super::PhotonDiagnostics,
        ) {
            source_for_step(
                active,
                &BOUNDS,
                "test",
                1.0,
                Some(&response()),
                &fractions(elements),
                complete,
                2.0,
                0.0,
            )
            .unwrap()
        }

        /// Gy/h from `eV/s/g` of photons at a given air/material ratio, with B = 2.
        fn gy_h(ev_per_s_g: f64, ratio: f64) -> f64 {
            ratio * ev_per_s_g * EV * 1000.0 * 3600.0
        }

        fn close(a: f64, b: f64) -> bool {
            (a - b).abs() <= 1e-12 * b.abs().max(1e-300)
        }

        #[test]
        fn small_subthreshold_share_keeps_the_proxy() {
            let n = nuclide(&[(500.0, 1.0e-6), (1.0e4, 1.0), (1.0e6, 1.0)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
            let expected = gy_h(1.0e4, 2.0) + gy_h(1.0e6, 0.5);
            let total = src.contact_gamma_air_dose_proxy_Gy_h.unwrap();
            assert!(close(total, expected), "{total} vs {expected}");
            let own = src.by_nuclide[0].contact_gamma_air_dose_proxy_Gy_h.unwrap();
            assert!(close(own, expected));
            assert!(close(
                src.dose_response_subthreshold_power_W_g,
                500.0e-6 * EV
            ));
            assert_eq!(
                diag.response_subthreshold_power_W_g,
                src.dose_response_subthreshold_power_W_g
            );
            assert_eq!(diag.response_excluded_power_W_g, 0.0);
            assert!(src.dose_response_power_coverage.unwrap() < 1.0);
        }

        /// The grouped sources of a run's nuclides, as a composition's summed source would carry.
        fn grouped(src: &super::super::PhotonSourceOut) -> BTreeMap<String, NuclideGroupSource> {
            src.by_nuclide
                .iter()
                .map(|n| {
                    (
                        n.nuclide.clone(),
                        NuclideGroupSource {
                            photons_s_g: n.groups.iter().map(|g| g.photons_s_g).collect(),
                            power_W_g: n.groups.iter().map(|g| g.power_W_g).collect(),
                            source_power_W_g: n.source_power_W_g,
                        },
                    )
                })
                .collect()
        }

        #[test]
        fn grouped_proxy_is_the_proxy_of_a_run() {
            let cases: [&[(f64, f64)]; 5] = [
                &[(1.0e4, 1.0), (1.0e6, 1.0)],
                &[(500.0, 1.0e-6), (1.0e4, 1.0), (1.0e6, 1.0)],
                &[(500.0, 1.0), (1.0e6, 1.0)],
                &[(500.0, 1.0e-6), (1.0e6, 1.0), (5.0e7, 1.0e-3)],
                &[(500.0, 1.0e-6), (1.0e6, 1.0), (2.0e8, 1.0e-3)],
            ];
            for lines in cases {
                let n = nuclide(lines);
                let (src, _) = run(&[("A", &n, 3.0)], &["H"], true);
                let fractions = fractions(&["H"]);
                let ungrouped = src.ungrouped_power_W_g;
                let got = contact_proxy_of_groups(
                    &response(),
                    &fractions,
                    2.0,
                    &grouped(&src),
                    ungrouped,
                );
                match src.contact_gamma_air_dose_proxy_Gy_h {
                    Some(expected) => {
                        let value = got.proxy_Gy_h.unwrap();
                        assert!(close(value, expected), "{lines:?}: {value} vs {expected}");
                        assert!(got.unavailable_reason.is_none());
                    }
                    None => {
                        assert!(got.proxy_Gy_h.is_none(), "{lines:?}");
                        assert!(got.unavailable_reason.is_some());
                    }
                }
            }
        }

        #[test]
        fn grouped_proxy_without_a_curve_is_unavailable_with_a_reason() {
            let n = nuclide(&[(1.0e6, 1.0)]);
            let (src, _) = run(&[("A", &n, 1.0)], &["H"], true);
            let fractions = fractions(&["H", "Xx"]);
            let got = contact_proxy_of_groups(&response(), &fractions, 2.0, &grouped(&src), 0.0);
            assert!(got.proxy_Gy_h.is_none());
            assert!(got.unavailable_reason.unwrap().contains("Xx"));
        }

        #[test]
        fn large_subthreshold_share_withholds_the_proxy() {
            let n = nuclide(&[(500.0, 1.0), (1.0e6, 1.0)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
            assert!(src.contact_gamma_air_dose_proxy_Gy_h.is_none());
            assert!(src.by_nuclide[0]
                .contact_gamma_air_dose_proxy_Gy_h
                .is_none());
            assert!(close(src.dose_response_subthreshold_power_W_g, 500.0 * EV));
            assert_eq!(diag.response_excluded_power_W_g, 0.0);
        }

        #[test]
        fn nuclide_share_over_tolerance_still_adds_to_a_total_within_tolerance() {
            // A: own share 5e-4 (over 1e-4). The step's share is ~5e-10 (under 1e-4).
            let a = nuclide(&[(500.0, 1.0), (1.0e6, 1.0)]);
            let b = nuclide(&[(1.0e4, 1.0)]);
            let (src, _) = run(&[("A", &a, 1.0e-3), ("B", &b, 1.0e6)], &["H"], true);
            assert!(src.by_nuclide[0]
                .contact_gamma_air_dose_proxy_Gy_h
                .is_none());
            let expected_b = gy_h(1.0e6 * 1.0e4, 2.0);
            let own_b = src.by_nuclide[1].contact_gamma_air_dose_proxy_Gy_h.unwrap();
            assert!(close(own_b, expected_b));
            let expected_total = gy_h(1.0e-3 * 1.0e6, 0.5) + expected_b;
            let total = src.contact_gamma_air_dose_proxy_Gy_h.unwrap();
            assert!(close(total, expected_total), "{total} vs {expected_total}");
        }

        #[test]
        fn subthreshold_with_centroid_above_top_energy_is_refused() {
            let n = nuclide(&[(500.0, 1.0e-6), (1.0e6, 1.0), (5.0e7, 1.0e-3)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
            assert!(src.contact_gamma_air_dose_proxy_Gy_h.is_none());
            assert!(src.by_nuclide[0]
                .contact_gamma_air_dose_proxy_Gy_h
                .is_none());
            assert!(close(diag.response_excluded_power_W_g, 5.0e4 * EV));
            assert!(diag.response_subthreshold_power_W_g > 0.0);
        }

        #[test]
        fn subthreshold_with_missing_element_is_refused() {
            let n = nuclide(&[(500.0, 1.0e-6), (1.0e6, 1.0)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H", "Xx"], false);
            assert!(src.contact_gamma_air_dose_proxy_Gy_h.is_none());
            assert_eq!(diag.response_missing_elements, vec!["Xx".to_string()]);
            assert!(diag.response_excluded_power_W_g > 0.0);
        }

        #[test]
        fn subthreshold_with_group_under_or_overflow_is_refused() {
            for stray in [50.0, 2.0e8] {
                let n = nuclide(&[(500.0, 1.0e-6), (1.0e6, 1.0), (stray, 1.0e-3)]);
                let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
                assert!(src.contact_gamma_air_dose_proxy_Gy_h.is_none(), "{stray}");
                assert!(src.by_nuclide[0]
                    .contact_gamma_air_dose_proxy_Gy_h
                    .is_none());
                assert!(diag.response_excluded_power_W_g > 0.0);
                assert!(diag.response_subthreshold_power_W_g > 0.0);
            }
        }

        #[test]
        fn no_subthreshold_group_leaves_results_and_json_unchanged() {
            let n = nuclide(&[(1.0e4, 1.0), (1.0e6, 1.0)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
            let expected = gy_h(1.0e4, 2.0) + gy_h(1.0e6, 0.5);
            assert!(close(
                src.contact_gamma_air_dose_proxy_Gy_h.unwrap(),
                expected
            ));
            assert!(close(src.dose_response_power_coverage.unwrap(), 1.0));
            assert_eq!(src.dose_response_subthreshold_power_W_g, 0.0);
            assert_eq!(diag.response_subthreshold_power_W_g, 0.0);
            let json = serde_json::to_string(&src).unwrap();
            assert!(!json.contains("dose_response_subthreshold_power_W_g"));
            let diag_json = serde_json::to_string(&diag).unwrap();
            assert!(!diag_json.contains("response_subthreshold_power_W_g"));
        }

        #[test]
        fn subthreshold_field_serializes_when_present_and_reads_back_when_absent() {
            let n = nuclide(&[(500.0, 1.0e-6), (1.0e6, 1.0)]);
            let (src, diag) = run(&[("A", &n, 1.0)], &["H"], true);
            let json = serde_json::to_string(&src).unwrap();
            assert!(json.contains("dose_response_subthreshold_power_W_g"));
            assert!(serde_json::to_string(&diag)
                .unwrap()
                .contains("response_subthreshold_power_W_g"));
            let mut value: serde_json::Value = serde_json::from_str(&json).unwrap();
            value
                .as_object_mut()
                .unwrap()
                .remove("dose_response_subthreshold_power_W_g");
            let back: super::super::PhotonSourceOut = serde_json::from_value(value).unwrap();
            assert_eq!(back.dose_response_subthreshold_power_W_g, 0.0);
        }
    }

    mod parity {
        use super::super::{
            add_bremsstrahlung, add_point_source_dose, material_z_eff, source_for_step,
            PhotonResponse, PhotonSourceOut, ResponseCurve, FISPACT_24_BOUNDARIES_EV,
            STEINER_22_BOUNDARIES_EV,
        };
        use crate::run::EV;
        use crate::spec::{BremsstrahlungOptions, PointSourceOptions};
        use actinv_data::decay::{DiscreteRadiation, Mode, Nuclide, Spectrum};
        use std::collections::BTreeMap;

        // Group 0 is 0.5-1.5 MeV, so a 1 MeV line sits on its midpoint and on a tabulated energy.
        const BOUNDS: [f64; 3] = [5.0e5, 1.5e6, 1.0e8];
        // Manual Eq. (65): a = 2.76e-6 per keV.
        const A: f64 = 2.76e-6;

        fn curve(values: [f64; 4]) -> ResponseCurve {
            ResponseCurve {
                energy_eV: vec![1.0e3, 1.0e4, 1.0e6, 1.0e7],
                values_cm2_g: values.to_vec(),
            }
        }

        /// Air mu_en/rho 0.03 and element "H" mu/rho 0.06 cm2/g at 1 MeV; air mu/rho 0.06 there.
        fn response(with_air_attenuation: bool) -> PhotonResponse {
            let mut elements = BTreeMap::new();
            elements.insert("H".to_string(), curve([4500.0, 5.0, 0.06, 0.03]));
            PhotonResponse {
                schema: "actinv-photon-response-1".into(),
                provenance: serde_json::Value::Null,
                air_mass_energy_absorption: curve([3000.0, 10.0, 0.03, 0.02]),
                air_mass_attenuation: with_air_attenuation
                    .then(|| curve([3100.0, 11.0, 0.06, 0.04])),
                element_mass_attenuation: elements,
            }
        }

        fn fractions() -> BTreeMap<String, f64> {
            BTreeMap::from([("H".to_string(), 1.0)])
        }

        fn line(energy: f64, intensity: f64) -> DiscreteRadiation {
            DiscreteRadiation {
                energy,
                d_energy: 0.0,
                rtyp: 0.0,
                transition_type: 0.0,
                intensity,
                d_intensity: 0.0,
                pair_intensity: 0.0,
                d_pair_intensity: 0.0,
                conversion_total: 0.0,
                d_conversion_total: 0.0,
                conversion_k: 0.0,
                d_conversion_k: 0.0,
                conversion_l: 0.0,
                d_conversion_l: 0.0,
            }
        }

        fn spectrum(styp: f64, average_energy: f64, discrete: Vec<DiscreteRadiation>) -> Spectrum {
            Spectrum {
                styp,
                lcon: 0,
                lcov: 0,
                fd: 1.0,
                d_fd: 0.0,
                average_energy,
                d_average_energy: 0.0,
                fc: 0.0,
                d_fc: 0.0,
                discrete,
                continuous: None,
            }
        }

        /// A decaying nuclide with gamma lines `(energy_eV, intensity)` whose mean EM energy
        /// equals the line sum (source scale one), and a STYP=1 spectrum when `beta_mean_eV` is set.
        fn nuclide(gamma_lines: &[(f64, f64)], beta_mean_eV: Option<f64>) -> Nuclide {
            let mut spectra = Vec::new();
            if !gamma_lines.is_empty() {
                let discrete = gamma_lines.iter().map(|&(e, i)| line(e, i)).collect();
                spectra.push(spectrum(0.0, 0.0, discrete));
            }
            if let Some(mean) = beta_mean_eV {
                spectra.push(spectrum(1.0, mean, Vec::new()));
            }
            Nuclide {
                mat: 0,
                za: 19042,
                awr: 1.0,
                liso: 0,
                nst: 0,
                half_life: 1.0,
                d_half_life: 0.0,
                energies: vec![0.0, 0.0, gamma_lines.iter().map(|&(e, i)| e * i).sum()],
                // A beta-minus emitter decays by beta-minus alone; the bremsstrahlung model needs
                // the branching ratio.
                modes: beta_mean_eV
                    .map(|_| Mode {
                        rtyp: 1.0,
                        rfs: 0.0,
                        q: 0.0,
                        dq: 0.0,
                        br: 1.0,
                        dbr: 0.0,
                    })
                    .into_iter()
                    .collect(),
                spectra,
            }
        }

        fn build(n: &Nuclide, activity: f64, mass_g: f64, r: &PhotonResponse) -> PhotonSourceOut {
            source_for_step(
                &[("X", n, activity)],
                &BOUNDS,
                "test",
                mass_g,
                Some(r),
                &fractions(),
                true,
                2.0,
                0.0,
            )
            .unwrap()
            .0
        }

        fn point(distance_m: f64, air_attenuation: bool) -> PointSourceOptions {
            PointSourceOptions {
                distance_m,
                air_attenuation,
                air_density_g_cm3: 1.0e-3,
            }
        }

        fn brems(names: &[&str]) -> BremsstrahlungOptions {
            BremsstrahlungOptions {
                nuclides: names.iter().map(|n| n.to_string()).collect(),
            }
        }

        fn close(a: f64, b: f64) -> bool {
            (a - b).abs() <= 1e-12 * b.abs().max(1e-300)
        }

        #[test]
        fn steiner_22_matches_the_manual_table() {
            let b = STEINER_22_BOUNDARIES_EV;
            assert_eq!(b.len(), 23); // 22 groups
            assert_eq!(b[0], 0.0);
            assert_eq!(b[22], 1.4e7); // table ends at 14 MeV
            assert!(b.windows(2).all(|w| w[1] > w[0]));
            // Spot checks against Table 6: group 2 is 0.01-0.10 MeV, group 5 is 0.40-1.00 MeV,
            // group 20 is 8.00-10.00 MeV.
            assert_eq!((b[1], b[2]), (1.0e4, 1.0e5));
            assert_eq!((b[4], b[5]), (4.0e5, 1.0e6));
            assert_eq!((b[19], b[20]), (8.0e6, 1.0e7));
            // Where the two tables share an edge they agree.
            for edge in [1.0e4, 1.0e5, 2.0e5, 4.0e5, 1.0e6, 2.0e6, 1.0e7, 1.4e7] {
                assert!(FISPACT_24_BOUNDARIES_EV.contains(&edge));
                assert!(b.contains(&edge));
            }
        }

        #[test]
        fn steiner_22_bins_a_line_into_its_manual_group_and_overflows_above_14_mev() {
            let n = nuclide(&[(1.1e7, 1.0), (1.5e7, 0.5)], None);
            let (src, diag) = source_for_step(
                &[("X", &n, 1.0)],
                &STEINER_22_BOUNDARIES_EV,
                "steiner-22",
                1.0,
                None,
                &BTreeMap::new(),
                false,
                2.0,
                0.0,
            )
            .unwrap();
            assert_eq!(src.groups.len(), 22);
            assert_eq!(src.groups[20].photons_s_g, 1.0); // group 21: 10-12 MeV
            let others: f64 = (0..22)
                .filter(|g| *g != 20)
                .map(|g| src.groups[g].photons_s_g)
                .sum();
            assert_eq!(others, 0.0);
            assert_eq!(diag.group_overflow_photons_s_g, 0.5);
        }

        #[test]
        fn point_source_dose_matches_a_hand_computed_single_line() {
            // 2 Bq/g of a 1 MeV, one-photon line in 3 g, seen at 2 m through air of 1e-3 g/cm3.
            let n = nuclide(&[(1.0e6, 1.0)], None);
            let r = response(true);
            let mut src = build(&n, 2.0, 3.0, &r);
            add_point_source_dose(&mut src, Some(&r), &point(2.0, true), 3.0);
            let power_W = 2.0 * 1.0e6 * EV * 3.0;
            let transmission = (-0.06_f64 * 1.0e-3 * 100.0 * 2.0).exp();
            let expected = 0.03 * 0.1 * transmission * power_W
                / (4.0 * std::f64::consts::PI * 2.0 * 2.0)
                * 3600.0;
            let out = src.point_source_dose.as_ref().unwrap();
            let dose = out.gamma_air_dose_Sv_h.unwrap();
            assert!(close(dose, expected), "{dose} vs {expected}");
            assert!(close(
                out.gamma_air_dose_Sv_h_per_g.unwrap(),
                expected / 3.0
            ));
            assert_eq!(out.distance_m, 2.0);
            assert_eq!(out.mass_g, 3.0);
            assert_eq!(out.air_density_g_cm3, Some(1.0e-3));
            assert!(out.unavailable_reason.is_none());
            // The only nuclide carries the whole dose.
            let own = src.by_nuclide[0].point_source_gamma_air_dose_Sv_h.unwrap();
            assert!(close(own, expected));
            // The contact proxy is untouched by the new component.
            assert!(src.contact_gamma_air_dose_proxy_Gy_h.is_some());
        }

        #[test]
        fn point_source_dose_scales_as_inverse_square_distance_and_linearly_in_mass() {
            let n = nuclide(&[(1.0e6, 1.0)], None);
            let r = response(true);
            let dose = |distance: f64, mass: f64, air: bool| {
                let mut src = build(&n, 2.0, mass, &r);
                add_point_source_dose(&mut src, Some(&r), &point(distance, air), mass);
                src.point_source_dose.unwrap().gamma_air_dose_Sv_h.unwrap()
            };
            // Without air attenuation the dose is exactly 1/d^2.
            assert!(close(dose(1.0, 1.0, false) / dose(2.0, 1.0, false), 4.0));
            assert!(close(dose(0.5, 1.0, false) / dose(1.5, 1.0, false), 9.0));
            // Air attenuation only lowers it, and more so farther away.
            assert!(dose(2.0, 1.0, true) < dose(2.0, 1.0, false));
            assert!(
                dose(2.0, 1.0, true) / dose(2.0, 1.0, false)
                    < dose(1.0, 1.0, true) / dose(1.0, 1.0, false)
            );
            // Linear in the source mass, with or without attenuation.
            assert!(close(dose(1.0, 3.0, true) / dose(1.0, 1.0, true), 3.0));
            assert!(close(dose(1.0, 7.0, false) / dose(1.0, 1.0, false), 7.0));
        }

        #[test]
        fn point_source_dose_reports_missing_inputs_instead_of_inferring_them() {
            let n = nuclide(&[(1.0e6, 1.0)], None);
            // Air attenuation requested but the response carries no air mu/rho curve.
            let r = response(false);
            let mut src = build(&n, 1.0, 1.0, &r);
            add_point_source_dose(&mut src, Some(&r), &point(1.0, true), 1.0);
            let out = src.point_source_dose.as_ref().unwrap();
            assert!(out.gamma_air_dose_Sv_h.is_none());
            assert!(out
                .unavailable_reason
                .as_ref()
                .unwrap()
                .contains("air_mass_attenuation"));
            // Choosing no attenuation explicitly is a different, allowed calculation.
            add_point_source_dose(&mut src, Some(&r), &point(1.0, false), 1.0);
            assert!(src
                .point_source_dose
                .as_ref()
                .unwrap()
                .gamma_air_dose_Sv_h
                .is_some());
            // No response at all.
            add_point_source_dose(&mut src, None, &point(1.0, false), 1.0);
            let out = src.point_source_dose.as_ref().unwrap();
            assert!(out.gamma_air_dose_Sv_h.is_none());
            assert!(out
                .unavailable_reason
                .as_ref()
                .unwrap()
                .contains("photon.response"));
        }

        #[test]
        fn point_source_dose_is_withheld_when_power_falls_outside_the_response() {
            // A 5e7 eV line is above the response's 1e7 eV ceiling.
            let n = nuclide(&[(1.0e6, 1.0), (5.0e7, 1.0e-3)], None);
            let r = response(true);
            let mut src = build(&n, 1.0, 1.0, &r);
            add_point_source_dose(&mut src, Some(&r), &point(1.0, true), 1.0);
            let out = src.point_source_dose.as_ref().unwrap();
            assert!(out.gamma_air_dose_Sv_h.is_none());
            assert!(close(out.excluded_power_W, 5.0e4 * EV));
            assert!(out.unavailable_reason.is_some());
        }

        #[test]
        fn bremsstrahlung_and_point_source_are_off_by_default() {
            let n = nuclide(&[(1.0e6, 1.0)], Some(1.0e6));
            let r = response(true);
            let src = build(&n, 2.0, 3.0, &r);
            assert!(src.bremsstrahlung.is_none());
            assert!(src.point_source_dose.is_none());
            let json = serde_json::to_string(&src).unwrap();
            for key in [
                "bremsstrahlung",
                "point_source_dose",
                "point_source_gamma_air_dose_Sv_h",
            ] {
                assert!(!json.contains(key), "{key} leaked into the default output");
            }
            // Older result files without the new keys still read back.
            let back: PhotonSourceOut = serde_json::from_str(&json).unwrap();
            assert!(back.bremsstrahlung.is_none() && back.point_source_dose.is_none());
        }

        #[test]
        fn bremsstrahlung_is_a_separate_additive_component_matching_eq_66() {
            // K42-like emitter: 1 MeV mean beta energy, Z_eff 26, 2 Bq/g, 3 g.
            let n = nuclide(&[(1.0e6, 1.0)], Some(1.0e6));
            let r = response(true);
            let before = build(&n, 2.0, 3.0, &r);
            let mut src = before.clone();
            add_bremsstrahlung(
                &mut src,
                &[("X", &n, 2.0)],
                &brems(&["X"]),
                &Ok(26.0),
                3.0,
                Some(&r),
                &fractions(),
                true,
                2.0,
            );
            // Nothing outside the new component moved: the decay-photon source is not merged.
            let mut after = serde_json::to_value(&src).unwrap();
            after.as_object_mut().unwrap().remove("bremsstrahlung");
            assert_eq!(after, serde_json::to_value(&before).unwrap());

            let b = src.bremsstrahlung.as_ref().unwrap();
            assert!(b.inputs_complete && b.missing_inputs.is_empty());
            assert_eq!(b.z_eff, Some(26.0));
            assert!(b.requested_nuclides_without_activity.is_empty());
            assert_eq!(b.zero_lower_edge_group_power_W_g, 0.0);
            // Group 0 is 500-1500 keV and E0 = 1000 keV lies inside it: Eq. (66) case 2.
            let per_decay = A * 26.0 * (1000.0 * 2.0_f64.ln() - 500.0);
            assert!(close(b.groups[0].photons_s_g, 2.0 * per_decay));
            assert!(close(b.groups[0].photons_s, 6.0 * per_decay));
            // Eq. (67): energy is the photon number times the 1 MeV bin midpoint.
            assert!(close(b.groups[0].centroid_eV, 1.0e6));
            assert!(close(b.power_W_g, 2.0 * per_decay * 1.0e6 * EV));
            assert!(close(b.power_W, 3.0 * b.power_W_g));
            // Group 1 starts above E0: Eq. (66) case 3.
            assert_eq!(b.groups[1].photons_s_g, 0.0);
            assert!(close(b.photons_s_g, b.by_nuclide[0].photons_s_g));
            // Contact dose by the same B/2 mu_a/mu_m formula: B = 2, mu_a/mu_m = 0.03/0.06.
            let contact = b.contact_gamma_air_dose_proxy_Gy_h.unwrap();
            assert!(close(contact, 0.5 * b.power_W_g * 1000.0 * 3600.0));
            assert!(close(
                b.by_nuclide[0].contact_gamma_air_dose_proxy_Gy_h.unwrap(),
                contact
            ));
            // The decay-photon contact dose is a different, unchanged number.
            assert_eq!(
                src.contact_gamma_air_dose_proxy_Gy_h,
                before.contact_gamma_air_dose_proxy_Gy_h
            );

            // With a point-source dose requested after it, the component gets its own point dose.
            add_point_source_dose(&mut src, Some(&r), &point(2.0, false), 3.0);
            let b = src.bremsstrahlung.as_ref().unwrap();
            let expected = 0.03 * 0.1 * b.power_W / (4.0 * std::f64::consts::PI * 4.0) * 3600.0;
            assert!(close(b.point_source_gamma_air_dose_Sv_h.unwrap(), expected));
            assert!(close(
                b.by_nuclide[0].point_source_gamma_air_dose_Sv_h.unwrap(),
                expected
            ));
        }

        #[test]
        fn bremsstrahlung_lowest_edge_group_is_left_empty_and_its_energy_reported() {
            // Boundaries 0, 10 keV, 10 MeV; E0 = 500 keV, Z = 26.
            let n = nuclide(&[], Some(5.0e5));
            let (mut src, _) = source_for_step(
                &[("X", &n, 4.0)],
                &[0.0, 1.0e4, 1.0e7],
                "test",
                1.0,
                None,
                &BTreeMap::new(),
                false,
                2.0,
                0.0,
            )
            .unwrap();
            add_bremsstrahlung(
                &mut src,
                &[("X", &n, 4.0)],
                &brems(&["X"]),
                &Ok(26.0),
                1.0,
                None,
                &BTreeMap::new(),
                false,
                2.0,
            );
            let b = src.bremsstrahlung.as_ref().unwrap();
            assert_eq!(b.groups[0].photons_s_g, 0.0);
            // Exact energy below 10 keV: aZ (E0 u - u^2/2) with u = 10 keV.
            let zero_edge = 4.0 * A * 26.0 * (500.0 * 10.0 - 50.0) * 1000.0 * EV;
            assert!(close(b.zero_lower_edge_group_power_W_g, zero_edge));
            // Group 1 is 10-10000 keV with E0 inside: case 2.
            let per_decay = A * 26.0 * (500.0 * (500.0_f64 / 10.0).ln() - (500.0 - 10.0));
            assert!(close(b.groups[1].photons_s_g, 4.0 * per_decay));
            // No response: no dose, but nothing is invented either.
            assert!(b.contact_gamma_air_dose_proxy_Gy_h.is_none());
        }

        #[test]
        fn bremsstrahlung_reports_missing_inputs_and_inactive_nuclides() {
            let r = response(true);
            let no_beta = nuclide(&[(1.0e6, 1.0)], None);
            let mut src = build(&no_beta, 1.0, 1.0, &r);
            add_bremsstrahlung(
                &mut src,
                &[("X", &no_beta, 1.0)],
                &brems(&["X", "Y"]),
                &Err("no data for Tc".to_string()),
                1.0,
                Some(&r),
                &fractions(),
                true,
                2.0,
            );
            let b = src.bremsstrahlung.as_ref().unwrap();
            assert!(!b.inputs_complete);
            assert!(b.by_nuclide.is_empty());
            assert_eq!(b.photons_s_g, 0.0);
            assert_eq!(b.z_eff, None);
            assert_eq!(
                b.z_eff_unavailable_reason.as_deref(),
                Some("no data for Tc")
            );
            assert_eq!(b.missing_inputs.len(), 1);
            assert_eq!(b.missing_inputs[0].nuclide, "X");
            assert!(b.missing_inputs[0]
                .missing
                .contains("mean beta-minus energy"));
            assert!(b.missing_inputs[0]
                .missing
                .contains("material Z_eff: no data for Tc"));
            // "Y" was requested but is not in the active inventory.
            assert_eq!(b.requested_nuclides_without_activity, vec!["Y".to_string()]);
        }

        #[test]
        fn z_eff_is_the_atom_fraction_mean_atomic_number() {
            let iron = BTreeMap::from([((26054, 0), 1.0), ((26056, 0), 3.0)]);
            assert!(close(material_z_eff(&iron, &[]).unwrap(), 26.0));
            // H2O: (2*1 + 8) / 3 atoms.
            let water = BTreeMap::from([((1001, 0), 2.0), ((8016, 0), 1.0)]);
            assert!(close(material_z_eff(&water, &[]).unwrap(), 10.0 / 3.0));
            assert!(material_z_eff(&water, &["Tc".to_string()])
                .unwrap_err()
                .contains("Tc"));
            assert!(material_z_eff(&BTreeMap::new(), &[]).is_err());
        }
    }

    mod mesh_export {
        use super::super::{
            export_openmc_mesh, mesh_cell_from_source, MeshPhotonCell, PhotonGroupOut,
            PhotonSourceOut,
        };

        fn source(groups: Vec<(f64, f64)>, total: f64) -> PhotonSourceOut {
            let groups = groups
                .into_iter()
                .map(|(centroid_eV, photons_s)| PhotonGroupOut {
                    low_eV: centroid_eV * 0.9,
                    high_eV: centroid_eV * 1.1,
                    centroid_eV,
                    photons_s_g: photons_s,
                    photons_s,
                    power_W_g: 0.0,
                    power_W: 0.0,
                })
                .collect::<Vec<_>>();
            PhotonSourceOut {
                group_structure: "test".into(),
                boundaries_eV: vec![1.0, 2.0],
                lines: Vec::new(),
                groups,
                by_nuclide: Vec::new(),
                grouped_photons_s_g: total,
                grouped_photons_s: total,
                total_photons_s_g: total,
                total_photons_s: total,
                source_power_W_g: 0.0,
                source_power_W: 0.0,
                ungrouped_power_W_g: 0.0,
                unrepresented_gamma_power_W_g: 0.0,
                represented_gamma_power_fraction: 1.0,
                contact_gamma_air_dose_proxy_Gy_h: None,
                dose_response_power_coverage: None,
                dose_response_subthreshold_power_W_g: 0.0,
                point_source_dose: None,
                bremsstrahlung: None,
                estimated_gamma_power_W_g: None,
                excluded_xray_power_W_g: None,
                estimated_spectra: None,
            }
        }

        fn bounds() -> [[f64; 2]; 3] {
            [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]
        }

        #[test]
        fn normalizes_weights_and_conserves_strength() {
            let s = source(vec![(1e6, 3.0), (2e6, 1.0)], 4.0);
            let cell = mesh_cell_from_source("c".into(), bounds(), 1.0, &s)
                .unwrap()
                .unwrap();
            assert!((cell.strength_photons_s - 4.0).abs() < 1e-15);
            assert_eq!(cell.energy_eV, vec![1e6, 2e6]);
            let wsum: f64 = cell.weight.iter().sum();
            assert!((wsum - 1.0).abs() < 1e-15);
            assert!((cell.weight[0] - 0.75).abs() < 1e-15);
        }

        #[test]
        fn zero_photon_cell_returns_none() {
            let s = source(vec![], 0.0);
            assert!(mesh_cell_from_source("c".into(), bounds(), 1.0, &s)
                .unwrap()
                .is_none());
        }

        #[test]
        fn rejects_non_positive_volume() {
            let s = source(vec![(1e6, 1.0)], 1.0);
            assert!(mesh_cell_from_source("c".into(), bounds(), 0.0, &s)
                .unwrap_err()
                .contains("volume"));
            assert!(mesh_cell_from_source("c".into(), bounds(), -1.0, &s)
                .unwrap_err()
                .contains("volume"));
        }

        #[test]
        fn rejects_omitted_photon_strength() {
            // total claims 10 photons/s but groups only carry 4
            let s = source(vec![(1e6, 3.0), (2e6, 1.0)], 10.0);
            assert!(mesh_cell_from_source("c".into(), bounds(), 1.0, &s)
                .unwrap_err()
                .contains("conserv"));
        }

        #[test]
        fn rejects_degenerate_bounds_and_empty_cells() {
            let cell = MeshPhotonCell {
                id: "c".into(),
                bounds_cm: [[1.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
                volume_cm3: 1.0,
                strength_photons_s: 1.0,
                energy_eV: vec![1e6],
                weight: vec![1.0],
            };
            assert!(export_openmc_mesh(&[cell])
                .unwrap_err()
                .contains("degenerate bounds"));
            assert!(export_openmc_mesh(&[]).unwrap_err().contains("no photon"));
        }

        #[test]
        fn emits_box_per_cell_with_absolute_total() {
            let cell = MeshPhotonCell {
                id: "1,1,1".into(),
                bounds_cm: [[-2.0, -1.0], [-2.0, -1.0], [-2.0, -1.0]],
                volume_cm3: 1.0,
                strength_photons_s: 5.0,
                energy_eV: vec![1e6],
                weight: vec![1.0],
            };
            let out = export_openmc_mesh(&[cell]).unwrap();
            assert!(out.contains("openmc.stats.Box([-2.0"));
            assert!(out.contains("# cell 1,1,1"));
            assert!(out.contains("TOTAL_PHOTONS_S = 5.00000000000000000e0"));
            assert!(out.contains("particle=\"photon\""));
        }
    }

    /// P140 hardening regressions: each asserts the corrected behaviour and fails on the code
    /// that predates the repair.
    mod p140 {
        use super::super::{
            add_bremsstrahlung, add_point_source_dose, bremsstrahlung_energy_below_keV,
            bremsstrahlung_photons_per_electron, source_for_step, PhotonResponse, PhotonSourceOut,
            ResponseCurve,
        };
        use crate::run::EV;
        use crate::spec::{BremsstrahlungOptions, PointSourceOptions};
        use actinv_data::decay::{DiscreteRadiation, Mode, Nuclide, Spectrum};
        use std::collections::BTreeMap;

        // Manual Eq. (65): a = 2.76e-6 per keV.
        const A: f64 = 2.76e-6;
        // Group 0 is 0.5-1.5 MeV, so a 1 MeV line sits on its midpoint and on a tabulated energy.
        const GAMMA_BOUNDS: [f64; 3] = [5.0e5, 1.5e6, 1.0e8];

        fn close(a: f64, b: f64) -> bool {
            (a - b).abs() <= 1e-9 * b.abs().max(1e-300)
        }

        fn curve(values: [f64; 4]) -> ResponseCurve {
            ResponseCurve {
                energy_eV: vec![1.0e3, 1.0e4, 1.0e6, 1.0e7],
                values_cm2_g: values.to_vec(),
            }
        }

        fn response() -> PhotonResponse {
            let mut elements = BTreeMap::new();
            elements.insert("H".to_string(), curve([4500.0, 5.0, 0.06, 0.03]));
            PhotonResponse {
                schema: "actinv-photon-response-1".into(),
                provenance: serde_json::Value::Null,
                air_mass_energy_absorption: curve([3000.0, 10.0, 0.03, 0.02]),
                air_mass_attenuation: Some(curve([3100.0, 11.0, 0.06, 0.04])),
                element_mass_attenuation: elements,
            }
        }

        fn fractions() -> BTreeMap<String, f64> {
            BTreeMap::from([("H".to_string(), 1.0)])
        }

        fn gamma_line(energy: f64, intensity: f64) -> DiscreteRadiation {
            DiscreteRadiation {
                energy,
                d_energy: 0.0,
                rtyp: 0.0,
                transition_type: 0.0,
                intensity,
                d_intensity: 0.0,
                pair_intensity: 0.0,
                d_pair_intensity: 0.0,
                conversion_total: 0.0,
                d_conversion_total: 0.0,
                conversion_k: 0.0,
                d_conversion_k: 0.0,
                conversion_l: 0.0,
                d_conversion_l: 0.0,
            }
        }

        fn spectrum(styp: f64, average_energy: f64, discrete: Vec<DiscreteRadiation>) -> Spectrum {
            Spectrum {
                styp,
                lcon: 0,
                lcov: 0,
                fd: 1.0,
                d_fd: 0.0,
                average_energy,
                d_average_energy: 0.0,
                fc: 0.0,
                d_fc: 0.0,
                discrete,
                continuous: None,
            }
        }

        /// A decaying nuclide with gamma lines `(energy_eV, intensity)` (mean EM energy equal to
        /// their sum), a STYP=1 spectrum with ENDF list item ER `beta_er_eV` (the average energy
        /// per decay of the parent) and decay modes `(RTYP, branching ratio)`.
        fn nuclide(
            gamma_lines: &[(f64, f64)],
            beta_er_eV: Option<f64>,
            modes: &[(f64, f64)],
        ) -> Nuclide {
            let mut spectra = Vec::new();
            if !gamma_lines.is_empty() {
                let discrete = gamma_lines.iter().map(|&(e, i)| gamma_line(e, i)).collect();
                spectra.push(spectrum(0.0, 0.0, discrete));
            }
            if let Some(er) = beta_er_eV {
                spectra.push(spectrum(1.0, er, Vec::new()));
            }
            Nuclide {
                mat: 0,
                za: 19042,
                awr: 1.0,
                liso: 0,
                nst: 0,
                half_life: 1.0,
                d_half_life: 0.0,
                energies: vec![0.0, 0.0, gamma_lines.iter().map(|&(e, i)| e * i).sum()],
                modes: modes
                    .iter()
                    .map(|&(rtyp, br)| Mode {
                        rtyp,
                        rfs: 0.0,
                        q: 0.0,
                        dq: 0.0,
                        br,
                        dbr: 0.0,
                    })
                    .collect(),
                spectra,
            }
        }

        fn brems(names: &[&str]) -> BremsstrahlungOptions {
            BremsstrahlungOptions {
                nuclides: names.iter().map(|n| n.to_string()).collect(),
            }
        }

        fn point(distance_m: f64) -> PointSourceOptions {
            PointSourceOptions {
                distance_m,
                air_attenuation: false,
                air_density_g_cm3: 1.0e-3,
            }
        }

        /// Photon-free source of the single nuclide "X" plus its bremsstrahlung component.
        fn brems_source(n: &Nuclide, activity: f64, bounds: &[f64], z: f64) -> PhotonSourceOut {
            let (mut src, _) = source_for_step(
                &[("X", n, activity)],
                bounds,
                "test",
                1.0,
                None,
                &BTreeMap::new(),
                false,
                2.0,
                0.0,
            )
            .unwrap();
            add_bremsstrahlung(
                &mut src,
                &[("X", n, activity)],
                &brems(&["X"]),
                &Ok(z),
                1.0,
                None,
                &BTreeMap::new(),
                false,
                2.0,
            );
            src
        }

        /// Eqs. (66)-(67) for `activity * electrons_per_decay` electrons of `e0_eV` each:
        /// `(photons/s/g, power W/g in the groups, power W/g in the zero-lower-edge group)`.
        fn expected_brems(
            activity: f64,
            electrons_per_decay: f64,
            e0_eV: f64,
            z: f64,
            bounds: &[f64],
        ) -> (f64, f64, f64) {
            let e0_keV = e0_eV / 1000.0;
            let rate = activity * electrons_per_decay;
            let (mut photons, mut power, mut zero_edge) = (0.0, 0.0, 0.0);
            for w in bounds.windows(2) {
                let (lo, hi) = (w[0], w[1]);
                if lo <= 0.0 {
                    let below = bremsstrahlung_energy_below_keV(z, e0_keV, hi / 1000.0);
                    zero_edge += rate * below * 1000.0 * EV;
                    continue;
                }
                let per_electron =
                    bremsstrahlung_photons_per_electron(z, e0_keV, lo / 1000.0, hi / 1000.0);
                let n = rate * per_electron;
                photons += n;
                power += n * 0.5 * (lo + hi) * EV;
            }
            (photons, power, zero_edge)
        }

        const BREMS_BOUNDS: [f64; 4] = [1.0e4, 1.0e5, 3.0e5, 1.0e6];

        // ---- F1: STYP=1 average_energy is per decay of the parent (already branching-weighted)

        #[test]
        fn p140_ph_f1_partial_beta_emitter_uses_electron_fraction_and_per_beta_energy() {
            // Cu-64 and K-40 (ENDF/B-VIII.0): ER is the beta-minus energy per decay of the parent,
            // so one electron per decay of mean energy ER is wrong; f electrons of ER/f are right.
            for (er, modes, f) in [
                (7.340812e4, [(2.0, 0.615), (1.0, 0.385)], 0.385),
                (4.993307e5, [(2.0, 0.1086), (1.0, 0.8914)], 0.8914),
            ] {
                let n = nuclide(&[], Some(er), &modes);
                let src = brems_source(&n, 2.0, &BREMS_BOUNDS, 26.0);
                let b = src.bremsstrahlung.as_ref().unwrap();
                assert!(b.missing_inputs.is_empty(), "{:?}", b.missing_inputs);
                let nuc = &b.by_nuclide[0];
                let e0 = er / f;
                assert!(
                    close(nuc.mean_beta_energy_eV, e0),
                    "ER {er}: E0 {} vs {e0}",
                    nuc.mean_beta_energy_eV
                );
                let (photons, power, _) = expected_brems(2.0, f, e0, 26.0, &BREMS_BOUNDS);
                assert!(
                    close(nuc.photons_s_g, photons),
                    "ER {er}: photons {} vs {photons}",
                    nuc.photons_s_g
                );
                assert!(
                    close(b.power_W_g, power),
                    "ER {er}: {} vs {power}",
                    b.power_W_g
                );
            }
        }

        #[test]
        fn p140_ph_f1_endpoint_region_above_the_branch_weighted_energy_is_populated() {
            // Cu-64: the code's E0 is 73.4 keV, so the [100, 300] keV group stays empty; the
            // physical 190.7 keV mean beta energy populates it.
            let n = nuclide(&[], Some(7.340812e4), &[(2.0, 0.615), (1.0, 0.385)]);
            let src = brems_source(&n, 1.0, &BREMS_BOUNDS, 26.0);
            let b = src.bremsstrahlung.as_ref().unwrap();
            assert!(b.groups[1].photons_s_g > 0.0, "{:?}", b.groups[1]);
        }

        #[test]
        fn p140_ph_f1_multi_particle_beta_codes_count_as_beta_electrons() {
            // RTYP 1.5 (beta-minus then neutron) contains the digit 1 and carries a beta-minus
            // electron; EC (2.0) does not. f = 0.2 + 0.1.
            let er = 1.0e5;
            let n = nuclide(&[], Some(er), &[(1.0, 0.2), (1.5, 0.1), (2.0, 0.7)]);
            let src = brems_source(&n, 3.0, &BREMS_BOUNDS, 26.0);
            let b = src.bremsstrahlung.as_ref().unwrap();
            assert!(b.missing_inputs.is_empty(), "{:?}", b.missing_inputs);
            let f = 0.3;
            assert!(close(b.by_nuclide[0].mean_beta_energy_eV, er / f));
            let (photons, _, _) = expected_brems(3.0, f, er / f, 26.0, &BREMS_BOUNDS);
            assert!(close(b.by_nuclide[0].photons_s_g, photons));
        }

        #[test]
        fn p140_ph_f1_nuclide_without_beta_minus_branching_is_a_missing_input() {
            // A STYP=1 average energy but no decay mode with a beta-minus electron (f == 0), and
            // a record with no modes at all: the electron count is unknown, so nothing is emitted.
            for modes in [vec![(2.0, 1.0)], Vec::new()] {
                let n = nuclide(&[], Some(1.0e5), &modes);
                let src = brems_source(&n, 1.0, &BREMS_BOUNDS, 26.0);
                let b = src.bremsstrahlung.as_ref().unwrap();
                assert!(!b.inputs_complete, "modes {modes:?}");
                assert_eq!(b.missing_inputs.len(), 1, "modes {modes:?}");
                assert_eq!(b.missing_inputs[0].nuclide, "X");
                assert!(b.by_nuclide.is_empty());
                assert_eq!(b.photons_s_g, 0.0);
            }
        }

        #[test]
        fn p140_ph_f1_guard_pure_beta_minus_emitter_keeps_er_as_the_energy() {
            // BR(beta-) = 1: f = 1 and E0 = ER, so the repair must not move this case.
            let n = nuclide(&[], Some(7.0e5), &[(1.0, 1.0)]);
            let src = brems_source(&n, 2.0, &BREMS_BOUNDS, 26.0);
            let b = src.bremsstrahlung.as_ref().unwrap();
            assert!(close(b.by_nuclide[0].mean_beta_energy_eV, 7.0e5));
            let (photons, power, _) = expected_brems(2.0, 1.0, 7.0e5, 26.0, &BREMS_BOUNDS);
            assert!(close(b.photons_s_g, photons));
            assert!(close(b.power_W_g, power));
        }

        // ---- F6: completeness diagnostics. The field names below are the proposed wire names.

        #[test]
        fn p140_ph_f6_zero_edge_energy_share_is_reported() {
            // Share of the analytic emitted energy a Z E0^2 / 2 that falls below the first
            // nonzero edge u = 10 keV and is therefore not in the grouped photons.
            for er in [1.0e5, 2.5e5, 5.0e5] {
                let n = nuclide(&[], Some(er), &[(1.0, 1.0)]);
                let src = brems_source(&n, 2.0, &[0.0, 1.0e4, 1.0e5, 1.0e6], 26.0);
                let json = serde_json::to_value(src.bremsstrahlung.as_ref().unwrap()).unwrap();
                let e0 = er / 1000.0;
                let u = 10.0_f64;
                let expected = (e0 * u - 0.5 * u * u) / (0.5 * e0 * e0);
                let share = json["zero_lower_edge_energy_fraction"]
                    .as_f64()
                    .unwrap_or_else(|| panic!("no zero_lower_edge_energy_fraction in {json}"));
                assert!(close(share, expected), "E0 {e0} keV: {share} vs {expected}");
            }
        }

        #[test]
        fn p140_ph_f6_energy_closure_compares_emitted_to_the_analytic_total() {
            // E0 = 120 keV sits inside the [100, 200] keV group; Eq. (67) assigns that group's
            // photons the 150 keV midpoint, and the [10, 100] keV group its 55 keV midpoint, so
            // the emitted energy does not close on a Z E0^2 / 2 per electron.
            let bounds = [0.0, 1.0e4, 1.0e5, 2.0e5, 1.0e6];
            let (activity, z, er) = (4.0, 26.0, 1.2e5);
            let n = nuclide(&[], Some(er), &[(1.0, 1.0)]);
            let src = brems_source(&n, activity, &bounds, z);
            let json = serde_json::to_value(src.bremsstrahlung.as_ref().unwrap()).unwrap();
            let (_, power, zero_edge) = expected_brems(activity, 1.0, er, z, &bounds);
            let e0_keV = er / 1000.0;
            let analytic = activity * A * z * 0.5 * e0_keV * e0_keV * 1000.0 * EV;
            let expected = (power + zero_edge) / analytic;
            assert!(expected > 1.5, "scenario must overshoot: {expected}");
            let closure = json["energy_closure"]
                .as_f64()
                .unwrap_or_else(|| panic!("no energy_closure in {json}"));
            assert!(close(closure, expected), "{closure} vs {expected}");
        }

        // ---- F5: the point-source dose must not be reported while gamma power is unrepresented

        fn source_with_unrepresented_power() -> PhotonSourceOut {
            let normal = nuclide(&[(1.0e6, 1.0)], None, &[]);
            // Decay-record EM energy but no STYP=0 spectrum: its power cannot be placed in groups.
            let mut orphan = nuclide(&[], None, &[]);
            orphan.energies[2] = 2.0e5;
            let r = response();
            let (src, _) = source_for_step(
                &[("A", &normal, 1.0), ("B", &orphan, 1.0)],
                &GAMMA_BOUNDS,
                "test",
                1.0,
                Some(&r),
                &fractions(),
                true,
                2.0,
                0.0,
            )
            .unwrap();
            assert!(src.unrepresented_gamma_power_W_g > 0.0, "precondition");
            src
        }

        #[test]
        fn p140_ph_f5_point_source_dose_is_withheld_while_gamma_power_is_unrepresented() {
            let mut src = source_with_unrepresented_power();
            add_point_source_dose(&mut src, Some(&response()), &point(1.0), 1.0);
            let out = src.point_source_dose.as_ref().unwrap();
            assert!(
                out.gamma_air_dose_Sv_h.is_none(),
                "dose {:?} reported with {} W/g of gamma power unrepresented",
                out.gamma_air_dose_Sv_h,
                src.unrepresented_gamma_power_W_g
            );
            assert!(out.gamma_air_dose_Sv_h_per_g.is_none());
            assert!(out.unavailable_reason.is_some());
        }

        #[test]
        fn p140_ph_f5_unrepresented_gamma_power_is_counted_as_excluded_power() {
            let mut src = source_with_unrepresented_power();
            let missing_W = src.unrepresented_gamma_power_W_g * 1.0;
            add_point_source_dose(&mut src, Some(&response()), &point(1.0), 1.0);
            let out = src.point_source_dose.as_ref().unwrap();
            assert!(
                close(out.excluded_power_W, missing_W),
                "excluded {} vs unrepresented {missing_W}",
                out.excluded_power_W
            );
        }

        // ---- F7: add_point_source_dose is pub and must not trust its options

        fn simple_source(mass_g: f64) -> PhotonSourceOut {
            let n = nuclide(&[(1.0e6, 1.0)], None, &[]);
            let r = response();
            source_for_step(
                &[("A", &n, 2.0)],
                &GAMMA_BOUNDS,
                "test",
                mass_g,
                Some(&r),
                &fractions(),
                true,
                2.0,
                0.0,
            )
            .unwrap()
            .0
        }

        #[test]
        fn p140_ph_f7_point_source_dose_refuses_unusable_distances() {
            // Control: the shortest allowed distance is evaluated.
            let mut src = simple_source(1.0);
            add_point_source_dose(&mut src, Some(&response()), &point(0.3), 1.0);
            let d = src.point_source_dose.as_ref().unwrap().gamma_air_dose_Sv_h;
            assert!(d.is_some_and(f64::is_finite), "{d:?}");
            for bad in [0.0, 0.1, 0.29, -1.0, f64::NAN, f64::INFINITY] {
                let mut src = simple_source(1.0);
                add_point_source_dose(&mut src, Some(&response()), &point(bad), 1.0);
                let out = src.point_source_dose.as_ref().unwrap();
                assert!(
                    out.gamma_air_dose_Sv_h.is_none(),
                    "distance {bad}: dose {:?}",
                    out.gamma_air_dose_Sv_h
                );
                assert!(out.gamma_air_dose_Sv_h_per_g.is_none(), "distance {bad}");
                assert!(
                    out.unavailable_reason
                        .as_deref()
                        .is_some_and(|r| r.contains("distance")),
                    "distance {bad}: {:?}",
                    out.unavailable_reason
                );
                assert!(
                    src.by_nuclide
                        .iter()
                        .all(|n| n.point_source_gamma_air_dose_Sv_h.is_none()),
                    "distance {bad}: per-nuclide dose reported"
                );
            }
        }

        #[test]
        fn p140_ph_f7_point_source_dose_refuses_unusable_mass() {
            for bad in [0.0, -1.0, f64::NAN] {
                let mut src = simple_source(1.0);
                add_point_source_dose(&mut src, Some(&response()), &point(1.0), bad);
                let out = src.point_source_dose.as_ref().unwrap();
                assert!(
                    out.gamma_air_dose_Sv_h.is_none() && out.gamma_air_dose_Sv_h_per_g.is_none(),
                    "mass {bad}: dose {:?}, per g {:?}",
                    out.gamma_air_dose_Sv_h,
                    out.gamma_air_dose_Sv_h_per_g
                );
                assert!(
                    out.unavailable_reason
                        .as_deref()
                        .is_some_and(|r| r.contains("mass")),
                    "mass {bad}: {:?}",
                    out.unavailable_reason
                );
            }
        }
    }

    mod estimate {
        use super::super::{
            add_point_source_dose, allowed_intervals, contact_extrema, curve_value, dose_range,
            estimate_model, estimate_shape, exp_moments, extent, material_mu, point_candidates,
            point_extrema, source_for_step, source_for_step_with, DoseModel, DoseRange, ModeClass,
            PhotonGroupOut, PhotonResponse, ResponseCurve, SourceModel, ESTIMATE_EXPONENT,
        };
        use crate::run::EV;
        use crate::spec::PointSourceOptions;
        use actinv_data::decay::{DiscreteRadiation, Mode, Nuclide, Spectrum};
        use std::collections::BTreeMap;

        const BOUNDS: [f64; 5] = [0.0, 1.0e4, 1.0e5, 1.0e6, 1.0e7];
        const ESTIMATE: SourceModel = SourceModel {
            estimate_missing: true,
            exclude_xrays: false,
        };

        fn rel(a: f64, b: f64) -> f64 {
            (a - b).abs() / b.abs().max(1e-300)
        }

        fn mode(rtyp: f64, q: f64, br: f64) -> Mode {
            Mode {
                rtyp,
                rfs: 0.0,
                q,
                dq: 0.0,
                br,
                dbr: 0.0,
            }
        }

        fn line_spectrum(styp: f64, lines: &[(f64, f64)]) -> Spectrum {
            Spectrum {
                styp,
                lcon: 0,
                lcov: 0,
                fd: 1.0,
                d_fd: 0.0,
                average_energy: 0.0,
                d_average_energy: 0.0,
                fc: 0.0,
                d_fc: 0.0,
                discrete: lines
                    .iter()
                    .map(|&(energy, intensity)| DiscreteRadiation {
                        energy,
                        d_energy: 0.0,
                        rtyp: 0.0,
                        transition_type: 0.0,
                        intensity,
                        d_intensity: 0.0,
                        pair_intensity: 0.0,
                        d_pair_intensity: 0.0,
                        conversion_total: 0.0,
                        d_conversion_total: 0.0,
                        conversion_k: 0.0,
                        d_conversion_k: 0.0,
                        conversion_l: 0.0,
                        d_conversion_l: 0.0,
                    })
                    .collect(),
                continuous: None,
            }
        }

        fn nuclide(e_light: f64, e_em: f64, modes: Vec<Mode>, spectra: Vec<Spectrum>) -> Nuclide {
            Nuclide {
                mat: 0,
                za: 27060,
                awr: 58.0,
                liso: 0,
                nst: 0,
                half_life: 1.0,
                d_half_life: 0.0,
                energies: vec![e_light, 0.0, e_em],
                modes,
                spectra,
            }
        }

        fn curve(values: [f64; 5]) -> ResponseCurve {
            ResponseCurve {
                energy_eV: vec![1.0e3, 1.0e4, 1.0e5, 1.0e6, 1.0e7],
                values_cm2_g: values.to_vec(),
            }
        }

        fn response() -> PhotonResponse {
            let mut elements = BTreeMap::new();
            elements.insert("H".to_string(), curve([4500.0, 5.0, 0.15, 0.06, 0.03]));
            PhotonResponse {
                schema: "actinv-photon-response-1".into(),
                provenance: serde_json::Value::Null,
                air_mass_energy_absorption: curve([3000.0, 10.0, 0.2, 0.03, 0.05]),
                air_mass_attenuation: Some(curve([4000.0, 12.0, 0.17, 0.06, 0.04])),
                element_mass_attenuation: elements,
            }
        }

        fn fractions() -> BTreeMap<String, f64> {
            BTreeMap::from([("H".to_string(), 1.0)])
        }

        fn build(
            nu: &Nuclide,
            activity: f64,
            model: &SourceModel,
        ) -> (
            super::super::PhotonSourceOut,
            super::super::PhotonDiagnostics,
        ) {
            source_for_step_with(
                &[("Co60", nu, activity)],
                &BOUNDS,
                "test",
                2.0,
                Some(&response()),
                &fractions(),
                true,
                2.0,
                0.0,
                model,
            )
            .unwrap()
        }

        fn beta_minus() -> Nuclide {
            nuclide(2.0e5, 7.5e4, vec![mode(1.0, 0.0, 1.0)], vec![])
        }

        #[test]
        fn closed_form_matches_quadrature() {
            let (c, k, lo, hi) = (2.5, 4.0e-6, 3.0e5, 9.0e5);
            let (n, e) = exp_moments(c, k, lo, hi);
            let steps = 400_000;
            let dx = (hi - lo) / steps as f64;
            let (mut n_ref, mut e_ref) = (0.0, 0.0);
            for i in 0..steps {
                let x = lo + (i as f64 + 0.5) * dx;
                let y = c * (-k * x).exp();
                n_ref += y * dx;
                e_ref += x * y * dx;
            }
            assert!(rel(n, n_ref) < 1e-10, "{n} {n_ref}");
            assert!(rel(e, e_ref) < 1e-10, "{e} {e_ref}");
        }

        #[test]
        fn estimate_energy_is_the_mean_em_energy() {
            let nu = beta_minus();
            let model = estimate_model(&nu).unwrap();
            assert_eq!(model.mode, ModeClass::BetaMinus);
            assert_eq!(model.e_max_eV, 4.0e5);
            let sh = estimate_shape(&nu, &model, &BOUNDS).unwrap();
            let energy: f64 =
                sh.group_moment.iter().sum::<f64>() + sh.under_energy + sh.over_energy;
            assert!(rel(energy, 7.5e4) < 1e-12, "{energy}");
            let count: f64 = sh.group_count.iter().sum::<f64>() + sh.under_count + sh.over_count;
            // Independent closed form over (0, E_max]: C = E a^2 / (E_max^2 (1 - (1 + a) e^-a)).
            let a = ESTIMATE_EXPONENT;
            let c = 7.5e4 * a * a / (4.0e5_f64.powi(2) * (1.0 - (1.0 + a) * (-a).exp()));
            let count_ref = c * 4.0e5 * (1.0 - (-a).exp()) / a;
            assert!(rel(count, count_ref) < 1e-12, "{count} {count_ref}");
            assert!(rel(sh.source_count, count_ref) < 1e-12);
        }

        #[test]
        fn photons_beyond_the_structure_are_overflow_and_energy_is_conserved() {
            let nu = nuclide(2.0e6, 7.5e4, vec![mode(1.0, 0.0, 1.0)], vec![]);
            let model = estimate_model(&nu).unwrap();
            let short = [0.0, 1.0e4, 1.0e5, 1.0e6];
            let sh = estimate_shape(&nu, &model, &short).unwrap();
            assert!(sh.over_energy > 0.0 && sh.under_energy == 0.0);
            let energy: f64 =
                sh.group_moment.iter().sum::<f64>() + sh.under_energy + sh.over_energy;
            assert!(rel(energy, 7.5e4) < 1e-12);
        }

        #[test]
        fn q_value_caps_e_max() {
            let capped = nuclide(0.0, 1.0e5, vec![mode(2.0, 1.0e6, 1.0)], vec![]);
            let m = estimate_model(&capped).unwrap();
            assert_eq!(m.mode, ModeClass::BetaPlusEc);
            assert_eq!((m.e_max_manual_eV, m.e_max_eV), (5.0e6, 1.0e6));
            assert_eq!(m.q_cap_eV, Some(1.0e6));
            // A larger Q leaves the manual E_max; no Q-value leaves no cap.
            let loose = nuclide(0.0, 1.0e5, vec![mode(2.0, 8.0e6, 1.0)], vec![]);
            let m = estimate_model(&loose).unwrap();
            assert_eq!((m.e_max_eV, m.q_cap_eV), (5.0e6, Some(8.0e6)));
            let none = nuclide(0.0, 1.0e5, vec![mode(2.0, 0.0, 1.0)], vec![]);
            let m = estimate_model(&none).unwrap();
            assert_eq!((m.e_max_eV, m.q_cap_eV), (5.0e6, None));
            // A mode without branching does not set the cap.
            let zero = nuclide(
                0.0,
                1.0e5,
                vec![mode(1.0, 9.0e6, 0.0), mode(2.0, 1.0e6, 1.0)],
                vec![],
            );
            let m = estimate_model(&zero).unwrap();
            assert_eq!((m.mode, m.q_cap_eV), (ModeClass::BetaPlusEc, Some(1.0e6)));
        }

        #[test]
        fn dominant_mode_sets_e_max() {
            // The larger summed branching ratio wins, and the Q-value cap spans every mode.
            let nu = nuclide(
                1.0e5,
                3.0e4,
                vec![mode(1.0, 2.0e6, 0.3), mode(3.0, 5.0e4, 0.7)],
                vec![],
            );
            let m = estimate_model(&nu).unwrap();
            assert_eq!(m.mode, ModeClass::IsomericTransition);
            assert_eq!((m.e_max_manual_eV, m.e_max_eV), (3.0e4, 3.0e4));
            assert_eq!(m.q_cap_eV, Some(2.0e6));
            // Ties go to beta-minus; the sequential decay 1.5 is a beta-minus mode.
            let tie = nuclide(
                1.0e5,
                3.0e4,
                vec![mode(1.5, 0.0, 0.5), mode(2.0, 0.0, 0.5)],
                vec![],
            );
            let m = estimate_model(&tie).unwrap();
            assert_eq!((m.mode, m.e_max_eV), (ModeClass::BetaMinus, 2.0e5));
            // RTYP 10 (unknown origin), alpha and a record without modes are other.
            for modes in [
                vec![mode(10.0, 0.0, 1.0)],
                vec![mode(4.0, 0.0, 1.0)],
                Vec::new(),
            ] {
                let m = estimate_model(&nuclide(1.0e5, 3.0e4, modes, vec![])).unwrap();
                assert_eq!((m.mode, m.e_max_eV), (ModeClass::Other, 5.0e6));
            }
            // A beta-minus record without a light-particle energy falls back to 5 MeV.
            let m =
                estimate_model(&nuclide(0.0, 3.0e4, vec![mode(1.0, 0.0, 1.0)], vec![])).unwrap();
            assert_eq!(m.e_max_eV, 5.0e6);
            assert!(
                estimate_model(&nuclide(1.0e5, 0.0, vec![mode(1.0, 0.0, 1.0)], vec![])).is_none()
            );
        }

        #[test]
        fn estimate_books_power_as_estimated_not_unrepresented() {
            let nu = beta_minus();
            let expected = 3.0e6 * 7.5e4 * EV;
            let (src, diag) = build(&nu, 3.0e6, &ESTIMATE);
            assert!(rel(src.estimated_gamma_power_W_g.unwrap(), expected) < 1e-12);
            assert_eq!(src.unrepresented_gamma_power_W_g, 0.0);
            assert!(rel(src.source_power_W_g, expected) < 1e-12);
            assert!(diag
                .nuclides_with_em_energy_but_no_photon_spectrum
                .is_empty());
            assert_eq!(diag.nuclides_with_estimated_photon_spectrum.len(), 1);
            let n = &src.estimated_spectra.as_ref().unwrap().by_nuclide[0];
            assert!(rel(n.power_W_g, expected) < 1e-12);
            assert!(rel(n.photons_s_g, 3.0e6 * n.photons_per_decay) < 1e-12);
            // The default books the same power as unrepresented.
            let (report, _) = build(&nu, 3.0e6, &SourceModel::default());
            assert_eq!(report.unrepresented_gamma_power_W_g, expected);
            assert_eq!(report.source_power_W_g, 0.0);
            assert!(
                report.estimated_spectra.is_none() && report.estimated_gamma_power_W_g.is_none()
            );
        }

        /// Contact proxy per unit power at one energy, from the response directly (B = 2).
        fn contact_rho(e: f64) -> f64 {
            let r = response();
            let ratio = curve_value(&r.air_mass_energy_absorption, e).unwrap()
                / material_mu(&r, &fractions(), e).unwrap();
            ratio * 1000.0 * 3600.0
        }

        fn contact_range(groups: &[PhotonGroupOut], e_cap: f64) -> DoseRange {
            let r = response();
            let f = fractions();
            let value = |e: f64| {
                Some(
                    curve_value(&r.air_mass_energy_absorption, e)? / material_mu(&r, &f, e)?
                        * 1000.0
                        * 3600.0,
                )
            };
            let extrema = |a: f64, b: f64| contact_extrema(&r, &f, 2.0, a, b);
            let model = DoseModel {
                value: &value,
                extrema: &extrema,
            };
            dose_range(groups, e_cap, 1.0e3, &model).range.unwrap()
        }

        #[test]
        fn contact_bounds_contain_the_estimate_and_are_exact_extrema() {
            let nu = beta_minus();
            let (src, _) = build(&nu, 3.0e6, &ESTIMATE);
            let est = src.estimated_spectra.as_ref().unwrap();
            let n = &est.by_nuclide[0];
            let range = n.contact_gamma_air_dose_proxy_Gy_h.as_ref().unwrap();
            assert!(range.lower <= range.estimate && range.estimate <= range.upper);
            let reference: f64 = n
                .groups
                .iter()
                .filter(|g| g.power_W_g > 0.0)
                .map(|g| g.power_W_g * contact_rho(g.centroid_eV))
                .sum();
            assert!(rel(range.estimate, reference) < 1e-12);
            // No Q-value: every group below the top of the structure counts.
            assert_eq!(n.e_cap_eV, 1.0e7);
            let power: f64 = n.groups.iter().map(|g| g.power_W_g).sum();
            // The part of the first group below the response's lowest energy gives no dose.
            assert_eq!(range.lower, 0.0);
            let (_, hi) = contact_extrema(&response(), &fractions(), 2.0, 1.0e3, 1.0e7).unwrap();
            assert!(rel(range.upper, power * hi) < 1e-12);
            // With no evaluated nuclide the step's estimate is the estimated nuclide's.
            let step = &est.contact_gamma_air_dose_proxy_Gy_h;
            assert_eq!(step.represented_only, Some(0.0));
            assert!(rel(step.estimate.unwrap(), range.estimate) < 1e-12);
            assert!(rel(step.upper.unwrap(), range.upper) < 1e-12);
            assert!(rel(step.estimated_share.unwrap(), 1.0) < 1e-12);
            assert!(rel(range.share_of_step_estimate.unwrap(), 1.0) < 1e-12);
        }

        #[test]
        fn q_cap_narrows_the_bounds_to_the_intervals_below_it() {
            let nu = nuclide(2.0e5, 7.5e4, vec![mode(1.0, 5.0e4, 1.0)], vec![]);
            let (src, _) = build(&nu, 3.0e6, &ESTIMATE);
            let n = &src.estimated_spectra.as_ref().unwrap().by_nuclide[0];
            assert_eq!(
                (n.e_max_manual_eV, n.e_max_eV, n.e_cap_eV),
                (4.0e5, 5.0e4, 5.0e4)
            );
            assert_eq!(
                allowed_intervals(&BOUNDS, n.e_cap_eV),
                vec![[0.0, 1.0e4], [1.0e4, 5.0e4]]
            );
            let range = n.contact_gamma_air_dose_proxy_Gy_h.as_ref().unwrap();
            let power: f64 = n.groups.iter().map(|g| g.power_W_g).sum();
            let (r, f) = (response(), fractions());
            let (_, hi_a) = contact_extrema(&r, &f, 2.0, 1.0e3, 1.0e4).unwrap();
            let (_, hi_b) = contact_extrema(&r, &f, 2.0, 1.0e4, 5.0e4).unwrap();
            assert_eq!(range.lower, 0.0);
            assert!(rel(range.upper, power * hi_a.max(hi_b)) < 1e-12);
            assert!(range.upper >= range.estimate);
        }

        #[test]
        fn power_law_ratio_has_its_extremum_at_an_endpoint() {
            let (r, f) = (response(), fractions());
            // One log-log piece (1e4 to 1e5 eV): the air-to-material ratio is a power law.
            let (lo, hi) = contact_extrema(&r, &f, 2.0, 2.0e4, 8.0e4).unwrap();
            let (a, b) = (contact_rho(2.0e4), contact_rho(8.0e4));
            assert!(rel(lo, a.min(b)) < 1e-12 && rel(hi, a.max(b)) < 1e-12);
            // Over the whole table no energy leaves the extrema.
            let (lo, hi) = contact_extrema(&r, &f, 2.0, 1.0e3, 1.0e7).unwrap();
            for i in 0..=4000 {
                let e = 1.0e3 * 1.0e4_f64.powf(i as f64 / 4000.0);
                let v = contact_rho(e.min(1.0e7));
                assert!(
                    v >= lo * (1.0 - 1e-12) && v <= hi * (1.0 + 1e-12),
                    "{e}: {v}"
                );
            }
            // Past the top of the table the extrema are unavailable.
            assert!(contact_extrema(&r, &f, 2.0, 1.0e6, 2.0e7).is_none());
        }

        #[test]
        fn point_source_curve_with_an_interior_maximum_is_found() {
            // f(E) = E exp(-1e-6 E) peaks at E* = 1/1e-6 = 1e6 eV, above both end values.
            let values = point_candidates(1.0, 1.0, 1.0, Some((1.0e-6, 1.0)), 1.0e5, 1.0e7);
            assert_eq!(values.len(), 3);
            let (_, hi) = extent(&values);
            let end = |e: f64| e * (-1.0e-6 * e).exp();
            assert!(rel(hi, 1.0e6 * (-1.0_f64).exp()) < 1e-12);
            assert!(hi > end(1.0e5) && hi > end(1.0e7));
            // A stationary point outside the interval, or no attenuation, adds no candidate.
            assert_eq!(
                point_candidates(1.0, 1.0, 1.0, Some((1.0e-6, 1.0)), 1.0e7, 2.0e7).len(),
                2
            );
            assert_eq!(point_candidates(1.0, 1.0, 1.0, None, 1.0e5, 1.0e7).len(), 2);
            // Dense scan: nothing exceeds the extrema.
            let (lo, hi) = extent(&values);
            for i in 0..=10_000 {
                let e = 1.0e5 + (1.0e7 - 1.0e5) * i as f64 / 10_000.0;
                assert!(end(e) >= lo * (1.0 - 1e-12) && end(e) <= hi * (1.0 + 1e-12));
            }
        }

        #[test]
        fn evaluated_spectrum_with_arbitrary_lines_lies_within_the_bounds() {
            // Lines at arbitrary energies, none above the cap; the model's own dose.
            let lines = [(3.3e3, 0.2), (4.7e4, 0.5), (7.7e5, 0.1), (2.9e6, 0.01)];
            let e_em: f64 = lines.iter().map(|&(e, y)| e * y).sum();
            let nu = nuclide(
                0.0,
                e_em,
                vec![mode(2.0, 0.0, 1.0)],
                vec![line_spectrum(0.0, &lines)],
            );
            let point = PointSourceOptions {
                distance_m: 1.0,
                air_attenuation: true,
                air_density_g_cm3: 1.20479e-3,
            };
            let (mut src, _) = build(&nu, 3.0e6, &SourceModel::default());
            add_point_source_dose(&mut src, Some(&response()), &point, 2.0);
            let proxy = src.contact_gamma_air_dose_proxy_Gy_h.unwrap();
            let dose = src
                .point_source_dose
                .as_ref()
                .unwrap()
                .gamma_air_dose_Sv_h
                .unwrap();
            let r = response();
            let value = |e: f64| {
                let mu_en = curve_value(&r.air_mass_energy_absorption, e)?;
                let mu_rho = curve_value(r.air_mass_attenuation.as_ref().unwrap(), e)?;
                Some(
                    mu_en * 0.1 * (-mu_rho * point.air_density_g_cm3 * 100.0).exp()
                        / (4.0 * std::f64::consts::PI)
                        * 3600.0
                        * 2.0,
                )
            };
            let extrema = |a: f64, b: f64| {
                point_extrema(&r, r.air_mass_attenuation.as_ref(), &point, 2.0, a, b)
            };
            let point_model = DoseModel {
                value: &value,
                extrema: &extrema,
            };
            for cap in [1.0e7, 3.0e6] {
                let contact = contact_range(&src.groups, cap);
                assert!(rel(contact.estimate, proxy) < 1e-12);
                assert!(
                    contact.lower <= proxy && proxy <= contact.upper,
                    "cap {cap}"
                );
                let range = dose_range(&src.groups, cap, 1.0e3, &point_model)
                    .range
                    .unwrap();
                assert!(rel(range.estimate, dose) < 1e-12);
                assert!(range.lower <= dose && dose <= range.upper, "cap {cap}");
            }
        }

        #[test]
        fn point_source_dose_is_given_with_bounds_instead_of_withheld() {
            let nu = beta_minus();
            let point = PointSourceOptions {
                distance_m: 1.0,
                air_attenuation: true,
                air_density_g_cm3: 1.20479e-3,
            };
            let (mut src, _) = build(&nu, 3.0e6, &ESTIMATE);
            add_point_source_dose(&mut src, Some(&response()), &point, 2.0);
            let dose = src
                .point_source_dose
                .as_ref()
                .unwrap()
                .gamma_air_dose_Sv_h
                .unwrap();
            let est = src.estimated_spectra.as_ref().unwrap();
            let step = est.point_source_gamma_air_dose_Sv_h.as_ref().unwrap();
            assert_eq!(step.represented_only, Some(0.0));
            let (value, lower, upper) = (
                step.estimate.unwrap(),
                step.lower.unwrap(),
                step.upper.unwrap(),
            );
            assert!(lower <= value && value <= upper && lower < upper);
            assert_eq!(dose, value);
            let own = est.by_nuclide[0]
                .point_source_gamma_air_dose_Sv_h
                .as_ref()
                .unwrap();
            assert!(rel(own.estimate, value) < 1e-12);
            // Without the estimate the dose stays withheld.
            let (mut report, _) = build(&nu, 3.0e6, &SourceModel::default());
            add_point_source_dose(&mut report, Some(&response()), &point, 2.0);
            let out = report.point_source_dose.as_ref().unwrap();
            assert!(out.gamma_air_dose_Sv_h.is_none() && out.unavailable_reason.is_some());
            assert!(report.estimated_spectra.is_none());
        }

        #[test]
        fn step_point_source_dose_is_the_estimate_by_definition() {
            // An evaluated and an estimated nuclide share the 10 to 100 keV group, so one dose
            // of the combined groups would differ from the sum at each nuclide's own centroid.
            let evaluated = nuclide(
                0.0,
                4.7e4 * 0.5,
                vec![mode(2.0, 0.0, 1.0)],
                vec![line_spectrum(0.0, &[(4.7e4, 0.5)])],
            );
            let estimated = beta_minus();
            let point = PointSourceOptions {
                distance_m: 1.0,
                air_attenuation: true,
                air_density_g_cm3: 1.20479e-3,
            };
            let (mut src, _) = source_for_step_with(
                &[("Fe59", &evaluated, 3.0e6), ("Co60", &estimated, 3.0e6)],
                &BOUNDS,
                "test",
                2.0,
                Some(&response()),
                &fractions(),
                true,
                2.0,
                0.0,
                &ESTIMATE,
            )
            .unwrap();
            add_point_source_dose(&mut src, Some(&response()), &point, 2.0);
            let out = src.point_source_dose.as_ref().unwrap();
            let step = src
                .estimated_spectra
                .as_ref()
                .unwrap()
                .point_source_gamma_air_dose_Sv_h
                .as_ref()
                .unwrap();
            let own = src.estimated_spectra.as_ref().unwrap().by_nuclide[0]
                .point_source_gamma_air_dose_Sv_h
                .as_ref()
                .unwrap()
                .estimate;
            assert_eq!(out.gamma_air_dose_Sv_h, step.estimate);
            assert_eq!(
                out.gamma_air_dose_Sv_h_per_g,
                step.estimate.map(|d| d / 2.0)
            );
            assert!(out.unavailable_reason.is_none());
            assert!(rel(step.estimate.unwrap(), step.represented_only.unwrap() + own) < 1e-12);
        }

        fn xray_nuclide(e_em: f64) -> Nuclide {
            nuclide(
                0.0,
                e_em,
                vec![mode(2.0, 0.0, 1.0)],
                vec![
                    line_spectrum(0.0, &[(1.0e5, 1.0)]),
                    line_spectrum(9.0, &[(2.0e4, 0.5)]),
                ],
            )
        }

        #[test]
        fn xray_exclusion_removes_the_scaled_styp9_power() {
            // Evaluated total 1.1e5 eV against a mean EM energy of 2.2e5 eV: scale 2.
            let nu = xray_nuclide(2.2e5);
            let on = SourceModel {
                estimate_missing: false,
                exclude_xrays: true,
            };
            let (src, diag) = build(&nu, 4.0e3, &on);
            let excluded = 4.0e3 * 2.0 * 0.5 * 2.0e4 * EV;
            assert!(rel(src.excluded_xray_power_W_g.unwrap(), excluded) < 1e-12);
            assert!(rel(diag.excluded_xray_power_W_g, excluded) < 1e-12);
            assert!(rel(src.by_nuclide[0].excluded_xray_power_W_g.unwrap(), excluded) < 1e-12);
            assert!(rel(src.source_power_W_g, 4.0e3 * 2.0 * 1.0e5 * EV) < 1e-12);
            assert_eq!(src.unrepresented_gamma_power_W_g, 0.0);
            assert_eq!(src.lines.len(), 1);
            assert_eq!(src.lines[0].energy_eV, 1.0e5);
            let (full, _) = build(&nu, 4.0e3, &SourceModel::default());
            assert!(rel(full.source_power_W_g, 4.0e3 * 2.2e5 * EV) < 1e-12);
            assert!(full.excluded_xray_power_W_g.is_none());
            assert!(rel(src.source_power_W_g + excluded, full.source_power_W_g) < 1e-12);
            assert_eq!(src.by_nuclide[0].energy_normalization, 2.0);
        }

        #[test]
        fn xray_only_nuclide_is_excluded_not_unrepresented() {
            let nu = nuclide(
                0.0,
                1.0e4,
                vec![mode(2.0, 0.0, 1.0)],
                vec![line_spectrum(9.0, &[(2.0e4, 0.5)])],
            );
            let on = SourceModel {
                estimate_missing: false,
                exclude_xrays: true,
            };
            let (src, diag) = build(&nu, 4.0e3, &on);
            assert_eq!(src.unrepresented_gamma_power_W_g, 0.0);
            assert!(diag
                .nuclides_with_em_energy_but_no_photon_spectrum
                .is_empty());
            assert_eq!(src.source_power_W_g, 0.0);
            assert!(rel(src.excluded_xray_power_W_g.unwrap(), 4.0e3 * 1.0e4 * EV) < 1e-12);
            // Kept, the same nuclide is an ordinary source.
            let (full, _) = build(&nu, 4.0e3, &SourceModel::default());
            assert!(rel(full.source_power_W_g, 4.0e3 * 1.0e4 * EV) < 1e-12);
        }

        #[test]
        fn default_output_has_no_new_keys() {
            let nu = xray_nuclide(1.1e5);
            let (src, diag) = source_for_step(
                &[("Co60", &nu, 1.0)],
                &BOUNDS,
                "test",
                1.0,
                Some(&response()),
                &fractions(),
                true,
                2.0,
                0.0,
            )
            .unwrap();
            let value = serde_json::to_value(&src).unwrap();
            for key in [
                "estimated_gamma_power_W_g",
                "excluded_xray_power_W_g",
                "estimated_spectra",
            ] {
                assert!(value.get(key).is_none(), "{key}");
            }
            assert!(value["by_nuclide"][0]
                .get("excluded_xray_power_W_g")
                .is_none());
            let value = serde_json::to_value(&diag).unwrap();
            for key in [
                "nuclides_with_estimated_photon_spectrum",
                "estimated_gamma_power_W_g",
                "excluded_xray_power_W_g",
            ] {
                assert!(value.get(key).is_none(), "{key}");
            }
        }
    }
}
