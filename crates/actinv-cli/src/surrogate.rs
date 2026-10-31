//! D4 — certified surrogate over the optimize design axes.
//!
//! `actinv surrogate fit` trains a multilinear interpolant on a
//! rectilinear grid of *certified-edge* solves (the declared response
//! edge on each solve — a banded edge when the spec is banded) and emits
//! an `actinv-surrogate-1` artifact.
//!
//! `actinv surrogate eval` answers `ŷ(x) ⊕ ε_cert` where
//!
//! ```text
//! ε_cert(x) = r_max + L̂ · ‖x − x_nearest‖
//! ```
//!
//! with `r_max` the max residual over seeded holdout solves the fit never
//! saw and `L̂` the max secant slope over grid edges — an *estimate* of
//! the interpolation constant, named as such in the certificate. Sparse
//! neighborhoods yield honestly wide ε; points outside the axes bounds
//! are refused rather than extrapolated. The artifact records the
//! training spec sha and solver identity so staleness is detectable.

use serde::Deserialize;
use serde_json::Value;
use std::path::{Path, PathBuf};

use crate::optimize::{apply_axes, response_edge, select_step, sha256_hex, Axis, Edge};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SurrogateSpec {
    pub schema: String,
    pub base_spec: String,
    /// Each entry is an optimize design axis plus `"points": N ≥ 2` —
    /// the grid resolution along that axis.
    pub axes: Vec<GridAxis>,
    pub responses: Vec<SurrogateResponse>,
    /// Seeded interior solves never used to fit; their max residual is
    /// the holdout term of ε_cert.
    #[serde(default = "default_holdout")]
    pub holdout_points: usize,
    #[serde(default = "default_seed")]
    pub seed: u64,
}

fn default_holdout() -> usize {
    4
}
fn default_seed() -> u64 {
    74
}

