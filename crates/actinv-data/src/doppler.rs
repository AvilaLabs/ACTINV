//! Checked, windowed SIGMA1 Doppler broadening.

use libm::{erf, exp};

const SQPI: f64 = 1.772_453_850_905_516;
const WINDOW: f64 = 8.0;
pub const KB_EV_PER_K: f64 = 8.617_333_262e-5;

#[inline]
fn poly_f(coefficients: &[f64; 5], value: f64) -> f64 {
    let exponential = exp(-value * value);
    let error = erf(value);
    let f0 = SQPI / 2.0 * error;
    let f1 = -exponential / 2.0;
    let f2 = SQPI / 4.0 * error - value * exponential / 2.0;
    let f3 = -(value * value + 1.0) * exponential / 2.0;
    let f4 =
        3.0 * SQPI / 8.0 * error - (value * value * value / 2.0 + 3.0 * value / 4.0) * exponential;
    coefficients[0] * f0
        + coefficients[1] * f1
        + coefficients[2] * f2
        + coefficients[3] * f3
        + coefficients[4] * f4
}

#[inline]
fn poly_f_inf(coefficients: &[f64; 5]) -> f64 {
    coefficients[0] * SQPI / 2.0 + coefficients[2] * SQPI / 4.0 + coefficients[4] * 3.0 * SQPI / 8.0
}

fn validate(
    energy: &[f64],
    sigma: &[f64],
    temperature_k: f64,
    awr: f64,
    output: &[f64],
) -> Result<(), String> {
    if energy.len() < 2 || sigma.len() != energy.len() {
        return Err("Doppler input energy/sigma arrays must have matching length >= 2".into());
    }
    if !temperature_k.is_finite() || temperature_k < 0.0 {
        return Err("Doppler temperature must be finite and nonnegative".into());
    }
    if !awr.is_finite() || awr <= 0.0 {
        return Err("Doppler target AWR must be finite and positive".into());
    }
    if energy
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
        || energy.windows(2).any(|pair| pair[1] <= pair[0])
    {
        return Err(
            "Doppler input energies must be finite, positive and strictly increasing".into(),
        );
    }
    if sigma.iter().any(|value| !value.is_finite()) {
        return Err("Doppler input contains a nonfinite cross section".into());
    }
    if output
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
    {
        return Err("Doppler output energies must be finite and positive".into());
    }
    Ok(())
}

fn zero_k(energy: &[f64], sigma: &[f64], output: &[f64]) -> Vec<f64> {
    output
        .iter()
        .map(|&value| {
            if value <= energy[0] {
                sigma[0] * (energy[0] / value).sqrt()
            } else if value >= energy[energy.len() - 1] {
                sigma[sigma.len() - 1]
            } else {
                let upper = energy.partition_point(|point| *point <= value);
                let lower = upper - 1;
                let weight = (value - energy[lower]) / (energy[upper] - energy[lower]);
                sigma[lower] + weight * (sigma[upper] - sigma[lower])
            }
        })
        .collect()
}

