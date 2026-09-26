//! Certified probabilistic clearance (P54): `actinv clearance`.
//!
//! Evaluates the IAEA-style sum-of-ratios criterion `S = Σᵢ aᵢ/Lᵢ` per cell
//! (or per run result) at a named schedule step and certifies
//! `P(S < 1)` as an interval bounded by the two declared combination rules —
//! independence (√Σσᵢ²) and full correlation (Σσᵢ) — under the declared
//! linearized-Gaussian propagation model. Coverage ledgers make sure nothing
//! outside the statistic is silently ignored.

use serde_json::{json, Value};

/// Bundled IAEA 2004 clearance table (RS-G-1.7 Table 2 basis, Bq g⁻¹).
/// Canonical artifact: `data/clearance_iaea_2004.json`.
const BUNDLED_LIMITS: &str = include_str!("../../../data/clearance_iaea_2004.json");

pub struct LimitsTable {
    /// RS-G-1.7-style keys ("Fe-55", "Co-60m") -> Bq/g.
    pub limits: Vec<(String, f64)>,
    pub source: String,
    pub sha256: String,
}

impl LimitsTable {
    pub fn bundled() -> Result<Self, String> {
        Self::parse(BUNDLED_LIMITS.to_string())
    }

    pub fn parse(text: String) -> Result<Self, String> {
        let doc: Value = serde_json::from_str(&text)
            .map_err(|e| format!("clearance limits file is not JSON: {e}"))?;
        if doc.get("schema").and_then(Value::as_str) != Some("actinv-clearance-limits-1") {
            return Err("limits file schema is not actinv-clearance-limits-1".into());
        }
        let source = doc
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("unspecified")
            .to_string();
        let obj = doc
            .get("limits")
            .and_then(Value::as_object)
            .ok_or("limits file carries no 'limits' object")?;
        let mut limits: Vec<(String, f64)> = Vec::with_capacity(obj.len());
        for (k, v) in obj {
            let lim = v
                .as_f64()
                .ok_or_else(|| format!("limits entry '{k}' is not a number"))?;
            if !lim.is_finite() || lim <= 0.0 {
                return Err(format!("limits entry '{k}' must be a positive finite Bq/g"));
            }
            limits.push((k.clone(), lim));
        }
        limits.sort_by(|a, b| a.0.cmp(&b.0));
        let sha256 = sha256_hex(text.as_bytes());
        Ok(LimitsTable {
            limits,
            source,
            sha256,
        })
    }
}

/// Normalise an ACTINV inventory name ("Co60m1", "Fe55") to the RS-G-1.7
/// dashed table form ("Co-60m", "Fe-55"). `m1`/`m2` map to `m`/`m2`.
pub fn table_key(nuclide: &str) -> Option<String> {
    let bytes = nuclide.as_bytes();
    let mut split = None;
    for (i, &b) in bytes.iter().enumerate() {
        if b.is_ascii_digit() {
            split = Some(i);
            break;
        }
    }
    let i = split?;
    let (elem, rest) = nuclide.split_at(i);
    if elem.is_empty() || !elem.chars().next()?.is_ascii_uppercase() {
        return None;
    }
    let mut digits_end = rest.len();
    let mut iso = String::new();
    for (j, c) in rest.char_indices() {
        if c.is_ascii_digit() {
            continue;
        }
        digits_end = j;
        iso = rest[j..].to_string();
        break;
    }
    let mass = &rest[..digits_end];
    let iso = match iso.as_str() {
        "" => String::new(),
        "m1" => "m".to_string(),
        "m2" => "m2".to_string(),
        other => other.to_string(),
    };
    Some(format!("{elem}-{mass}{iso}"))
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Standard normal CDF Φ(x) = ½[1 + erf(x/√2)].
fn normal_cdf(x: f64) -> f64 {
    0.5 * (1.0 + erf(x * std::f64::consts::FRAC_1_SQRT_2))
}

fn erf(x: f64) -> f64 {
    // Abramowitz–Stegun 7.1.26, |ε| ≤ 1.5e-7 — declared accuracy class for a
    // certification *interval* whose endpoints already bracket the model.
    let t = 1.0 / (1.0 + 0.3275911 * x.abs());
    let poly = t
        * (0.254829592
            + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
    let y = 1.0 - poly * (-x * x).exp();
    if x >= 0.0 {
        y
    } else {
        -y
    }
}

fn step_from<'a>(record: &'a Value, step: u64) -> Result<&'a Value, String> {
    let steps = record
        .pointer("/result/steps")
        .or_else(|| record.get("steps"))
        .and_then(Value::as_array)
        .ok_or("record carries no result.steps array")?;
    steps
        .iter()
        .find(|s| s.get("step").and_then(Value::as_u64) == Some(step))
        .ok_or_else(|| format!("record has no step {step} (carries {} steps)", steps.len()))
}

fn f64v(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

fn response_sigma(resp: &Value) -> f64 {
    for key in ["combined_standard_uncertainty", "mf33_standard_uncertainty"] {
        if let Some(v) = f64v(resp, key) {
            return v;
        }
    }
    0.0
}

fn cell_id_of(rec: &Value, index: usize) -> String {
    rec.get("id")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("cell-{index}"))
}

