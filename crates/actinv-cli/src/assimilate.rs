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

/// The measured body of an assay document: independent per-response
/// lines, or a linear-combination measurement.
#[derive(Clone, Debug)]
pub enum AssayBody {
    /// Each entry fuses independently at the shared `time_s`.
    Entries(Vec<AssayEntry>),
    /// A measurement of y = Σ coefficient·response_value — a dose-rate,
    /// gross-gamma, or summed activity. `terms` are (response,
    /// coefficient); `value`/`standard_uncertainty` describe the
    /// measured combination.
    Mixture {
        terms: Vec<(String, f64)>,
        value: f64,
        standard_uncertainty: f64,
    },
}

/// Parse an `actinv-assay-1` document into its measured body plus the
/// shared measurement time. Three shapes are accepted:
///
/// - scalar (back-compatible): `response`, `value`,
///   `standard_uncertainty` at the top level;
/// - multi-nuclide: `entries: [{response, value, standard_uncertainty}]`
///   — a gamma-spectroscopy-style count that measures several nuclide
///   activities in one shot. Each entry fuses independently at the
///   shared `time_s`; provenance carries the single document sha.
/// - mixture: `mixture: [{response, coefficient}]` with top-level
///   `value`/`standard_uncertainty` — the measured linear combination
///   constrains the terms jointly via a Kalman H-row update; each term
///   narrows by its share of the combination and the emitted record
///   carries the induced correlation matrix.
///
/// Declaring more than one shape is an error — the document is
/// ambiguous.
pub fn parse_assay(av: &Value, source: &str) -> Result<(f64, AssayBody), String> {
    if av["schema"].as_str() != Some(ASSAY_SCHEMA) {
        return Err(format!("{source}: assay schema must be {ASSAY_SCHEMA}"));
    }
    let time_s = av["time_s"]
        .as_f64()
        .ok_or_else(|| format!("{source}: assay requires numeric `time_s`"))?;
    if !time_s.is_finite() {
        return Err(format!("{source}: assay time_s must be finite"));
    }
    let has_entries = av["entries"].is_array();
    let has_mixture = av["mixture"].is_array();
    // `response` is the unique scalar marker — `value`/`su` also appear
    // on mixture documents as the measured combination.
    let has_scalar = av["response"].is_string();
    let declared = has_entries as u8 + has_mixture as u8 + has_scalar as u8;
    if declared == 0 {
        return Err(format!(
            "{source}: assay requires `response`, `entries`, or `mixture`"
        ));
    }
    if declared > 1 {
        return Err(format!(
            "{source}: assay declares multiple measurement shapes — pick one"
        ));
    }
    if has_mixture {
        let terms_v = av["mixture"].as_array().expect("checked");
        if terms_v.is_empty() {
            return Err(format!("{source}: assay `mixture` is empty"));
        }
        let value = av["value"]
            .as_f64()
            .ok_or_else(|| format!("{source}: mixture assay requires numeric `value`"))?;
        let su = av["standard_uncertainty"].as_f64().ok_or_else(|| {
            format!("{source}: mixture assay requires numeric `standard_uncertainty`")
        })?;
        if value <= 0.0 {
            return Err(format!(
                "{source}: mixture value must be positive for a log-scale update"
            ));
        }
        if !su.is_finite() || su <= 0.0 {
            return Err(format!(
                "{source}: mixture standard_uncertainty must be positive"
            ));
        }
        let mut terms = Vec::new();
        for (i, t) in terms_v.iter().enumerate() {
            let resp = t["response"]
                .as_str()
                .ok_or_else(|| format!("{source}: mixture[{i}] requires a `response` key"))?;
            let c = t["coefficient"]
                .as_f64()
                .ok_or_else(|| format!("{source}: mixture[{i}] requires numeric `coefficient`"))?;
            if !c.is_finite() {
                return Err(format!("{source}: mixture[{i}] coefficient must be finite"));
            }
            terms.push((resp.to_string(), c));
        }
        return Ok((
            time_s,
            AssayBody::Mixture {
                terms,
                value,
                standard_uncertainty: su,
            },
        ));
    }
    let mut out = Vec::new();
    if let Some(entries) = av["entries"].as_array() {
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
    Ok((time_s, AssayBody::Entries(out)))
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

/// One term of a mixture measurement: coefficient cᵢ, the response's
/// prior nominal, and its ln-space standard deviation.
#[derive(Clone)]
pub struct MixtureTerm {
    pub response: String,
    pub coefficient: f64,
    pub nominal: f64,
    /// Relative ln-space σ (prior_su/nominal).
    pub s_ln: f64,
    /// The response's own band multiplier — each posterior keeps it.
    pub normal_multiplier: f64,
}

/// Result of a mixture fusion: per-term posterior + the induced
/// cross-term correlation matrix (the measurement entangles the terms —
/// marginals alone would overstate independence).
pub struct MixtureFusion {
    /// Forward value y₀ = Σ cᵢ·nomᵢ.
    pub forward: f64,
    /// Innovation ν = ln(y_meas/y₀).
    pub innovation: f64,
    /// Innovation variance S = s_m² + HᵀΣH.
    pub s_innovation: f64,
    /// Fractional weights Hᵢ = cᵢ·nomᵢ/y₀ — each term's share of the
    /// measured combination.
    pub weights: Vec<f64>,
    /// Kalman gains Kᵢ = sᵢ²Hᵢ/S.
    pub gains: Vec<f64>,
    /// Per-term posterior nominal xᵢ' = nomᵢ·e^{Kᵢν}.
    pub posteriors: Vec<f64>,
    /// Per-term posterior ln-σ: sᵢ' = sᵢ·√(1 − KᵢHᵢ).
    pub posterior_s_ln: Vec<f64>,
    /// Induced correlation ρᵢⱼ (i≠j): −sᵢsⱼHᵢHⱼ/(S·sᵢ'sⱼ'). Diagonal 1.
    pub correlations: Vec<Vec<f64>>,
}

/// Kalman H-row update for a measurement of y = Σ cᵢ·xᵢ where each
/// ln xᵢ ~ N(ln nomᵢ, sᵢ²) independently. ln y ≈ ln y₀ + Σ Hᵢ·δln xᵢ
/// with Hᵢ = cᵢ·nomᵢ/y₀ — the fractional-contribution linearization.
/// A one-term mixture reduces exactly to `fuse`.
pub fn fuse_mixture(terms: &[MixtureTerm], meas: f64, meas_su: f64) -> MixtureFusion {
    let y0: f64 = terms.iter().map(|t| t.coefficient * t.nominal).sum();
    let y0 = y0.max(1e-300);
    let weights: Vec<f64> = terms
        .iter()
        .map(|t| t.coefficient * t.nominal / y0)
        .collect();
    let s_m = (meas_su / meas).max(1e-300);
    let hss: f64 = terms
        .iter()
        .zip(&weights)
        .map(|(t, h)| h * h * t.s_ln * t.s_ln)
        .sum();
    let s_inn = s_m * s_m + hss;
    let nu = (meas / y0).ln();
    let gains: Vec<f64> = terms
        .iter()
        .zip(&weights)
        .map(|(t, h)| t.s_ln * t.s_ln * h / s_inn)
        .collect();
    let posteriors: Vec<f64> = terms
        .iter()
        .zip(&gains)
        .map(|(t, k)| t.nominal * (k * nu).exp())
        .collect();
    let posterior_s_ln: Vec<f64> = terms
        .iter()
        .zip(&weights)
        .zip(&gains)
        .map(|((t, h), k)| t.s_ln * (1.0 - k * h).max(0.0).sqrt())
        .collect();
    // Posterior covariance: Σ' = Σ − ΣHᵀS⁻¹HΣ, so the induced
    // covᵢⱼ = −(sᵢ²Hᵢ)(sⱼ²Hⱼ)/S and ρᵢⱼ = covᵢⱼ/(sᵢ'sⱼ').
    let correlations: Vec<Vec<f64>> = (0..terms.len())
        .map(|i| {
            (0..terms.len())
                .map(|j| {
                    if i == j {
                        1.0
                    } else {
                        -terms[i].s_ln
                            * terms[i].s_ln
                            * terms[j].s_ln
                            * terms[j].s_ln
                            * weights[i]
                            * weights[j]
                            / (s_inn * posterior_s_ln[i] * posterior_s_ln[j])
                    }
                })
                .collect()
        })
        .collect();
    MixtureFusion {
        forward: y0,
        innovation: nu,
        s_innovation: s_inn.sqrt(),
        weights,
        gains,
        posteriors,
        posterior_s_ln,
        correlations,
    }
}

/// Apply a mixture posterior to a response object in place — same target
/// fields as `apply_fusion`, stamped `kind: mixture` with the term's
/// share of the measured combination.
pub fn apply_mixture_fusion(
    resp: &mut Value,
    post: f64,
    post_s_ln: f64,
    multiplier: f64,
    weight: f64,
    assay_sha: &str,
) {
    let obj = match resp.as_object_mut() {
        Some(o) => o,
        None => return,
    };
    let half = (multiplier * post_s_ln).exp();
    let band = [post / half, post * half];
    obj.insert("nominal".into(), json!(post));
    obj.insert(
        "combined_standard_uncertainty".into(),
        json!(post * post_s_ln),
    );
    obj.insert("relative_standard_uncertainty".into(), json!(post_s_ln));
    obj.insert("normal_interval".into(), json!(band));
    obj.insert("conservative_interval".into(), json!(band));
    obj.insert(
        "assimilation".into(),
        json!({
            "kind": "mixture",
            "weight_in_measured_combination": weight,
            "posterior_relative_standard_uncertainty": post_s_ln,
            "assay_sha256": assay_sha,
            "note": "posterior after a linear-combination measurement; marginals are exact but the fused responses are now correlated — see the mixture record's correlation matrix",
        }),
    );
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
    let (time_s, body) = parse_assay(&assay, assay_path)?;
    let provenance = json!({
        "result_sha256": actinv_data::builder::sha256_file(std::path::Path::new(result_path))
            .unwrap_or_default(),
        "assay_sha256": actinv_data::builder::sha256_file(std::path::Path::new(assay_path))
            .unwrap_or_default(),
    });
    let assay_sha =
        actinv_data::builder::sha256_file(std::path::Path::new(assay_path)).unwrap_or_default();
    let (out, updated) = fuse_body(&result, time_s, &body, provenance, &assay_sha)?;
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    if let Some(p) = emit_result {
        std::fs::write(p, serde_json::to_string_pretty(&updated).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}

/// sha256 of a serialized value — in-memory provenance for the
/// workbench path, which has no files to hash.
fn sha256_value(v: &Value) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(serde_json::to_vec(v).unwrap_or_default());
    format!("{:x}", h.finalize())
}

/// File-free fusion for the workbench: identical math and emit semantics
/// to `run`, on in-memory values. Returns (assimilation summary, updated
/// result document) — the caller decides whether to adopt the update.
pub fn fuse_document(
    result: &Value,
    assay: &Value,
    source: &str,
) -> Result<(Value, Value), String> {
    let (time_s, body) = parse_assay(assay, source)?;
    let assay_sha = sha256_value(assay);
    let provenance = json!({
        "result_sha256": sha256_value(result),
        "assay_sha256": assay_sha,
        "source": source,
    });
    fuse_body(result, time_s, &body, provenance, &assay_sha)
}

/// Shared fusion core: dispatch on the parsed assay body, produce the
/// `actinv-assimilated-1` summary and the updated result document (the
/// equivalent of `--emit-result`). One math path serves the CLI and the
/// workbench.
fn fuse_body(
    result: &Value,
    time_s: f64,
    body: &AssayBody,
    provenance: Value,
    assay_sha: &str,
) -> Result<(Value, Value), String> {
    match body {
        AssayBody::Entries(entries) => {
            entries_document(result, time_s, entries, provenance, assay_sha)
        }
        AssayBody::Mixture {
            terms,
            value,
            standard_uncertainty,
        } => mixture_document(
            result,
            time_s,
            terms,
            *value,
            *standard_uncertainty,
            provenance,
            assay_sha,
        ),
    }
}

/// Entries path: fuse every measured response independently, emit the
/// per-entry updates plus the legacy scalar fields when one entry is
/// present, and return the updated result with `assimilated` provenance.
fn entries_document(
    result: &Value,
    time_s: f64,
    entries: &[AssayEntry],
    provenance: Value,
    assay_sha: &str,
) -> Result<(Value, Value), String> {
    let updates: Vec<EntryUpdate> = entries
        .iter()
        .map(|e| fuse_entry(result, time_s, e))
        .collect::<Result<_, _>>()?;

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
    // Re-issue the run result with each fused response band replaced by
    // its posterior — the document feeds `decide`/`clearance`/another
    // `assimilate` unchanged, carrying `assimilation` provenance per
    // response.
    let mut updated = result.clone();
    for u in &updates {
        if let Some(st) = updated["steps"].as_array_mut().and_then(|steps| {
            steps.iter_mut().find(|s| {
                s["t_s"]
                    .as_f64()
                    .map(|t| is_close(t, time_s))
                    .unwrap_or(false)
            })
        }) {
            if let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{}", u.entry.response))
            {
                apply_fusion(r, &u.fusion, assay_sha);
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
    Ok((out, updated))
}

/// A term's pulled prior from the result at the assay time.
fn term_prior(result: &Value, time_s: f64, resp: &str) -> Result<(f64, f64, f64), String> {
    let step =
        find_step(result, time_s).ok_or_else(|| format!("result has no step at t_s={time_s}"))?;
    let r = step
        .pointer(&format!("/uncertainty/responses/{resp}"))
        .ok_or_else(|| {
            format!("result step at t_s={time_s} carries no uncertainty for '{resp}'")
        })?;
    let nominal = r["nominal"].as_f64().unwrap_or(0.0);
    let su = r["combined_standard_uncertainty"]
        .as_f64()
        .or_else(|| r["mf33_standard_uncertainty"].as_f64())
        .unwrap_or(0.0);
    if nominal <= 0.0 || su <= 0.0 {
        return Err(format!(
            "mixture term '{resp}' has non-positive prior (nominal={nominal}, su={su})"
        ));
    }
    let mult = r["normal_multiplier"].as_f64().unwrap_or(1.959964);
    Ok((nominal, su, mult))
}

/// Fold a linear-combination measurement (dose-rate, gross activity)
/// into the term bands jointly. The forward value y₀ = Σcᵢxᵢ carries an
/// ln-space band from the term σs through the H weights; the measurement
/// is checked against it for the consistency verdict.
#[allow(clippy::too_many_arguments)]
fn mixture_document(
    result: &Value,
    time_s: f64,
    terms: &[(String, f64)],
    value: f64,
    su: f64,
    provenance: Value,
    assay_sha: &str,
) -> Result<(Value, Value), String> {
    let mut mterms = Vec::with_capacity(terms.len());
    for (resp, c) in terms {
        let (nominal, s_abs, mult) = term_prior(result, time_s, resp)?;
        mterms.push(MixtureTerm {
            response: resp.clone(),
            coefficient: *c,
            nominal,
            s_ln: (s_abs / nominal).max(1e-300),
            normal_multiplier: mult,
        });
    }
    let f = fuse_mixture(&mterms, value, su);

    // Consistency verdict on the forward value: the prior predicts
    // y₀·exp(±m·√S_prior) — the analogue of the scalar interval check.
    let s_prior = {
        let v: f64 = mterms
            .iter()
            .zip(&f.weights)
            .map(|(t, h)| h * h * t.s_ln * t.s_ln)
            .sum();
        v.sqrt()
    };
    let mult = mterms
        .iter()
        .map(|t| t.normal_multiplier)
        .fold(1.0_f64, f64::max);
    let (lo, hi) = (
        f.forward * (-mult * s_prior).exp(),
        f.forward * (mult * s_prior).exp(),
    );
    let verdict = if value >= lo && value <= hi {
        "consistent"
    } else {
        let width = (hi / lo.max(1e-300)).ln().abs();
        let dist = if value > hi {
            (value / hi).ln()
        } else {
            (lo / value).ln()
        };
        if dist <= 0.5 * width.max(1e-300) {
            "marginal"
        } else {
            "conflict"
        }
    };

    let term_records: Vec<Value> = mterms
        .iter()
        .enumerate()
        .map(|(i, t)| {
            json!({
                "response": t.response,
                "coefficient": t.coefficient,
                "share_of_combination": f.weights[i],
                "kalman_gain": f.gains[i],
                "prior_nominal": t.nominal,
                "posterior": f.posteriors[i],
                "posterior_relative_standard_uncertainty": f.posterior_s_ln[i],
            })
        })
        .collect();
    let out = json!({
        "schema": "actinv-assimilated-1",
        "kind": "mixture",
        "time_s": time_s,
        "measured_combination": {
            "value": value,
            "standard_uncertainty": su,
            "forward_value": f.forward,
            "forward_band": [lo, hi],
            "innovation_ln": f.innovation,
            "innovation_sigma": f.s_innovation,
        },
        "terms": term_records,
        "induced_correlations": {
            "responses": mterms.iter().map(|t| t.response.clone()).collect::<Vec<_>>(),
            "matrix": f.correlations,
            "note": "the measurement entangles the terms — off-diagonal correlation means a later assay on one member moves the others; marginal bands remain individually correct",
        },
        "verdict": verdict,
        "provenance": provenance,
        "note": "Kalman H-row update on ln(y)=ln(Σcᵢxᵢ): posterior marginals shrink by each term's share of the measured combination; the terms are correlated afterward — use the emitted matrix for any joint statement",
    });
    let mut updated = result.clone();
    for (i, t) in mterms.iter().enumerate() {
        if let Some(st) = updated["steps"].as_array_mut().and_then(|steps| {
            steps.iter_mut().find(|s| {
                s["t_s"]
                    .as_f64()
                    .map(|tt| is_close(tt, time_s))
                    .unwrap_or(false)
            })
        }) {
            if let Some(r) = st.pointer_mut(&format!("/uncertainty/responses/{}", t.response)) {
                apply_mixture_fusion(
                    r,
                    f.posteriors[i],
                    f.posterior_s_ln[i],
                    t.normal_multiplier,
                    f.weights[i],
                    assay_sha,
                );
            }
        }
    }
    updated
        .as_object_mut()
        .ok_or("result did not serialize as an object")?
        .insert(
            "assimilated".into(),
            json!({
                "kind": "mixture",
                "time_s": time_s,
                "responses": mterms.iter().map(|t| t.response.clone()).collect::<Vec<_>>(),
                "verdict": verdict,
            }),
        );
    Ok((out, updated))
}
