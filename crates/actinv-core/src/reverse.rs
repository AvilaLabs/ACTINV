#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Reverse calculation (P23 G2): flux estimation from measured activities.
//!
//! In the linear (trace) regime each computed activity is proportional to the flux normalization.
//! Scalar mode estimates one common multiplier by weighted least squares; `--segments` estimates an
//! independent multiplier per irradiation step by Lawson–Hanson non-negative least squares on the
//! superposition sensitivity matrix. Anything outside the linear regime — a coupled-mode problem,
//! feed/removal schedules, absent nuclides, degenerate sensitivity matrices — is a named error.

use crate::run;
use crate::spec::Spec;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const MEASUREMENTS_FORMAT: &str = "actinv-reverse-input-1";
const RESULT_FORMAT: &str = "actinv-reverse-1";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MeasurementsFile {
    #[serde(default)]
    format: Option<String>,
    measurements: Vec<MeasurementIn>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MeasurementIn {
    step: serde_json::Value,
    nuclide: String,
    activity_Bq_per_g: f64,
    #[serde(default)]
    sigma_Bq_per_g: Option<f64>,
}

struct Measurement {
    step: usize, // 1-based schedule step
    nuclide: String,
    activity: f64,
    weight: f64,
    declared_sigma: Option<f64>,
}

fn sha256_text(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

fn parse_measurements(text: &str, n_steps: usize) -> Result<Vec<Measurement>, String> {
    let file: MeasurementsFile = serde_json::from_str(text)
        .map_err(|e| format!("reverse: measurements file is not actinv-reverse-input-1: {e}"))?;
    if let Some(format) = &file.format {
        if format != MEASUREMENTS_FORMAT {
            return Err(format!(
                "reverse: measurements format '{format}' is not {MEASUREMENTS_FORMAT}"
            ));
        }
    }
    if file.measurements.is_empty() {
        return Err("reverse: measurements file declares no measurements".into());
    }
    let mut resolved = Vec::with_capacity(file.measurements.len());
    let mut seen = std::collections::BTreeSet::new();
    for m in &file.measurements {
        let step = match &m.step {
            serde_json::Value::String(s) if s == "last" => n_steps,
            serde_json::Value::Number(n) => {
                n.as_u64()
                    .filter(|v| *v >= 1 && *v <= n_steps as u64)
                    .ok_or_else(|| format!("reverse: step {n} is outside the schedule"))?
                    as usize
            }
            other => {
                return Err(format!(
                    "reverse: step must be a 1-based index or \"last\", not {other}"
                ))
            }
        };
        if !m.activity_Bq_per_g.is_finite() || m.activity_Bq_per_g < 0.0 {
            return Err(format!(
                "reverse: activity for {} at step {step} must be finite and nonnegative",
                m.nuclide
            ));
        }
        let weight = match m.sigma_Bq_per_g {
            Some(s) if !s.is_finite() || s <= 0.0 => {
                return Err(format!(
                    "reverse: sigma for {} at step {step} must be finite and positive",
                    m.nuclide
                ));
            }
            Some(s) => 1.0 / (s * s),
            None => 1.0,
        };
        if !seen.insert((step, m.nuclide.clone())) {
            return Err(format!(
                "reverse: duplicate measurement of {} at step {step}",
                m.nuclide
            ));
        }
        resolved.push(Measurement {
            step,
            nuclide: m.nuclide.clone(),
            activity: m.activity_Bq_per_g,
            weight,
            declared_sigma: m.sigma_Bq_per_g,
        });
    }
    Ok(resolved)
}

/// Solve `A x = b` (dense, square) by Gaussian elimination with partial pivoting.
#[allow(clippy::needless_range_loop)]
fn dense_solve(a: &[Vec<f64>], b: &[f64]) -> Result<Vec<f64>, String> {
    let n = a.len();
    let mut m: Vec<Vec<f64>> = a
        .iter()
        .cloned()
        .zip(b.iter())
        .map(|(r, &bi)| {
            let mut row = r;
            row.push(bi);
            row
        })
        .collect();
    // Singularity is relative to the matrix scale (as NNLS's tolerance is): an absolute
    // epsilon rejected well-conditioned systems of small magnitude and never fired for
    // large ones.
    let scale = a
        .iter()
        .flat_map(|row| row.iter())
        .fold(0.0f64, |acc, v| acc.max(v.abs()));
    let threshold = scale * n.max(1) as f64 * f64::EPSILON;
    for col in 0..n {
        let mut pivot = col;
        for row in col + 1..n {
            if m[row][col].abs() > m[pivot][col].abs() {
                pivot = row;
            }
        }
        let magnitude = m[pivot][col].abs();
        if magnitude.is_nan() || magnitude <= threshold {
            return Err("reverse: singular sensitivity subproblem".into());
        }
        m.swap(col, pivot);
        for row in col + 1..n {
            let factor = m[row][col] / m[col][col];
            for k in col..=n {
                m[row][k] -= factor * m[col][k];
            }
        }
    }
    let mut x = vec![0.0; n];
    for row in (0..n).rev() {
        let mut sum = m[row][n];
        for k in row + 1..n {
            sum -= m[row][k] * x[k];
        }
        x[row] = sum / m[row][row];
    }
    Ok(x)
}

/// Lawson–Hanson NNLS: `min ||W^(1/2)(A x - b)||` subject to `x >= 0`.
/// `a` is the m×n sensitivity matrix already weighted (each row times sqrt(w_i)).
fn nnls(a: &[Vec<f64>], b: &[f64]) -> Result<Vec<f64>, String> {
    let m = a.len();
    let n = a[0].len();
    let mut x = vec![0.0f64; n];
    let mut passive = vec![false; n];
    let tolerance = {
        let scale: f64 = a
            .iter()
            .flat_map(|row| row.iter())
            .fold(0.0, |acc, v| acc.max(v.abs()));
        scale * m.max(n) as f64 * f64::EPSILON
    };
    let max_iterations = 10 * n + 50;
    for _ in 0..max_iterations {
        // residual and gradient of the weighted objective
        let mut gradient = vec![0.0; n];
        for i in 0..m {
            let mut residual = -b[i];
            for j in 0..n {
                residual += a[i][j] * x[j];
            }
            for j in 0..n {
                gradient[j] += a[i][j] * residual;
            }
        }
        // Lawson–Hanson uses w = A^T(b - Ax): the negative gradient
        let mut entering: Option<usize> = None;
        for j in 0..n {
            if !passive[j]
                && -gradient[j] > tolerance
                && entering.is_none_or(|t| -gradient[j] > -gradient[t])
            {
                entering = Some(j);
            }
        }
        let Some(entering) = entering else {
            return Ok(x); // no improving variable: converged
        };
        passive[entering] = true;
        // inner loop: solve the passive least-squares problem, then push
        // nonpositive variables back to the zero set
        for _ in 0..max_iterations {
            let p: Vec<usize> = (0..n).filter(|&j| passive[j]).collect();
            let k = p.len();
            let mut normal = vec![vec![0.0; k]; k];
            let mut rhs = vec![0.0; k];
            for i in 0..m {
                for (u, &ju) in p.iter().enumerate() {
                    rhs[u] += a[i][ju] * b[i];
                    for (v, &jv) in p.iter().enumerate() {
                        normal[u][v] += a[i][ju] * a[i][jv];
                    }
                }
            }
            let z_passive = dense_solve(&normal, &rhs)
                .map_err(|_| "reverse: singular passive set during NNLS".to_string())?;
            let mut z = vec![0.0; n];
            for (u, &ju) in p.iter().enumerate() {
                z[ju] = z_passive[u];
            }
            if p.iter().all(|&j| z[j] > tolerance) {
                x = z;
                break;
            }
            // Step to the first passive variable that reaches zero. A variable already at
            // the boundary (x = 0, or a tolerance-sized z not below x) blocks the step:
            // its ratio is 0, not the NaN of 0/0 that `f64::min` silently dropped,
            // which could leave alpha infinite and fill x with inf/NaN.
            let mut alpha = f64::INFINITY;
            for &j in &p {
                if z[j] <= tolerance {
                    let denominator = x[j] - z[j];
                    let ratio = if denominator > 0.0 {
                        x[j] / denominator
                    } else {
                        0.0
                    };
                    alpha = alpha.min(ratio);
                }
            }
            for j in 0..n {
                x[j] += alpha * (z[j] - x[j]);
                if passive[j] && x[j] <= tolerance {
                    x[j] = 0.0;
                    passive[j] = false;
                }
            }
        }
        if passive.iter().all(|&p| !p) {
            return Ok(x);
        }
    }
    Err(format!(
        "reverse: NNLS did not converge within {max_iterations} iterations"
    ))
}

/// Eigenvalues of a dense symmetric matrix by the cyclic Jacobi method.
#[allow(clippy::needless_range_loop)]
fn jacobi_eigenvalues(matrix: &[Vec<f64>]) -> Vec<f64> {
    let n = matrix.len();
    let mut a: Vec<Vec<f64>> = matrix.to_vec();
    let reference: f64 = a
        .iter()
        .enumerate()
        .fold(0.0f64, |acc, (i, r)| acc.max(r[i].abs()))
        .max(1.0);
    for _ in 0..64 {
        let mut off_squared = 0.0f64;
        for i in 0..n {
            for j in i + 1..n {
                off_squared += a[i][j] * a[i][j];
            }
        }
        let off = off_squared.sqrt();
        if off <= reference * 1e-15 {
            break;
        }
        for p in 0..n {
            for q in p + 1..n {
                if a[p][q] == 0.0 {
                    continue;
                }
                let theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q]);
                let t = if theta >= 0.0 { 1.0 } else { -1.0 }
                    / (theta.abs() + (1.0 + theta * theta).sqrt());
                let c = 1.0 / (1.0 + t * t).sqrt();
                let s = t * c;
                for k in 0..n {
                    let akp = a[k][p];
                    let akq = a[k][q];
                    a[k][p] = c * akp - s * akq;
                    a[k][q] = s * akp + c * akq;
                }
                for k in 0..n {
                    let apk = a[p][k];
                    let aqk = a[q][k];
                    a[p][k] = c * apk - s * aqk;
                    a[q][k] = s * apk + c * aqk;
                }
            }
        }
    }
    a.iter().enumerate().map(|(i, r)| r[i]).collect()
}

