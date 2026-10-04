//! D6a — evaluation spread: the same spec solved under the alternate
//! decay primacy.
//!
//! `actinv eval-spread SPEC.json [OUT.json]`
//!
//! The declared `decay.primary` solves first; then `primary` and
//! `fallback` are swapped and the spec is solved again. Every emitted
//! quantity — per-nuclide activity, heat components, and each declared
//! uncertainty response — is compared stepwise in log space:
//! `spread = max_t |ln(x_alt(t) / x_base(t))|`.
//!
//! Honesty: this is the decay-evaluation sensitivity of the answer, not
//! an uncertainty band. The emitted `suggested_unmodeled_relative` is
//! the largest observed spread — a *candidate* extra term for
//! `uncertainty.unmodeled_relative`, not an implied sigma: where two
//! evaluations disagree the truth is inside neither file, and the
//! spread only bounds how far the choice moves the answer.

use actinv_core::{run::run as run_spec, spec::Spec};
use serde_json::{json, Value};

fn sha256_text(text: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(text.as_bytes());
    format!("{:x}", h.finalize())
}

/// max_t |ln(alt/base)| for a pair of aligned step series extracted by
/// `grab`; entries missing or nonpositive on either side are skipped and
/// counted.
fn series_spread(
    base: &[Value],
    alt: &[Value],
    grab: impl Fn(&Value) -> Option<f64>,
) -> (f64, Option<f64>, usize) {
    let mut max_spread = 0.0_f64;
    let mut at = None;
    let mut skipped = 0usize;
    for (b, a) in base.iter().zip(alt.iter()) {
        let (vb, va) = match (grab(b), grab(a)) {
            (Some(vb), Some(va)) => (vb, va),
            _ => {
                skipped += 1;
                continue;
            }
        };
        if vb <= 0.0 || va <= 0.0 {
            skipped += 1;
            continue;
        }
        let spread = (va / vb).ln().abs();
        if spread > max_spread {
            max_spread = spread;
            at = b["t_s"].as_f64();
        }
    }
    (max_spread, at, skipped)
}

