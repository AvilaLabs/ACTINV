//! P162: starting data for a new problem. A cited material library embedded in the binary, and four
//! analytic source spectra integrated over the `fispact-709` groups.
//!
//! These are textbook shapes and generic compositions, not a facility's spectrum or a user's
//! material certificate; the labels and notes say so.

use actinv_data::composition::z_of;
use actinv_data::groups::GroupStructure;
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};

const MATERIALS_JSON: &str = include_str!("../data/materials-v1.json");
const MATERIALS_SCHEMA: &str = "actinv-materials-1";
const BASES: [&str; 3] = ["nominal", "specification_maxima", "measured_heat"];
const SUM_TOLERANCE: f64 = 1e-9;

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MaterialSource {
    pub citation: String,
    pub table: String,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MaterialPreset {
    pub id: String,
    pub name: String,
    pub category: String,
    /// weight percent by element symbol, every value positive, summing to 100
    pub composition_wt_percent: BTreeMap<String, f64>,
    /// `nominal`, `specification_maxima` or `measured_heat`
    pub basis: String,
    pub density_g_cm3: Option<f64>,
    pub source: MaterialSource,
    pub note: String,
    /// the standard atomic weights used where a composition was converted from atom percent
    #[serde(default)]
    pub atomic_weights_used: Option<BTreeMap<String, f64>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MaterialFile {
    schema: String,
    materials: Vec<MaterialPreset>,
}

fn validate(entry: &MaterialPreset) -> Result<(), String> {
    let id = &entry.id;
    if id.is_empty() || entry.name.is_empty() || entry.source.citation.trim().is_empty() {
        return Err(format!(
            "material '{id}' needs an id, a name and a citation"
        ));
    }
    if !BASES.contains(&entry.basis.as_str()) {
        return Err(format!(
            "material '{id}' has unknown basis '{}'",
            entry.basis
        ));
    }
    if entry.composition_wt_percent.is_empty() {
        return Err(format!("material '{id}' has no composition"));
    }
    for (symbol, value) in &entry.composition_wt_percent {
        if z_of(symbol).is_none() {
            return Err(format!("material '{id}' has unknown element '{symbol}'"));
        }
        if !value.is_finite() || *value <= 0.0 {
            return Err(format!(
                "material '{id}': {symbol} must be positive, got {value}"
            ));
        }
    }
    let sum: f64 = entry.composition_wt_percent.values().sum();
    if (sum - 100.0).abs() > SUM_TOLERANCE {
        return Err(format!("material '{id}' sums to {sum}, not 100"));
    }
    if entry
        .density_g_cm3
        .is_some_and(|d| !d.is_finite() || d <= 0.0)
    {
        return Err(format!("material '{id}' has a non-positive density"));
    }
    Ok(())
}

/// The embedded material library, validated: known element symbols, positive values summing to 100.
pub fn materials() -> Result<Vec<MaterialPreset>, String> {
    let file: MaterialFile = serde_json::from_str(MATERIALS_JSON)
        .map_err(|e| format!("material library is not valid: {e}"))?;
    if file.schema != MATERIALS_SCHEMA {
        return Err(format!(
            "material library has schema '{}', expected '{MATERIALS_SCHEMA}'",
            file.schema
        ));
    }
    let mut seen = BTreeSet::new();
    for entry in &file.materials {
        validate(entry)?;
        if !seen.insert(entry.id.as_str()) {
            return Err(format!("material id '{}' appears twice", entry.id));
        }
    }
    Ok(file.materials)
}

/// One library entry by id.
pub fn material(id: &str) -> Result<MaterialPreset, String> {
    let all = materials()?;
    let known: Vec<String> = all.iter().map(|m| m.id.clone()).collect();
    all.into_iter().find(|m| m.id == id).ok_or_else(|| {
        format!(
            "unknown material '{id}'; the library has: {}",
            known.join(", ")
        )
    })
}

/// A standard closed-form neutron spectrum shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpectrumShape {
    Fusion14MeV,
    Fission,
    Thermal,
    OneOverE,
}

impl SpectrumShape {
    pub const ALL: [SpectrumShape; 4] = [
        SpectrumShape::Fusion14MeV,
        SpectrumShape::Fission,
        SpectrumShape::Thermal,
        SpectrumShape::OneOverE,
    ];

    pub fn id(self) -> &'static str {
        match self {
            SpectrumShape::Fusion14MeV => "fusion-14mev",
            SpectrumShape::Fission => "fission",
            SpectrumShape::Thermal => "thermal",
            SpectrumShape::OneOverE => "one-over-e",
        }
    }

    pub fn from_id(id: &str) -> Result<Self, String> {
        Self::ALL
            .into_iter()
            .find(|shape| shape.id() == id)
            .ok_or_else(|| {
                let ids: Vec<&str> = Self::ALL.iter().map(|s| s.id()).collect();
                format!(
                    "unknown source shape '{id}'; the shapes are: {}",
                    ids.join(", ")
                )
            })
    }

    pub fn label(self) -> &'static str {
        match self {
            SpectrumShape::Fusion14MeV => "generic D-T 14.1 MeV line",
            SpectrumShape::Fission => "generic fission (Watt) spectrum",
            SpectrumShape::Thermal => "generic thermal (Maxwellian) spectrum",
            SpectrumShape::OneOverE => "generic 1/E slowing-down spectrum",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            SpectrumShape::Fusion14MeV => {
                "A generic shape, not a facility's spectrum: all flux in the group containing 14.1 MeV."
            }
            SpectrumShape::Fission => {
                "A generic shape, not a facility's spectrum: a Watt spectrum for U-235 thermal fission (a = 0.988 MeV, b = 2.249 per MeV)."
            }
            SpectrumShape::Thermal => {
                "A generic shape, not a facility's spectrum: a Maxwellian at kT = 0.0253 eV."
            }
            SpectrumShape::OneOverE => {
                "A generic shape, not a facility's spectrum: flux per unit energy proportional to 1/E from 0.5 eV to 1 MeV, zero outside."
            }
        }
    }
}