fn invert(matrix: &[Vec<f64>]) -> Result<Vec<Vec<f64>>, String> {
    let n = matrix.len();
    let mut inv = Vec::with_capacity(n);
    for col in 0..n {
        let mut unit = vec![0.0; n];
        unit[col] = 1.0;
        inv.push(dense_solve(matrix, &unit)?);
    }
    // inv holds columns; transpose to rows
    Ok((0..n)
        .map(|r| (0..n).map(|c| inv[c][r]).collect())
        .collect())
}

/// Reverse-calculate flux multipliers from measured activities.
///
/// `problem_json`/`measurements_json` are the literal caller-supplied texts; their SHA-256 values are
/// recorded in the result's `sensitivity` identity block. `spec` must already be parsed and have its
/// relative paths resolved by the caller.
#[allow(clippy::needless_range_loop)]
pub fn solve(
    spec: &Spec,
    problem_json: &str,
    measurements_json: &str,
    segments: bool,
) -> Result<serde_json::Value, String> {
    if spec
        .schedule
        .iter()
        .any(|step| step.feed.is_some() || step.removal.is_some())
    {
        return Err(
            "reverse: feed/removal schedules are outside the linear reverse problem".into(),
        );
    }
    let n_steps = spec.schedule.len();
    let measurements = parse_measurements(measurements_json, n_steps)?;
    let irradiation: Vec<usize> = (0..n_steps)
        .filter(|&i| spec.schedule[i].flux > 0.0)
        .collect();
    if irradiation.is_empty() {
        return Err("reverse: the schedule declares no irradiation step".into());
    }
    if segments && measurements.len() < irradiation.len() {
        return Err(format!(
            "reverse: {} measurements cannot determine {} segment multipliers",
            measurements.len(),
            irradiation.len()
        ));
    }

    // A forward run at a multiplier pattern: irradiation steps in `active` take unit
    // multiplier; every other step is fixed at zero.
    let sensitivity_run = |active: Option<usize>| -> Result<run::RunResult, String> {
        let mut unit = spec.clone();
        for (i, step) in unit.schedule.iter_mut().enumerate() {
            step.flux = if step.flux > 0.0 && active.is_none_or(|only| only == i) {
                1.0
            } else {
                0.0
            };
        }
        run::run(&unit, "reverse")
    };

    let first = sensitivity_run(if segments { Some(irradiation[0]) } else { None })?;
    if first.mode != "trace" {
        return Err(format!(
            "reverse: the problem resolved to '{}' mode; reverse requires the linear trace regime",
            first.mode
        ));
    }
    // A measurement is valid when its nuclide appears in the computed inventory at that step.
    let sensitivity = |result: &run::RunResult, measurement: &Measurement| -> Result<f64, String> {
        let step = &result.steps[measurement.step - 1];
        if !step
            .inventory
            .iter()
            .any(|nuclide| nuclide.nuclide == measurement.nuclide)
        {
            return Err(format!(
                "reverse: measured nuclide {} is absent from the computed inventory at step {}",
                measurement.nuclide, measurement.step
            ));
        }
        Ok(step
            .activity_Bq_per_g
            .get(&measurement.nuclide)
            .copied()
            .unwrap_or(0.0))
    };

    let result = if !segments {
        let a: Vec<f64> = measurements
            .iter()
            .map(|measurement| sensitivity(&first, measurement))
            .collect::<Result<_, _>>()?;
        let denominator: f64 = a
            .iter()
            .zip(measurements.iter())
            .map(|(a_i, m_i)| m_i.weight * a_i * a_i)
            .sum();
        if denominator <= 0.0 {
            return Err("reverse: every sensitivity coefficient is zero".into());
        }
        let estimate: f64 = a
            .iter()
            .zip(measurements.iter())
            .map(|(a_i, m_i)| m_i.weight * a_i * m_i.activity)
            .sum::<f64>()
            / denominator;
        let standard_error = denominator.recip().sqrt();
        let rows: Vec<_> = measurements
            .iter()
            .zip(a.iter())
            .map(|(m_i, a_i)| {
                let predicted = estimate * a_i;
                serde_json::json!({
                    "step": m_i.step,
                    "nuclide": m_i.nuclide,
                    "activity_Bq_per_g": m_i.activity,
                    "sensitivity_Bq_per_g_per_multiplier": a_i,
                    "predicted_Bq_per_g": predicted,
                    "residual_Bq_per_g": m_i.activity - predicted,
                    "weight": m_i.weight,
                })
            })
            .collect();
        let chi_square: f64 = measurements
            .iter()
            .zip(a.iter())
            .map(|(m_i, a_i)| {
                let residual = m_i.activity - estimate * a_i;
                m_i.weight * residual * residual
            })
            .sum();
        serde_json::json!({
            "format": RESULT_FORMAT,
            "mode": "normalization",
            "regime": "trace",
            "estimates": {
                "multiplier": estimate,
                "standard_error": standard_error,
            },
            "measurements": rows,
            "chi_square": chi_square,
            "degrees_of_freedom": measurements.len() - 1,
            "sensitivity": {
                "problem_sha256": sha256_text(problem_json),
                "measurements_sha256": sha256_text(measurements_json),
                "forward_runs": 1,
            },
            "method_limits": "weighted least squares on the exact linear trace-regime response: the estimate is an absolute common flux multiplier on the declared irradiation steps, cooling steps stay at zero, sigma-less measurements carry unit weight, and no covariance, transport or burnup feedback is inferred",
        })
    } else {
        let k = irradiation.len();
        let mut columns = vec![Vec::with_capacity(measurements.len()); k];
        columns[0] = measurements
            .iter()
            .map(|measurement| sensitivity(&first, measurement))
            .collect::<Result<_, _>>()?;
        let mut forward_runs = 1usize;
        for (column_index, &step_index) in irradiation.iter().enumerate().skip(1) {
            let result = sensitivity_run(Some(step_index))?;
            forward_runs += 1;
            columns[column_index] = measurements
                .iter()
                .map(|measurement| sensitivity(&result, measurement))
                .collect::<Result<_, _>>()?;
        }
        for (column_index, column) in columns.iter().enumerate() {
            if column.iter().all(|v| *v == 0.0) {
                return Err(format!(
                    "reverse: segment at schedule step {} has an all-zero sensitivity column",
                    irradiation[column_index] + 1
                ));
            }
        }
        // weighted design matrix W^(1/2) A and RHS W^(1/2) b
        let aw: Vec<Vec<f64>> = measurements
            .iter()
            .enumerate()
            .map(|(i, m_i)| {
                let root = m_i.weight.sqrt();
                (0..k).map(|j| root * columns[j][i]).collect()
            })
            .collect();
        let bw: Vec<f64> = measurements
            .iter()
            .map(|m_i| m_i.weight.sqrt() * m_i.activity)
            .collect();
        // normal matrix for conditioning and standard errors
        let mut normal = vec![vec![0.0; k]; k];
        for i in 0..measurements.len() {
            for u in 0..k {
                for v in 0..k {
                    normal[u][v] += aw[i][u] * aw[i][v];
                }
            }
        }
        let eigenvalues = jacobi_eigenvalues(&normal);
        let (mut lmax, mut lmin) = (0.0f64, f64::INFINITY);
        for &lambda in &eigenvalues {
            lmax = lmax.max(lambda);
            lmin = lmin.min(lambda);
        }
        if lmin <= lmax * 1e-14 || !lmin.is_finite() {
            return Err(format!(
                "reverse: the sensitivity matrix is rank-deficient (smallest normal-matrix eigenvalue {lmin:.3e} vs largest {lmax:.3e})"
            ));
        }
        let condition = (lmax / lmin).sqrt();
        let x = nnls(&aw, &bw)?;
        let covariance = invert(&normal)?;
        let estimates: Vec<_> = irradiation
            .iter()
            .enumerate()
            .map(|(j, &step_index)| {
                serde_json::json!({
                    "step": step_index + 1,
                    "multiplier": x[j],
                    "standard_error": covariance[j][j].max(0.0).sqrt(),
                })
            })
            .collect();
        let rows: Vec<_> = measurements
            .iter()
            .enumerate()
            .map(|(i, m_i)| {
                let predicted: f64 = (0..k).map(|j| x[j] * columns[j][i]).sum();
                serde_json::json!({
                    "step": m_i.step,
                    "nuclide": m_i.nuclide,
                    "activity_Bq_per_g": m_i.activity,
                    "predicted_Bq_per_g": predicted,
                    "residual_Bq_per_g": m_i.activity - predicted,
                    "weight": m_i.weight,
                })
            })
            .collect();
        let chi_square: f64 = measurements
            .iter()
            .enumerate()
            .map(|(i, m_i)| {
                let predicted: f64 = (0..k).map(|j| x[j] * columns[j][i]).sum();
                let residual = m_i.activity - predicted;
                m_i.weight * residual * residual
            })
            .sum();
        serde_json::json!({
            "format": RESULT_FORMAT,
            "mode": "segments",
            "regime": "trace",
            "estimates": estimates,
            "measurements": rows,
            "chi_square": chi_square,
            "degrees_of_freedom": measurements.len() - k,
            "condition_number": condition,
            "sensitivity": {
                "problem_sha256": sha256_text(problem_json),
                "measurements_sha256": sha256_text(measurements_json),
                "forward_runs": forward_runs,
                "segments": irradiation.iter().map(|&i| i + 1).collect::<Vec<_>>(),
            },
            "method_limits": "Lawson-Hanson NNLS on the exact linear trace-regime superposition: each estimate is an absolute multiplier for one irradiation step, cooling steps stay at zero, sigma-less measurements carry unit weight, and no covariance, transport or burnup feedback is inferred",
        })
    };
    Ok(result)
}

