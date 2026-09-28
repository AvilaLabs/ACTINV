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
    /// Named components as cell-id lists; a component clears only when
    /// every member cell clears every evaluated time.
    #[serde(default)]
    components: Option<HashMap<String, Vec<String>>>,
    /// Detector points for a point-kernel photon-flux estimate.
    #[serde(default)]
    dose_points: Option<Vec<DosePoint>>,
    /// D5 dose-rate assimilation: a measured dose (or photon flux) at a
    /// named dose point is a linear combination over cells —
    /// y = Σ_cell kᵢ·Φᵢ with kᵢ = atten·coeff/(4πrᵢ²). The constraint
    /// lands on each cell's declared response under the proportionality
    /// Φ ∝ x — emitted openly as the dose-share weight per cell.
    #[serde(default)]
    dose_assays: Option<Vec<DoseAssayRef>>,
}

#[derive(serde::Deserialize)]
struct DoseAssayRef {
    /// Name of a declared dose_point the measurement was taken at.
    dose_point: String,
    /// Path to an actinv-assay-1 scalar document — `response` declares
    /// which cell band the constraint lands on; `value` is the measured
    /// dose_gy_h (or photon_flux_cm2_s when the point has no
    /// dose_coeff).
    assay: String,
}

#[derive(serde::Deserialize)]
struct DosePoint {
    name: String,
    position_cm: [f64; 3],
    /// Optional shield slabs between every cell and this point:
    /// flux attenuates by exp(-Σ mu_cm_inv · thickness_cm).
    #[serde(default)]
    shields: Option<Vec<ShieldSlab>>,
    /// Optional photon→dose conversion applied to the attenuated flux:
    /// Gy/h per (photon·cm^-2·s^-1), one scalar per point.
    #[serde(default)]
    dose_coeff_gy_cm2_per_photon_h: Option<f64>,
}

#[derive(serde::Deserialize)]
struct ShieldSlab {
    mu_cm_inv: f64,
    thickness_cm: f64,
}

#[derive(serde::Deserialize)]
struct CellAssay {
    /// Mesh cell id the assay measures.
    cell: String,
    /// Path to an actinv-assay-1 document, resolved against the spec dir.
    assay: String,
    /// Cells this assay also informs through a declared ln-correlation:
    /// x_target += rho·(s_target/s_src)·k·(ln y - ln x_src) and
    /// s_target² → s_target²·(1 − rho²·k). ρ is user-declared.
    #[serde(default)]
    propagates: Option<Vec<Propagation>>,
}

