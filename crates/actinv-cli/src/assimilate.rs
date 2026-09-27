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

pub fn run(result_path: &str, assay_path: &str, out_path: Option<&str>) -> Result<Value, String> {
    let result_text = std::fs::read_to_string(result_path)
        .map_err(|e| format!("cannot read {result_path}: {e}"))?;
    let result: Value =
        serde_json::from_str(&result_text).map_err(|e| format!("{result_path}: {e}"))?;
    let assay_text = std::fs::read_to_string(assay_path)
        .map_err(|e| format!("cannot read {assay_path}: {e}"))?;
    let assay: Value =
        serde_json::from_str(&assay_text).map_err(|e| format!("{assay_path}: {e}"))?;
    if assay["schema"].as_str() != Some(ASSAY_SCHEMA) {
        return Err(format!("assay schema must be {ASSAY_SCHEMA}"));
    }
    let response = assay["response"]
        .as_str()
        .ok_or("assay requires a `response` key")?;
    let time_s = assay["time_s"]
        .as_f64()
        .ok_or("assay requires numeric `time_s`")?;
    let meas = assay["value"]
        .as_f64()
        .ok_or("assay requires numeric `value`")?;
    let meas_su = assay["standard_uncertainty"]
        .as_f64()
        .ok_or("assay requires numeric `standard_uncertainty`")?;
    if meas <= 0.0 {
        return Err("assay value must be positive for a log-scale update".into());
    }
    if meas_su <= 0.0 || meas_su.is_nan() {
        return Err("assay standard_uncertainty must be positive".into());
    }

    let step =
        find_step(&result, time_s).ok_or_else(|| format!("result has no step at t_s={time_s}"))?;
    let resp = step
        .pointer(&format!("/uncertainty/responses/{response}"))
        .ok_or_else(|| {
            format!("result step at t_s={time_s} carries no uncertainty for '{response}'")
        })?;
    let nominal = resp["nominal"]
        .as_f64()
        .ok_or("response carries no nominal")?;
    if nominal <= 0.0 {
        return Err(format!(
            "response '{response}' nominal is {nominal} — log-scale update needs a positive prior"
        ));
    }
    let prior_su = resp["combined_standard_uncertainty"]
        .as_f64()
        .or_else(|| resp["mf33_standard_uncertainty"].as_f64())
        .ok_or("response carries no standard uncertainty")?;
    let multiplier = resp["normal_multiplier"].as_f64().unwrap_or(1.959964);

    // Relative-σ fusion in ln space.
    let s_p = (prior_su / nominal).max(1e-300);
    let s_m = (meas_su / meas).max(1e-300);
    let x = nominal.ln();
    let y = meas.ln();
    let k = s_p * s_p / (s_p * s_p + s_m * s_m);
    let x_post = x + k * (y - x);
    let s_post = (s_p * s_p * (1.0 - k)).sqrt();
    let post = x_post.exp();
    let half = (multiplier * s_post).exp();
    let band = [post / half, post * half];

    // Consistency verdict: is the measurement inside the prior band?
    let prior_band = resp["conservative_interval"]
        .as_array()
        .and_then(|a| Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?)));
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

    let out = json!({
        "schema": "actinv-assimilated-1",
        "response": response,
        "time_s": time_s,
        "prior": {
            "nominal": nominal,
            "combined_standard_uncertainty": prior_su,
            "relative_standard_uncertainty": s_p,
            "band": prior_band.map(|(l,h)| vec![l, h]),
        },
        "assay": {
            "value": meas,
            "standard_uncertainty": meas_su,
            "relative_standard_uncertainty": s_m,
        },
        "update": {
            "kalman_gain": k,
            "space": "ln(response)",
            "posterior": post,
            "posterior_relative_standard_uncertainty": s_post,
            "posterior_band": band,
            "confidence_level": resp["confidence_level"],
            "normal_multiplier": multiplier,
            "shrunk": s_post < s_p,
        },
        "verdict": verdict,
        "provenance": {
            "result_sha256": actinv_data::builder::sha256_file(std::path::Path::new(result_path))
                .unwrap_or_default(),
            "assay_sha256": actinv_data::builder::sha256_file(std::path::Path::new(assay_path))
                .unwrap_or_default(),
        },
        "note": "Gaussian fusion on ln-scale between the solver's propagated band and the assay's declared lognormal uncertainty; `conflict` means the measurement lies outside the prior's declared interval — a real discrepancy between model and assay, not a reason to silently widen",
    });
    if let Some(p) = out_path {
        std::fs::write(p, serde_json::to_string_pretty(&out).unwrap())
            .map_err(|e| format!("cannot write {p}: {e}"))?;
    }
    Ok(out)
}