/// Lower-triangular Cholesky factor C = L·Lᵀ; `None` when C is not
/// positive definite.
#[allow(clippy::needless_range_loop)]
fn cholesky(c: &[Vec<f64>]) -> Option<Vec<Vec<f64>>> {
    let n = c.len();
    let mut l = vec![vec![0.0; n]; n];
    for i in 0..n {
        for j in 0..=i {
            let mut s = c[i][j];
            for k in 0..j {
                s -= l[i][k] * l[j][k];
            }
            if i == j {
                if s.is_nan() || s <= 0.0 || !s.is_finite() {
                    return None;
                }
                l[i][i] = s.sqrt();
            } else {
                l[i][j] = s / l[j][j];
            }
        }
    }
    Some(l)
}

/// Solve L·x = b for lower-triangular L.
fn forward_solve(l: &[Vec<f64>], b: &[f64]) -> Vec<f64> {
    let n = b.len();
    let mut x = vec![0.0; n];
    for i in 0..n {
        let mut s = b[i];
        for j in 0..i {
            s -= l[i][j] * x[j];
        }
        x[i] = s / l[i][i];
    }
    x
}

const QUALIFIED_FORMAT: &str = "actinv-reverse-qualified-1";
/// Declared resolvability thresholds (P55 protocol): a segment is
/// `resolvable` when its relative posterior σ stays under REL_SIGMA_MAX and
/// its worst posterior correlation stays under RHO_MAX.
const REL_SIGMA_MAX: f64 = 0.5;
const RHO_MAX: f64 = 0.95;
/// Weak ridge prior on the posterior precision (fraction of the mean
/// diagonal information) — keeps degenerate directions finite and labelled
/// rather than exploding.
const RIDGE_FRACTION: f64 = 1e-9;