#[derive(Debug, Deserialize)]
pub struct GridAxis {
    #[serde(flatten)]
    pub axis: Axis,
    pub points: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SurrogateResponse {
    pub response: String,
    pub time_s: f64,
    #[serde(default = "crate::optimize::edge_nominal")]
    pub edge: Edge,
}

struct XorShift(u64);
impl XorShift {
    fn next(&mut self) -> f64 {
        // xorshift64* → [0,1)
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        (x.wrapping_mul(0x2545F4914F6CDD1D) >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// Multilinear interpolation over a rectilinear grid. `values` is
/// row-major over axes (last axis fastest). Returns None outside bounds.
fn predict(grids: &[Vec<f64>], values: &[f64], x: &[f64]) -> Option<f64> {
    let n = grids.len();
    if n == 0 || x.len() != n {
        return None;
    }
    let mut cell_lo = Vec::with_capacity(n);
    let mut cell_hi = Vec::with_capacity(n);
    let mut t = Vec::with_capacity(n);
    for (d, g) in grids.iter().enumerate() {
        if g.len() < 2 || x[d] < g[0] || x[d] > *g.last()? {
            return None;
        }
        // interval containing x[d]
        let i = g
            .iter()
            .position(|v| *v > x[d])
            .unwrap_or(g.len() - 1)
            .saturating_sub(1);
        let (lo, hi) = (g[i], g[i + 1]);
        cell_lo.push(i);
        cell_hi.push(i + 1);
        t.push(if hi > lo {
            (x[d] - lo) / (hi - lo)
        } else {
            0.0
        });
    }
    // strides: last axis fastest
    let strides: Vec<usize> = (0..n)
        .map(|d| grids[d + 1..].iter().map(|g| g.len()).product())
        .collect();
    let idx = |corner: &[usize]| -> usize {
        corner
            .iter()
            .enumerate()
            .map(|(d, &c)| c * strides[d])
            .sum()
    };
    // 2^n corner blend
    let mut acc = 0.0;
    for mask in 0..(1usize << n) {
        let mut w = 1.0;
        let mut corner = vec![0usize; n];
        for d in 0..n {
            if mask & (1 << d) != 0 {
                corner[d] = cell_hi[d];
                w *= t[d];
            } else {
                corner[d] = cell_lo[d];
                w *= 1.0 - t[d];
            }
        }
        acc += w * values[idx(&corner)];
    }
    Some(acc)
}

/// Euclidean distance from x to the nearest grid node.
fn dist_to_grid(grids: &[Vec<f64>], x: &[f64]) -> f64 {
    let n = grids.len();
    let total: usize = grids.iter().map(|g| g.len()).product();
    let strides: Vec<usize> = (0..n)
        .map(|d| grids[d + 1..].iter().map(|g| g.len()).product())
        .collect();
    let mut best = f64::INFINITY;
    for flat in 0..total {
        let mut sq = 0.0;
        for (d, g) in grids.iter().enumerate() {
            let c = flat / strides[d] % g.len();
            let dv = x[d] - g[c];
            sq += dv * dv;
        }
        best = best.min(sq.sqrt());
    }
    best
}

fn eval_solve(
    base_doc: &Value,
    axes: &[Axis],
    x: &[f64],
    responses: &[SurrogateResponse],
    cache: &mut actinv_core::run::PreparedCache,
) -> Result<Vec<f64>, String> {
    let doc = apply_axes(base_doc, axes, x)?;
    let canon = serde_json::to_string(&doc).map_err(|e| e.to_string())?;
    let resolved = crate::resolve_catalog_json(&canon)?;
    let spec = actinv_core::spec::Spec::from_json(&resolved)?;
    let result = actinv_core::run::run_with_cache(&spec, "surrogate", cache)?;
    responses
        .iter()
        .map(|r| {
            select_step(&result.steps, r.time_s).and_then(|st| {
                response_edge(st, &r.response, r.edge)
                    .and_then(|v| v.ok_or("response absent".into()))
            })
        })
        .collect()
}

/// Artifact name of a response: `response@time:Edge` (time to 3 decimals).
fn response_label(r: &SurrogateResponse) -> String {
    format!("{}@{:.3}:{:?}", r.response, r.time_s, r.edge)
}

/// Two responses with one label would be indistinguishable in the artifact.
fn check_unique_responses(responses: &[SurrogateResponse]) -> Result<(), String> {
    let mut seen = std::collections::BTreeSet::new();
    for r in responses {
        let label = response_label(r);
        if !seen.insert(label.clone()) {
            return Err(format!(
                "surrogate spec defines response '{label}' more than once"
            ));
        }
    }
    Ok(())
}

pub fn run_fit(spec_path: &str, out_dir: Option<&str>) -> Result<Value, String> {
    let text =
        std::fs::read_to_string(spec_path).map_err(|e| format!("cannot read {spec_path}: {e}"))?;
    let spec: SurrogateSpec =
        serde_json::from_str(&text).map_err(|e| format!("{spec_path}: {e}"))?;
    if spec.schema != "actinv-surrogate-1" {
        return Err("surrogate spec must declare schema actinv-surrogate-1".into());
    }
    if spec.axes.is_empty() || spec.axes.len() > 4 {
        return Err("surrogate spec needs 1..=4 axes".into());
    }
    if spec.axes.iter().any(|a| a.points < 2 || a.points > 16) {
        return Err("surrogate axis points must be 2..=16".into());
    }
    if spec.responses.is_empty() {
        return Err("surrogate spec needs ≥1 responses".into());
    }
    check_unique_responses(&spec.responses)?;
    // base_spec is relative to the surrogate spec's directory, as in
    // optimize and decide.
    let base_path = Path::new(spec_path)
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(&spec.base_spec);
    let base_text = std::fs::read_to_string(&base_path)
        .map_err(|e| format!("cannot read base spec {}: {e}", base_path.display()))?;
    let base_doc: Value = serde_json::from_str(&base_text)
        .map_err(|e| format!("base spec {}: {e}", base_path.display()))?;
    let base_sha = sha256_hex(base_text.as_bytes());
    let axes: Vec<Axis> = spec.axes.iter().map(|a| a.axis.clone()).collect();

    // rectilinear grid nodes, last axis fastest
    let grids: Vec<Vec<f64>> = spec
        .axes
        .iter()
        .map(|a| {
            let [lo, hi] = a.axis.bounds();
            let n = a.points;
            (0..n)
                .map(|i| lo + (hi - lo) * (i as f64) / (n - 1) as f64)
                .collect()
        })
        .collect();
    let n_axes = grids.len();
    let n_nodes: usize = grids.iter().map(|g| g.len()).product();
    let strides: Vec<usize> = (0..n_axes)
        .map(|d| grids[d + 1..].iter().map(|g| g.len()).product())
        .collect();
    let node_x = |flat: usize| -> Vec<f64> {
        (0..n_axes)
            .map(|d| grids[d][flat / strides[d] % grids[d].len()])
            .collect()
    };

    let mut cache = actinv_core::run::PreparedCache::new();
    // ---- training solves --------------------------------------------------
    // per node: one value per response, in spec.responses order
    let mut values: Vec<Vec<f64>> = Vec::with_capacity(n_nodes);
    for flat in 0..n_nodes {
        let x = node_x(flat);
        values.push(eval_solve(
            &base_doc,
            &axes,
            &x,
            &spec.responses,
            &mut cache,
        )?);
    }
    let flat_values = |ri: usize| -> Vec<f64> { values.iter().map(|v| v[ri]).collect() };

    // ---- holdout solves (seeded interior points) ---------------------------
    let mut rng = XorShift(spec.seed.max(1));
    let mut holdout_pts = Vec::with_capacity(spec.holdout_points);
    let mut r_max = vec![0.0f64; spec.responses.len()];
    for _ in 0..spec.holdout_points {
        let x: Vec<f64> = spec
            .axes
            .iter()
            .map(|a| {
                let [lo, hi] = a.axis.bounds();
                lo + (hi - lo) * rng.next()
            })
            .collect();
        let actual = eval_solve(&base_doc, &axes, &x, &spec.responses, &mut cache)?;
        for (ri, av) in actual.iter().enumerate() {
            let fv = flat_values(ri);
            if let Some(pred) = predict(&grids, &fv, &x) {
                r_max[ri] = r_max[ri].max((av - pred).abs());
            }
        }
        holdout_pts.push(x);
    }

    // ---- secant-slope bound per response -----------------------------------
    let l_hat: Vec<f64> = (0..spec.responses.len())
        .map(|ri| {
            let fv = flat_values(ri);
            let mut l = 0.0f64;
            for flat in 0..n_nodes {
                for d in 0..n_axes {
                    let c = flat / strides[d] % grids[d].len();
                    if c + 1 < grids[d].len() {
                        let nb = flat + strides[d];
                        let dx = (grids[d][c + 1] - grids[d][c]).abs().max(1e-300);
                        l = l.max((fv[nb] - fv[flat]).abs() / dx);
                    }
                }
            }
            l
        })
        .collect();

    let responses_out: Vec<Value> = spec
        .responses
        .iter()
        .enumerate()
        .map(|(ri, r)| {
            serde_json::json!({
                "name": response_label(r),
                "response": r.response,
                "time_s": r.time_s,
                "edge": format!("{:?}", r.edge),
                "values": flat_values(ri),
                "holdout_max_residual": r_max[ri],
                "lipschitz_estimate": l_hat[ri],
            })
        })
        .collect();

    let artifact = serde_json::json!({
        "schema": "actinv-surrogate-1",
        "solver": concat!("actinv-core ", env!("CARGO_PKG_VERSION")),
        "base_spec_sha256": base_sha,
        "surspec_sha256": sha256_hex(text.as_bytes()),
        "axes": spec.axes.iter().enumerate().map(|(i, a)| serde_json::json!({
            "axis": axis_name(&a.axis),
            "bounds": a.axis.bounds(),
            "grid": grids[i],
        })).collect::<Vec<_>>(),
        "responses": responses_out,
        "training": {
            "n_nodes": n_nodes,
            "holdout_points": spec.holdout_points,
            "seed": spec.seed,
            "holdout_x": holdout_pts,
        },
        "certificate": {
            "formula": "epsilon_cert(x) = holdout_max_residual + lipschitz_estimate * dist(x, nearest grid node)",
            "honesty": "lipschitz_estimate is a max secant slope over the grid — an estimate of the interpolation constant, not a theorem; sparse neighborhoods emit honestly wide epsilon_cert rather than false confidence",
            "in_domain": "inside every axis bounds; eval refuses extrapolation",
        },
    });
    let dir = out_dir
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(".").to_path_buf());
    std::fs::create_dir_all(&dir).map_err(|e| format!("cannot create {dir:?}: {e}"))?;
    let out_path = dir.join("surrogate.json");
    std::fs::write(&out_path, serde_json::to_string_pretty(&artifact).unwrap())
        .map_err(|e| format!("cannot write {out_path:?}: {e}"))?;
    Ok(serde_json::json!({
        "n_nodes": n_nodes,
        "axes": n_axes,
        "holdout": spec.holdout_points,
        "r_max": r_max,
        "lipschitz": l_hat,
        "out": out_path.display().to_string(),
    }))
}

pub(crate) fn axis_name(axis: &Axis) -> String {
    match axis {
        Axis::CompositionFraction { element, .. } => format!("composition_fraction:{element}"),
        Axis::FluxScale { .. } => "flux_scale".into(),
        Axis::StepDt { step, .. } => format!("step_dt:{step}"),
    }
}

/// In-memory certified surrogate: the grids, per-response values and the
/// fitted honesty constants. Shared by the `eval` CLI and the optimizer's
/// surrogate tier.
pub struct SurrogateResponseEval {
    /// Fully qualified fitted response identifier (`name` field), the
    /// bare response path, and its fitted (ŷ, ε_cert) pair.
    pub name: String,
    pub response: String,
    pub time_s: f64,
    pub y: Option<f64>,
    pub eps: f64,
    pub holdout_max_residual: f64,
    pub lipschitz_term: f64,
}

/// A JSON array whose every entry is a finite number; anything else is an
/// error rather than a silently shortened vector.
fn f64_array(v: &Value, what: &str) -> Result<Vec<f64>, String> {
    v.as_array()
        .ok_or_else(|| format!("{what} missing"))?
        .iter()
        .map(|e| {
            e.as_f64()
                .filter(|f| f.is_finite())
                .ok_or_else(|| format!("{what} has a non-numeric or non-finite entry"))
        })
        .collect()
}

pub struct Surrogate {
    /// Positional axis identifiers (`axis_name` strings) — the optimizer
    /// asserts they match its design_axes order before trusting evals.
    pub axis_names: Vec<String>,
    pub grids: Vec<Vec<f64>>,
    responses: Vec<(String, String, f64, Vec<f64>, f64, f64)>,
    pub artifact_sha256: String,
}

impl Surrogate {
    pub fn load_file(path: &str) -> Result<Surrogate, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read {path}: {e}"))?;
        let artifact: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
        Surrogate::from_artifact(&artifact, sha256_hex(text.as_bytes()))
    }

    pub fn from_artifact(artifact: &Value, sha: String) -> Result<Surrogate, String> {
        if artifact["schema"].as_str() != Some("actinv-surrogate-1") {
            return Err("not an actinv-surrogate-1 artifact".into());
        }
        let axes = artifact["axes"].as_array().ok_or("artifact axes missing")?;
        let mut grids: Vec<Vec<f64>> = Vec::with_capacity(axes.len());
        let mut axis_names: Vec<String> = Vec::with_capacity(axes.len());
        for (i, a) in axes.iter().enumerate() {
            let grid = f64_array(&a["grid"], &format!("artifact axes[{i}].grid"))?;
            if grid.len() < 2 {
                return Err(format!("artifact axes[{i}].grid needs at least 2 nodes"));
            }
            grids.push(grid);
            axis_names.push(
                a["axis"]
                    .as_str()
                    .ok_or_else(|| format!("artifact axes[{i}].axis missing"))?
                    .to_string(),
            );
        }
        if grids.is_empty() {
            return Err("artifact has no axes".into());
        }
        let n_nodes: usize = grids.iter().map(|g| g.len()).product();
        let mut responses = Vec::new();
        for (i, r) in artifact["responses"]
            .as_array()
            .ok_or("artifact responses missing")?
            .iter()
            .enumerate()
        {
            let text = |k: &str| -> Result<String, String> {
                r[k].as_str()
                    .map(String::from)
                    .ok_or_else(|| format!("artifact responses[{i}].{k} missing"))
            };
            let num = |k: &str| -> Result<f64, String> {
                r[k].as_f64()
                    .filter(|v| v.is_finite())
                    .ok_or_else(|| format!("artifact responses[{i}].{k} missing or not finite"))
            };
            let values = f64_array(&r["values"], &format!("artifact responses[{i}].values"))?;
            if values.len() != n_nodes {
                return Err(format!(
                    "artifact responses[{i}].values has {} entries, grid needs {n_nodes}",
                    values.len()
                ));
            }
            responses.push((
                text("name")?,
                text("response")?,
                num("time_s")?,
                values,
                num("holdout_max_residual")?,
                num("lipschitz_estimate")?,
            ));
        }
        if responses.is_empty() {
            return Err("artifact has no responses".into());
        }
        Ok(Surrogate {
            axis_names,
            grids,
            responses,
            artifact_sha256: sha,
        })
    }

    /// Evaluate at x: None when out of domain (the caller's responsibility
    /// is to refuse extrapolation by falling back, never to widen silently).
    /// Each response yields (ŷ, ε_cert) with ε_cert never zero-width.
    pub fn eval(&self, x: &[f64]) -> Option<Vec<SurrogateResponseEval>> {
        if x.len() != self.grids.len() {
            return None;
        }
        for (d, g) in self.grids.iter().enumerate() {
            if g.is_empty() || x[d] < g[0] || x[d] > *g.last().unwrap() {
                return None;
            }
        }
        let d_nearest = dist_to_grid(&self.grids, x);
        Some(
            self.responses
                .iter()
                .map(|(name, response, time_s, values, r_max, l)| {
                    let y = predict(&self.grids, values, x);
                    // Never emit a zero-width certificate: below the
                    // observed residual the solver's own numerical noise
                    // still bounds the surrogate's honesty. The floor is
                    // relative to ŷ.
                    let eps = (r_max + l * d_nearest)
                        .max(y.map(|v| v.abs() * 1e-9).unwrap_or(0.0))
                        .max(1e-300);
                    SurrogateResponseEval {
                        name: name.clone(),
                        response: response.clone(),
                        time_s: *time_s,
                        y,
                        eps,
                        holdout_max_residual: *r_max,
                        lipschitz_term: l * d_nearest,
                    }
                })
                .collect(),
        )
    }
}

pub fn run_eval(
    artifact_path: &str,
    x_path: &str,
    out_path: Option<&str>,
) -> Result<Value, String> {
    let artifact_text = std::fs::read_to_string(artifact_path)
        .map_err(|e| format!("cannot read {artifact_path}: {e}"))?;
    let artifact: Value =
        serde_json::from_str(&artifact_text).map_err(|e| format!("{artifact_path}: {e}"))?;
    let sur = Surrogate::from_artifact(&artifact, sha256_hex(artifact_text.as_bytes()))?;
    let x_text =
        std::fs::read_to_string(x_path).map_err(|e| format!("cannot read {x_path}: {e}"))?;
    let xdoc: Value = serde_json::from_str(&x_text).map_err(|e| format!("{x_path}: {e}"))?;
    let x: Vec<f64> = xdoc["x"]
        .as_array()
        .ok_or("eval input must be {\"x\": [..]}")?
        .iter()
        .map(|v| v.as_f64().ok_or("x entries must be numeric"))
        .collect::<Result<_, _>>()?;
    let evals = sur.eval(&x).ok_or_else(|| {
        let bounds: Vec<String> = sur
            .grids
            .iter()
            .map(|g| match (g.first(), g.last()) {
                (Some(lo), Some(hi)) => format!("[{lo}, {hi}]"),
                _ => "[]".to_string(),
            })
            .collect();
        format!("x outside surrogate domain {bounds:?} — refuse to extrapolate")
    })?;
    let d_nearest = dist_to_grid(&sur.grids, &x);
    let responses: Vec<Value> = evals
        .iter()
        .map(|e| {
            serde_json::json!({
                "name": e.name,
                "surrogate": e.y,
                "epsilon_cert": e.eps,
                "band": e.y.map(|v| [v - e.eps, v + e.eps]),
                "holdout_max_residual": e.holdout_max_residual,
                "lipschitz_term": e.lipschitz_term,
            })
        })
        .collect();
    let out = serde_json::json!({
        "schema": "actinv-surrogate-eval-1",
        "x": x,
        "dist_to_nearest_grid_node": d_nearest,
        "artifact_sha256": sur.artifact_sha256,
        "certificate": artifact["certificate"].clone(),
        "responses": responses,
    });
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resp(name: &str, t: f64) -> SurrogateResponse {
        SurrogateResponse {
            response: name.into(),
            time_s: t,
            edge: Edge::Nominal,
        }
    }

    fn artifact() -> Value {
        serde_json::json!({
            "schema": "actinv-surrogate-1",
            "axes": [{"axis": "flux_scale", "grid": [0.0, 1.0, 2.0]}],
            "responses": [{
                "name": "heat.total@1.000:Nominal", "response": "heat.total",
                "time_s": 1.0, "values": [0.0, 10.0, 20.0],
                "holdout_max_residual": 0.5, "lipschitz_estimate": 10.0,
            }],
        })
    }

    #[test]
    fn duplicate_responses_are_refused() {
        assert!(
            check_unique_responses(&[resp("heat.total", 1.0), resp("heat.alpha", 1.0)]).is_ok()
        );
        // same label, and times equal at three decimals
        assert!(
            check_unique_responses(&[resp("heat.total", 1.0), resp("heat.total", 1.0)]).is_err()
        );
        assert!(
            check_unique_responses(&[resp("heat.total", 1.0), resp("heat.total", 1.0001)]).is_err()
        );
    }

    #[test]
    fn response_label_format_is_stable() {
        assert_eq!(
            response_label(&resp("heat.total", 3.0)),
            "heat.total@3.000:Nominal"
        );
    }

    #[test]
    fn well_formed_artifact_loads_and_predicts() {
        let sur = Surrogate::from_artifact(&artifact(), "sha".into()).unwrap();
        let ev = sur.eval(&[0.5]).unwrap();
        assert_eq!(ev[0].y, Some(5.0));
    }

    #[test]
    fn malformed_artifacts_are_errors() {
        let mut a = artifact();
        a["responses"][0]["values"] = serde_json::json!([0.0, "x", 20.0]);
        assert!(Surrogate::from_artifact(&a, "s".into()).is_err());
        let mut a = artifact();
        a["responses"][0]["values"] = serde_json::json!([0.0, 10.0]);
        assert!(Surrogate::from_artifact(&a, "s".into()).is_err());
        let mut a = artifact();
        a["axes"][0]["grid"] = serde_json::json!([]);
        assert!(Surrogate::from_artifact(&a, "s".into()).is_err());
        let mut a = artifact();
        a["responses"][0].as_object_mut().unwrap().remove("time_s");
        assert!(Surrogate::from_artifact(&a, "s".into()).is_err());
        let mut a = artifact();
        a.as_object_mut().unwrap().remove("responses");
        assert!(Surrogate::from_artifact(&a, "s".into()).is_err());
    }
}
