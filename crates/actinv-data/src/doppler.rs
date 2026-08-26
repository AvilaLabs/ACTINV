//! Checked, windowed SIGMA1 Doppler broadening.

use libm::{erf, erfc, exp};
use rayon::prelude::*;

const SQPI: f64 = 1.772_453_850_905_516;
const WINDOW: f64 = 8.0;
pub const KB_EV_PER_K: f64 = 8.617_333_262e-5;

fn erf_difference(low: f64, high: f64) -> f64 {
    if low >= 0.0 {
        erfc(low) - erfc(high)
    } else if high <= 0.0 {
        erfc(-high) - erfc(-low)
    } else {
        erf(high) - erf(low)
    }
}

fn gaussian_moments(low: f64, high: f64) -> [f64; 5] {
    let low_squared = low * low;
    let high_squared = high * high;
    let low_exponential = exp(-low_squared);
    let high_exponential = exp(-high_squared);
    let zeroth = 0.5 * SQPI * erf_difference(low, high);
    let squared_difference = high_squared - low_squared;
    let first = if squared_difference >= 0.0 {
        -0.5 * low_exponential * (-squared_difference).exp_m1()
    } else {
        0.5 * high_exponential * squared_difference.exp_m1()
    };
    let second = 0.5 * (zeroth + low * low_exponential - high * high_exponential);
    let third =
        0.5 * ((low_squared + 1.0) * low_exponential - (high_squared + 1.0) * high_exponential);
    let fourth = 1.5 * second
        + 0.5 * (low * low_squared * low_exponential - high * high_squared * high_exponential);
    [zeroth, first, second, third, fourth]
}

#[inline]
fn poly_integral(coefficients: &[f64; 5], low: f64, high: f64) -> f64 {
    coefficients
        .iter()
        .zip(gaussian_moments(low, high))
        .map(|(coefficient, moment)| coefficient * moment)
        .sum()
}

#[inline]
fn poly_integral_to_inf(coefficients: &[f64; 5], low: f64) -> f64 {
    let low_squared = low * low;
    let exponential = exp(-low_squared);
    let zeroth = 0.5 * SQPI * erfc(low);
    let moments = [
        zeroth,
        0.5 * exponential,
        0.5 * (zeroth + low * exponential),
        0.5 * (low_squared + 1.0) * exponential,
        1.5 * 0.5 * (zeroth + low * exponential) + 0.5 * low * low_squared * exponential,
    ];
    coefficients
        .iter()
        .zip(moments)
        .map(|(coefficient, moment)| coefficient * moment)
        .sum()
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
        || energy.windows(2).any(|pair| pair[1] < pair[0])
    {
        return Err("Doppler input energies must be finite, positive and nondecreasing".into());
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
    let mut slope_x2 = vec![0.0; energy.len() - 1];
    for index in 0..energy.len() - 1 {
        let interval = energy[index + 1] - energy[index];
        if interval > 0.0 {
            let slope = (sigma[index + 1] - sigma[index]) / interval;
            slope_x2[index] = slope * kt;
        }
    }
    let (sigma_low, sigma_high) = (sigma[0], sigma[sigma.len() - 1]);
    let (x_low, x_high) = (x[0], x[x.len() - 1]);
    let result = output_energy
        .par_iter()
        .map(|&energy_out| {
            let y = (energy_out / kt).sqrt();
            let mut accumulated = 0.0;
            for sign in [1.0f64, -1.0] {
                let shifted_y = sign * y;
                let low_x = shifted_y - WINDOW;
                let high_x = shifted_y + WINDOW;
                let first = x.partition_point(|point| *point < low_x).saturating_sub(1);
                let last = x.partition_point(|point| *point <= high_x).min(x.len() - 1);
                for index in first..last {
                    let (low, high) = (x[index] - shifted_y, x[index + 1] - shifted_y);
                    let b = slope_x2[index];
                    let centered_sigma =
                        sigma[index] + b * (shifted_y - x[index]) * (shifted_y + x[index]);
                    let by2 = b * shifted_y * shifted_y;
                    // Expand sigma(x)*x^2 around u=x-y.  Forming this from the global intercept `a` would subtract
                    // O(slope*E) terms to recover O(sigma), which loses all useful bits near high-energy steps.
                    let coefficients = [
                        centered_sigma * shifted_y * shifted_y,
                        2.0 * shifted_y * (centered_sigma + by2),
                        centered_sigma + 5.0 * by2,
                        4.0 * b * shifted_y,
                        b,
                    ];
                    accumulated += sign * poly_integral(&coefficients, low, high);
                }
                let low_coefficients = [
                    sigma_low * x_low * shifted_y,
                    sigma_low * x_low,
                    0.0,
                    0.0,
                    0.0,
                ];
                accumulated +=
                    sign * poly_integral(&low_coefficients, -shifted_y, x_low - shifted_y);
                let high_coefficients = [
                    sigma_high * shifted_y * shifted_y,
                    2.0 * sigma_high * shifted_y,
                    sigma_high,
                    0.0,
                    0.0,
                ];
                accumulated += sign * poly_integral_to_inf(&high_coefficients, x_high - shifted_y);
            }
            accumulated / (y * y * SQPI)
        })
        .collect();
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

    #[test]
    fn exact_double_point_step_is_finite() {
        let energy = [1.0, 2.0, 2.0, 4.0];
        let sigma = [0.0, 0.0, 1.0, 1.0];
        let output = [1.5, 1.9, 2.0, 2.1, 3.0];
        let broadened = broaden(&energy, &sigma, 293.6, 50.0, &output).unwrap();
        assert!(broadened
            .iter()
            .all(|value| value.is_finite() && *value >= 0.0));
        assert!(broadened.windows(2).all(|pair| pair[1] >= pair[0]));
    }

    #[test]
    fn high_energy_double_point_step_is_stable_and_monotone() {
        let edge = 3.0e7;
        let temperature = 293.6;
        let awr = 226.0;
        let width = (4.0 * KB_EV_PER_K * temperature * edge / awr).sqrt();
        let energy = [1.0e-5, edge, edge, 1.0e9];
        let sigma = [1.0, 1.0, 0.0, 0.0];
        let output: Vec<f64> = (-64..=64)
            .map(|index| edge + width * f64::from(index) / 8.0)
            .collect();
        let broadened = broaden(&energy, &sigma, temperature, awr, &output).unwrap();
        let minimum = broadened.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = broadened.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        assert!(
            broadened
                .iter()
                .all(|value| value.is_finite() && *value >= -1e-10 && *value <= 1.0 + 1e-10),
            "range [{minimum}, {maximum}]"
        );
        assert!(broadened.windows(2).all(|pair| pair[1] <= pair[0] + 1e-12));
    }
}
