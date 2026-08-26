//! `actinv` Python module: one CRAM-16 step on a sparse real matrix (P2-G5).
use pyo3::prelude::*;
use num_complex::Complex64 as C64;
use actinv_core::sparse::Csc;
use actinv_core::doppler as dop;
use actinv_core::cram::{Cram, step};

/// cram_step(n, rows, cols, vals, n0, dt, alpha0, theta_re, theta_im, alpha_re, alpha_im) -> list[float]
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn cram_step(n: usize, rows: Vec<usize>, cols: Vec<usize>, vals: Vec<f64>, n0: Vec<f64>, dt: f64, alpha0: f64,
             theta_re: Vec<f64>, theta_im: Vec<f64>, alpha_re: Vec<f64>, alpha_im: Vec<f64>) -> PyResult<Vec<f64>> {
    let trip: Vec<(usize, usize, C64)> = rows.iter().zip(cols.iter()).zip(vals.iter()).map(|((&i, &j), &v)| (i, j, C64::new(v, 0.0))).collect();
    let a = Csc::from_triplets(n, &trip);
    let c = Cram { alpha0, theta: theta_re.iter().zip(theta_im.iter()).map(|(&r, &i)| C64::new(r, i)).collect(), alpha: alpha_re.iter().zip(alpha_im.iter()).map(|(&r, &i)| C64::new(r, i)).collect() };
    let (y, _) = step(&a, &n0, dt, &c).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
    Ok(y)
}

/// broaden(e, sig, T_K, awr, eout) -> list[float] — SIGMA1 Doppler broadening.
#[pyfunction]
fn broaden(e: Vec<f64>, sig: Vec<f64>, t_k: f64, awr: f64, eout: Vec<f64>) -> PyResult<Vec<f64>> {
    Ok(dop::broaden(&e, &sig, t_k, awr, &eout))
}

#[pymodule]
fn actinv(m: &Bound<'_, PyModule>) -> PyResult<()> { m.add_function(wrap_pyfunction!(cram_step, m)?)?; m.add_function(wrap_pyfunction!(broaden, m)?)?; Ok(()) }
