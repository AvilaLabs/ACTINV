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
use std::collections::BTreeMap;
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
    let base_text = std::fs::read_to_string(&spec.base_spec)
        .map_err(|e| format!("cannot read base spec {}: {e}", spec.base_spec))?;
    let base_doc: Value = serde_json::from_str(&base_text)
        .map_err(|e| format!("base spec {}: {e}", spec.base_spec))?;
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
    let mut values: Vec<BTreeMap<String, f64>> = Vec::with_capacity(n_nodes);
    for flat in 0..n_nodes {
        let x = node_x(flat);
        let v = eval_solve(&base_doc, &axes, &x, &spec.responses, &mut cache)?;
        values.push(
            spec.responses
                .iter()
                .map(|r| format!("{}@{:.3}:{:?}", r.response, r.time_s, r.edge))
                .zip(v)
                .collect(),
        );
    }
    let flat_values = |ri: usize| -> Vec<f64> {
        values
            .iter()
            .map(|m| *m.iter().nth(ri).map(|(_, v)| v).unwrap())
            .collect()
    };

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
                "name": format!("{}@{:.3}:{}", r.response, r.time_s,
                                format!("{:?}", r.edge)),
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

fn axis_name(axis: &Axis) -> String {
    match axis {
        Axis::CompositionFraction { element, .. } => format!("composition_fraction:{element}"),
        Axis::FluxScale { .. } => "flux_scale".into(),
        Axis::StepDt { step, .. } => format!("step_dt:{step}"),
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
    if artifact["schema"].as_str() != Some("actinv-surrogate-1") {
        return Err("not an actinv-surrogate-1 artifact".into());
    }
    let x_text =
        std::fs::read_to_string(x_path).map_err(|e| format!("cannot read {x_path}: {e}"))?;
    let xdoc: Value = serde_json::from_str(&x_text).map_err(|e| format!("{x_path}: {e}"))?;
    let x: Vec<f64> = xdoc["x"]
        .as_array()
        .ok_or("eval input must be {\"x\": [..]}")?
        .iter()
        .map(|v| v.as_f64().ok_or("x entries must be numeric"))
        .collect::<Result<_, _>>()?;
    let axes = artifact["axes"].as_array().ok_or("artifact axes missing")?;
    let grids: Vec<Vec<f64>> = axes
        .iter()
        .map(|a| {
            a["grid"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .filter_map(|v| v.as_f64())
                .collect()
        })
        .collect();
    if x.len() != grids.len() {
        return Err(format!(
            "x has {} dims, artifact has {}",
            x.len(),
            grids.len()
        ));
    }
    // refuse extrapolation
    for (d, g) in grids.iter().enumerate() {
        if x[d] < g[0] || x[d] > *g.last().unwrap() {
            return Err(format!(
                "x[{d}]={} outside surrogate domain [{}, {}] — refuse to extrapolate",
                x[d],
                g[0],
                g.last().unwrap()
            ));
        }
    }
    let d_nearest = dist_to_grid(&grids, &x);
    let responses: Vec<Value> = artifact["responses"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .map(|r| {
            let values: Vec<f64> = r["values"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .filter_map(|v| v.as_f64())
                .collect();
            let y = predict(&grids, &values, &x);
            let r_max = r["holdout_max_residual"].as_f64().unwrap_or(0.0);
            let l = r["lipschitz_estimate"].as_f64().unwrap_or(0.0);
            // Never emit a zero-width certificate: below the observed
            // residual the solver's own numerical noise still bounds the
            // surrogate's honesty. The floor is relative to ŷ.
            let eps = (r_max + l * d_nearest)
                .max(y.map(|v| v.abs() * 1e-9).unwrap_or(0.0))
                .max(1e-300);
            serde_json::json!({
                "name": r["name"],
                "surrogate": y,
                "epsilon_cert": eps,
                "band": y.map(|v| [v - eps, v + eps]),
                "holdout_max_residual": r_max,
                "lipschitz_term": l * d_nearest,
            })
        })
        .collect();
    let out = serde_json::json!({
        "schema": "actinv-surrogate-eval-1",
        "x": x,
        "dist_to_nearest_grid_node": d_nearest,
        "artifact_sha256": sha256_hex(artifact_text.as_bytes()),
        "certificate": artifact["certificate"].clone(),
        "responses": responses,
    });
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}