/// Evaluate one step's activity map against the limits table.
fn evaluate_step(
    step_obj: &Value,
    table: &std::collections::BTreeMap<String, f64>,
    confidence: f64,
) -> Result<Value, String> {
    let activities = step_obj
        .get("activity_Bq_per_g")
        .and_then(Value::as_object)
        .ok_or("step carries no activity_Bq_per_g map")?;
    let responses = step_obj
        .pointer("/uncertainty/responses")
        .and_then(Value::as_object)
        .ok_or(
            "step carries no uncertainty.responses — clearance requires an uncertainty-banded run",
        )?;

    let mut sum_ratio = 0.0;
    let mut var_ind = 0.0;
    let mut sig_cons = 0.0;
    let mut banded_ratio = 0.0;
    let mut unbanded_ratio = 0.0;
    let mut unregulated_act = 0.0;
    let mut total_act = 0.0;
    let mut regulated_count = 0usize;
    let mut nuclides = Vec::new();
    let mut dominant_ratio: Option<(f64, &str)> = None;
    let mut dominant_sigma: Option<(f64, &str)> = None;

    for (nuc, av) in activities {
        let a = av.as_f64().unwrap_or(0.0);
        if !(a.is_finite() && a > 0.0) {
            continue;
        }
        total_act += a;
        let Some(tkey) = table_key(nuc) else {
            unregulated_act += a;
            nuclides.push(json!({
                "nuclide": nuc,
                "activity_Bq_g": a,
                "in_table": false,
                "banded": false,
            }));
            continue;
        };
        let Some(&limit) = table.get(&tkey) else {
            unregulated_act += a;
            nuclides.push(json!({
                "nuclide": nuc,
                "table_label": tkey,
                "activity_Bq_g": a,
                "in_table": false,
                "banded": false,
            }));
            continue;
        };
        regulated_count += 1;
        let resp = responses.get(&format!("activity:{nuc}"));
        let sigma = resp.map(response_sigma).unwrap_or(0.0);
        let r_i = a / limit;
        let sr_i = sigma / limit;
        let banded = sr_i > 0.0;
        sum_ratio += r_i;
        var_ind += sr_i * sr_i;
        sig_cons += sr_i;
        if banded {
            banded_ratio += r_i;
        } else {
            unbanded_ratio += r_i;
        }
        if dominant_ratio.map_or(true, |(v, _)| r_i > v) {
            dominant_ratio = Some((r_i, nuc));
        }
        if dominant_sigma.map_or(true, |(v, _)| sr_i > v) {
            dominant_sigma = Some((sr_i, nuc));
        }
        nuclides.push(json!({
            "nuclide": nuc,
            "table_label": tkey,
            "activity_Bq_g": a,
            "limit_Bq_g": limit,
            "ratio": r_i,
            "sigma_Bq_g": sigma,
            "sigma_ratio": sr_i,
            "banded": banded,
            "in_table": true,
        }));
    }

    let sigma_ind = var_ind.sqrt();
    let (p_lo, p_hi, classification) = if sigma_ind == 0.0 && sig_cons == 0.0 {
        let det = if sum_ratio < 1.0 {
            "deterministic_clear"
        } else {
            "deterministic_fail"
        };
        let p = if sum_ratio < 1.0 { 1.0 } else { 0.0 };
        (p, p, det.to_string())
    } else {
        let p_ind = normal_cdf((1.0 - sum_ratio) / sigma_ind.max(1e-300));
        let p_cons = normal_cdf((1.0 - sum_ratio) / sig_cons.max(1e-300));
        let (lo, hi) = (p_cons.min(p_ind), p_cons.max(p_ind));
        let cls = if lo >= confidence {
            "clears_certified"
        } else if hi <= 1.0 - confidence {
            "fails_certified"
        } else {
            "indeterminate"
        };
        (lo, hi, cls.to_string())
    };

    let clears = match classification.as_str() {
        "clears_certified" | "deterministic_clear" => Some(true),
        "fails_certified" | "deterministic_fail" => Some(false),
        _ => None,
    };
    Ok(json!({
        "record": "clearance",
        "sum_ratio": sum_ratio,
        "sigma_sum_independent": sigma_ind,
        "sigma_sum_conservative": sig_cons,
        "p_clear_interval": [p_lo, p_hi],
        "classification": classification,
        "clears": clears,
        "dominant_ratio": dominant_ratio.map(|(v, n)| json!({"nuclide": n, "ratio": v})),
        "dominant_sigma": dominant_sigma.map(|(v, n)| json!({"nuclide": n, "sigma_ratio": v})),
        "coverage": {
            "banded_ratio_share": if sum_ratio > 0.0 { banded_ratio / sum_ratio } else { 0.0 },
            "unbanded_ratio_share": if sum_ratio > 0.0 { unbanded_ratio / sum_ratio } else { 0.0 },
            "unregulated_activity_share": if total_act > 0.0 { unregulated_act / total_act } else { 0.0 },
            "regulated_count": regulated_count,
        },
        "nuclides": nuclides,
    }))
}

