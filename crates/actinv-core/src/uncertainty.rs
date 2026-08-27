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
    pub collapsed_cross_section_b: f64,
    pub covariance_covered: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct SensitivityOut {
    pub parameter: SensitivityParameter,
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
    pub sensitivities: Vec<SensitivityOut>,
}

#[derive(Debug, Serialize)]
pub struct StepUncertainty {
    pub method: &'static str,
    pub uncovered_library_rows: Vec<usize>,
    pub absent_cross_parameter_pairs: usize,
    pub maximum_covariance_asymmetry_barn2: f64,
    pub responses: BTreeMap<String, ResponseUncertainty>,
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

pub fn response_band(input: BandInput) -> Result<ResponseUncertainty, String> {
    if !input.nominal.is_finite()
        || !input.alternate.is_finite()
        || !input.variance.is_finite()
        || input.variance < 0.0
    {
        return Err("nonfinite response value or invalid propagated variance".into());
    }
    let standard_uncertainty = input.variance.sqrt();
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
        .filter(|record| record.parameter.covariance_covered && record.value != 0.0)
        .count();
    let total_parameters = input
        .sensitivities
        .iter()
        .filter(|record| record.value != 0.0)
        .count();
    Ok(ResponseUncertainty {
        nominal: input.nominal,
        unit: input.unit,
        mf33_standard_uncertainty: standard_uncertainty,
        relative_standard_uncertainty: (input.nominal != 0.0)
            .then_some(standard_uncertainty / input.nominal.abs()),
        confidence_level: input.confidence_level,
        normal_multiplier: input.normal_multiplier,
        normal_interval,
        cram_order_bound,
        conservative_interval,
        negative_variance_roundoff_removed: input.negative_variance_roundoff_removed,
        coverage: if covered_parameters == total_parameters {
            "complete".into()
        } else {
            "partial".into()
        },
        covered_parameters,
        total_parameters,
        sensitivities: input.sensitivities,
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
