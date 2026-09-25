//! First-order MF=33 response propagation and its explicit reporting types.

use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize)]
pub struct SensitivityParameter {
    pub library_row: usize,
    pub target: usize,
    pub target_nuclide: String,
    #[serde(rename = "target_ZA")]
    pub target_za: i32,
    #[serde(rename = "target_LISO")]
    pub target_liso: i32,
    #[serde(rename = "MT")]
    pub mt: i32,
    #[serde(rename = "ZAP")]
    pub zap: i32,
    #[serde(rename = "LFS")]
    pub lfs: i32,
    #[serde(rename = "LMF")]
    pub lmf: i32,
    /// Which schedule spectrum this parameter's collapse belongs to: 0 is the
    /// base spectrum, higher values index the schedule's distinct step
    /// overrides. Single-spectrum runs report 0 for every parameter.
    pub spectrum: usize,
    pub collapsed_cross_section_b: f64,
    pub covariance_covered: bool,
    /// The parameter's self block was excluded under the frozen
    /// asymmetry/PSD defect rules; it contributes nothing to the variance.
    pub covariance_excluded: bool,
}

/// A decay-constant uncertainty parameter (P20 G4): one radioactive nuclide's
/// `lambda = ln2 / T_half`, propagated as a diagonal (uncorrelated) variance.
#[derive(Clone, Debug, Serialize)]
pub struct DecayParameter {
    pub nuclide: String,
    #[serde(rename = "ZA")]
    pub za: i32,
    #[serde(rename = "LISO")]
    pub liso: i32,
    /// Decay constant in s^-1.
    pub lambda_s: f64,
    /// Standard uncertainty of `lambda` in s^-1, `lambda * dThalf/Thalf` from
    /// the MF=8/MT=457 LIST field; zero marks a file carrying no such field.
    pub standard_uncertainty_s: f64,
    /// Whether the evaluated decay file provides a half-life uncertainty.
    pub covered: bool,
}

/// A fission-yield uncertainty parameter (P20 G4): one (parent, product)
/// independent yield at the case's incident energy, diagonal variance.
#[derive(Clone, Debug, Serialize)]
pub struct YieldParameter {
    pub parent_nuclide: String,
    #[serde(rename = "parent_ZA")]
    pub parent_za: i32,
    #[serde(rename = "parent_LISO")]
    pub parent_liso: i32,
    pub product_nuclide: String,
    #[serde(rename = "product_ZA")]
    pub product_za: i32,
    #[serde(rename = "product_LISO")]
    pub product_liso: i32,
    /// Which schedule spectrum this parameter's parent-rate collapse belongs
    /// to: 0 is the base spectrum. Single-spectrum runs report 0.
    pub spectrum: usize,
    /// Effective independent yield at the case's incident energy.
    pub yield_value: f64,
    /// Interpolated independent-yield standard uncertainty from the MF=8/MT=454
    /// `DY` field; zero marks an entry carrying no uncertainty data.
    pub standard_uncertainty: f64,
    /// Whether the yield evaluation provides an uncertainty for this entry.
    pub covered: bool,
}

/// Per-channel uncertainty breakdown for one response band.
#[derive(Debug, Serialize)]
pub struct ChannelReport {
    pub channel: &'static str,
    /// `propagated` (variance carried into the band) or `not_evaluated`
    /// (named as uncovered by this band).
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub standard_uncertainty: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub coverage: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub covered_parameters: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_parameters: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<&'static str>,
}

#[derive(Clone, Debug, Serialize)]
pub struct SensitivityOut {
    pub parameter: SensitivityParameter,
    pub value: f64,
    pub unit: String,
}

#[derive(Debug, Serialize)]
pub struct DecaySensitivityOut {
    pub parameter: DecayParameter,
    /// Response derivative with respect to the decay constant.
    pub value: f64,
    pub unit: String,
}

#[derive(Debug, Serialize)]
pub struct YieldSensitivityOut {
    pub parameter: YieldParameter,
    /// Response derivative with respect to the independent yield.
    pub value: f64,
    pub unit: String,
}