/// Emit `actinv-clearance-1` records for every cell/result in INPUT.
/// INPUT may be an `actinv mesh` NDJSON (record:cell) or an `actinv run`
/// result JSON (single object with a steps array).
pub fn emit_clearance(
    input_bytes: &[u8],
    step: u64,
    limits_path: Option<&str>,
    confidence: f64,
) -> Result<String, String> {
    if !confidence.is_finite() || !(0.0..1.0).contains(&confidence) {
        return Err("confidence must be in the open interval (0, 1)".into());
    }
    let started = std::time::Instant::now();
    let input_sha = sha256_hex(input_bytes);
    let (table, table_sha, table_source) = match limits_path {
        Some(path) => {
            let text = std::fs::read_to_string(path)
                .map_err(|e| format!("cannot read limits file {path}: {e}"))?;
            let t = LimitsTable::parse(text)?;
            (
                t.limits
                    .iter()
                    .cloned()
                    .collect::<std::collections::BTreeMap<_, _>>(),
                t.sha256.clone(),
                t.source.clone(),
            )
        }
        None => {
            let t = LimitsTable::bundled()?;
            (
                t.limits
                    .iter()
                    .cloned()
                    .collect::<std::collections::BTreeMap<_, _>>(),
                t.sha256.clone(),
                t.source.clone(),
            )
        }
    };

    let text = std::str::from_utf8(input_bytes).map_err(|e| format!("input is not UTF-8: {e}"))?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err("input is empty".into());
    }

    // Shape detection: try a whole-document parse first (run result JSON may
    // be pretty-printed over many lines); else treat the file as NDJSON.
    let records: Vec<Value> = if let Ok(doc) = serde_json::from_str::<Value>(trimmed) {
        vec![doc]
    } else if trimmed.starts_with('{') {
        trimmed
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).map_err(|e| format!("input line is not JSON: {e}")))
            .collect::<Result<_, _>>()?
    } else {
        return Err("input is neither a run result JSON nor a mesh NDJSON".into());
    };

    let mut out_lines = Vec::new();
    out_lines.push(json!({
        "record": "header",
        "schema": "actinv-clearance-1",
        "input_sha256": input_sha,
        "step": step,
        "confidence_threshold": confidence,
        "limits": {
            "sha256": table_sha,
            "source": table_source,
            "count": table.len(),
        },
        "probability_model": "per-nuclide activities are Gaussian marginals under the linearized MF33 XS propagation (same coverage class as the emitted bands); P(S<1) is bracketed by the independent and fully-correlated variance rules",
        "clearance_rule": "sum-of-ratios S = sum a_i/L_i < 1 (IAEA unconditional-clearance convention); nuclides absent from the table are excluded from S and reported in unregulated_activity_share",
    }));

    let mut n_cells = 0usize;
    let mut n_clear = 0usize;
    let mut n_fail = 0usize;
    let mut n_indeterminate = 0usize;
    let mut n_deterministic = 0usize;
    for rec in records.iter() {
        let kind = rec.get("record").and_then(Value::as_str);
        let target = match kind {
            Some("cell") => Some(rec),
            Some("header") | Some("correlation") | Some("footer") => None,
            Some(other) => return Err(format!("unexpected mesh record '{other}'")),
            None => {
                if rec.get("steps").is_some() {
                    Some(rec)
                } else {
                    None
                }
            }
        };
        let Some(target) = target else { continue };
        let step_obj = step_from(target, step).map_err(|e| format!("cell {n_cells}: {e}"))?;
        let cell_id = cell_id_of(target, n_cells);
        let mut eval = evaluate_step(step_obj, &table, confidence)?;
        eval["cell_index"] = json!(n_cells);
        eval["cell_id"] = json!(cell_id);
        match eval["classification"].as_str().unwrap_or("") {
            "clears_certified" => n_clear += 1,
            "deterministic_clear" => {
                n_clear += 1;
                n_deterministic += 1;
            }
            "fails_certified" => n_fail += 1,
            "deterministic_fail" => {
                n_fail += 1;
                n_deterministic += 1;
            }
            _ => n_indeterminate += 1,
        }
        n_cells += 1;
        out_lines.push(eval);
    }
    if n_cells == 0 {
        return Err("input carries no cell records or run results".into());
    }

    out_lines.push(json!({
        "record": "footer",
        "cells_evaluated": n_cells,
        "cells_certified_clear": n_clear,
        "cells_certified_fail": n_fail,
        "cells_indeterminate": n_indeterminate,
        "cells_deterministic": n_deterministic,
        "totals_cover": "certifications cover the MF33-propagated channel only; unbanded_ratio_share and unregulated_activity_share mark what the statistic does not cover",
        "wall_time_s": started.elapsed().as_secs_f64(),
    }));

    let mut out = String::new();
    for line in out_lines {
        out.push_str(&serde_json::to_string(&line).map_err(|e| e.to_string())?);
        out.push('\n');
    }
    Ok(out)
}