pub fn run(spec_path: &str, out: Option<&str>) -> Result<Value, String> {
    let text =
        std::fs::read_to_string(spec_path).map_err(|e| format!("cannot read {spec_path}: {e}"))?;
    let resolved = crate::resolve_catalog_json(&text).map_err(|e| format!("{spec_path}: {e}"))?;
    let doc: Value = serde_json::from_str(&resolved).map_err(|e| format!("{spec_path}: {e}"))?;
    let spec = Spec::from_json(&resolved).map_err(|e| format!("{spec_path}: {e}"))?;
    let fallback = spec
        .decay
        .fallback
        .as_deref()
        .filter(|f| !f.is_empty())
        .ok_or(
            "eval-spread needs decay.fallback: declare the alternate decay \
             evaluation (e.g. JEFF-3.3 beside ENDF/B-VIII.0) so the spread \
             can be measured",
        )?;
    let primary = spec.decay.primary.clone();

    let base = serde_json::to_value(run_spec(&spec, "eval-spread")?)
        .map_err(|e| format!("serialise base run: {e}"))?;

    // Alternate arm: swap the primacy. The merged view is identical, so
    // XS coverage is unchanged; only which evaluation's decay constants
    // lead (and which fills gaps) differs.
    let mut alt_doc = doc.clone();
    let decay = alt_doc["decay"]
        .as_object_mut()
        .ok_or("spec decay block is not an object")?;
    decay.insert("primary".into(), json!(fallback));
    decay.insert("fallback".into(), json!(primary));
    let alt_text =
        serde_json::to_string(&alt_doc).map_err(|e| format!("serialise swapped spec: {e}"))?;
    let alt_spec = Spec::from_json(&alt_text).map_err(|e| format!("swapped spec: {e}"))?;
    let alt = serde_json::to_value(run_spec(&alt_spec, "eval-spread")?)
        .map_err(|e| format!("serialise alternate run: {e}"))?;

    let steps_b = base["steps"]
        .as_array()
        .ok_or("base result has no steps array")?;
    let steps_a = alt["steps"]
        .as_array()
        .ok_or("alternate result has no steps array")?;
    if steps_b.len() != steps_a.len() {
        return Err("alternate run produced a different step count".into());
    }

    // Collect the compared quantity names from the base run.
    let mut nuclides: Vec<String> = Vec::new();
    let mut heats: Vec<String> = Vec::new();
    let mut responses: Vec<String> = Vec::new();
    for s in steps_b {
        if let Some(m) = s["activity_Bq_per_g"].as_object() {
            for k in m.keys() {
                if !nuclides.contains(k) {
                    nuclides.push(k.clone());
                }
            }
        }
        if let Some(m) = s["heat_W_per_g"].as_object() {
            for k in m.keys() {
                if !heats.contains(k) {
                    heats.push(k.clone());
                }
            }
        }
        if let Some(m) = s["uncertainty"]["responses"].as_object() {
            for k in m.keys() {
                if !responses.contains(k) {
                    responses.push(k.clone());
                }
            }
        }
    }
    nuclides.sort();
    heats.sort();
    responses.sort();

    let mut skipped = 0usize;
    let mut per_nuclide = serde_json::Map::new();
    for name in &nuclides {
        let (spread, at, skip) =
            series_spread(steps_b, steps_a, |s| s["activity_Bq_per_g"][name].as_f64());
        skipped += skip;
        // at_time_s is null when the spread never rose above zero.
        per_nuclide.insert(
            name.clone(),
            json!({"max_abs_ln_ratio": spread, "at_time_s": at}),
        );
    }
    let mut per_heat = serde_json::Map::new();
    for name in &heats {
        let (spread, at, skip) =
            series_spread(steps_b, steps_a, |s| s["heat_W_per_g"][name].as_f64());
        skipped += skip;
        per_heat.insert(
            name.clone(),
            json!({"max_abs_ln_ratio": spread, "at_time_s": at}),
        );
    }
    let mut per_response = serde_json::Map::new();
    for name in &responses {
        let (spread, at, skip) = series_spread(steps_b, steps_a, |s| {
            s["uncertainty"]["responses"][name]["nominal"].as_f64()
        });
        skipped += skip;
        per_response.insert(
            name.clone(),
            json!({"max_abs_ln_ratio": spread, "at_time_s": at}),
        );
    }

    let max_spread = per_nuclide
        .values()
        .chain(per_heat.values())
        .chain(per_response.values())
        .filter_map(|v| v["max_abs_ln_ratio"].as_f64())
        .fold(0.0_f64, f64::max);

    let inputs = base["certificate"]["inputs"].clone();
    let summary = json!({
        "schema": "actinv-eval-spread-1",
        "spec": spec_path,
        "spec_sha256": sha256_text(&text),
        "comparison": format!(
            "decay.primary '{}' vs alternate primacy '{}' (roles swapped)",
            primary, fallback
        ),
        "decay_inputs": {
            "primary": inputs["decay_primary"],
            "fallback": inputs["decay_fallback"],
        },
        "n_steps": steps_b.len(),
        "skipped_nonpositive_pairs": skipped,
        "per_nuclide_activity": per_nuclide,
        "per_heat_component": per_heat,
        "per_response_nominal": per_response,
        "max_abs_ln_ratio": max_spread,
        "suggested_unmodeled_relative": max_spread,
        "note": "evaluation sensitivity, not a sigma: the spread bounds how far the decay-evaluation choice moves the answer; fold into unmodeled_relative deliberately",
    });
    if let Some(path) = out {
        std::fs::write(
            path,
            serde_json::to_string_pretty(&summary).expect("serialise spread"),
        )
        .map_err(|e| format!("cannot write {path}: {e}"))?;
    }
    Ok(summary)
}