/// Qualified inverse (P55): per-segment irradiation-history estimation under
/// generalized least squares with `C = C_meas + C_model`, where C_model is
/// the propagated nuclear-data covariance of the sensitivity columns,
/// `G_iᵀ Σ G_j` with `G_i = Σ_k f₀_k·J_{i,k}` at the first-pass NNLS point.
/// Emits `actinv-reverse-qualified-1` NDJSON.
#[allow(clippy::needless_range_loop)]
pub fn solve_qualified(
    spec: &Spec,
    problem_json: &str,
    measurements_json: &str,
) -> Result<(String, serde_json::Value), String> {
    use crate::flux::sha256_file;
    use std::collections::{BTreeMap, HashMap};

    if spec
        .schedule
        .iter()
        .any(|step| step.feed.is_some() || step.removal.is_some())
    {
        return Err(
            "reverse-qualified: feed/removal schedules are outside the linear inverse problem"
                .into(),
        );
    }
    let uncertainty = spec
        .uncertainty
        .as_ref()
        .ok_or("reverse-qualified requires a spec with an uncertainty block")?;
    if uncertainty.responses.is_empty() {
        return Err(
            "reverse-qualified requires uncertainty.responses declaring activity:<nuclide> for \
             every measured nuclide"
                .into(),
        );
    }
    let n_steps = spec.schedule.len();
    let measurements = parse_measurements(measurements_json, n_steps)?;
    let irradiation: Vec<usize> = (0..n_steps)
        .filter(|&i| spec.schedule[i].flux > 0.0)
        .collect();
    if irradiation.is_empty() {
        return Err("reverse-qualified: the schedule declares no irradiation step".into());
    }
    let (m, k) = (measurements.len(), irradiation.len());
    if m < k {
        return Err(format!(
            "reverse-qualified: {m} measurements cannot determine {k} segment multipliers"
        ));
    }
    // Qualified mode requires every measurement to carry a declared
    // measurement σ — the inverse claims are only as honest as the inputs.
    for m_i in &measurements {
        if m_i.declared_sigma.is_none() {
            return Err(format!(
                "reverse-qualified: measurement {} at step {} declares no sigma_Bq_per_g — \
                 unit weighting is not permitted under the qualified mode",
                m_i.nuclide, m_i.step
            ));
        }
        if !uncertainty
            .responses
            .iter()
            .any(|r| r == &format!("activity:{}", m_i.nuclide))
        {
            return Err(format!(
                "reverse-qualified: measured nuclide {} has no banded response \
                 (uncertainty.responses lacks 'activity:{}')",
                m_i.nuclide, m_i.nuclide
            ));
        }
    }

    // ---- forward solves: one unit-flux run per irradiation segment --------
    let mut columns: Vec<Vec<f64>> = vec![Vec::with_capacity(m); k];
    let mut j_maps: Vec<Vec<HashMap<usize, f64>>> = Vec::with_capacity(k);
    let mut response_sigma: Vec<Vec<f64>> = vec![vec![0.0; k]; m];
    let mut selected_rows: std::collections::BTreeSet<usize> = std::collections::BTreeSet::new();
    let mut forward_runs = 0usize;
    let mut mode_seen = String::new();
    for (column_index, &step_index) in irradiation.iter().enumerate() {
        let mut unit = spec.clone();
        for (i, step) in unit.schedule.iter_mut().enumerate() {
            step.flux = if step.flux > 0.0 && i == step_index {
                1.0
            } else {
                0.0
            };
        }
        let result = run::run(&unit, "reverse-qualified")?;
        forward_runs += 1;
        mode_seen = result.mode.clone();
        if result.mode != "trace" {
            return Err(format!(
                "reverse-qualified: the problem resolved to '{}' mode; the linear inverse \
                 requires the trace regime",
                result.mode
            ));
        }
        let mut maps: Vec<HashMap<usize, f64>> = Vec::with_capacity(m);
        for (i, m_i) in measurements.iter().enumerate() {
            let step = &result.steps[m_i.step - 1];
            // A nuclide absent from a segment's step inventory is a causal
            // zero — that irradiation happens later or makes none. It is only
            // an error if every segment leaves the measurement dead.
            let activity = step
                .activity_Bq_per_g
                .get(&m_i.nuclide)
                .copied()
                .unwrap_or(0.0);
            columns[column_index].push(activity);
            let mut map = HashMap::new();
            if activity != 0.0 {
                if let Some(resp) = step
                    .uncertainty
                    .as_ref()
                    .and_then(|u| u.responses.get(&format!("activity:{}", m_i.nuclide)))
                {
                    response_sigma[i][column_index] = resp
                        .combined_standard_uncertainty
                        .unwrap_or(resp.mf33_standard_uncertainty);
                    for s in &resp.sensitivities {
                        if s.parameter.spectrum != 0 {
                            return Err(
                                "reverse-qualified: multi-spectrum sensitivity records are \
                                 unsupported"
                                    .into(),
                            );
                        }
                        if s.parameter.covariance_covered {
                            selected_rows.insert(s.parameter.library_row);
                        }
                        if s.value != 0.0 {
                            *map.entry(s.parameter.library_row).or_insert(0.0) += s.value;
                        }
                    }
                }
            }
            maps.push(map);
        }
        j_maps.push(maps);
    }
    for (i, m_i) in measurements.iter().enumerate() {
        if (0..k).all(|seg| columns[seg][i] == 0.0) {
            return Err(format!(
                "reverse-qualified: measured nuclide {} at step {} is never produced by any \
                 irradiation segment",
                m_i.nuclide, m_i.step
            ));
        }
    }

    // ---- nuclear-data covariance collapse -------------------------------
    let lib_sha = sha256_file(&spec.library.path)?;
    if let Some(declared) = &spec.library.sha256 {
        if !lib_sha.eq_ignore_ascii_case(declared) {
            return Err(
                "reverse-qualified: activation library does not match the spec's declared hash"
                    .into(),
            );
        }
    }
    let library = actinv_data::library::read_npz(&spec.library.path)?;
    let cov_sha = sha256_file(&uncertainty.covariance.path)?;
    if !cov_sha.eq_ignore_ascii_case(&uncertainty.covariance.sha256) {
        return Err(
            "reverse-qualified: covariance sidecar does not match the spec's declared hash".into(),
        );
    }
    let covariance = actinv_data::covariance::read_npz(&uncertainty.covariance.path)?;
    let phi = spec.spectrum.flux_per_group.clone();
    let selected: Vec<usize> = selected_rows.into_iter().collect();
    let sparse =
        covariance.collapse_sparse_weighted_multi(&library, &[&phi], &selected, &|_, _| 1.0)?;
    let n_covered = sparse.row_indices.len();
    let mut row_pos: HashMap<usize, usize> = HashMap::new();
    for (i, &row) in sparse.row_indices.iter().enumerate() {
        row_pos.insert(row, i);
    }
    // Densify each (measurement, segment) sensitivity onto covered positions.
    let j_dense: Vec<Vec<Vec<f64>>> = j_maps
        .iter()
        .map(|maps| {
            maps.iter()
                .map(|map| {
                    let mut v = vec![0.0; n_covered];
                    for (&row, &val) in map {
                        if let Some(&pos) = row_pos.get(&row) {
                            v[pos] += val;
                        }
                    }
                    v
                })
                .collect()
        })
        .collect();

    // ---- pass 1: measurement-weighted NNLS point -------------------------
    let aw0: Vec<Vec<f64>> = (0..m)
        .map(|i| {
            let root = measurements[i].weight.sqrt();
            (0..k).map(|j| root * columns[j][i]).collect()
        })
        .collect();
    let bw0: Vec<f64> = measurements
        .iter()
        .map(|m_i| m_i.weight.sqrt() * m_i.activity)
        .collect();
    let f0 = nnls(&aw0, &bw0)?;
    if f0.iter().all(|&v| v <= 0.0) {
        return Err(
            "reverse-qualified: the first-pass estimate is identically zero; cannot evaluate \
             the model covariance"
                .into(),
        );
    }

    // G_i = Σ_k f0_k · J_{i,k} — model covariance of the fitted response is
    // quadratic in f: C_model[i,j] = Σ_kl f0_k f0_l · J_ikᵀΣJ_jl.
    let g: Vec<Vec<f64>> = (0..m)
        .map(|i| {
            let mut gi = vec![0.0; n_covered];
            for (seg, maps) in j_dense.iter().enumerate() {
                let w = f0[seg];
                if w != 0.0 {
                    for (p, &v) in maps[i].iter().enumerate() {
                        gi[p] += w * v;
                    }
                }
            }
            gi
        })
        .collect();
    let mut c_model = vec![vec![0.0; m]; m];
    for i in 0..m {
        for j in 0..=i {
            let mut acc = 0.0;
            for (&(l, r), &v) in &sparse.entries {
                acc += g[i][l] * v * g[j][r];
            }
            c_model[i][j] = acc;
            c_model[j][i] = acc;
        }
    }
    let c_meas_diag: Vec<f64> = measurements.iter().map(|m_i| 1.0 / m_i.weight).collect();
    let mut c_total = c_model.clone();
    for i in 0..m {
        c_total[i][i] += c_meas_diag[i];
    }

    // ---- pass 2: GLS under the total covariance ---------------------------
    let l = cholesky(&c_total).ok_or(
        "reverse-qualified: the total covariance (measurement + model) is not positive definite",
    )?;
    // whiten the design columns: aw = L⁻¹ A — columnwise solves, each over
    // the m measurement entries (solving per-measurement rows of length k
    // against the m×m factor would silently truncate)
    let mut aw = vec![vec![0.0; k]; m];
    for (j, col) in columns.iter().enumerate() {
        let x = forward_solve(&l, col);
        for i in 0..m {
            aw[i][j] = x[i];
        }
    }
    let bw = forward_solve(
        &l,
        &measurements
            .iter()
            .map(|m_i| m_i.activity)
            .collect::<Vec<_>>(),
    );
    let f_hat = nnls(&aw, &bw)?;

    // Posterior: C_post = (Aᵀ C⁻¹ A + λI)⁻¹ on the free coordinates.
    let mut precision = vec![vec![0.0; k]; k];
    for i in 0..m {
        for u in 0..k {
            for v in 0..k {
                precision[u][v] += aw[i][u] * aw[i][v];
            }
        }
    }
    let mean_diag: f64 = (0..k).map(|j| precision[j][j]).sum::<f64>() / k.max(1) as f64;
    let ridge = RIDGE_FRACTION * mean_diag;
    let mut prec_ridge = precision.clone();
    for j in 0..k {
        prec_ridge[j][j] += ridge;
    }
    let post_cov = invert(&prec_ridge)
        .map_err(|e| format!("reverse-qualified: posterior precision is singular: {e}"))?;
    let post_sigma: Vec<f64> = post_cov
        .iter()
        .enumerate()
        .map(|(j, r)| r[j].max(0.0).sqrt())
        .collect();
    let post_corr: Vec<Vec<f64>> = (0..k)
        .map(|u| {
            (0..k)
                .map(|v| {
                    if post_sigma[u] > 0.0 && post_sigma[v] > 0.0 {
                        post_cov[u][v] / (post_sigma[u] * post_sigma[v])
                    } else {
                        f64::NAN
                    }
                })
                .collect()
        })
        .collect();

    // ---- identifiability --------------------------------------------------
    let post_eigs = jacobi_eigenvalues(&post_cov);
    let lam_max = post_eigs.iter().cloned().fold(0.0, f64::max);
    let effective_rank = post_eigs
        .iter()
        .filter(|&&lam| lam > 1e-8 * lam_max)
        .count();
    let identifiability: Vec<serde_json::Value> = (0..k)
        .map(|j| {
            let mut reasons: Vec<serde_json::Value> = Vec::new();
            if f_hat[j] <= 0.0 {
                reasons.push(serde_json::json!(
                    "multiplier pinned at zero by the nonnegativity constraint"
                ));
            } else if post_sigma[j] > REL_SIGMA_MAX * f_hat[j] {
                reasons.push(serde_json::json!(format!(
                    "relative posterior sigma {:.3} exceeds {}",
                    post_sigma[j] / f_hat[j],
                    REL_SIGMA_MAX
                )));
            }
            let worst = (0..k)
                .filter(|&j2| j2 != j)
                .filter_map(|j2| post_corr[j][j2].is_finite().then_some((j2, post_corr[j][j2])))
                .max_by(|a, b| a.1.abs().total_cmp(&b.1.abs()));
            if let Some((j2, rho)) = worst {
                if rho.abs() >= RHO_MAX {
                    reasons.push(serde_json::json!(format!(
                        "posterior correlation {rho:.3} with segment at step {} exceeds {}",
                        irradiation[j2] + 1,
                        RHO_MAX
                    )));
                }
            }
            serde_json::json!({
                "step": irradiation[j] + 1,
                "resolvable": reasons.is_empty(),
                "relative_posterior_sigma": if f_hat[j] > 0.0 { serde_json::json!(post_sigma[j] / f_hat[j]) } else { serde_json::Value::Null },
                "worst_correlation": worst.map(|(j2, rho)| serde_json::json!({
                    "segment_step": irradiation[j2] + 1, "rho": rho })),
                "reasons": reasons,
            })
        })
        .collect();

    // ---- consistency ------------------------------------------------------
    let residual: Vec<f64> = (0..m)
        .map(|i| measurements[i].activity - (0..k).map(|j| f_hat[j] * columns[j][i]).sum::<f64>())
        .collect();
    let whitened = forward_solve(&l, &residual);
    let chi_square: f64 = whitened.iter().map(|v| v * v).sum();
    let pulls: Vec<serde_json::Value> = (0..m)
        .map(|i| {
            let pull = residual[i] / c_total[i][i].max(f64::MIN_POSITIVE).sqrt();
            serde_json::json!({
                "step": measurements[i].step,
                "nuclide": measurements[i].nuclide,
                "activity_Bq_per_g": measurements[i].activity,
                "sigma_Bq_per_g": c_meas_diag[i].sqrt(),
                "residual_Bq_per_g": residual[i],
                "pull": pull,
                "flagged": pull.abs() > 3.0,
            })
        })
        .collect();

    // ---- emit -------------------------------------------------------------
    let confidence = uncertainty.confidence_level;
    let z = crate::uncertainty::normal_multiplier(confidence);
    let mut out = String::new();
    out.push_str(
        &serde_json::to_string(&serde_json::json!({
            "record": "header",
            "schema": QUALIFIED_FORMAT,
            "problem_sha256": sha256_text(problem_json),
            "measurements_sha256": sha256_text(measurements_json),
            "activation_library_sha256": lib_sha,
            "covariance_sha256": cov_sha,
            "forward_runs": forward_runs,
            "regime": mode_seen,
            "segments": irradiation.iter().map(|&i| i + 1).collect::<Vec<_>>(),
            "statistical_model": {
                "estimate": "two-pass: measurement-weighted NNLS point f0, then GLS NNLS under C = C_meas + C_model(f0)",
                "c_model": "C_model[i,j] = (Σ_k f0_k J_{i,k})ᵀ Σ (Σ_l f0_l J_{j,l}) — propagated MF33 XS covariance, shared-parameter correlations included",
                "posterior": "C_post = (Aᵀ C⁻¹ A + λI)⁻¹, λ = 1e-9·mean(diag(AᵀC⁻¹A)) — a declared weak ridge keeping degenerate directions finite",
                "coverage": "cross_section_mf33 only — same scope as the forward bands; decay/yield channels and transport/model discrepancy are not inferred",
            },
            "thresholds": {
                "resolvable_relative_sigma_max": REL_SIGMA_MAX,
                "resolvable_rho_max": RHO_MAX,
                "pull_flag": 3.0,
            },
            "confidence_level": confidence,
        }))
        .map_err(|e| e.to_string())?,
    );
    out.push('\n');
    // Sensitivity records carry the raw J maps — checkers apply their own
    // coverage filter rather than trusting the emit's.
    for (seg, maps) in j_maps.iter().enumerate() {
        for (i, map) in maps.iter().enumerate() {
            // Full sensitivity map (all nonzero rows) — checkers apply their
            // own coverage filter rather than trusting the emit's.
            let mut entries: BTreeMap<String, f64> = BTreeMap::new();
            for (&row, &v) in map {
                entries.insert(row.to_string(), v);
            }
            out.push_str(
                &serde_json::to_string(&serde_json::json!({
                    "record": "sensitivity",
                    "measurement": i,
                    "step": measurements[i].step,
                    "nuclide": measurements[i].nuclide,
                    "segment": seg,
                    "segment_step": irradiation[seg] + 1,
                    "a_Bq_per_g": columns[seg][i],
                    "response_sigma_Bq_per_g": response_sigma[i][seg],
                    "entries": entries,
                }))
                .map_err(|e| e.to_string())?,
            );
            out.push('\n');
        }
    }
    // Kalman gain rows K_j = (C_post·AᵀC⁻¹)_j — which measurements drive
    // each segment's estimate.
    let mut cinv_a = vec![vec![0.0; k]; m];
    for j in 0..k {
        let x = dense_solve(&c_total, &columns[j])
            .map_err(|e| format!("reverse-qualified: C⁻¹A solve failed: {e}"))?;
        for i in 0..m {
            cinv_a[i][j] = x[i];
        }
    }
    for (j, &step_index) in irradiation.iter().enumerate() {
        let mut gain: Vec<(usize, f64)> = (0..m)
            .map(|i| {
                (
                    i,
                    (0..k).map(|v| post_cov[j][v] * cinv_a[i][v]).sum::<f64>(),
                )
            })
            .collect();
        gain.sort_by(|a, b| b.1.abs().total_cmp(&a.1.abs()));
        let top3: Vec<serde_json::Value> = gain
            .iter()
            .take(3)
            .map(|&(i, g)| {
                serde_json::json!({
                    "step": measurements[i].step,
                    "nuclide": measurements[i].nuclide,
                    "gain": g,
                })
            })
            .collect();
        out.push_str(
            &serde_json::to_string(&serde_json::json!({
                "record": "estimate",
                "segment": j,
                "step": step_index + 1,
                "top_sensitivity_contributors": top3,
                "multiplier": f_hat[j],
                "posterior_sigma": post_sigma[j],
                "confidence_interval": [f_hat[j] - z * post_sigma[j],
                                        f_hat[j] + z * post_sigma[j]],
                "relative_posterior_sigma": if f_hat[j] > 0.0 {
                    serde_json::json!(post_sigma[j] / f_hat[j])
                } else {
                    serde_json::Value::Null
                },
                "first_pass_multiplier": f0[j],
            }))
            .map_err(|e| e.to_string())?,
        );
        out.push('\n');
    }
    out.push_str(
        &serde_json::to_string(&serde_json::json!({
            "record": "posterior",
            "covariance": post_cov,
            "correlation": post_corr,
            "eigenvalues": post_eigs,
            "effective_rank": effective_rank,
            "covariance_model": c_model,
            "covariance_measurement_diagonal": c_meas_diag,
            "covariance_total": c_total,
        }))
        .map_err(|e| e.to_string())?,
    );
    out.push('\n');
    out.push_str(
        &serde_json::to_string(&serde_json::json!({
            "record": "identifiability",
            "segments": identifiability,
            "resolvable_count": identifiability.iter()
                .filter(|s| s["resolvable"].as_bool() == Some(true)).count(),
        }))
        .map_err(|e| e.to_string())?,
    );
    out.push('\n');
    out.push_str(
        &serde_json::to_string(&serde_json::json!({
            "record": "consistency",
            "chi_square": chi_square,
            "degrees_of_freedom": m.saturating_sub(k),
            "pulls": pulls,
        }))
        .map_err(|e| e.to_string())?,
    );
    out.push('\n');
    let summary = serde_json::json!({
        "segments": k,
        "measurements": m,
        "forward_runs": forward_runs,
        "covered_parameters": n_covered,
        "chi_square": chi_square,
        "resolvable": identifiability.iter()
            .filter(|s| s["resolvable"].as_bool() == Some(true)).count(),
    });
    Ok((out, summary))
}