#[derive(Debug, Serialize)]
pub struct ResponseUncertainty {
    pub nominal: f64,
    pub unit: String,
    pub mf33_standard_uncertainty: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub relative_standard_uncertainty: Option<f64>,
    pub confidence_level: f64,
    pub normal_multiplier: f64,
    pub normal_interval: [f64; 2],
    pub cram_order_bound: f64,
    pub conservative_interval: [f64; 2],
    pub negative_variance_roundoff_removed: f64,
    pub coverage: String,
    pub covered_parameters: usize,
    pub total_parameters: usize,
    /// Per-channel breakdown; every band names the channels it does not cover.
    pub channels: Vec<ChannelReport>,
    /// Standard uncertainty over every propagated channel, emitted only when a
    /// channel beyond `cross_section_mf33` was requested. `normal_interval` and
    /// `conservative_interval` are built on this combined variance in that case.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub combined_standard_uncertainty: Option<f64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub decay_sensitivities: Vec<DecaySensitivityOut>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub yield_sensitivities: Vec<YieldSensitivityOut>,
    pub sensitivities: Vec<SensitivityOut>,
    /// P50 value-of-information table; present only when the spec requested
    /// `uncertainty.voi`. Absence is byte-identical to pre-P50 output.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub voi: Option<VoiReport>,
}

#[derive(Debug, Serialize)]
pub struct StepUncertainty {
    pub method: &'static str,
    pub uncovered_library_rows: Vec<usize>,
    pub absent_cross_parameter_pairs: usize,
    pub maximum_covariance_asymmetry_barn2: f64,
    pub excluded_blocks: Vec<actinv_data::covariance::ExcludedBlock>,
    /// Kept radioactive nuclides whose decay file carries no half-life
    /// uncertainty; present only when the `decay_constants` channel runs.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub uncovered_decay_constants: Vec<String>,
    /// Active (parent, product) yield edges whose evaluation carries no `DY`
    /// uncertainty; present only when the `fission_yields` channel runs.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub uncovered_yield_products: Vec<String>,
    pub responses: BTreeMap<String, ResponseUncertainty>,
}

/// One non-MF=33 channel's propagation result for a response band.
pub struct ChannelData<S> {
    /// Channel variance (diagonal: sum of (sensitivity * sigma)^2).
    pub variance: f64,
    /// Sensitivity-bearing parameters with a file-provided uncertainty.
    pub covered_parameters: usize,
    /// Sensitivity-bearing parameters in the channel.
    pub total_parameters: usize,
    pub sensitivities: Vec<S>,
}

pub struct BandInput {
    pub nominal: f64,
    pub alternate: f64,
    pub unit: String,
    pub confidence_level: f64,
    pub normal_multiplier: f64,
    pub variance: f64,
    pub negative_variance_roundoff_removed: f64,
    pub sensitivities: Vec<SensitivityOut>,
    /// `Some` when the `decay_constants` channel was requested; `None`
    /// preserves the G2 output shape exactly.
    pub decay_channel: Option<ChannelData<DecaySensitivityOut>>,
    /// `Some` when the `fission_yields` channel was requested.
    pub fission_yield_channel: Option<ChannelData<YieldSensitivityOut>>,
}

/// One ranked parameter in a P50 value-of-information table.
#[derive(Debug, Serialize)]
pub struct VoiEntry {
    /// `cross_section_mf33`, `decay_constants` or `fission_yields`.
    pub channel: &'static str,
    /// The channel's parameter record (SensitivityParameter, DecayParameter or
    /// YieldParameter), serialized as emitted elsewhere in this response.
    pub parameter: serde_json::Value,
    /// Response derivative with respect to the parameter, in this response's
    /// sensitivity unit.
    pub sensitivity: f64,
    /// Declared per-parameter standard uncertainty; emitted for the diagonal
    /// channels (decay, yield), absent for MF=33 rows whose dispersion lives
    /// in the covariance block.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub standard_uncertainty: Option<f64>,
    /// This parameter's share of the propagated variance:
    /// `s_i·(Σ·s)_i` for MF=33 (negative under anticorrelation),
    /// `(s_i·σ_i)^2` for the diagonal channels.
    pub variance_share: f64,
    /// `variance_share / total_propagated_variance`; null when the total
    /// variance is zero or nonfinite.
    pub share_fraction: Option<f64>,
}

/// Sensitivity-bearing parameters with no covariance coverage; named honestly,
/// never ranked at zero.
#[derive(Debug, Serialize)]
pub struct VoiUnranked {
    pub count: usize,
    /// sqrt(Σ s_i^2) over the family's uncovered sensitivity-bearing
    /// parameters — a magnitude proxy, not a variance.
    pub sensitivity_l2: f64,
}

