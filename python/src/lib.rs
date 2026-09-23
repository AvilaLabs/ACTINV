//! Python interface to ACTINV's solver and numerical building blocks.

use actinv_core::cram::{step, Cram};
use actinv_core::doppler as dop;
use actinv_core::sparse::Csc;
use actinv_core::{run::run as core_run, spec::Spec};
use num_complex::Complex64 as C64;
use pyo3::prelude::*;

/// Console-script bridge. Use Python's `sys.argv`, which omits the interpreter path that appears in process argv.
#[pyfunction]
fn _cli(py: Python<'_>) -> PyResult<()> {
    let args = py
        .import("sys")?
        .getattr("argv")?
        .extract::<Vec<String>>()?;
    actinv_cli::command::main_from(args);
    Ok(())
}

/// cram_step(n, rows, cols, vals, n0, dt, alpha0, theta_re, theta_im, alpha_re, alpha_im) -> list[float]
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn cram_step(
    py: Python<'_>,
    n: usize,
    rows: Vec<usize>,
    cols: Vec<usize>,
    vals: Vec<f64>,
    n0: Vec<f64>,
    dt: f64,
    alpha0: f64,
    theta_re: Vec<f64>,
    theta_im: Vec<f64>,
    alpha_re: Vec<f64>,
    alpha_im: Vec<f64>,
) -> PyResult<Vec<f64>> {
    let trip: Vec<(usize, usize, C64)> = rows
        .iter()
        .zip(cols.iter())
        .zip(vals.iter())
        .map(|((&i, &j), &v)| (i, j, C64::new(v, 0.0)))
        .collect();
    let a = Csc::from_triplets(n, &trip);
    let c = Cram {
        alpha0,
        theta: theta_re
            .iter()
            .zip(theta_im.iter())
            .map(|(&r, &i)| C64::new(r, i))
            .collect(),
        alpha: alpha_re
            .iter()
            .zip(alpha_im.iter())
            .map(|(&r, &i)| C64::new(r, i))
            .collect(),
    };
    let (y, _) = py
        .detach(|| step(&a, &n0, dt, &c))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    Ok(y)
}

/// broaden(e, sig, T_K, awr, eout) -> list[float] — SIGMA1 Doppler broadening.
#[pyfunction]
fn broaden(
    py: Python<'_>,
    e: Vec<f64>,
    sig: Vec<f64>,
    t_k: f64,
    awr: f64,
    eout: Vec<f64>,
) -> PyResult<Vec<f64>> {
    py.detach(|| dop::broaden(&e, &sig, t_k, awr, &eout))
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

/// run(spec_json: str) -> str — solve a problem specification and return the result as JSON.
/// The same core function the `actinv` CLI and the harness call: one binary, three entry points (P5 G3).
/// The solve releases the GIL, so other Python threads keep running meanwhile.
#[pyfunction]
fn run(py: Python<'_>, spec_json: &str) -> PyResult<String> {
    let resolved = actinv_cli::resolve_catalog_json(spec_json)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let spec = Spec::from_json(&resolved).map_err(pyo3::exceptions::PyValueError::new_err)?;
    let r = py
        .detach(|| core_run(&spec, "python"))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    serde_json::to_string(&r).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// reverse(problem_json: str, measurements_json: str, segments: bool) -> str
/// Flux estimation from measured activities in the linear regime.
#[pyfunction]
fn reverse(
    py: Python<'_>,
    problem_json: &str,
    measurements_json: &str,
    segments: bool,
) -> PyResult<String> {
    let resolved = actinv_cli::resolve_catalog_json(problem_json)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let spec = Spec::from_json(&resolved).map_err(pyo3::exceptions::PyValueError::new_err)?;
    let result = py
        .detach(|| actinv_core::reverse::solve(&spec, &resolved, measurements_json, segments))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// validate(spec_json: str) -> str — parse and validate without solving.
#[pyfunction]
fn validate(spec_json: &str) -> PyResult<String> {
    let resolved = actinv_cli::resolve_catalog_json(spec_json)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let s = Spec::from_json(&resolved).map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok(format!(
        "{} — {} groups, {} steps",
        s.spec,
        s.spectrum.flux_per_group.len(),
        s.schedule.len()
    ))
}

#[pymodule]
fn actinv(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(_cli, m)?)?;
    m.add_function(wrap_pyfunction!(cram_step, m)?)?;
    m.add_function(wrap_pyfunction!(broaden, m)?)?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(reverse, m)?)?;
    m.add_function(wrap_pyfunction!(validate, m)?)?;
    m.add_function(wrap_pyfunction!(_example_json, m)?)?;
    let code =
        std::ffi::CString::new(include_str!("objects.py")).expect("Python source has no NUL");
    m.py().run(&code, Some(&m.dict()), None)?;
    Ok(())
}

#[pyfunction]
fn _example_json(data_dir: &str) -> PyResult<String> {
    let spec = actinv_cli::workflow::new_example(std::path::Path::new(data_dir))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    serde_json::to_string(&spec)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}