#[derive(serde::Deserialize)]
struct Propagation {
    cell: String,
    rho: f64,
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

/// A propagated ln-shift destined for a sibling cell, computed from the
/// source cell's prior and one assay entry.
#[derive(Clone)]
struct PropagatedShift {
    /// k·(ln y − ln x_src) — the source cell's assimilated ln-shift.
    delta: f64,
    /// Source assay entry's Kalman gain.
    k_src: f64,
    /// Source prior relative σ.
    s_src: f64,
    /// Source cell id.
    src: String,
    /// User-declared ln-correlation between the two cells' responses.
    rho: f64,
    /// Response key the source entry measured — selects the band the
    /// propagated shift applies to in the target cell.
    response: String,
    /// Measurement time of the source entry.
    time_s: f64,
}

/// A cell record's (nominal, relative σ) for (response, t_s), plus the
/// Kalman gain an assay would produce — used for propagation sources.
fn prior_and_gain(
    rec: &Value,
    response: &str,
    t_s: f64,
    meas: f64,
    su: f64,
) -> Result<(f64, f64, f64), String> {
    let steps = rec["result"]["steps"]
        .as_array()
        .ok_or("result carries no steps")?;
    let st = steps
        .iter()
        .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
        .ok_or_else(|| format!("assay time_s={t_s} matches no step"))?;
    let r = st
        .pointer(&format!("/uncertainty/responses/{response}"))
        .ok_or_else(|| format!("assay response '{response}' has no certified band"))?;
    let nominal = r["nominal"].as_f64().unwrap_or(0.0);
    let s_p = r["combined_standard_uncertainty"]
        .as_f64()
        .or_else(|| r["mf33_standard_uncertainty"].as_f64())
        .unwrap_or(0.0);
    if nominal <= 0.0 || s_p <= 0.0 {
        return Err(format!(
            "'{response}' prior is not positive — cannot propagate"
        ));
    }
    let s_p_rel = s_p / nominal;
    let s_m_rel = su / meas;
    let k = s_p_rel * s_p_rel / (s_p_rel * s_p_rel + s_m_rel * s_m_rel);
    Ok((nominal, s_p_rel, k))
}

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
    /// Mixture assays per cell: (time_s, terms, measured value, su, sha).
    type LoadedMixture = (f64, Vec<(String, f64)>, f64, f64, String);
    let mut mixture_assays: HashMap<String, Vec<LoadedMixture>> = HashMap::new();
    for a in spec.assays.iter().flatten() {
        let ap = base.join(&a.assay);
        let atext = std::fs::read_to_string(&ap)
            .map_err(|e| format!("cannot read {}: {e}", ap.display()))?;
        let av: Value =
            serde_json::from_str(&atext).map_err(|e| format!("{}: {e}", ap.display()))?;
        let (t_s, body) = crate::assimilate::parse_assay(&av, &ap.display().to_string())?;
        let sha = actinv_data::builder::sha256_file(&ap).unwrap_or_default();
        match body {
            crate::assimilate::AssayBody::Entries(entries) => {
                for e in entries {
                    assays.entry(a.cell.clone()).or_default().push((
                        e.response,
                        t_s,
                        e.value,
                        e.standard_uncertainty,
                        sha.clone(),
                    ));
                }
            }
            crate::assimilate::AssayBody::Mixture {
                terms,
                value,
                standard_uncertainty,
            } => {
                if a.propagates.is_some() {
                    return Err(format!(
                        "{}: mixture assays cannot propagate — the induced cross-term covariance has no single ln-shift to send",
                        ap.display()
                    ));
                }
                mixture_assays.entry(a.cell.clone()).or_default().push((
                    t_s,
                    terms,
                    value,
                    standard_uncertainty,
                    sha,
                ));
            }
        }
    }
    let mut assimilated_cells: Vec<String> = Vec::new();
    // Propagation sources: target cell id -> (src ln-shift δ, src Kalman
    // gain, src prior relative σ, src cell id, declared rho). Computed in
    // a pre-pass so ordering of cell records in the stream doesn't matter.
    let mut propagations: HashMap<String, Vec<PropagatedShift>> = HashMap::new();
    if spec.assays.iter().flatten().any(|a| a.propagates.is_some()) {
        for a in spec.assays.iter().flatten() {
            let Some(propagates) = &a.propagates else {
                continue;
            };
            let loaded = assays.get(&a.cell).cloned().unwrap_or_default();
            // Every entry of a multi-nuclide assay propagates on its own
            // response — the selector travels inside the shift.
            for (resp, t_s, meas, su, _sha) in &loaded {
                for line in raw.lines().filter(|l| l.trim().starts_with('{')) {
                    let rec: Value =
                        serde_json::from_str(line).map_err(|e| format!("mesh output line: {e}"))?;
                    if rec["record"].as_str() != Some("cell")
                        || rec["id"].as_str() != Some(a.cell.as_str())
                    {
                        continue;
                    }
                    let (x_a, s_a, k) = prior_and_gain(&rec, resp, *t_s, *meas, *su)
                        .map_err(|e| format!("cell {}: {e}", a.cell))?;
                    let delta = k * (meas.ln() - x_a.ln());
                    for p in propagates {
                        if !p.rho.is_finite() || p.rho.abs() > 1.0 {
                            return Err(format!(
                                "propagation rho={} for '{}' must satisfy |rho| <= 1",
                                p.rho, p.cell
                            ));
                        }
                        propagations
                            .entry(p.cell.clone())
                            .or_default()
                            .push(PropagatedShift {
                                delta,
                                k_src: k,
                                s_src: s_a,
                                src: a.cell.clone(),
                                rho: p.rho,
                                response: resp.clone(),
                                time_s: *t_s,
                            });
                    }
                }
            }
        }
    }