/// Ranked variance-share table emitted inside a `ResponseUncertainty` when the
/// spec requests `uncertainty.voi` (P50).
#[derive(Debug, Serialize)]
pub struct VoiReport {
    /// Up to `top` parameters by `|variance_share|` descending.
    pub top: Vec<VoiEntry>,
    /// The variance the emitted band was built on (MF=33 plus declared
    /// diagonal channels), not a recomputation.
    pub total_propagated_variance: f64,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub unranked: BTreeMap<&'static str, VoiUnranked>,
}

pub fn propagated_variance(
    sensitivities: &[f64],
    covariance: &[f64],
) -> Result<(f64, f64), String> {
    let size = sensitivities.len();
    if covariance.len() != size * size {
        return Err(format!(
            "covariance has {} entries for {size} sensitivities",
            covariance.len()
        ));
    }
    if sensitivities.iter().any(|value| !value.is_finite())
        || covariance.iter().any(|value| !value.is_finite())
    {
        return Err("nonfinite sensitivity or covariance component".into());
    }
    let mut variance = 0.0;
    let mut absolute_sum = 0.0;
    for row in 0..size {
        for column in 0..size {
            let term = sensitivities[row] * covariance[row * size + column] * sensitivities[column];
            variance += term;
            absolute_sum += term.abs();
        }
    }
    if !variance.is_finite() || !absolute_sum.is_finite() {
        return Err("propagated response variance overflowed".into());
    }
    let tolerance = 128.0 * f64::EPSILON * absolute_sum;
    if variance < -tolerance {
        return Err(format!(
            "materially negative propagated response variance {variance:.17e} (round-off bound {tolerance:.17e})"
        ));
    }
    if variance < 0.0 {
        Ok((0.0, -variance))
    } else {
        Ok((variance, 0.0))
    }
}

pub fn normal_multiplier(confidence_level: f64) -> f64 {
    let target = 0.5 * (1.0 + confidence_level);
    let mut low = 0.0;
    let mut high = 10.0;
    for _ in 0..80 {
        let middle = 0.5 * (low + high);
        let probability = 0.5 * (1.0 + libm::erf(middle / std::f64::consts::SQRT_2));
        if probability < target {
            low = middle;
        } else {
            high = middle;
        }
    }
    0.5 * (low + high)
}

fn channel_coverage(covered: usize, total: usize) -> &'static str {
    if covered == total {
        "complete"
    } else {
        "partial"
    }
}

