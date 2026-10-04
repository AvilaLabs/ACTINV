//! SIGMA1 Doppler broadening (Cullen): exact kernel integration for a cross section that is linear in energy between
//! grid points; 1/v extrapolation below the first point, constant above the last. Same algebra as controls/doppler.py.
//!
//! sigma_T(y) = 1/(y^2 sqrt(pi)) * Integral x^2 sigma(x) [ e^{-(x-y)^2} - e^{-(x+y)^2} ] dx,   x = sqrt(E/kT), kT = k T / A.
use libm::{erf, exp};

const SQPI: f64 = 1.772_453_850_905_516; // sqrt(pi)
const WINDOW: f64 = 8.0; // kernel is < 1e-28 beyond this many half-widths
const KB: f64 = 8.617333262e-5; // eV/K

/// F_n(t) = Integral^t s^n e^{-s^2} ds for n = 0..4, returned as a fused evaluation with the coefficients c0..c4.
#[inline]
fn poly_f(c: &[f64; 5], t: f64) -> f64 {
    let e = exp(-t * t);
    let er = erf(t);
    let f0 = SQPI / 2.0 * er;
    let f1 = -e / 2.0;
    let f2 = SQPI / 4.0 * er - t * e / 2.0;
    let f3 = -(t * t + 1.0) * e / 2.0;
    let f4 = 3.0 * SQPI / 8.0 * er - (t * t * t / 2.0 + 3.0 * t / 4.0) * e;
    c[0] * f0 + c[1] * f1 + c[2] * f2 + c[3] * f3 + c[4] * f4
}

/// Limits of F_n as t -> +infinity (odd n vanish).
#[inline]
fn poly_f_inf(c: &[f64; 5]) -> f64 {
    c[0] * SQPI / 2.0 + c[2] * SQPI / 4.0 + c[4] * 3.0 * SQPI / 8.0
}

/// Broaden `sig` given on ascending `e` (eV) from 0 K to `t_k` for target mass ratio `awr`, evaluated at `eout`.
/// Inputs are validated and reported as errors: this routine backs the Python API, where a
/// panic would surface as an uncatchable `PanicException`.
pub fn broaden(
    e: &[f64],
    sig: &[f64],
    t_k: f64,
    awr: f64,
    eout: &[f64],
) -> Result<Vec<f64>, String> {
    let n = e.len();
    if n < 2 || sig.len() != n {
        return Err("grid and cross section must have the same length >= 2".into());
    }
    if !(t_k.is_finite() && t_k > 0.0) {
        return Err("Doppler temperature must be finite and positive".into());
    }
    if !(awr.is_finite() && awr > 0.0) {
        return Err("Doppler target AWR must be finite and positive".into());
    }
    if e.iter().any(|value| !value.is_finite() || *value <= 0.0)
        || e.windows(2).any(|pair| pair[1] < pair[0])
    {
        return Err("Doppler input energies must be finite, positive and nondecreasing".into());
    }
    if sig.iter().any(|value| !value.is_finite()) {
        return Err("Doppler input contains a nonfinite cross section".into());
    }
    if eout.iter().any(|value| !value.is_finite() || *value <= 0.0) {
        return Err("Doppler output energies must be finite and positive".into());
    }
    let kt = KB * t_k / awr;
    let x: Vec<f64> = e.iter().map(|v| (v / kt).sqrt()).collect();
    // segment coefficients: sigma = a + b x^2 on [x_k, x_{k+1}]  (linear in E; zero-length segments carry no weight)
    let mut a = vec![0.0; n - 1];
    let mut b = vec![0.0; n - 1];
    for k in 0..n - 1 {
        let de = e[k + 1] - e[k];
        let slope = if de > 0.0 {
            (sig[k + 1] - sig[k]) / de
        } else {
            0.0
        };
        a[k] = sig[k] - slope * e[k];
        b[k] = slope * kt;
    }
    let (s0, sn, x0, xn) = (sig[0], sig[n - 1], x[0], x[n - 1]);
    let mut out = vec![0.0; eout.len()];
    for (oi, &eo) in eout.iter().enumerate() {
        let y = (eo / kt).sqrt();
        let mut acc = 0.0;
        for &sign in &[1.0f64, -1.0] {
            let yy = sign * y;
            // segments whose t-range intersects [-WINDOW, WINDOW]
            let lo_x = yy - WINDOW;
            let hi_x = yy + WINDOW;
            let k0 = match x.binary_search_by(|p| p.total_cmp(&lo_x)) {
                Ok(i) => i,
                Err(i) => i.saturating_sub(1),
            };
            let k1 = match x.binary_search_by(|p| p.total_cmp(&hi_x)) {
                Ok(i) => i,
                Err(i) => i,
            }
            .min(n - 1);
            for k in k0..k1 {
                let (t_lo, t_hi) = (x[k] - yy, x[k + 1] - yy);
                let (ak, bk) = (a[k], b[k]);
                let c = [
                    ak * yy * yy + bk * yy.powi(4),
                    2.0 * ak * yy + 4.0 * bk * yy.powi(3),
                    ak + 6.0 * bk * yy * yy,
                    4.0 * bk * yy,
                    bk,
                ];
                acc += sign * (poly_f(&c, t_hi) - poly_f(&c, t_lo));
            }
            // 1/v tail below x_0: integrand sig_0 x_0 (t + yy)
            let c_low = [s0 * x0 * yy, s0 * x0, 0.0, 0.0, 0.0];
            acc += sign * (poly_f(&c_low, x0 - yy) - poly_f(&c_low, -yy));
            // constant tail above x_N: integrand sig_N (t + yy)^2, to infinity
            let c_hi = [sn * yy * yy, 2.0 * sn * yy, sn, 0.0, 0.0];
            acc += sign * (poly_f_inf(&c_hi) - poly_f(&c_hi, xn - yy));
        }
        out[oi] = acc / (y * y * SQPI);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::broaden;

    #[test]
    fn invalid_inputs_are_errors_not_panics() {
        let (e, s) = ([1.0, 2.0, 4.0], [3.0, 2.0, 1.0]);
        assert!(broaden(&e, &s, 293.6, 55.0, &[1.5, 3.0]).is_ok());
        assert!(broaden(&e[..1], &s[..1], 293.6, 55.0, &[1.5]).is_err());
        assert!(broaden(&e, &s[..2], 293.6, 55.0, &[1.5]).is_err());
        assert!(broaden(&e, &s, 0.0, 55.0, &[1.5]).is_err());
        assert!(broaden(&e, &s, 293.6, 55.0, &[f64::NAN]).is_err());
        assert!(broaden(&e, &s, 293.6, 55.0, &[-1.0]).is_err());
        assert!(broaden(&[2.0, 1.0], &[1.0, 1.0], 293.6, 55.0, &[1.5]).is_err());
    }

    #[test]
    fn one_over_v_is_preserved() {
        // SIGMA1's exact law: a 1/v cross section is invariant under broadening.
        let e: Vec<f64> = (0..400).map(|i| 1e-3 * 1.05f64.powi(i)).collect();
        let s: Vec<f64> = e.iter().map(|v| 1.0 / v.sqrt()).collect();
        let out = broaden(&e, &s, 293.6, 55.0, &[1.0, 10.0]).unwrap();
        for (energy, value) in [1.0f64, 10.0].iter().zip(out) {
            assert!((value * energy.sqrt() - 1.0).abs() < 1e-3, "{value}");
        }
    }
}
