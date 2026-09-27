//! D3 leg 1 — facility activation twin: the decision layer over a mesh run.
//!
//! `actinv twin TWINSPEC.json [OUT.json]`
//!
//! Reads the streaming `actinv mesh` output (one JSON record per cell,
//! each carrying the cell's full `RunResult`) and evaluates declared
//! clearance limits on the certified response bands — per cell, per
//! time, per limit. A cell clears only when its *band edge* clears:
//! nominal-on-the-line doesn't count.
//!
//! The emitted `actinv-twin-1` record carries the cell × time × limit
//! margin matrix, per-cell verdicts, and a facility rollup (cleared vs
//! restricted cells, the binding cell per limit, margin ranking) — the
//! queryable whole-facility activation surface ALARA's batch output
//! never produces banded.

use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::io::Read as _;
use std::path::Path;

const TWIN_SCHEMA: &str = "actinv-twin-1";

#[derive(serde::Deserialize)]
struct TwinSpec {
    spec: String,
    mesh_output: String,
    /// Clearance limits evaluated against each cell's certified bands.
    limits: Vec<LimitDecl>,
    /// Decision times in seconds; absent → every step time.
    #[serde(default)]
    times_s: Option<Vec<f64>>,
    /// D5→D3: assays applied to named cells before margins are scored —
    /// the assay's own `response`/`time_s` select the band it updates.
    #[serde(default)]
    assays: Option<Vec<CellAssay>>,
}

#[derive(serde::Deserialize)]
struct CellAssay {
    /// Mesh cell id the assay measures.
    cell: String,
    /// Path to an actinv-assay-1 document, resolved against the spec dir.
    assay: String,
}

#[derive(serde::Deserialize)]
struct LimitDecl {
    name: String,
    /// Response key into `step.uncertainty.responses` — e.g.
    /// "heat.total", "activity.total", "activity:Fe56".
    response: String,
    limit: f64,
    /// "le" (default): certified upper edge must sit below `limit`.
    /// "ge": certified lower edge must sit above `limit`.
    #[serde(default = "le")]
    sense: String,
}

/// Parsed assay: (response key, time_s, measured value, std uncertainty,
/// assay file sha256).
type LoadedAssay = (String, f64, f64, f64, String);

fn le() -> String {
    "le".into()
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= a.abs().max(b.abs()).max(1e-300) * 1e-9
}