/// The D-T neutron energy, eV.
const FUSION_ENERGY_EV: f64 = 14.1e6;
/// Thermal kT at 293.6 K as used for the 0.0253 eV reference, eV.
const KT_EV: f64 = 0.0253;
/// Watt parameters for U-235 thermal fission, eV and 1/eV.
const WATT_A_EV: f64 = 0.988e6;
const WATT_B_PER_EV: f64 = 2.249e-6;
/// 1/E region, eV.
const ONE_OVER_E_LOW_EV: f64 = 0.5;
const ONE_OVER_E_HIGH_EV: f64 = 1.0e6;
/// Below this the Watt group integral uses a power series (the error-function form cancels
/// badly at low energy); above it, the error-function form.
const WATT_SERIES_LIMIT_EV: f64 = 1.0e5;

/// 1 - (1 + x) e^-x, the Maxwellian cumulative fraction at x = E / kT.
fn maxwell_cdf(x: f64) -> f64 {
    if x < 1.0 {
        // sum over n >= 2 of (-x)^n (n - 1) / n!, free of the cancellation in 1 - (1 + x) e^-x
        let mut power = x * x / 2.0; // (-x)^2 / 2!
        let mut sum = power;
        for n in 3..60 {
            power *= -x / n as f64;
            sum += power * (n - 1) as f64;
            if power.abs() < 1e-20 * sum.abs() {
                break;
            }
        }
        sum
    } else {
        1.0 - (1.0 + x) * (-x).exp()
    }
}

/// (1 + x) e^-x, the Maxwellian tail fraction.
fn maxwell_tail(x: f64) -> f64 {
    (1.0 + x) * (-x).exp()
}

fn maxwell_group(lo: f64, hi: f64) -> f64 {
    let (xl, xh) = (lo / KT_EV, hi / KT_EV);
    if xl >= 1.0 {
        maxwell_tail(xl) - maxwell_tail(xh)
    } else {
        maxwell_cdf(xh) - maxwell_cdf(xl)
    }
}

/// erf(z2) - erf(z1) for z2 > z1, from the complementary function on the side where it is small.
fn erf_difference(z1: f64, z2: f64) -> f64 {
    if z1 > 0.0 {
        libm::erfc(z1) - libm::erfc(z2)
    } else if z2 < 0.0 {
        libm::erfc(-z2) - libm::erfc(-z1)
    } else {
        libm::erf(z2) - libm::erf(z1)
    }
}

