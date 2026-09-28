//! D5 leg 1 — continuous assimilation: fold an assay back into a
//! certified response band.
//!
//! `actinv assimilate --result RUN.json --assay ASSAY.json [--out OUT.json]`
//!
//! The update is a Gaussian fusion on the log-magnitude (the band's own
//! scale): the prior is the solver's `combined_standard_uncertainty`
//! relative to the nominal, the measurement is log-normal with the
//! assay's declared relative standard uncertainty. The Kalman gain
//! `K = s_p²/(s_p²+s_m²)` is emitted openly — a precise assay pulls the
//! posterior hard toward the measurement; a sloppy one barely moves it.
//!
//! Honesty: the assay asserts truth about a quantity the prior only
//! bounds. When the measurement lands outside the prior's declared
//! interval the update still runs but the record is stamped `conflict`
//! — the instrument of record says the model or the assay is wrong,
//! and shrinking toward it without flagging would be dishonest.

use serde_json::{json, Value};

const ASSAY_SCHEMA: &str = "actinv-assay-1";

/// One measured line inside an assay document: a response key, the
/// measured value, and its absolute standard uncertainty.
#[derive(Clone, Debug)]
pub struct AssayEntry {
    pub response: String,
    pub value: f64,
    pub standard_uncertainty: f64,
}

/// Parse an `actinv-assay-1` document into its measured entries plus the
/// shared measurement time. Two shapes are accepted:
///
/// - scalar (back-compatible): `response`, `value`,
///   `standard_uncertainty` at the top level;
/// - multi-nuclide: `entries: [{response, value, standard_uncertainty}]`
///   — a gamma-spectroscopy-style count that measures several nuclide
///   activities in one shot. Each entry fuses independently at the
///   shared `time_s`; provenance carries the single document sha.
///
/// Declaring both shapes is an error — the document is ambiguous.
pub fn parse_assay(av: &Value, source: &str) -> Result<(f64, Vec<AssayEntry>), String> {
    if av["schema"].as_str() != Some(ASSAY_SCHEMA) {
        return Err(format!("{source}: assay schema must be {ASSAY_SCHEMA}"));
    }
    let time_s = av["time_s"]
        .as_f64()
        .ok_or_else(|| format!("{source}: assay requires numeric `time_s`"))?;
    if !time_s.is_finite() {
        return Err(format!("{source}: assay time_s must be finite"));
    }
    let scalar =
        av["response"].is_string() || av["value"].is_f64() || av["standard_uncertainty"].is_f64();
    let entries_v = av["entries"].as_array();
    if scalar && entries_v.is_some() {
        return Err(format!(
            "{source}: assay declares both scalar fields and `entries` — pick one shape"
        ));
    }
    let mut out = Vec::new();
    if let Some(entries) = entries_v {
        if entries.is_empty() {
            return Err(format!("{source}: assay `entries` is empty"));
        }
        for (i, e) in entries.iter().enumerate() {
            let resp = e["response"]
                .as_str()
                .ok_or_else(|| format!("{source}: entries[{i}] requires a `response` key"))?;
            let meas = e["value"]
                .as_f64()
                .ok_or_else(|| format!("{source}: entries[{i}] requires numeric `value`"))?;
            let su = e["standard_uncertainty"].as_f64().ok_or_else(|| {
                format!("{source}: entries[{i}] requires numeric `standard_uncertainty`")
            })?;
            if meas <= 0.0 {
                return Err(format!(
                    "{source}: entries[{i}] value must be positive for a log-scale update"
                ));
            }
            if !su.is_finite() || su <= 0.0 {
                return Err(format!(
                    "{source}: entries[{i}] standard_uncertainty must be positive"
                ));
            }
            out.push(AssayEntry {
                response: resp.to_string(),
                value: meas,
                standard_uncertainty: su,
            });
        }
    } else {
        let resp = av["response"].as_str().ok_or_else(|| {
            format!("{source}: assay requires a `response` key or an `entries` array")
        })?;
        let meas = av["value"]
            .as_f64()
            .ok_or_else(|| format!("{source}: assay requires numeric `value`"))?;
        let su = av["standard_uncertainty"]
            .as_f64()
            .ok_or_else(|| format!("{source}: assay requires numeric `standard_uncertainty`"))?;
        if meas <= 0.0 {
            return Err(format!(
                "{source}: assay value must be positive for a log-scale update"
            ));
        }
        if !su.is_finite() || su <= 0.0 {
            return Err(format!(
                "{source}: assay standard_uncertainty must be positive"
            ));
        }
        out.push(AssayEntry {
            response: resp.to_string(),
            value: meas,
            standard_uncertainty: su,
        });
    }
    Ok((time_s, out))
}

fn is_close(a: f64, b: f64) -> bool {
    (a - b).abs() <= a.abs().max(b.abs()).max(1e-300) * 1e-9
}

fn find_step(result: &Value, time_s: f64) -> Option<&Value> {
    result["steps"].as_array()?.iter().find(|st| {
        st["t_s"]
            .as_f64()
            .map(|t| is_close(t, time_s))
            .unwrap_or(false)
    })
}