pub fn run(spec_path: &str, out_path: Option<&str>) -> Result<Value, String> {
    let text =
        std::fs::read_to_string(spec_path).map_err(|e| format!("cannot read {spec_path}: {e}"))?;
    let spec: TwinSpec = serde_json::from_str(&text).map_err(|e| format!("{spec_path}: {e}"))?;
    if spec.spec != TWIN_SCHEMA {
        return Err(format!(
            "twin spec must declare \"spec\": \"{TWIN_SCHEMA}\""
        ));
    }
    if spec.limits.is_empty() {
        return Err("twin spec declares no clearance limits".into());
    }
    for l in &spec.limits {
        if l.sense != "le" && l.sense != "ge" {
            return Err(format!("limit '{}' sense must be \"le\" or \"ge\"", l.name));
        }
    }
    let mesh_path = spec.mesh_output.clone();
    let base = Path::new(spec_path)
        .parent()
        .map(std::path::Path::to_path_buf)
        .unwrap_or_else(|| Path::new(".").to_path_buf());
    let mesh_path = base.join(&mesh_path);
    if !mesh_path.exists() {
        return Err(format!(
            "mesh_output {} does not exist",
            mesh_path.display()
        ));
    }
    let mut file = std::fs::File::open(&mesh_path)
        .map_err(|e| format!("cannot open {}: {e}", mesh_path.display()))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw)
        .map_err(|e| format!("cannot read {}: {e}", mesh_path.display()))?;

    // Pre-load assays: cell id -> (fusion inputs + response/time selectors).
    // Each assay is validated up front so a malformed one fails the run
    // before any margin is scored on a silently un-updated band.
    let mut assays: HashMap<String, Vec<LoadedAssay>> = HashMap::new();
    for a in spec.assays.iter().flatten() {
        let ap = base.join(&a.assay);
        let atext = std::fs::read_to_string(&ap)
            .map_err(|e| format!("cannot read {}: {e}", ap.display()))?;
        let av: Value =
            serde_json::from_str(&atext).map_err(|e| format!("{}: {e}", ap.display()))?;
        if av["schema"].as_str() != Some("actinv-assay-1") {
            return Err(format!(
                "{}: assay schema must be actinv-assay-1",
                ap.display()
            ));
        }
        let resp = av["response"].as_str().unwrap_or_default().to_string();
        let t = av["time_s"].as_f64().unwrap_or(f64::NAN);
        let meas = av["value"].as_f64().unwrap_or(0.0);
        let su = av["standard_uncertainty"].as_f64().unwrap_or(0.0);
        if !t.is_finite() || meas <= 0.0 || su <= 0.0 {
            return Err(format!(
                "{}: assay needs positive value, uncertainty, time_s",
                ap.display()
            ));
        }
        let sha = actinv_data::builder::sha256_file(&ap).unwrap_or_default();
        assays
            .entry(a.cell.clone())
            .or_default()
            .push((resp, t, meas, su, sha));
    }
    let mut assimilated_cells: Vec<String> = Vec::new();

    let mut cells: Vec<Value> = Vec::new();
    let mut n_cells = 0usize;
    // (response, time) -> the binding cell (minimum margin)
    let mut binding: Map<String, Value> = Map::new();
    for line in raw.lines().filter(|l| l.trim().starts_with('{')) {
        let mut rec: Value =
            serde_json::from_str(line).map_err(|e| format!("mesh output line: {e}"))?;
        if rec["record"].as_str() != Some("cell") {
            continue;
        }
        n_cells += 1;
        let id = rec["id"].as_str().unwrap_or("?").to_string();
        // Assays fuse into the cell's band before any margin is scored.
        for (resp_name, t_s, meas, su, sha) in assays.get(&id).cloned().unwrap_or_default() {
            let Some(steps) = rec["result"]["steps"].as_array_mut() else {
                return Err(format!("cell {id}: result carries no steps"));
            };
            let Some(st) = steps
                .iter_mut()
                .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
            else {
                return Err(format!("cell {id}: assay time_s={t_s} matches no step"));
            };
            let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{resp_name}")) else {
                return Err(format!(
                    "cell {id}: assay response '{resp_name}' has no certified band"
                ));
            };
            let nominal = r["nominal"].as_f64().unwrap_or(0.0);
            let su_prior = r["combined_standard_uncertainty"]
                .as_f64()
                .or_else(|| r["mf33_standard_uncertainty"].as_f64())
                .unwrap_or(0.0);
            let mult = r["normal_multiplier"].as_f64().unwrap_or(1.959964);
            if nominal <= 0.0 || su_prior <= 0.0 {
                return Err(format!(
                    "cell {id}: '{resp_name}' prior is not positive — cannot fuse"
                ));
            }
            let f = crate::assimilate::fuse(nominal, su_prior, meas, su, mult);
            crate::assimilate::apply_fusion(r, &f, &sha);
            assimilated_cells.push(id.clone());
        }
        let steps = rec["result"]["steps"]
            .as_array()
            .ok_or_else(|| format!("cell {id}: result carries no steps"))?;
        let mut cell_rows = Vec::new();
        let mut cell_worst = f64::INFINITY;
        for st in steps {
            let t = st["t_s"].as_f64().unwrap_or(f64::NAN);
            if let Some(ts) = &spec.times_s {
                if !ts.iter().any(|&tt| close(tt, t)) {
                    continue;
                }
            }
            for l in &spec.limits {
                let band = st
                    .pointer(&format!("/uncertainty/responses/{}", l.response))
                    .and_then(|r| r["conservative_interval"].as_array());
                let Some(band) = band else {
                    return Err(format!(
                        "cell {id} step t={t}: no certified band for '{}'",
                        l.response
                    ));
                };
                let lo = band.first().and_then(Value::as_f64).unwrap_or(f64::NAN);
                let hi = band.get(1).and_then(Value::as_f64).unwrap_or(f64::NAN);
                // Signed margin: positive = the *band edge* clears.
                let margin = if l.sense == "le" {
                    (l.limit - hi) / l.limit.abs().max(1e-300)
                } else {
                    (lo - l.limit) / l.limit.abs().max(1e-300)
                };
                cell_worst = cell_worst.min(margin);
                cell_rows.push(json!({
                    "t_s": t, "limit": l.name, "band": [lo, hi],
                    "margin": margin, "clears": margin >= 0.0,
                }));
                let key = format!("{}@{}", l.name, t);
                let worse = binding
                    .get(&key)
                    .and_then(|b| b["margin"].as_f64())
                    .map(|m| margin < m)
                    .unwrap_or(true);
                if worse {
                    binding.insert(
                        key,
                        json!({"cell": id, "margin": margin, "band": [lo, hi],
                               "limit": l.limit}),
                    );
                }
            }
        }
        cells.push(json!({
            "cell": id,
            "ordinal": rec["ordinal"],
            "verdict": if cell_worst >= 0.0 { "cleared" } else { "restricted" },
            "worst_margin": cell_worst,
            "entries": cell_rows,
        }));
    }
    if n_cells == 0 {
        return Err(format!("{} carried no cell records", mesh_path.display()));
    }
    let cleared = cells.iter().filter(|c| c["verdict"] == "cleared").count();
    let out = json!({
        "schema": "actinv-twin-1",
        "mesh_output": mesh_path.display().to_string(),
        "cells": n_cells,
        "limits": spec.limits.iter().map(|l| json!({
            "name": l.name, "response": l.response,
            "limit": l.limit, "sense": l.sense,
        })).collect::<Vec<_>>(),
        "per_cell": cells,
        "facility": {
            "cleared": cleared,
            "restricted": n_cells - cleared,
            "binding_cells": binding,
            "assimilated_cells": assimilated_cells,
        },
        "note": "clearance is on the certified band edge, not the nominal — a cell clears only when its conservative interval clears the limit",
    });
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}