pub fn response_band(input: BandInput) -> Result<ResponseUncertainty, String> {
    let extra_channels = input.decay_channel.is_some() || input.fission_yield_channel.is_some();
    if !input.nominal.is_finite()
        || !input.alternate.is_finite()
        || !input.variance.is_finite()
        || input.variance < 0.0
        || input
            .decay_channel
            .as_ref()
            .is_some_and(|channel| !channel.variance.is_finite() || channel.variance < 0.0)
        || input
            .fission_yield_channel
            .as_ref()
            .is_some_and(|channel| !channel.variance.is_finite() || channel.variance < 0.0)
    {
        return Err("nonfinite response value or invalid propagated variance".into());
    }
    let mf33_uncertainty = input.variance.sqrt();
    let combined_variance = input.variance
        + input
            .decay_channel
            .as_ref()
            .map_or(0.0, |channel| channel.variance)
        + input
            .fission_yield_channel
            .as_ref()
            .map_or(0.0, |channel| channel.variance);
    let standard_uncertainty = combined_variance.sqrt();
    let half_width = input.normal_multiplier * standard_uncertainty;
    let cram_order_bound = (input.alternate - input.nominal).abs();
    let normal_interval = [input.nominal - half_width, input.nominal + half_width];
    let conservative_interval = [
        normal_interval[0] - cram_order_bound,
        normal_interval[1] + cram_order_bound,
    ];
    if normal_interval.iter().any(|value| !value.is_finite())
        || conservative_interval.iter().any(|value| !value.is_finite())
    {
        return Err("uncertainty interval overflowed".into());
    }
    let covered_parameters = input
        .sensitivities
        .iter()
        .filter(|record| {
            record.parameter.covariance_covered
                && !record.parameter.covariance_excluded
                && record.value != 0.0
        })
        .count();
    let total_parameters = input
        .sensitivities
        .iter()
        .filter(|record| record.value != 0.0)
        .count();
    let mf33_coverage = channel_coverage(covered_parameters, total_parameters);
    let decay_coverage = input
        .decay_channel
        .as_ref()
        .map(|channel| channel_coverage(channel.covered_parameters, channel.total_parameters));
    let yield_coverage = input
        .fission_yield_channel
        .as_ref()
        .map(|channel| channel_coverage(channel.covered_parameters, channel.total_parameters));
    let band_complete = mf33_coverage == "complete"
        && decay_coverage.is_none_or(|coverage| coverage == "complete")
        && yield_coverage.is_none_or(|coverage| coverage == "complete");
    let channel_coverage = if band_complete { "complete" } else { "partial" };
    let channels = vec![
        ChannelReport {
            channel: "cross_section_mf33",
            status: "propagated",
            standard_uncertainty: Some(mf33_uncertainty),
            coverage: Some(mf33_coverage.into()),
            covered_parameters: Some(covered_parameters),
            total_parameters: Some(total_parameters),
            note: None,
        },
        match &input.decay_channel {
            Some(channel) => ChannelReport {
                channel: "decay_constants",
                status: "propagated",
                standard_uncertainty: Some(channel.variance.sqrt()),
                coverage: Some(decay_coverage.unwrap_or("partial").into()),
                covered_parameters: Some(channel.covered_parameters),
                total_parameters: Some(channel.total_parameters),
                note: Some(
                    "MF=8/MT=457 half-life uncertainties propagated as diagonal variances; the evaluations carry no correlation data",
                ),
            },
            None => ChannelReport {
                channel: "decay_constants",
                status: "not_evaluated",
                standard_uncertainty: None,
                coverage: None,
                covered_parameters: None,
                total_parameters: None,
                note: Some(
                    "MF=8/MT=457 half-life uncertainties are not propagated by this band",
                ),
            },
        },
        match &input.fission_yield_channel {
            Some(channel) => ChannelReport {
                channel: "fission_yields",
                status: "propagated",
                standard_uncertainty: Some(channel.variance.sqrt()),
                coverage: Some(yield_coverage.unwrap_or("partial").into()),
                covered_parameters: Some(channel.covered_parameters),
                total_parameters: Some(channel.total_parameters),
                note: Some(
                    "MF=8/MT=454 independent-yield uncertainties propagated as diagonal variances; the evaluations carry no correlation data",
                ),
            },
            None => ChannelReport {
                channel: "fission_yields",
                status: "not_evaluated",
                standard_uncertainty: None,
                coverage: None,
                covered_parameters: None,
                total_parameters: None,
                note: Some(
                    "MF=8/MT=454 independent-yield uncertainties are not propagated by this band",
                ),
            },
        },
        ChannelReport {
            channel: "uncovered_remainder",
            status: "not_evaluated",
            standard_uncertainty: None,
            coverage: None,
            covered_parameters: None,
            total_parameters: None,
            note: Some(
                "incident-flux, material-composition, response-coefficient and model-discrepancy terms are named uncovered",
            ),
        },
    ];
    Ok(ResponseUncertainty {
        nominal: input.nominal,
        unit: input.unit,
        mf33_standard_uncertainty: mf33_uncertainty,
        relative_standard_uncertainty: (input.nominal != 0.0)
            .then_some(standard_uncertainty / input.nominal.abs()),
        confidence_level: input.confidence_level,
        normal_multiplier: input.normal_multiplier,
        normal_interval,
        cram_order_bound,
        conservative_interval,
        negative_variance_roundoff_removed: input.negative_variance_roundoff_removed,
        coverage: channel_coverage.into(),
        covered_parameters,
        total_parameters,
        channels,
        combined_standard_uncertainty: extra_channels.then_some(standard_uncertainty),
        decay_sensitivities: input
            .decay_channel
            .map(|channel| channel.sensitivities)
            .unwrap_or_default(),
        yield_sensitivities: input
            .fission_yield_channel
            .map(|channel| channel.sensitivities)
            .unwrap_or_default(),
        sensitivities: input.sensitivities,
        voi: None,
    })
}

#[cfg(test)]
mod tests {
    use super::propagated_variance;

    #[test]
    fn cross_covariance_is_retained() {
        let sensitivity = [2.0, -3.0];
        let covariance = [4.0, 0.5, 0.5, 9.0];
        let (variance, residue) = propagated_variance(&sensitivity, &covariance).unwrap();
        assert_eq!(variance, 91.0);
        assert_eq!(residue, 0.0);
    }

    #[test]
    fn materially_negative_variance_fails() {
        assert!(propagated_variance(&[1.0, 1.0], &[1.0, -2.0, -2.0, 1.0]).is_err());
    }
}