/// Integral of exp(-E/a) sinh(sqrt(bE)) over [lo, hi] by power series in y = E / a, for
/// hi <= `WATT_SERIES_LIMIT_EV` (y <= about 0.1, so a few dozen terms converge fully).
fn watt_series(lo: f64, hi: f64) -> f64 {
    let ab = WATT_A_EV * WATT_B_PER_EV;
    let (yl, yh) = (lo / WATT_A_EV, hi / WATT_A_EV);
    // e_n = sum over j + k = n of (-1)^j / j! * (ab)^k / (2k + 1)!
    const TERMS: usize = 40;
    let mut a_coeff = Vec::with_capacity(TERMS);
    let mut b_coeff = Vec::with_capacity(TERMS);
    let (mut fact_j, mut fact_k, mut ab_k) = (1.0f64, 1.0f64, 1.0f64);
    for n in 0..TERMS {
        if n > 0 {
            fact_j *= n as f64;
            ab_k *= ab;
            fact_k *= ((2 * n) * (2 * n + 1)) as f64;
        }
        a_coeff.push(if n % 2 == 0 { 1.0 } else { -1.0 } / fact_j);
        b_coeff.push(ab_k / fact_k);
    }
    let mut total = 0.0;
    for n in 0..TERMS {
        let e_n: f64 = (0..=n).map(|j| a_coeff[j] * b_coeff[n - j]).sum();
        let power = n as f64 + 1.5;
        total += e_n * (yh.powf(power) - yl.powf(power)) / power;
    }
    WATT_B_PER_EV.sqrt() * WATT_A_EV.powf(1.5) * total
}

/// Integral of exp(-E/a) sinh(sqrt(bE)) over [lo, hi] in closed form. With u = sqrt(E) and the
/// square completed, exp(-u^2/a) 2 sinh(sqrt(b) u) = exp(ab/4) [exp(-(u-c)^2/a) - exp(-(u+c)^2/a)]
/// with c = a sqrt(b) / 2, and each piece integrates against u to a Gaussian and an error function.
fn watt_erf(lo: f64, hi: f64) -> f64 {
    let (a, b) = (WATT_A_EV, WATT_B_PER_EV);
    let c = a * b.sqrt() / 2.0;
    let s = a.sqrt();
    let (u1, u2) = (lo.sqrt(), hi.sqrt());
    let (zp1, zp2) = ((u1 - c) / s, (u2 - c) / s);
    let (zm1, zm2) = ((u1 + c) / s, (u2 + c) / s);
    let gauss = (-zp2 * zp2).exp() - (-zp1 * zp1).exp() - (-zm2 * zm2).exp() + (-zm1 * zm1).exp();
    let erfs = erf_difference(zp1, zp2) + erf_difference(zm1, zm2);
    (a * b / 4.0).exp() * (-(a / 2.0) * gauss + c * (std::f64::consts::PI * a).sqrt() / 2.0 * erfs)
}

fn watt_group(lo: f64, hi: f64) -> f64 {
    if hi <= WATT_SERIES_LIMIT_EV {
        watt_series(lo, hi)
    } else if lo >= WATT_SERIES_LIMIT_EV {
        watt_erf(lo, hi)
    } else {
        watt_series(lo, WATT_SERIES_LIMIT_EV) + watt_erf(WATT_SERIES_LIMIT_EV, hi)
    }
}

fn one_over_e_group(lo: f64, hi: f64) -> f64 {
    let (l, h) = (lo.max(ONE_OVER_E_LOW_EV), hi.min(ONE_OVER_E_HIGH_EV));
    if h > l {
        (h / l).ln()
    } else {
        0.0
    }
}