/// The shared log-Gaussian fusion: prior (nominal, relative σ) meets a
/// measurement (value, relative σ) in ln space. Returned values feed both
/// `assimilate`'s record and `twin`'s in-place band update.
pub struct Fusion {
    pub posterior: f64,
    pub posterior_rel_su: f64,
    pub band: [f64; 2],
    pub kalman_gain: f64,
}

pub fn fuse(nominal: f64, prior_su: f64, meas: f64, meas_su: f64, multiplier: f64) -> Fusion {
    let s_p = (prior_su / nominal).max(1e-300);
    let s_m = (meas_su / meas).max(1e-300);
    let k = s_p * s_p / (s_p * s_p + s_m * s_m);
    let x_post = nominal.ln() + k * (meas.ln() - nominal.ln());
    let s_post = (s_p * s_p * (1.0 - k)).sqrt();
    let post = x_post.exp();
    let half = (multiplier * s_post).exp();
    Fusion {
        posterior: post,
        posterior_rel_su: s_post,
        band: [post / half, post * half],
        kalman_gain: k,
    }
}

/// Apply a fusion to a response uncertainty object in place: nominal,
/// standard uncertainties, and both intervals move to the posterior, and
/// the fusion is stamped under `assimilation` for provenance.
pub fn apply_fusion(resp: &mut Value, f: &Fusion, assay_sha: &str) {
    let obj = match resp.as_object_mut() {
        Some(o) => o,
        None => return,
    };
    obj.insert("nominal".into(), json!(f.posterior));
    obj.insert(
        "combined_standard_uncertainty".into(),
        json!(f.posterior * f.posterior_rel_su),
    );
    obj.insert(
        "relative_standard_uncertainty".into(),
        json!(f.posterior_rel_su),
    );
    obj.insert("normal_interval".into(), json!(f.band));
    obj.insert("conservative_interval".into(), json!(f.band));
    obj.insert(
        "assimilation".into(),
        json!({
            "kalman_gain": f.kalman_gain,
            "posterior_relative_standard_uncertainty": f.posterior_rel_su,
            "assay_sha256": assay_sha,
            "note": "posterior after log-Gaussian fusion with the declared assay; intervals are the posterior band at the response's own confidence multiplier",
        }),
    );
}

/// One fused entry's record: prior, assay, update math, verdict.
struct EntryUpdate {
    entry: AssayEntry,
    fusion: Fusion,
    verdict: &'static str,
    prior_nominal: f64,
    prior_su: f64,
    prior_band: Option<(f64, f64)>,
    confidence_level: Value,
    normal_multiplier: f64,
}

fn fuse_entry(result: &Value, time_s: f64, e: &AssayEntry) -> Result<EntryUpdate, String> {
    let step =
        find_step(result, time_s).ok_or_else(|| format!("result has no step at t_s={time_s}"))?;
    let resp = step
        .pointer(&format!("/uncertainty/responses/{}", e.response))
        .ok_or_else(|| {
            format!(
                "result step at t_s={time_s} carries no uncertainty for '{}'",
                e.response
            )
        })?;
    let nominal = resp["nominal"]
        .as_f64()
        .ok_or("response carries no nominal")?;
    if nominal <= 0.0 {
        return Err(format!(
            "response '{}' nominal is {nominal} — log-scale update needs a positive prior",
            e.response
        ));
    }
    let prior_su = resp["combined_standard_uncertainty"]
        .as_f64()
        .or_else(|| resp["mf33_standard_uncertainty"].as_f64())
        .ok_or("response carries no standard uncertainty")?;
    let multiplier = resp["normal_multiplier"].as_f64().unwrap_or(1.959964);

    let f = fuse(
        nominal,
        prior_su,
        e.value,
        e.standard_uncertainty,
        multiplier,
    );
    let prior_band = resp["conservative_interval"]
        .as_array()
        .and_then(|a| Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?)));
    let meas = e.value;
    let verdict = match prior_band {
        Some((lo, hi)) if meas >= lo && meas <= hi => "consistent",
        Some((lo, hi)) => {
            let width = (hi / lo.max(1e-300)).ln().abs();
            let dist = if meas > hi {
                (meas / hi).ln()
            } else {
                (lo / meas).ln()
            };
            if dist <= 0.5 * width.max(1e-300) {
                "marginal"
            } else {
                "conflict"
            }
        }
        None => "assimilated",
    };
    Ok(EntryUpdate {
        entry: e.clone(),
        fusion: f,
        verdict,
        prior_nominal: nominal,
        prior_su,
        prior_band,
        confidence_level: resp["confidence_level"].clone(),
        normal_multiplier: multiplier,
    })
}

