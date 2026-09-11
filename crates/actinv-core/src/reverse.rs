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
    for col in 0..n {
        let mut pivot = col;
        for row in col + 1..n {
            if m[row][col].abs() > m[pivot][col].abs() {
                pivot = row;
            }
        }
        if m[pivot][col].abs() <= f64::EPSILON {
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
            let mut alpha = f64::INFINITY;
            for &j in &p {
                if z[j] <= tolerance {
                    alpha = alpha.min(x[j] / (x[j] - z[j]));
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
