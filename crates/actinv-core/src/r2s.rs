//! P52 — `actinv-r2s-source-1` banded decay-photon-source interchange.
//!
//! Pure transformation over an `actinv-mesh-result-1` ndjson stream: for one
//! step, each cell's per-nuclide photon strengths are combined with the
//! nuclide's propagated activity band (fractional standard uncertainty is
//! invariant under the constant photon-yield multiplier) into a banded
//! per-cell source, ledgered under declared combination rules. Nothing here
//! touches solver semantics; the emission is arithmetic on recorded results
//! only, so an independent checker can re-derive every number from the same
//! bytes.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const SCHEMA: &str = "actinv-r2s-source-1";

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn f(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

fn num(v: &Value, key: &str) -> Value {
    f(v, key).map(Value::from).unwrap_or(Value::Null)
}

fn u64v(v: &Value, key: &str) -> u64 {
    v.get(key).and_then(Value::as_u64).unwrap_or(0)
}

/// Fractional standard uncertainty of an activity response: combined across
/// requested channels when present, else the MF=33 band alone. `None` when the
/// response is absent or its nominal is non-positive — never invented.
fn rel_sigma(response: Option<&Value>) -> Option<f64> {
    let r = response?;
    let nominal = f(r, "nominal")?;
    if !(nominal.is_finite() && nominal > 0.0) {
        return None;
    }
    let sigma =
        f(r, "combined_standard_uncertainty").or_else(|| f(r, "mf33_standard_uncertainty"))?;
    if !(sigma.is_finite() && sigma >= 0.0) {
        return None;
    }
    Some(sigma / nominal)
}

/// Emit the `actinv-r2s-source-1` document for `step` over the full bytes of
/// an `actinv-mesh-result-1` stream. `Ok((ndjson_string, summary))`.
pub fn emit_r2s_source(mesh_bytes: &[u8], step: usize) -> Result<(String, Value), String> {
    let mesh_sha = sha256_hex(mesh_bytes);
    let text =
        std::str::from_utf8(mesh_bytes).map_err(|e| format!("mesh result is not UTF-8: {e}"))?;

    let mut out = String::new();
    let mut header_emitted = false;
    let mut cells_seen = 0u64;
    let mut total_photons = 0.0;
    let mut indep_sq = 0.0;
    let mut consv = 0.0;
    let mut partial_cells = 0u64;
    let mut footer_seen = false;

    for (line_no, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let rec: Value = serde_json::from_str(line)
            .map_err(|e| format!("line {}: not JSON: {e}", line_no + 1))?;
        match rec.get("record").and_then(Value::as_str) {
            Some("header") => {
                if header_emitted {
                    return Err("mesh result carries more than one header".into());
                }
                let mut h = Map::new();
                h.insert("record".into(), "header".into());
                h.insert("schema".into(), SCHEMA.into());
                h.insert("mesh_result_sha256".into(), mesh_sha.clone().into());
                h.insert(
                    "spec_fingerprint_sha256".into(),
                    rec.get("spec_fingerprint_sha256")
                        .cloned()
                        .unwrap_or(Value::Null),
                );
                h.insert(
                    "canonical_flux_sha256".into(),
                    rec.pointer("/certificate/canonical_flux/sha256_computed")
                        .cloned()
                        .unwrap_or(Value::Null),
                );
                h.insert("step".into(), step.into());
                h.insert(
                    "band_semantics".into(),
                    serde_json::json!({
                        "method": "first-order propagated band (P43/P44 qualifications)",
                        "sigma_field": "combined_standard_uncertainty | mf33_standard_uncertainty",
                        "applies_to": "source strength only — the emitted group spectrum is nominal",
                        "channels_reflect": "activation-side data only; photon yields carry no published covariance",
                        "combination_rules": {
                            "sigma_independent": "sqrt(sum sigma_i^2) — assumes independent contributions",
                            "sigma_conservative": "sum sigma_i — assumes full positive correlation"
                        }
                    }),
                );
                out.push_str(&serde_json::to_string(&Value::Object(h)).map_err(|e| e.to_string())?);
                out.push('\n');
                header_emitted = true;
            }
            Some("cell") => {
                if !header_emitted {
                    return Err("cell record before header".into());
                }
                let cell =
                    emit_cell(&rec, step).map_err(|e| format!("cell '{}': {e}", cell_id(&rec)))?;
                cells_seen += 1;
                total_photons += f(&cell, "photons_s").unwrap_or(0.0);
                match f(&cell, "sigma_photons_s_independent") {
                    Some(s) => indep_sq += s * s,
                    None => partial_cells += 1,
                }
                if let Some(s) = f(&cell, "sigma_photons_s_conservative") {
                    consv += s;
                }
                out.push_str(&serde_json::to_string(&cell).map_err(|e| e.to_string())?);
                out.push('\n');
            }
            Some("footer") => footer_seen = true,
            Some(other) => {
                return Err(format!(
                    "unknown mesh record '{other}' on line {}",
                    line_no + 1
                ));
            }
            None => return Err(format!("line {}: missing 'record' field", line_no + 1)),
        }
    }
    if !header_emitted {
        return Err("mesh result carries no header".into());
    }
    if !footer_seen {
        return Err("mesh result carries no footer — input is truncated or incomplete".into());
    }

    let footer = serde_json::json!({
        "record": "footer",
        "cell_count": cells_seen,
        "total_photons_s": total_photons,
        "sigma_total_independent": if partial_cells > 0 { Value::Null } else { indep_sq.sqrt().into() },
        "sigma_total_conservative": if partial_cells > 0 { Value::Null } else { consv.into() },
        "cells_partially_unbanded": partial_cells,
    });
    out.push_str(&serde_json::to_string(&footer).map_err(|e| e.to_string())?);
    out.push('\n');

    Ok((
        out,
        serde_json::json!({
            "cells": cells_seen,
            "total_photons_s": total_photons,
            "cells_partially_unbanded": partial_cells,
        }),
    ))
}

fn cell_id(rec: &Value) -> String {
    rec.get("id")
        .and_then(Value::as_str)
        .unwrap_or("<unknown>")
        .to_owned()
}

fn emit_cell(rec: &Value, step: usize) -> Result<Value, String> {
    let result = rec
        .get("result")
        .ok_or("record carries no 'result' object")?;
    let step_out = result
        .get("steps")
        .and_then(Value::as_array)
        .and_then(|s| s.iter().find(|v| u64v(v, "step") == step as u64))
        .ok_or_else(|| format!("result has no step {step}"))?;
    let photon = step_out
        .get("photon_source")
        .ok_or("step carries no photon_source — run mesh with photon emission")?;
    let uq = step_out
        .get("uncertainty")
        .ok_or("step carries no uncertainty report — the interchange requires banded runs")?;
    let responses = uq
        .get("responses")
        .and_then(Value::as_object)
        .ok_or("uncertainty report carries no responses")?;

    let total = f(photon, "total_photons_s").unwrap_or(0.0);
    let mut per_nuclide = Vec::new();
    let mut indep_sq = 0.0;
    let mut consv = 0.0;
    let mut unbanded = 0.0;
    let mut banded_count = 0u64;
    let mut nuc_count = 0u64;

    if let Some(list) = photon.get("by_nuclide").and_then(Value::as_array) {
        for entry in list {
            // A nuclide entry carries per-group bins but no flat total:
            // strength is the sum over its emitted group photons/s.
            let strength: f64 = entry
                .get("groups")
                .and_then(Value::as_array)
                .map(|gs| gs.iter().map(|g| f(g, "photons_s").unwrap_or(0.0)).sum())
                .unwrap_or(0.0);
            if !(strength.is_finite() && strength > 0.0) {
                continue;
            }
            nuc_count += 1;
            let name = entry
                .get("nuclide")
                .and_then(Value::as_str)
                .unwrap_or("<unnamed>")
                .to_owned();
            let response = responses.get(&format!("activity:{name}"));
            let nominal = response.and_then(|r| f(r, "nominal"));
            let rs = rel_sigma(response);
            let sigma = rs.map(|r| strength * r);
            if let Some(s) = sigma {
                banded_count += 1;
                indep_sq += s * s;
                consv += s;
            } else {
                unbanded += strength;
            }
            per_nuclide.push(serde_json::json!({
                "nuclide": name,
                "photons_s": strength,
                "activity_nominal_bq": nominal,
                "rel_sigma": rs,
                "sigma_photons_s": sigma,
            }));
        }
    }

    let partial = nuc_count > 0 && banded_count < nuc_count;
    let unbanded_share = if total > 0.0 { unbanded / total } else { 0.0 };
    let (sig_i, sig_c) = if nuc_count == 0 {
        (Value::from(0.0_f64), Value::from(0.0_f64))
    } else if partial {
        (Value::Null, Value::Null)
    } else {
        (Value::from(indep_sq.sqrt()), Value::from(consv))
    };

    let groups: Vec<Value> = photon
        .get("groups")
        .and_then(Value::as_array)
        .map(|gs| {
            gs.iter()
                .filter(|g| f(g, "photons_s").unwrap_or(0.0) > 0.0)
                .map(|g| {
                    serde_json::json!({
                        "centroid_eV": num(g, "centroid_eV"),
                        "photons_s": num(g, "photons_s"),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let coverage = serde_json::json!({
        "photon_nuclides": nuc_count,
        "banded_nuclides": banded_count,
        "unbanded_photon_share": unbanded_share,
        "uncovered_library_rows": uq.get("uncovered_library_rows")
            .and_then(Value::as_array).map(|v| v.len() as u64).unwrap_or(0),
        "uncovered_decay_constants": uq.get("uncovered_decay_constants")
            .and_then(Value::as_array).cloned().unwrap_or_default(),
        "uncovered_yield_products": uq.get("uncovered_yield_products")
            .and_then(Value::as_array).cloned().unwrap_or_default(),
        "excluded_blocks": uq.get("excluded_blocks")
            .and_then(Value::as_array).map(|v| v.len() as u64).unwrap_or(0),
    });

    Ok(serde_json::json!({
        "record": "cell",
        "ordinal": u64v(rec, "ordinal"),
        "id": cell_id(rec),
        "index": rec.get("index").cloned().unwrap_or(Value::Null),
        "bounds_cm": rec.get("bounds_cm").cloned().unwrap_or(Value::Null),
        "volume_cm3": num(rec, "volume_cm3"),
        "photons_s": total,
        "sigma_photons_s_independent": sig_i,
        "sigma_photons_s_conservative": sig_c,
        "groups": groups,
        "per_nuclide": per_nuclide,
        "coverage": coverage,
        "rebin": rec.get("rebin").cloned().unwrap_or(Value::Null),
        "step_t_s": f(step_out, "t_s"),
        "step_flux": f(step_out, "flux"),
    }))
}