    // Dose-rate assimilation pre-pass: for every dose assay, pull each
    // cell's photon emission and response prior at the assay time,
    // compute kernel coefficients, and fuse the mixture — posteriors are
    // applied per-cell in the main pass BEFORE scalar cell assays, so a
    // later cell assay sees the dose posterior as its prior (sequential
    // Bayes on the record's own bands).
    /// cell id -> (response, posterior, posterior ln-σ, dose-share,
    ///   band multiplier, assay sha, time_s)
    type DoseApplied = (String, f64, f64, f64, f64, String, f64);
    let mut dose_applied: HashMap<String, Vec<DoseApplied>> = HashMap::new();
    let mut dose_assay_records: Vec<Value> = Vec::new();
    for da in spec.dose_assays.iter().flatten() {
        let ap = base.join(&da.assay);
        let atext = std::fs::read_to_string(&ap)
            .map_err(|e| format!("cannot read {}: {e}", ap.display()))?;
        let av: Value =
            serde_json::from_str(&atext).map_err(|e| format!("{}: {e}", ap.display()))?;
        let (t_s, body) = crate::assimilate::parse_assay(&av, &ap.display().to_string())?;
        let crate::assimilate::AssayBody::Entries(entries) = body else {
            return Err(format!(
                "{}: dose assays take a scalar actinv-assay-1 document",
                ap.display()
            ));
        };
        if entries.len() != 1 {
            return Err(format!(
                "{}: dose assays carry exactly one response — the cell band the dose constraint lands on",
                ap.display()
            ));
        }
        let e = &entries[0];
        let Some(point) = spec
            .dose_points
            .iter()
            .flatten()
            .find(|p| p.name == da.dose_point)
        else {
            return Err(format!(
                "dose assay names unknown dose_point '{}'",
                da.dose_point
            ));
        };
        let od: f64 = point
            .shields
            .iter()
            .flatten()
            .map(|s| s.mu_cm_inv * s.thickness_cm)
            .sum();
        let atten = (-od).exp();
        let dcoef = point.dose_coeff_gy_cm2_per_photon_h.unwrap_or(1.0);
        let sha = actinv_data::builder::sha256_file(&ap).unwrap_or_default();

        // Collect per-cell terms: c_i = kernel_i·Φ_i(t)/nom_i(t) so the
        // forward value Σcᵢ·nomᵢ equals the computed dose exactly.
        let mut mterms: Vec<(String, crate::assimilate::MixtureTerm)> = Vec::new();
        for line in raw.lines().filter(|l| l.trim().starts_with('{')) {
            let rec: Value =
                serde_json::from_str(line).map_err(|e| format!("mesh output line: {e}"))?;
            if rec["record"].as_str() != Some("cell") {
                continue;
            }
            let id = rec["id"].as_str().unwrap_or("?").to_string();
            let centroid: [f64; 3] = (0..3)
                .map(|ax| {
                    let b = &rec["bounds_cm"][ax];
                    (b[0].as_f64().unwrap_or(0.0) + b[1].as_f64().unwrap_or(0.0)) / 2.0
                })
                .collect::<Vec<_>>()
                .try_into()
                .unwrap_or([0.0, 0.0, 0.0]);
            let v_eff = rec["volume_cm3"].as_f64().unwrap_or(1.0).max(1e-30);
            let dist = ((centroid[0] - point.position_cm[0]).powi(2)
                + (centroid[1] - point.position_cm[1]).powi(2)
                + (centroid[2] - point.position_cm[2]).powi(2))
            .sqrt()
            .max(0.5 * v_eff.cbrt());
            let kernel = atten * dcoef / (4.0 * std::f64::consts::PI * dist * dist);
            let Some(st) = rec["result"]["steps"].as_array().and_then(|ss| {
                ss.iter()
                    .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
            }) else {
                continue;
            };
            let photons_s: f64 = st
                .pointer("/photon_source/groups")
                .and_then(|g| g.as_array())
                .map(|gs| {
                    gs.iter()
                        .map(|g| g["photons_s"].as_f64().unwrap_or(0.0))
                        .sum()
                })
                .unwrap_or(0.0);
            if photons_s <= 0.0 {
                continue;
            }
            let Some(r) = st.pointer(&format!("/uncertainty/responses/{}", e.response)) else {
                return Err(format!(
                    "cell {id}: dose assay response '{}' has no certified band",
                    e.response
                ));
            };
            let nominal = r["nominal"].as_f64().unwrap_or(0.0);
            let su_prior = r["combined_standard_uncertainty"]
                .as_f64()
                .or_else(|| r["mf33_standard_uncertainty"].as_f64())
                .unwrap_or(0.0);
            if nominal <= 0.0 || su_prior <= 0.0 {
                return Err(format!(
                    "cell {id}: dose assay response '{}' prior is not positive",
                    e.response
                ));
            }
            mterms.push((
                id,
                crate::assimilate::MixtureTerm {
                    response: e.response.clone(),
                    coefficient: kernel * photons_s / nominal,
                    nominal,
                    s_ln: (su_prior / nominal).max(1e-300),
                    normal_multiplier: r["normal_multiplier"].as_f64().unwrap_or(1.959964),
                },
            ));
        }
        if mterms.is_empty() {
            return Err(format!(
                "dose assay '{}': no emitting cell at t_s={t_s}",
                da.dose_point
            ));
        }
        let f = crate::assimilate::fuse_mixture(
            &mterms.iter().map(|(_, t)| t.clone()).collect::<Vec<_>>(),
            e.value,
            e.standard_uncertainty,
        );
        for (i, (id, t)) in mterms.iter().enumerate() {
            dose_applied.entry(id.clone()).or_default().push((
                t.response.clone(),
                f.posteriors[i],
                f.posterior_s_ln[i],
                f.weights[i],
                t.normal_multiplier,
                sha.clone(),
                t_s,
            ));
        }
        let s_prior = {
            let v: f64 = mterms
                .iter()
                .zip(&f.weights)
                .map(|((_, t), h)| h * h * t.s_ln * t.s_ln)
                .sum();
            v.sqrt()
        };
        let mult = mterms
            .iter()
            .map(|(_, t)| t.normal_multiplier)
            .fold(1.0_f64, f64::max);
        let (lo, hi) = (
            f.forward * (-mult * s_prior).exp(),
            f.forward * (mult * s_prior).exp(),
        );
        let verdict = if e.value >= lo && e.value <= hi {
            "consistent"
        } else {
            let width = (hi / lo.max(1e-300)).ln().abs();
            let dist = if e.value > hi {
                (e.value / hi).ln()
            } else {
                (lo / e.value).ln()
            };
            if dist <= 0.5 * width.max(1e-300) {
                "marginal"
            } else {
                "conflict"
            }
        };
        dose_assay_records.push(json!({
            "dose_point": da.dose_point,
            "response": e.response,
            "time_s": t_s,
            "units": if point.dose_coeff_gy_cm2_per_photon_h.is_some() {
                "dose_gy_h" } else { "photon_flux_cm2_s" },
            "measured": e.value,
            "measured_su": e.standard_uncertainty,
            "forward": f.forward,
            "forward_band": [lo, hi],
            "forward_sigma_ln": s_prior,
            "innovation_ln": f.innovation,
            "verdict": verdict,
            "cells": mterms.iter().enumerate().map(|(i,(id,_))| json!({
                "cell": id, "dose_share": f.weights[i],
                "kalman_gain": f.gains[i], "posterior": f.posteriors[i],
            })).collect::<Vec<_>>(),
            "assay_sha256": sha,
        }));
    }