fn entry_json(u: &EntryUpdate) -> Value {
    let s_p = (u.prior_su / u.prior_nominal).max(1e-300);
    let meas = u.entry.value;
    let s_m = (u.entry.standard_uncertainty / meas).max(1e-300);
    let f = &u.fusion;
    json!({
        "response": u.entry.response,
        "prior": {
            "nominal": u.prior_nominal,
            "combined_standard_uncertainty": u.prior_su,
            "relative_standard_uncertainty": s_p,
            "band": u.prior_band.map(|(l,h)| vec![l, h]),
        },
        "assay": {
            "value": meas,
            "standard_uncertainty": u.entry.standard_uncertainty,
            "relative_standard_uncertainty": s_m,
        },
        "update": {
            "kalman_gain": f.kalman_gain,
            "space": "ln(response)",
            "posterior": f.posterior,
            "posterior_relative_standard_uncertainty": f.posterior_rel_su,
            "posterior_band": f.band,
            "confidence_level": u.confidence_level,
            "normal_multiplier": u.normal_multiplier,
            "shrunk": f.posterior_rel_su < s_p,
        },
        "verdict": u.verdict,
    })
}

/// Worst-case aggregate across entry verdicts — a single conflicting line
/// marks the whole assay conflicted rather than letting it hide among
/// consistent siblings.
fn overall_verdict(updates: &[EntryUpdate]) -> &'static str {
    if updates.iter().any(|u| u.verdict == "conflict") {
        "conflict"
    } else if updates.iter().any(|u| u.verdict == "marginal") {
        "marginal"
    } else if updates.iter().any(|u| u.verdict == "consistent") {
        "consistent"
    } else {
        "assimilated"
    }
}

pub fn run(
    result_path: &str,
    assay_path: &str,
    out_path: Option<&str>,
    emit_result: Option<&str>,
) -> Result<Value, String> {
    let result_text = std::fs::read_to_string(result_path)
        .map_err(|e| format!("cannot read {result_path}: {e}"))?;
    let result: Value =
        serde_json::from_str(&result_text).map_err(|e| format!("{result_path}: {e}"))?;
    let assay_text = std::fs::read_to_string(assay_path)
        .map_err(|e| format!("cannot read {assay_path}: {e}"))?;
    let assay: Value =
        serde_json::from_str(&assay_text).map_err(|e| format!("{assay_path}: {e}"))?;
    let (time_s, entries) = parse_assay(&assay, assay_path)?;

    let updates: Vec<EntryUpdate> = entries
        .iter()
        .map(|e| fuse_entry(&result, time_s, e))
        .collect::<Result<_, _>>()?;

    let provenance = json!({
        "result_sha256": actinv_data::builder::sha256_file(std::path::Path::new(result_path))
            .unwrap_or_default(),
        "assay_sha256": actinv_data::builder::sha256_file(std::path::Path::new(assay_path))
            .unwrap_or_default(),
    });
    let note = "Gaussian fusion on ln-scale between the solver's propagated band and the assay's declared lognormal uncertainty; `conflict` means the measurement lies outside the prior's declared interval — a real discrepancy between model and assay, not a reason to silently widen";

    let mut out = json!({
        "schema": "actinv-assimilated-1",
        "time_s": time_s,
        "entries": updates.len(),
        "updates": updates.iter().map(entry_json).collect::<Vec<_>>(),
        "verdict": overall_verdict(&updates),
        "provenance": provenance,
        "note": note,
    });
    // Back-compat: a single-entry assay also carries the legacy scalar
    // fields (response/prior/assay/update) so existing consumers of
    // actinv-assimilated-1 keep working unchanged.
    if updates.len() == 1 {
        let u = &updates[0];
        let ej = entry_json(u);
        let o = out.as_object_mut().expect("object");
        o.insert("response".into(), json!(u.entry.response));
        o.insert("prior".into(), ej["prior"].clone());
        o.insert("assay".into(), ej["assay"].clone());
        o.insert("update".into(), ej["update"].clone());
    }
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    if let Some(p) = emit_result {
        // Re-issue the run result with each fused response band replaced
        // by its posterior — the document feeds `decide`/`clearance`/
        // another `assimilate` unchanged, carrying `assimilation`
        // provenance per response.
        let mut updated = result.clone();
        let assay_sha =
            actinv_data::builder::sha256_file(std::path::Path::new(assay_path)).unwrap_or_default();
        for u in &updates {
            if let Some(st) = updated["steps"].as_array_mut().and_then(|steps| {
                steps.iter_mut().find(|s| {
                    s["t_s"]
                        .as_f64()
                        .map(|t| is_close(t, time_s))
                        .unwrap_or(false)
                })
            }) {
                if let Some(r) =
                    st.pointer_mut(&format!("/uncertainty/responses/{}", u.entry.response))
                {
                    apply_fusion(r, &u.fusion, &assay_sha);
                }
            }
        }
        updated
            .as_object_mut()
            .ok_or("result did not serialize as an object")?
            .insert(
                "assimilated".into(),
                json!({
                    "time_s": time_s,
                    "entries": updates.iter().map(|u| json!({
                        "response": u.entry.response,
                        "posterior": u.fusion.posterior,
                        "posterior_relative_standard_uncertainty": u.fusion.posterior_rel_su,
                        "verdict": u.verdict,
                    })).collect::<Vec<_>>(),
                    "verdict": overall_verdict(&updates),
                }),
            );
        std::fs::write(p, serde_json::to_string_pretty(&updated).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}