/// Group values of `shape` on the `fispact-709` structure, scaled so they sum to `total_flux`
/// (particles cm^-2 s^-1). Listed highest energy first, the order of the structure's published
/// boundaries and of a spec with `"descending": true`; the first value is the highest group.
pub fn spectrum(shape: SpectrumShape, total_flux: f64) -> Result<Vec<f64>, String> {
    if !total_flux.is_finite() || total_flux <= 0.0 {
        return Err(format!(
            "the total flux must be finite and positive, got {total_flux}"
        ));
    }
    let bounds = GroupStructure::fispact_709()?.boundaries_ev;
    let mut ascending: Vec<f64> = match shape {
        SpectrumShape::Fusion14MeV => {
            let mut values = vec![0.0; bounds.len() - 1];
            let group = bounds
                .windows(2)
                .position(|w| (w[0]..w[1]).contains(&FUSION_ENERGY_EV))
                .ok_or("14.1 MeV is outside the fispact-709 structure")?;
            values[group] = 1.0;
            values
        }
        SpectrumShape::Fission => bounds.windows(2).map(|w| watt_group(w[0], w[1])).collect(),
        SpectrumShape::Thermal => bounds
            .windows(2)
            .map(|w| maxwell_group(w[0], w[1]))
            .collect(),
        SpectrumShape::OneOverE => bounds
            .windows(2)
            .map(|w| one_over_e_group(w[0], w[1]))
            .collect(),
    };
    let sum: f64 = ascending.iter().sum();
    if !sum.is_finite() || sum <= 0.0 {
        return Err(format!(
            "the {} shape has no flux in the groups",
            shape.id()
        ));
    }
    for value in &mut ascending {
        *value = (*value / sum * total_flux).max(0.0);
    }
    ascending.reverse();
    Ok(ascending)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn simpson(f: impl Fn(f64) -> f64, lo: f64, hi: f64, panels: usize) -> f64 {
        let h = (hi - lo) / panels as f64;
        let mut sum = f(lo) + f(hi);
        for i in 1..panels {
            sum += f(lo + h * i as f64) * if i % 2 == 1 { 4.0 } else { 2.0 };
        }
        sum * h / 3.0
    }

    fn ascending_bounds() -> Vec<f64> {
        GroupStructure::fispact_709().unwrap().boundaries_ev
    }

    #[test]
    fn every_library_entry_parses_and_sums_to_100() {
        let all = materials().unwrap();
        assert_eq!(all.len(), 19);
        for entry in &all {
            let sum: f64 = entry.composition_wt_percent.values().sum();
            assert!((sum - 100.0).abs() < 1e-9, "{}: {sum}", entry.id);
            assert!(!entry.source.citation.is_empty(), "{}", entry.id);
            assert_eq!(material(&entry.id).unwrap().id, entry.id);
        }
        assert!(material("no-such-material")
            .unwrap_err()
            .contains("eurofer97"));
    }

    #[test]
    fn a_bad_entry_is_refused() {
        let mut entry = material("ss316").unwrap();
        entry.composition_wt_percent.insert("Xx".into(), 1.0);
        assert!(validate(&entry).unwrap_err().contains("unknown element"));
        let mut entry = material("ss316").unwrap();
        *entry.composition_wt_percent.get_mut("Fe").unwrap() += 1e-6;
        assert!(validate(&entry).unwrap_err().contains("not 100"));
        let mut entry = material("ss316").unwrap();
        entry.basis = "typical".into();
        assert!(validate(&entry).unwrap_err().contains("basis"));
    }

    #[test]
    fn lead_lithium_is_converted_from_atom_percent() {
        let entry = material("pb-15.7li").unwrap();
        let weights = entry.atomic_weights_used.as_ref().unwrap();
        let (li, pb) = (weights["Li"], weights["Pb"]);
        let li_wt = 100.0 * 15.7 * li / (15.7 * li + 84.3 * pb);
        let got = entry.composition_wt_percent["Li"];
        assert!((got - li_wt).abs() < 1e-9, "{got} vs {li_wt}");
        assert!((got / 0.62 - 1.0).abs() < 1e-3);
    }

    #[test]
    fn every_spectrum_sums_to_the_total() {
        for shape in SpectrumShape::ALL {
            for total in [1.0, 1e12, 3.7e15] {
                let values = spectrum(shape, total).unwrap();
                assert_eq!(values.len(), 709);
                assert!(values.iter().all(|v| v.is_finite() && *v >= 0.0));
                let sum: f64 = values.iter().sum();
                assert!(
                    (sum / total - 1.0).abs() < 1e-12,
                    "{}: {sum} vs {total}",
                    shape.id()
                );
            }
            assert!(shape.description().contains("generic shape"));
            assert!(shape.label().contains("generic"));
            assert_eq!(SpectrumShape::from_id(shape.id()).unwrap(), shape);
        }
        assert!(SpectrumShape::from_id("flat").is_err());
    }

    #[test]
    fn a_bad_total_is_refused() {
        for total in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(spectrum(SpectrumShape::Thermal, total).is_err());
        }
    }

    #[test]
    fn fusion_line_is_in_the_group_holding_14_1_mev() {
        let values = spectrum(SpectrumShape::Fusion14MeV, 1e12).unwrap();
        let bounds = ascending_bounds();
        // listed highest energy first, so list index i is ascending group 708 - i
        let index = values.iter().position(|v| *v > 0.0).unwrap();
        let group = 708 - index;
        assert_eq!(values.iter().filter(|v| **v > 0.0).count(), 1);
        assert!((bounds[group]..bounds[group + 1]).contains(&14.1e6));
        assert_eq!(values[index], 1e12);
    }

    #[test]
    fn one_over_e_is_zero_outside_its_range_and_flat_in_lethargy_inside() {
        let values = spectrum(SpectrumShape::OneOverE, 1.0).unwrap();
        let bounds = ascending_bounds();
        for (i, value) in values.iter().rev().enumerate() {
            let (lo, hi) = (bounds[i], bounds[i + 1]);
            if hi <= 0.5 || lo >= 1e6 {
                assert_eq!(*value, 0.0);
            } else if lo >= 0.5 && hi <= 1e6 {
                let expect = (hi / lo).ln() / (1e6f64 / 0.5).ln();
                assert!((value / expect - 1.0).abs() < 1e-12, "group {i}");
            }
        }
    }

    /// Compare a group integral with composite Simpson on the groups holding most of the flux.
    fn check_against_quadrature(shape: SpectrumShape, phi: impl Fn(f64) -> f64) {
        let values = spectrum(shape, 1.0).unwrap();
        let bounds = ascending_bounds();
        let ascending: Vec<f64> = values.iter().rev().copied().collect();
        let raw: Vec<f64> = bounds
            .windows(2)
            .map(|w| simpson(&phi, w[0], w[1], 4000))
            .collect();
        let raw_sum: f64 = raw.iter().sum();
        let mut checked = 0;
        for (g, (value, quad)) in ascending.iter().zip(&raw).enumerate() {
            if *value < 1e-6 {
                continue;
            }
            checked += 1;
            let expect = quad / raw_sum;
            assert!(
                (value / expect - 1.0).abs() < 1e-8,
                "{}: group {g}: {value} vs {expect}",
                shape.id()
            );
        }
        assert!(checked > 20, "{checked} groups checked");
    }

    #[test]
    fn watt_groups_match_quadrature() {
        check_against_quadrature(SpectrumShape::Fission, |e| {
            (-e / WATT_A_EV).exp() * (WATT_B_PER_EV * e).sqrt().sinh()
        });
    }

    #[test]
    fn maxwellian_groups_match_quadrature() {
        check_against_quadrature(SpectrumShape::Thermal, |e| e * (-e / KT_EV).exp());
    }

    #[test]
    fn watt_series_and_error_function_forms_agree_where_both_are_sound() {
        for (lo, hi) in [(1.0e4, 1.2e4), (5.0e4, 5.5e4), (9.0e4, 1.0e5)] {
            let (s, e) = (watt_series(lo, hi), watt_erf(lo, hi));
            assert!((s / e - 1.0).abs() < 1e-8, "{lo}: {s} vs {e}");
        }
    }

    #[test]
    fn maxwellian_cdf_series_matches_the_closed_form() {
        for x in [0.3f64, 0.9, 1.0, 2.5] {
            let direct = 1.0 - (1.0 + x) * (-x).exp();
            assert!((maxwell_cdf(x) / direct - 1.0).abs() < 1e-12, "{x}");
        }
    }
}