#[cfg(test)]
mod tests {
    use super::{cholesky, dense_solve, forward_solve, invert, nnls};

    #[test]
    fn cholesky_factors_and_rejects_nondefinite() {
        let c = vec![vec![4.0, 2.0], vec![2.0, 3.0]];
        let l = cholesky(&c).unwrap();
        // L·Lᵀ reconstructs C
        for i in 0..2 {
            for j in 0..2 {
                let s: f64 = (0..=i.min(j)).map(|k| l[i][k] * l[j][k]).sum();
                assert!((s - c[i][j]).abs() < 1e-12);
            }
        }
        // forward_solve inverts L: L x = b
        let b = vec![1.0, -2.0];
        let x = forward_solve(&l, &b);
        for i in 0..2 {
            let s: f64 = (0..=i).map(|j| l[i][j] * x[j]).sum();
            assert!((s - b[i]).abs() < 1e-12);
        }
        // indefinite → None
        assert!(cholesky(&[vec![1.0, 3.0], vec![3.0, 1.0]]).is_none());
    }

    #[test]
    fn gls_posterior_matches_known_two_measurement_case() {
        // A = I on k=2, C = diag(4,1)+vv^T with v=(1,1): the model
        // correlation must enter the posterior covariance.
        // C = [[5,1],[1,2]]; whiten aw = L⁻¹; prec = awᵀaw = C⁻¹.
        let c = vec![vec![5.0, 1.0], vec![1.0, 2.0]];
        let l = cholesky(&c).unwrap();
        let aw: Vec<Vec<f64>> = (0..2)
            .map(|j| {
                forward_solve(
                    &l,
                    &[
                        if j == 0 { 1.0 } else { 0.0 },
                        if j == 1 { 1.0 } else { 0.0 },
                    ],
                )
            })
            .collect();
        // aw[j] is the j-th whitened column; build precision properly
        let mut prec = vec![vec![0.0; 2]; 2];
        for i in 0..2 {
            for u in 0..2 {
                for v in 0..2 {
                    // whitened system row i, column j: aw_cols[j][i]
                    prec[u][v] += aw[u][i] * aw[v][i];
                }
            }
        }
        let post = invert(&prec).unwrap();
        // C⁻¹ = [[2,-1],[-1,5]]/9 → post = C/1 = [[5,1],[1,2]] (posterior of
        // an identity observation equals the prior covariance)
        assert!((post[0][0] - 5.0).abs() < 1e-9);
        assert!((post[0][1] - 1.0).abs() < 1e-9);
        assert!((post[1][1] - 2.0).abs() < 1e-9);
    }

    #[test]
    fn dense_solve_singularity_is_relative_to_matrix_scale() {
        // Well conditioned but tiny in magnitude: used to be called singular.
        let x = dense_solve(&[vec![1e-30, 0.0], vec![0.0, 2e-30]], &[1e-30, 4e-30]).unwrap();
        assert!(
            (x[0] - 1.0).abs() < 1e-12 && (x[1] - 2.0).abs() < 1e-12,
            "{x:?}"
        );
        assert!(dense_solve(&[vec![1.0, 1.0], vec![1.0, 1.0]], &[1.0, 1.0]).is_err());
    }

    #[test]
    fn nnls_clamps_to_the_nonnegative_orthant_with_finite_values() {
        let x = nnls(&[vec![1.0, 0.0], vec![0.0, 1.0]], &[2.0, -1.0]).unwrap();
        assert_eq!(x, vec![2.0, 0.0]);
        let x = nnls(
            &[vec![1.0, 1.0], vec![1.0, -1.0], vec![0.0, 1.0]],
            &[1.0, 1.0, -3.0],
        )
        .unwrap();
        assert!(x.iter().all(|v| v.is_finite() && *v >= 0.0), "{x:?}");
    }
}