/// Broaden a linearly interpolated 0 K cross section to `temperature_k`, evaluated at `output_energy`.
pub fn broaden(
    energy: &[f64],
    sigma: &[f64],
    temperature_k: f64,
    awr: f64,
    output_energy: &[f64],
) -> Result<Vec<f64>, String> {
    validate(energy, sigma, temperature_k, awr, output_energy)?;
    if temperature_k == 0.0 {
        return Ok(zero_k(energy, sigma, output_energy));
    }
    let kt = KB_EV_PER_K * temperature_k / awr;
    let x: Vec<f64> = energy.iter().map(|value| (value / kt).sqrt()).collect();
    let mut intercept = vec![0.0; energy.len() - 1];
    let mut slope_x2 = vec![0.0; energy.len() - 1];
    for index in 0..energy.len() - 1 {
        let slope = (sigma[index + 1] - sigma[index]) / (energy[index + 1] - energy[index]);
        intercept[index] = sigma[index] - slope * energy[index];
        slope_x2[index] = slope * kt;
    }
    let (sigma_low, sigma_high) = (sigma[0], sigma[sigma.len() - 1]);
    let (x_low, x_high) = (x[0], x[x.len() - 1]);
    let mut result = Vec::with_capacity(output_energy.len());
    for &energy_out in output_energy {
        let y = (energy_out / kt).sqrt();
        let mut accumulated = 0.0;
        for sign in [1.0f64, -1.0] {
            let shifted_y = sign * y;
            let low_x = shifted_y - WINDOW;
            let high_x = shifted_y + WINDOW;
            let first = match x.binary_search_by(|point| point.total_cmp(&low_x)) {
                Ok(index) => index,
                Err(index) => index.saturating_sub(1),
            };
            let last = match x.binary_search_by(|point| point.total_cmp(&high_x)) {
                Ok(index) | Err(index) => index,
            }
            .min(x.len() - 1);
            for index in first..last {
                let (low, high) = (x[index] - shifted_y, x[index + 1] - shifted_y);
                let (a, b) = (intercept[index], slope_x2[index]);
                let coefficients = [
                    a * shifted_y * shifted_y + b * shifted_y.powi(4),
                    2.0 * a * shifted_y + 4.0 * b * shifted_y.powi(3),
                    a + 6.0 * b * shifted_y * shifted_y,
                    4.0 * b * shifted_y,
                    b,
                ];
                accumulated += sign * (poly_f(&coefficients, high) - poly_f(&coefficients, low));
            }
            let low_coefficients = [
                sigma_low * x_low * shifted_y,
                sigma_low * x_low,
                0.0,
                0.0,
                0.0,
            ];
            accumulated += sign
                * (poly_f(&low_coefficients, x_low - shifted_y)
                    - poly_f(&low_coefficients, -shifted_y));
            let high_coefficients = [
                sigma_high * shifted_y * shifted_y,
                2.0 * sigma_high * shifted_y,
                sigma_high,
                0.0,
                0.0,
            ];
            accumulated += sign
                * (poly_f_inf(&high_coefficients) - poly_f(&high_coefficients, x_high - shifted_y));
        }
        result.push(accumulated / (y * y * SQPI));
    }
    Ok(result)
}

/// Exact SIGMA1 response of a zero-width line with area `area_barn_ev` centered at `resonance_ev`.
pub fn delta_line(
    area_barn_ev: f64,
    resonance_ev: f64,
    temperature_k: f64,
    awr: f64,
    output_ev: f64,
) -> Result<f64, String> {
    if !area_barn_ev.is_finite()
        || area_barn_ev < 0.0
        || !resonance_ev.is_finite()
        || resonance_ev <= 0.0
        || !temperature_k.is_finite()
        || temperature_k <= 0.0
        || !awr.is_finite()
        || awr <= 0.0
        || !output_ev.is_finite()
        || output_ev <= 0.0
    {
        return Err("invalid SIGMA1 delta-line argument".into());
    }
    let kt = KB_EV_PER_K * temperature_k / awr;
    let x = (resonance_ev / kt).sqrt();
    let y = (output_ev / kt).sqrt();
    Ok(
        area_barn_ev * x * (exp(-(x - y).powi(2)) - exp(-(x + y).powi(2)))
            / (2.0 * kt * y * y * SQPI),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_kelvin_is_identity_on_input_grid() {
        let e = [1.0, 2.0, 4.0];
        let s = [3.0, 5.0, 7.0];
        assert_eq!(broaden(&e, &s, 0.0, 55.0, &e).unwrap(), s);
    }

    #[test]
    fn one_over_v_is_invariant() {
        // All output kernel windows lie below the first tabulated point, where SIGMA1's declared extrapolation is
        // exactly 1/v rather than a piecewise-linear approximation to it.
        let energy = [100.0, 200.0];
        let sigma = [1.0, 1.0 / 2.0f64.sqrt()];
        let output = [0.1, 1.0, 10.0, 1.0e3];
        let broadened = broaden(&energy, &sigma, 900.0, 55.0, &output).unwrap();
        for (&actual, &energy) in broadened.iter().zip(&output) {
            let expected = (100.0 / energy).sqrt();
            // The 1 keV point is above the grid and intentionally exercises the constant high tail instead.
            if energy < 100.0 {
                assert!((actual - expected).abs() <= 2e-12 * expected);
            }
        }
    }
}