    let mut cells: Vec<Value> = Vec::new();
    let mut cell_verdicts: HashMap<String, String> = HashMap::new();
    let mut recommendations: Vec<Value> = Vec::new();
    let mut n_cells = 0usize;
    // (response, time) -> the binding cell (minimum margin)
    let mut binding: Map<String, Value> = Map::new();
    // Per detector point: accumulated attenuated photon flux per step time.
    struct PointState {
        position: [f64; 3],
        attenuation: f64,
        dose_coeff: Option<f64>,
        flux: HashMap<u64, f64>,
    }
    let mut point_rows: Vec<PointState> = spec
        .dose_points
        .iter()
        .flatten()
        .map(|p| {
            let od: f64 = p
                .shields
                .iter()
                .flatten()
                .map(|s| s.mu_cm_inv * s.thickness_cm)
                .sum();
            PointState {
                position: p.position_cm,
                attenuation: (-od).exp(),
                dose_coeff: p.dose_coeff_gy_cm2_per_photon_h,
                flux: HashMap::new(),
            }
        })
        .collect();
    for line in raw.lines().filter(|l| l.trim().starts_with('{')) {
        let mut rec: Value =
            serde_json::from_str(line).map_err(|e| format!("mesh output line: {e}"))?;
        if rec["record"].as_str() != Some("cell") {
            continue;
        }
        n_cells += 1;
        let id = rec["id"].as_str().unwrap_or("?").to_string();
        // Dose assays land first: the pre-pass already fused the linear
        // combination, so these write the joint posterior in place — a
        // later cell assay then sees it as its prior.
        for (resp_name, post, s_ln, weight, mult, sha, t_s) in
            dose_applied.get(&id).cloned().unwrap_or_default()
        {
            let Some(steps) = rec["result"]["steps"].as_array_mut() else {
                return Err(format!("cell {id}: result carries no steps"));
            };
            let Some(st) = steps
                .iter_mut()
                .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
            else {
                continue;
            };
            if let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{resp_name}")) {
                crate::assimilate::apply_mixture_fusion(r, post, s_ln, mult, weight, &sha);
                assimilated_cells.push(id.clone());
            }
        }
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
        // Mixture assays: one linear-combination measurement updates
        // several of this cell's response bands jointly (Kalman H-row).
        for (t_s, terms, meas, su, sha) in mixture_assays.get(&id).cloned().unwrap_or_default() {
            let mut mterms = Vec::with_capacity(terms.len());
            for (resp_name, coeff) in &terms {
                let Some(steps) = rec["result"]["steps"].as_array() else {
                    return Err(format!("cell {id}: result carries no steps"));
                };
                let Some(st) = steps
                    .iter()
                    .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
                else {
                    return Err(format!("cell {id}: assay time_s={t_s} matches no step"));
                };
                let Some(r) = st.pointer(&format!("/uncertainty/responses/{resp_name}")) else {
                    return Err(format!(
                        "cell {id}: mixture term '{resp_name}' has no certified band"
                    ));
                };
                let nominal = r["nominal"].as_f64().unwrap_or(0.0);
                let su_prior = r["combined_standard_uncertainty"]
                    .as_f64()
                    .or_else(|| r["mf33_standard_uncertainty"].as_f64())
                    .unwrap_or(0.0);
                if nominal <= 0.0 || su_prior <= 0.0 {
                    return Err(format!(
                        "cell {id}: mixture term '{resp_name}' prior is not positive"
                    ));
                }
                mterms.push(crate::assimilate::MixtureTerm {
                    response: resp_name.clone(),
                    coefficient: *coeff,
                    nominal,
                    s_ln: (su_prior / nominal).max(1e-300),
                    normal_multiplier: r["normal_multiplier"].as_f64().unwrap_or(1.959964),
                });
            }
            let f = crate::assimilate::fuse_mixture(&mterms, meas, su);
            let Some(steps) = rec["result"]["steps"].as_array_mut() else {
                return Err(format!("cell {id}: result carries no steps"));
            };
            let Some(st) = steps
                .iter_mut()
                .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
            else {
                continue;
            };
            for (i, t) in mterms.iter().enumerate() {
                if let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{}", t.response)) {
                    crate::assimilate::apply_mixture_fusion(
                        r,
                        f.posteriors[i],
                        f.posterior_s_ln[i],
                        t.normal_multiplier,
                        f.weights[i],
                        &sha,
                    );
                }
            }
            assimilated_cells.push(id.clone());
        }
        // Declared-correlation propagation: an assay on a sibling cell
        // shifts this cell's band by rho·(s_B/s_A)·k·(ln y - ln x_A) and
        // narrows its σ by sqrt(1 - rho²·k). Response and time come from
        // the source assay.
        for shift in propagations.get(&id).cloned().unwrap_or_default() {
            let PropagatedShift {
                delta,
                k_src,
                s_src,
                src,
                rho,
                response: resp_name,
                time_s: t_s,
            } = shift;
            let Some(steps) = rec["result"]["steps"].as_array_mut() else {
                return Err(format!("cell {id}: result carries no steps"));
            };
            let Some(st) = steps
                .iter_mut()
                .find(|s| s["t_s"].as_f64().map(|v| close(v, t_s)).unwrap_or(false))
            else {
                return Err(format!(
                    "cell {id}: propagation time_s={t_s} matches no step"
                ));
            };
            let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{resp_name}")) else {
                return Err(format!(
                    "cell {id}: propagated response '{resp_name}' has no certified band"
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
                    "cell {id}: '{resp_name}' prior is not positive — cannot propagate"
                ));
            }
            let s_b = su_prior / nominal;
            let x_post = nominal.ln() + rho * (s_b / s_src) * delta;
            let s_post = s_b * (1.0 - rho * rho * k_src).max(0.0).sqrt();
            let post = x_post.exp();
            let half = (mult * s_post).exp();
            let f = crate::assimilate::Fusion {
                posterior: post,
                posterior_rel_su: s_post,
                band: [post / half, post * half],
                kalman_gain: rho * rho * k_src,
            };
            crate::assimilate::apply_fusion(r, &f, "propagated");
            r["assimilation"]["propagated_from"] = json!({
                "cell": src, "declared_rho": rho,
                "note": "ln-shift propagated through the declared response correlation — rho is asserted, not inferred",
            });
            assimilated_cells.push(id.clone());
        }
        let steps = rec["result"]["steps"]
            .as_array()
            .ok_or_else(|| format!("cell {id}: result carries no steps"))?;
        // Cell centroid + attenuated distance for the dose points.
        let centroid: [f64; 3] = (0..3)
            .map(|ax| {
                let b = &rec["bounds_cm"][ax];
                (b[0].as_f64().unwrap_or(0.0) + b[1].as_f64().unwrap_or(0.0)) / 2.0
            })
            .collect::<Vec<_>>()
            .try_into()
            .unwrap_or([0.0, 0.0, 0.0]);
        let v_eff = rec["volume_cm3"].as_f64().unwrap_or(1.0).max(1e-30);
        let mut cell_rows = Vec::new();
        let mut cell_worst = f64::INFINITY;
        for st in steps {
            let t = st["t_s"].as_f64().unwrap_or(f64::NAN);
            if let Some(ts) = &spec.times_s {
                if !ts.iter().any(|&tt| close(tt, t)) {
                    continue;
                }
            }
            // Point-kernel: Σ_groups photons_s · atten / (4π r²).
            let photons_s: f64 = st
                .pointer("/photon_source/groups")
                .and_then(|g| g.as_array())
                .map(|gs| {
                    gs.iter()
                        .map(|g| g["photons_s"].as_f64().unwrap_or(0.0))
                        .sum()
                })
                .unwrap_or(0.0);
            if photons_s > 0.0 {
                for pt in point_rows.iter_mut() {
                    let dist = ((centroid[0] - pt.position[0]).powi(2)
                        + (centroid[1] - pt.position[1]).powi(2)
                        + (centroid[2] - pt.position[2]).powi(2))
                    .sqrt()
                    .max(0.5 * v_eff.cbrt());
                    *pt.flux.entry(t.to_bits()).or_insert(0.0) +=
                        photons_s * pt.attenuation / (4.0 * std::f64::consts::PI * dist * dist);
                }
            }
            for l in &spec.limits {
                let resp = st.pointer(&format!("/uncertainty/responses/{}", l.response));
                let band = resp.and_then(|r| r["conservative_interval"].as_array());
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
                // Where a restricted cell would flip: measurement precision
                // required to pull the posterior band edge under the limit,
                // assuming the assay lands at the prior nominal.
                if margin < 0.0 {
                    let nominal = resp.and_then(|r| r["nominal"].as_f64()).unwrap_or(0.0);
                    let su_p = resp
                        .and_then(|r| r["combined_standard_uncertainty"].as_f64())
                        .or_else(|| resp.and_then(|r| r["mf33_standard_uncertainty"].as_f64()))
                        .unwrap_or(0.0);
                    let mult = resp
                        .and_then(|r| r["normal_multiplier"].as_f64())
                        .unwrap_or(1.959964);
                    let s_p = su_p / nominal.max(1e-300);
                    // Posterior edge must reach the limit; solve for the
                    // posterior relσ then the assay precision that yields it
                    // (s_post² = s_p² s_m² / (s_p² + s_m²)).
                    let mut rec_v = json!({
                        "cell": id, "response": l.response, "limit": l.name,
                        "t_s": t, "margin": margin,
                        "note": "assumes the assay lands at the prior nominal",
                    });
                    if l.sense == "le" && nominal < l.limit && nominal > 0.0 {
                        let s_post_need = (l.limit / nominal).ln() / mult;
                        if s_post_need > 0.0 && s_post_need < s_p {
                            let s_m_need =
                                s_post_need * s_p / (s_p * s_p - s_post_need * s_post_need).sqrt();
                            rec_v["required_measurement_rel_su"] = json!(s_m_need);
                        }
                    }
                    // A low-side assay clears only if its value lands below
                    // this bound (for le): posterior edge ≈ meas·e^{m·s_post}
                    // → in the sharp-measurement limit, meas < limit.
                    rec_v["max_assay_value_for_clearance"] =
                        json!(if l.sense == "le" { l.limit } else { f64::NAN });
                    recommendations.push(rec_v);
                }
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
        let verdict = if cell_worst >= 0.0 {
            "cleared"
        } else {
            "restricted"
        };
        cell_verdicts.insert(id.clone(), verdict.to_string());
        cells.push(json!({
            "cell": id,
            "ordinal": rec["ordinal"],
            "verdict": verdict,
            "worst_margin": cell_worst,
            "entries": cell_rows,
        }));
    }
    if n_cells == 0 {
        return Err(format!("{} carried no cell records", mesh_path.display()));
    }
    // Component rollup: cleared only when every member cell cleared.
    let components: Map<String, Value> = spec
        .components
        .iter()
        .flatten()
        .map(|(name, ids)| {
            let missing: Vec<&String> = ids
                .iter()
                .filter(|i| !cell_verdicts.contains_key(*i))
                .collect();
            let verdict = if !missing.is_empty() {
                "unknown"
            } else if ids.iter().all(|i| cell_verdicts[i.as_str()] == "cleared") {
                "cleared"
            } else {
                "restricted"
            };
            (
                name.clone(),
                json!({
                    "cells": ids, "verdict": verdict,
                    "missing_cells": missing.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
                }),
            )
        })
        .collect();
    // Point-kernel photon flux per detector point — a screening estimate,
    // not a certified band (per-group photon strengths carry no band yet).
    let dose_points: Vec<Value> = spec
        .dose_points
        .iter()
        .flatten()
        .zip(point_rows.iter())
        .map(|(p, st)| {
            let mut times: Vec<Value> = st
                .flux
                .iter()
                .map(|(bits, flux)| {
                    let t = f64::from_bits(*bits);
                    let mut row = json!({
                        "t_s": t,
                        "photon_flux_cm2_s": flux,
                    });
                    if let Some(c) = st.dose_coeff {
                        row["dose_gy_h"] = json!(flux * c);
                    }
                    row
                })
                .collect();
            times.sort_by(|a, b| a["t_s"].as_f64().partial_cmp(&b["t_s"].as_f64()).unwrap());
            json!({
                "name": p.name, "position_cm": p.position_cm,
                "attenuation": st.attenuation,
                "steps": times,
            })
        })
        .collect();
    // Rank: cells an assay can actually clear come first (ordered by the
    // least-demanding precision), then cells needing a low-side assay.
    recommendations.sort_by(|a, b| {
        let key = |r: &Value| match r["required_measurement_rel_su"].as_f64() {
            Some(s) => (0, -s),
            None => (1, 0.0),
        };
        key(a).partial_cmp(&key(b)).unwrap()
    });
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
        "components": components,
        "dose_points": dose_points,
        "facility": {
            "cleared": cleared,
            "restricted": n_cells - cleared,
            "binding_cells": binding,
            "assimilated_cells": assimilated_cells,
            "assay_recommendations": recommendations,
            "dose_assimilations": dose_assay_records,
        },
        "note": "clearance is on the certified band edge, not the nominal — a cell clears only when its conservative interval clears the limit. dose_points flux is a point-kernel screening estimate (no scatter/buildup transport), not a certified band",
    });
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}
