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
                if let Some(s) = f(&cell, "sigma_photons_s_independent") {
                    indep_sq += s * s;
                }
                if let Some(s) = f(&cell, "sigma_photons_s_conservative") {
                    consv += s;
                }
                if cell
                    .pointer("/coverage/partially_unbanded")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    partial_cells += 1;
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
        "sigma_total_independent": indep_sq.sqrt(),
        "sigma_total_conservative": consv,
        "cells_partially_unbanded": partial_cells,
        "totals_cover": "banded contributions only; unbanded fractions are per-cell in coverage.unbanded_photon_share",
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
    // Cell sigmas cover the *banded* contributions only — a real lower bound
    // — with `partially_unbanded` + `unbanded_photon_share` marking what the
    // sums exclude. `null` only when no banded contribution exists at all.
    let (sig_i, sig_c) = if banded_count == 0 {
        (Value::from(0.0_f64), Value::from(0.0_f64))
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
        "partially_unbanded": partial,
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

// ---------------------------------------------------------------------------
// P53 — `actinv-r2s-joint-1`: correlated spatial dose bands.
//
// The per-cell bands of `actinv-r2s-source-1` are combined under bounding
// rules that pretend cells are independent (or fully correlated). Cells share
// the same underlying MF=33 covariances, so the honest system statement is the
// exact joint variance over the covered parameter set:
//
//     Var(T) = Jᵀ Σ_joint J ,  J_{c,p} = Σ_n (P_{c,n}/A_{c,n}) · s_{c,n,p}
//
// where Σ_joint is the multi-spectrum collapse across the cells' spectra and
// the sum runs over banded photon nuclides. This module streams the sparse
// assembled covariance (exclusions applied identically to the solve path)
// and accumulates the quadratic form and per-cell-pair covariances.
// ---------------------------------------------------------------------------

const JOINT_SCHEMA: &str = "actinv-r2s-joint-1";

/// Per-cell pooled quantities needed for the joint quadratic form:
/// `weights[(row)] = Σ_n (P_n/A_n)·s_{n,row}` accumulated later against the
/// collapse's (spectrum, covered-row) parameter indexing.
struct CellJoint {
    record: Value,
    /// Banded nuclide contributions: (sensitivity entries) already folded
    /// with the photon weight w_n = photons_s/activity_nominal.
    sens: Vec<(usize, f64)>, // (library_row, w_n · s_{n,row})
    photons_s: f64,
}

fn parse_mesh_spec(spec_bytes: &[u8]) -> Result<crate::mesh::MeshSpec, String> {
    serde_json::from_slice(spec_bytes)
        .map_err(|e| format!("mesh spec is not a valid actinv-mesh-1 spec: {e}"))
}

/// Emit the `actinv-r2s-joint-1` document for `step`. `mesh_bytes` is the
/// `actinv-mesh-result-1` stream; `spec_bytes` is the mesh spec, fingerprint-
/// verified against the mesh header so the collapse inputs are bound to the
/// run that produced the records.
pub fn emit_r2s_joint(
    mesh_bytes: &[u8],
    spec_bytes: &[u8],
    step: usize,
) -> Result<(String, Value), String> {
    use crate::flux::{rebin_equal_lethargy, sha256_file, FluxStream};

    let mesh_sha = sha256_hex(mesh_bytes);
    let text =
        std::str::from_utf8(mesh_bytes).map_err(|e| format!("mesh result is not UTF-8: {e}"))?;
    let spec = parse_mesh_spec(spec_bytes)?;
    let spec_fingerprint = spec.fingerprint_sha256()?;
    if spec.self_shielding.is_some() {
        return Err(
            "joint export does not yet support self-shielded runs (row_scale assumed 1.0)".into(),
        );
    }
    let uncertainty = spec
        .uncertainty
        .as_ref()
        .ok_or("joint export requires an uncertainty-banded mesh run")?;

    // ---- binding checks ---------------------------------------------------
    let mut lines = text.lines().filter(|l| !l.trim().is_empty());
    let header_line = lines.next().ok_or("mesh result is empty")?;
    let header: Value =
        serde_json::from_str(header_line).map_err(|e| format!("mesh header is not JSON: {e}"))?;
    if header.get("record").and_then(Value::as_str) != Some("header") {
        return Err("first mesh record is not a header".into());
    }
    if header
        .get("spec_fingerprint_sha256")
        .and_then(Value::as_str)
        != Some(spec_fingerprint.as_str())
    {
        return Err(format!(
            "mesh spec fingerprint mismatch: header records {}, supplied spec computes {}",
            header
                .get("spec_fingerprint_sha256")
                .and_then(Value::as_str)
                .unwrap_or("<none>"),
            spec_fingerprint
        ));
    }
    let activation_boundaries: Vec<f64> = header
        .get("activation_energy_boundaries_eV")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_f64).collect())
        .ok_or("mesh header carries no activation_energy_boundaries_eV")?;
    let flux_declared = header
        .pointer("/certificate/canonical_flux/sha256_computed")
        .and_then(Value::as_str)
        .ok_or("mesh header carries no canonical flux certificate")?;
    let flux_actual = sha256_file(&spec.flux.path)?;
    if !flux_actual.eq_ignore_ascii_case(&spec.flux.sha256) {
        return Err(format!(
            "flux file {} sha256 does not match the spec's declared hash",
            spec.flux.path
        ));
    }
    if !flux_actual.eq_ignore_ascii_case(flux_declared) {
        return Err("flux file does not match the mesh header's canonical_flux certificate".into());
    }
    let lib_sha = sha256_file(&spec.library.path)?;
    if let Some(declared) = &spec.library.sha256 {
        if !lib_sha.eq_ignore_ascii_case(declared) {
            return Err("activation library does not match the spec's declared hash".into());
        }
    }
    let library = actinv_data::library::read_npz(&spec.library.path)?;
    let covariance_ref = uncertainty.covariance.as_ref().ok_or(
        "r2s uncertainty propagation requires uncertainty.covariance (P93 flux-only mode is not supported by r2s)",
    )?;
    let cov_sha = sha256_file(&covariance_ref.path)?;
    if !cov_sha.eq_ignore_ascii_case(&covariance_ref.sha256) {
        return Err("covariance sidecar does not match the spec's declared hash".into());
    }
    let covariance = actinv_data::covariance::read_npz(&covariance_ref.path)?;

    // ---- per-cell spectra (collapse weights), flux-file order -------------
    let mut stream = FluxStream::open(&spec.flux.path)?;
    let mut phis: Vec<Vec<f64>> = Vec::new();
    let source_boundaries = stream.header.energy_boundaries_eV.clone();
    loop {
        let chunk = stream.read_chunk(64)?;
        if chunk.is_empty() {
            break;
        }
        for cell in chunk {
            let rebinned = rebin_equal_lethargy(
                &source_boundaries,
                &cell.flux_per_group,
                &activation_boundaries,
            )
            .map_err(|e| format!("cell '{}': {e}", cell.id))?;
            phis.push(rebinned.flux_per_group);
        }
    }
    stream.finish()?;

    // ---- cell records + pooled sensitivities ------------------------------
    let mut cells: Vec<CellJoint> = Vec::new();
    let mut selected_rows: std::collections::BTreeSet<usize> = std::collections::BTreeSet::new();
    let mut cell_line_no = 0usize;
    for (line_no, line) in text.lines().enumerate().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let rec: Value = serde_json::from_str(line)
            .map_err(|e| format!("line {}: not JSON: {e}", line_no + 1))?;
        match rec.get("record").and_then(Value::as_str) {
            Some("cell") => {
                let emitted =
                    emit_cell(&rec, step).map_err(|e| format!("cell '{}': {e}", cell_id(&rec)))?;
                let step_out = rec
                    .pointer("/result/steps")
                    .and_then(Value::as_array)
                    .and_then(|s| s.iter().find(|v| u64v(v, "step") == step as u64))
                    .ok_or("result has no emit step")?;
                let responses = step_out
                    .pointer("/uncertainty/responses")
                    .and_then(Value::as_object)
                    .ok_or("step carries no uncertainty responses")?;
                let mut sens: Vec<(usize, f64)> = Vec::new();
                if let Some(list) = step_out
                    .pointer("/photon_source/by_nuclide")
                    .and_then(Value::as_array)
                {
                    for entry in list {
                        let strength: f64 = entry
                            .get("groups")
                            .and_then(Value::as_array)
                            .map(|gs| gs.iter().map(|g| f(g, "photons_s").unwrap_or(0.0)).sum())
                            .unwrap_or(0.0);
                        if !(strength.is_finite() && strength > 0.0) {
                            continue;
                        }
                        let name = entry
                            .get("nuclide")
                            .and_then(Value::as_str)
                            .unwrap_or("<unnamed>");
                        let Some(response) = responses.get(&format!("activity:{name}")) else {
                            continue; // unbanded nuclide — coverage ledger, no J weight
                        };
                        let Some(activity) = f(response, "nominal") else {
                            continue;
                        };
                        if !(activity.is_finite() && activity > 0.0) {
                            continue;
                        }
                        let weight = strength / activity;
                        if let Some(entries) =
                            response.get("sensitivities").and_then(Value::as_array)
                        {
                            for s in entries {
                                let param = s.get("parameter").unwrap_or(&Value::Null);
                                let spectrum =
                                    param.get("spectrum").and_then(Value::as_u64).unwrap_or(0);
                                if spectrum != 0 {
                                    return Err(format!(
                                        "cell '{}': multi-spectrum sensitivity records are not \
                                         supported by joint export (parameter spectrum {spectrum})",
                                        cell_id(&rec)
                                    ));
                                }
                                let Some(row) = param.get("library_row").and_then(Value::as_u64)
                                else {
                                    continue;
                                };
                                if param
                                    .get("covariance_covered")
                                    .and_then(Value::as_bool)
                                    .unwrap_or(false)
                                {
                                    selected_rows.insert(row as usize);
                                }
                                let value = f(s, "value").unwrap_or(0.0);
                                if value != 0.0 {
                                    sens.push((row as usize, weight * value));
                                }
                            }
                        }
                    }
                }
                cells.push(CellJoint {
                    photons_s: f(&emitted, "photons_s").unwrap_or(0.0),
                    record: emitted,
                    sens,
                });
                cell_line_no += 1;
            }
            Some("footer") => break,
            Some(other) => {
                return Err(format!(
                    "unexpected mesh record '{other}' on line {}",
                    line_no + 1
                ))
            }
            None => return Err(format!("line {}: missing 'record' field", line_no + 1)),
        }
    }
    if cells.len() != phis.len() {
        return Err(format!(
            "mesh carries {} cell records but the flux file carries {} cells",
            cells.len(),
            phis.len()
        ));
    }
    let _ = cell_line_no;

    // ---- joint collapse + quadratic forms ---------------------------------
    let phi_refs: Vec<&[f64]> = phis.iter().map(Vec::as_slice).collect();
    let selected: Vec<usize> = selected_rows.into_iter().collect();
    let sparse =
        covariance.collapse_sparse_weighted_multi(&library, &phi_refs, &selected, &|_, _| 1.0)?;
    let n_covered = sparse
        .row_indices
        .len()
        .checked_div(phis.len())
        .unwrap_or(0);
    // row -> covered position (same ordering for every spectrum)
    let mut row_pos: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    for (i, &row) in sparse.row_indices.iter().enumerate() {
        if sparse.param_spectrum[i] == 0 {
            row_pos.insert(row, i);
        }
    }
    let n_cells = cells.len();
    let mut j = vec![0.0f64; n_covered * n_cells];
    for (c, cell) in cells.iter().enumerate() {
        let base = c * n_covered;
        for &(row, w) in &cell.sens {
            if let Some(&pos) = row_pos.get(&row) {
                j[base + pos] += w;
            }
        }
    }
    // Bucket the quadratic form by (spectrum, spectrum) → per-cell-pair
    // covariance of the photon totals; symmetric entries both stored.
    let mut cov: std::collections::BTreeMap<(usize, usize), f64> =
        std::collections::BTreeMap::new();
    for (&(l, r), &v) in &sparse.entries {
        let (sl, sr) = (sparse.param_spectrum[l], sparse.param_spectrum[r]);
        *cov.entry((sl, sr)).or_insert(0.0) += j[l] * v * j[r];
    }
    let mut var_total = 0.0f64;
    let mut cell_var = vec![0.0f64; n_cells];
    for (c, var) in cell_var.iter_mut().enumerate() {
        for c2 in 0..n_cells {
            let v = *cov.get(&(c, c2)).unwrap_or(&0.0);
            var_total += v;
            if c == c2 {
                *var = v;
            }
        }
    }
    let cell_sigma: Vec<f64> = cell_var.iter().map(|v| v.max(0.0).sqrt()).collect();
    let sigma_correlated = var_total.max(0.0).sqrt();
    let rho: Vec<Value> = (0..n_cells)
        .map(|c| {
            (0..n_cells)
                .map(|c2| {
                    if c == c2 {
                        Value::from(if cell_sigma[c] > 0.0 { 1.0 } else { 0.0 })
                    } else if cell_sigma[c] > 0.0 && cell_sigma[c2] > 0.0 {
                        Value::from(
                            *cov.get(&(c, c2)).unwrap_or(&0.0) / (cell_sigma[c] * cell_sigma[c2]),
                        )
                    } else {
                        Value::Null
                    }
                })
                .collect::<Vec<_>>()
        })
        .map(Value::from)
        .collect();

    // ---- emit -------------------------------------------------------------
    let mut out = String::new();
    out.push_str(&serde_json::to_string(&serde_json::json!({
        "record": "header",
        "schema": JOINT_SCHEMA,
        "mesh_result_sha256": mesh_sha,
        "spec_fingerprint_sha256": spec_fingerprint,
        "canonical_flux_sha256": flux_actual,
        "activation_library_sha256": lib_sha,
        "covariance_sha256": cov_sha,
        "step": step,
        "scope": {
            "channel": "cross_section_mf33 — the propagated channel per-cell bands cover",
            "joint_sensitivity": "J_{c,p} = sum_n (P_n/A_n)·s_{n,p} over banded photon nuclides",
            "sigma_correlated": "exact joint variance over covered MF33 parameters; not a bound — may fall below sigma_independent where cross-cell covariances are negative",
            "exclusions": "P20 block rules applied identically to the solve path",
            "self_shielding": "unsupported — export errors if the spec declares it",
        },
        "combination_rules": {
            "sigma_independent": "sqrt(sum sigma_i^2) — P52 bound, cells treated independent",
            "sigma_conservative": "sum sigma_i — P52 bound, full positive correlation",
            "sigma_correlated": "sqrt(J^T Sigma_joint J) — exact propagated band over covered parameters",
        }
    })).map_err(|e| e.to_string())?);
    out.push('\n');
    let mut total_photons = 0.0;
    let mut indep_sq = 0.0;
    let mut consv = 0.0;
    let mut partial_cells = 0u64;
    for (c, cell) in cells.iter().enumerate() {
        let mut record = cell.record.clone();
        record.as_object_mut().expect("cell record").insert(
            "sigma_photons_s_correlated".into(),
            Value::from(cell_sigma[c]),
        );
        out.push_str(&serde_json::to_string(&record).map_err(|e| e.to_string())?);
        out.push('\n');
        total_photons += cell.photons_s;
        if let Some(s) = f(&record, "sigma_photons_s_independent") {
            indep_sq += s * s;
        }
        if let Some(s) = f(&record, "sigma_photons_s_conservative") {
            consv += s;
        }
        if record
            .pointer("/coverage/partially_unbanded")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            partial_cells += 1;
        }
    }
    let excluded_block_keys: Vec<Value> = sparse
        .excluded_blocks
        .iter()
        .map(|b| {
            serde_json::json!({
                "target": b.target,
                "mt": b.mt,
                "mt1": b.mt1,
                "reason": b.reason,
                "measured_defect": b.measured_defect,
            })
        })
        .collect();
    out.push_str(
        &serde_json::to_string(&serde_json::json!({
            "record": "correlation",
            "cell_count": n_cells,
            "cell_sigma_correlated": cell_sigma,
            "rho": rho,
            "excluded_block_keys": excluded_block_keys,
        }))
        .map_err(|e| e.to_string())?,
    );
    out.push('\n');
    out.push_str(&serde_json::to_string(&serde_json::json!({
        "record": "footer",
        "cell_count": n_cells,
        "total_photons_s": total_photons,
        "sigma_total_independent": indep_sq.sqrt(),
        "sigma_total_conservative": consv,
        "sigma_total_correlated": sigma_correlated,
        "cells_partially_unbanded": partial_cells,
        "uncovered_rows": sparse.uncovered_rows.len(),
        "excluded_blocks": sparse.excluded_blocks.len(),
        "totals_cover": "banded contributions only; unbanded fractions are per-cell in coverage.unbanded_photon_share; correlated sigma covers the MF33 channel",
    }))
    .map_err(|e| e.to_string())?);
    out.push('\n');

    Ok((
        out,
        serde_json::json!({
            "cells": n_cells,
            "total_photons_s": total_photons,
            "sigma_total_correlated": sigma_correlated,
            "excluded_blocks": sparse.excluded_blocks.len(),
        }),
    ))
}

// ---------------------------------------------------------------------------
// A1 — `actinv-r2s-cov-1`: the photon-source covariance in factored form.
//
// Over (region c, photon group g) the first-order source covariance is
//
//     C_S = sum_k L_k L_k^T  +  blockdiag_c( sum_j M_{c,j} M_{c,j}^T )
//
// with `dS_{c,g}/dp = sum_n (P_{c,n,g}/A_{c,n}) · dA_{c,n}/dp` (the photon
// yields per decay are constants, so a nuclide's group spectrum scales with
// its activity). Shared columns `L_k` carry the nuclear-data channels, which
// every region sees; independent columns `M_{c,j}` carry each region's own
// flux-tally noise. A consumer forms, for any dose response R[c][g],
//
//     Var(D) = R^T C_S R + sum_c S_c^2 sigma_{R,c}^2 .
//
// Nothing here is new physics: the sensitivities are the ones the mesh run
// already recorded per activity response.
// ---------------------------------------------------------------------------

use actinv_data::covariance::SparseJointCollapse;
use std::collections::{BTreeMap, BTreeSet};

const COV_SCHEMA: &str = "actinv-r2s-cov-1";
/// Largest dense (region, group) block the MF=33 factorization will form.
const COV_DENSE_CAP: usize = 5000;
/// Pivoted-Cholesky stops when the largest remaining diagonal falls below
/// this fraction of the largest initial one; the dropped trace is reported.
const COV_RANK_TOL: f64 = 1.0e-12;

/// One region's source and scaled sensitivity vectors over the photon groups.
struct CovCell {
    id: String,
    ordinal: u64,
    index: Value,
    volume_cm3: Value,
    t_s: Option<f64>,
    boundaries: Vec<f64>,
    /// S[g]: photons/s of the whole region in each photon group.
    mean: Vec<f64>,
    /// Source in nuclides carrying no usable activity response.
    unbanded_photons_s: f64,
    /// library row -> dS[g]/d(one-group barns) for covariance-covered rows.
    mf33: BTreeMap<usize, Vec<f64>>,
    /// decay constant of nuclide -> dS[g]/dlambda · sigma_lambda.
    decay: BTreeMap<String, Vec<f64>>,
    /// "parent>product" -> dS[g]/dy · sigma_y.
    yields: BTreeMap<String, Vec<f64>>,
    /// source neutron group j -> dS[g]/dln(phi_j) · e_j.
    flux: BTreeMap<usize, Vec<f64>>,
}

fn group_rates(groups: Option<&Value>) -> Vec<f64> {
    groups
        .and_then(Value::as_array)
        .map(|gs| {
            gs.iter()
                .map(|g| f(g, "photons_s").unwrap_or(0.0))
                .collect()
        })
        .unwrap_or_default()
}

fn axpy(dst: &mut [f64], w: &[f64], k: f64) {
    for (d, x) in dst.iter_mut().zip(w) {
        *d += x * k;
    }
}

fn response_entries<'a>(response: &'a Value, key: &str) -> &'a [Value] {
    response
        .get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

/// Pivoted Cholesky of a symmetric positive-semidefinite `n x n` row-major
/// matrix: columns `L_k` (each length `n`) with `sum_k L_k L_k^T ~= a`, and
/// the fraction of the trace left unfactored.
fn psd_factor(a: &[f64], n: usize, rel_tol: f64) -> (Vec<Vec<f64>>, f64) {
    let mut d: Vec<f64> = (0..n).map(|i| a[i * n + i]).collect();
    let trace: f64 = d.iter().map(|v| v.max(0.0)).sum();
    let max0 = d.iter().copied().fold(0.0_f64, f64::max);
    let mut done = vec![false; n];
    let mut cols: Vec<Vec<f64>> = Vec::new();
    while cols.len() < n {
        let mut pivot = usize::MAX;
        let mut best = 0.0_f64;
        for (i, &v) in d.iter().enumerate() {
            if !done[i] && v > best {
                best = v;
                pivot = i;
            }
        }
        if pivot == usize::MAX || best <= rel_tol * max0 {
            break;
        }
        let root = best.sqrt();
        let mut col = vec![0.0; n];
        for (i, ci) in col.iter_mut().enumerate() {
            let mut s = a[i * n + pivot];
            for prev in &cols {
                s -= prev[i] * prev[pivot];
            }
            *ci = s / root;
        }
        for (di, ci) in d.iter_mut().zip(&col) {
            *di -= ci * ci;
        }
        done[pivot] = true;
        cols.push(col);
    }
    let rest: f64 = (0..n).filter(|&i| !done[i]).map(|i| d[i].max(0.0)).sum();
    (cols, if trace > 0.0 { rest / trace } else { 0.0 })
}

/// Read one mesh cell record at `step` into scaled sensitivity vectors.
fn parse_cov_cell(rec: &Value, step: usize) -> Result<CovCell, String> {
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
    let responses = step_out
        .pointer("/uncertainty/responses")
        .and_then(Value::as_object)
        .ok_or(
            "step carries no uncertainty responses — the covariance export requires banded runs",
        )?;
    let groups = photon
        .get("groups")
        .and_then(Value::as_array)
        .filter(|g| !g.is_empty())
        .ok_or("photon_source carries no groups")?;
    let mut boundaries: Vec<f64> = groups.iter().filter_map(|g| f(g, "low_eV")).collect();
    if let Some(high) = groups.last().and_then(|g| f(g, "high_eV")) {
        boundaries.push(high);
    }
    if boundaries.len() != groups.len() + 1 {
        return Err("photon groups carry no low_eV/high_eV bounds".into());
    }
    let ng = groups.len();
    let mean = group_rates(photon.get("groups"));

    let mut cell = CovCell {
        id: cell_id(rec),
        ordinal: u64v(rec, "ordinal"),
        index: rec.get("index").cloned().unwrap_or(Value::Null),
        volume_cm3: num(rec, "volume_cm3"),
        t_s: f(step_out, "t_s"),
        boundaries,
        mean,
        unbanded_photons_s: 0.0,
        mf33: BTreeMap::new(),
        decay: BTreeMap::new(),
        yields: BTreeMap::new(),
        flux: BTreeMap::new(),
    };
    let Some(list) = photon.get("by_nuclide").and_then(Value::as_array) else {
        return Ok(cell);
    };
    for entry in list {
        let rates = group_rates(entry.get("groups"));
        let strength: f64 = rates.iter().sum();
        if !(strength.is_finite() && strength > 0.0) {
            continue;
        }
        let name = entry
            .get("nuclide")
            .and_then(Value::as_str)
            .unwrap_or("<unnamed>");
        if rates.len() != ng {
            return Err(format!(
                "nuclide '{name}' carries {} photon groups, the aggregate carries {ng}",
                rates.len()
            ));
        }
        let response = responses.get(&format!("activity:{name}"));
        let usable = response.and_then(|r| {
            f(r, "nominal")
                .filter(|a| a.is_finite() && *a > 0.0)
                .map(|a| (r, a))
        });
        let Some((response, activity)) = usable else {
            cell.unbanded_photons_s += strength;
            continue;
        };
        // dS_g/dp = (P_g / A) · dA/dp for every channel below.
        let w: Vec<f64> = rates.iter().map(|p| p / activity).collect();

        for s in response_entries(response, "sensitivities") {
            let param = s.get("parameter").unwrap_or(&Value::Null);
            if u64v(param, "spectrum") != 0 {
                return Err(format!(
                    "cell '{}': multi-spectrum sensitivity records are not supported by the \
                     covariance export (parameter spectrum {})",
                    cell.id,
                    u64v(param, "spectrum")
                ));
            }
            let Some(row) = param.get("library_row").and_then(Value::as_u64) else {
                continue;
            };
            if !param
                .get("covariance_covered")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                continue;
            }
            let value = f(s, "value").unwrap_or(0.0);
            if value != 0.0 {
                let col = cell
                    .mf33
                    .entry(row as usize)
                    .or_insert_with(|| vec![0.0; ng]);
                axpy(col, &w, value);
            }
        }
        for s in response_entries(response, "decay_sensitivities") {
            let param = s.get("parameter").unwrap_or(&Value::Null);
            let sigma = f(param, "standard_uncertainty_s").unwrap_or(0.0);
            let value = f(s, "value").unwrap_or(0.0);
            if sigma > 0.0 && value != 0.0 {
                let key = param
                    .get("nuclide")
                    .and_then(Value::as_str)
                    .unwrap_or("<unnamed>")
                    .to_owned();
                let col = cell.decay.entry(key).or_insert_with(|| vec![0.0; ng]);
                axpy(col, &w, value * sigma);
            }
        }
        for s in response_entries(response, "yield_sensitivities") {
            let param = s.get("parameter").unwrap_or(&Value::Null);
            if u64v(param, "spectrum") != 0 {
                return Err(format!(
                    "cell '{}': multi-spectrum yield sensitivities are not supported by the \
                     covariance export",
                    cell.id
                ));
            }
            let sigma = f(param, "standard_uncertainty").unwrap_or(0.0);
            let value = f(s, "value").unwrap_or(0.0);
            if sigma > 0.0 && value != 0.0 {
                let key = format!(
                    "{}>{}",
                    param
                        .get("parent_nuclide")
                        .and_then(Value::as_str)
                        .unwrap_or("<unnamed>"),
                    param
                        .get("product_nuclide")
                        .and_then(Value::as_str)
                        .unwrap_or("<unnamed>")
                );
                let col = cell.yields.entry(key).or_insert_with(|| vec![0.0; ng]);
                axpy(col, &w, value * sigma);
            }
        }
        for s in response_entries(response, "flux_sensitivities") {
            let param = s.get("parameter").unwrap_or(&Value::Null);
            if u64v(param, "spectrum") != 0 {
                continue; // P93 propagates the base spectrum only
            }
            let e = f(param, "standard_uncertainty_relative").unwrap_or(0.0);
            let value = f(s, "value").unwrap_or(0.0);
            if e > 0.0 && value != 0.0 {
                let col = cell
                    .flux
                    .entry(u64v(param, "group") as usize)
                    .or_insert_with(|| vec![0.0; ng]);
                axpy(col, &w, value * e);
            }
        }
    }
    Ok(cell)
}

/// Stream the cell records of a mesh result (header first, footer last).
fn parse_cov_cells(text: &str, step: usize) -> Result<(Value, Vec<CovCell>), String> {
    let mut lines = text.lines().filter(|l| !l.trim().is_empty());
    let header_line = lines.next().ok_or("mesh result is empty")?;
    let header: Value =
        serde_json::from_str(header_line).map_err(|e| format!("mesh header is not JSON: {e}"))?;
    if header.get("record").and_then(Value::as_str) != Some("header") {
        return Err("first mesh record is not a header".into());
    }
    let mut cells = Vec::new();
    let mut footer_seen = false;
    for line in lines {
        let rec: Value =
            serde_json::from_str(line).map_err(|e| format!("mesh record is not JSON: {e}"))?;
        match rec.get("record").and_then(Value::as_str) {
            Some("cell") => cells.push(
                parse_cov_cell(&rec, step).map_err(|e| format!("cell '{}': {e}", cell_id(&rec)))?,
            ),
            Some("footer") => {
                footer_seen = true;
                break;
            }
            Some(other) => return Err(format!("unexpected mesh record '{other}'")),
            None => return Err("mesh record is missing its 'record' field".into()),
        }
    }
    if !footer_seen {
        return Err("mesh result carries no footer — input is truncated or incomplete".into());
    }
    if cells.is_empty() {
        return Err("mesh result carries no cell records".into());
    }
    Ok((header, cells))
}

/// Shared MF=33 columns: factor `G Σ G^T` over the active (region, group)
/// pairs, where `Σ` is the joint one-group covariance of the covered rows
/// (exclusions already applied). Returns columns laid out `[cell * ng + g]`,
/// the unfactored trace fraction and the dense order used.
fn mf33_shared_columns(
    cells: &[CovCell],
    ng: usize,
    sparse: &SparseJointCollapse,
) -> Result<(Vec<Vec<f64>>, f64, usize), String> {
    let n_cells = cells.len();
    let mut slot = vec![usize::MAX; n_cells * ng];
    let mut active = 0usize;
    for (c, cell) in cells.iter().enumerate() {
        for col in cell.mf33.values() {
            for (g, &x) in col.iter().enumerate() {
                if x != 0.0 && slot[c * ng + g] == usize::MAX {
                    slot[c * ng + g] = active;
                    active += 1;
                }
            }
        }
    }
    if active == 0 {
        return Ok((Vec::new(), 0.0, 0));
    }
    if active > COV_DENSE_CAP {
        return Err(format!(
            "MF=33 factorization needs a dense {active} x {active} block; the cap is \
             {COV_DENSE_CAP} (regions x photon groups with nonzero sensitivity)"
        ));
    }
    let mut dense = vec![0.0f64; active * active];
    for (&(l, r), &v) in &sparse.entries {
        let (Some(&cl), Some(&cr)) = (sparse.param_spectrum.get(l), sparse.param_spectrum.get(r))
        else {
            continue;
        };
        if cl >= n_cells || cr >= n_cells {
            continue;
        }
        let (Some(al), Some(ar)) = (
            cells[cl].mf33.get(&sparse.row_indices[l]),
            cells[cr].mf33.get(&sparse.row_indices[r]),
        ) else {
            continue;
        };
        for (g, &x) in al.iter().enumerate() {
            if x == 0.0 {
                continue;
            }
            let a = slot[cl * ng + g];
            for (g2, &y) in ar.iter().enumerate() {
                if y == 0.0 {
                    continue;
                }
                dense[a * active + slot[cr * ng + g2]] += x * v * y;
            }
        }
    }
    // The assembled map stores both triangles; symmetrize against round-off.
    for a in 0..active {
        for b in (a + 1)..active {
            let m = 0.5 * (dense[a * active + b] + dense[b * active + a]);
            dense[a * active + b] = m;
            dense[b * active + a] = m;
        }
    }
    if dense.iter().any(|v| !v.is_finite()) {
        return Err("MF=33 source covariance block is not finite".into());
    }
    let (factors, truncated) = psd_factor(&dense, active, COV_RANK_TOL);
    let columns = factors
        .into_iter()
        .map(|small| {
            let mut full = vec![0.0; n_cells * ng];
            for (i, &s) in slot.iter().enumerate() {
                if s != usize::MAX {
                    full[i] = small[s];
                }
            }
            full
        })
        .collect();
    Ok((columns, truncated, active))
}

fn rows_of(flat: &[f64], ng: usize) -> Vec<Vec<f64>> {
    flat.chunks(ng).map(<[f64]>::to_vec).collect()
}

fn to_line(v: &Value) -> Result<String, String> {
    let mut s = serde_json::to_string(v).map_err(|e| e.to_string())?;
    s.push('\n');
    Ok(s)
}

/// Assemble the document from parsed cells. `requested` names the propagated
/// channels; `sparse` is the joint MF=33 collapse when that channel is on.
fn build_cov_document(
    cells: &[CovCell],
    step: usize,
    mut header: Map<String, Value>,
    requested: &[&str],
    sparse: Option<&SparseJointCollapse>,
) -> Result<(String, Value), String> {
    let n_cells = cells.len();
    let first = cells.first().ok_or("no cells")?;
    let ng = first.mean.len();
    for cell in cells {
        if cell.boundaries != first.boundaries {
            return Err(format!(
                "cell '{}' has a photon group structure different from cell '{}'",
                cell.id, first.id
            ));
        }
    }
    let on = |name: &str| requested.contains(&name);

    // ---- shared columns ---------------------------------------------------
    let mut shared: Vec<(String, &'static str, Vec<f64>)> = Vec::new();
    let mut mf33_info = Value::Null;
    if on("cross_section_mf33") {
        let sparse = sparse.ok_or("MF=33 requested without a covariance collapse")?;
        let (cols, truncated, dense_order) = mf33_shared_columns(cells, ng, sparse)?;
        mf33_info = serde_json::json!({
            "representation": "pivoted-Cholesky factor of G Sigma G^T over the active (region, group) pairs; columns are uncorrelated",
            "dense_order": dense_order,
            "rank": cols.len(),
            "truncated_trace_fraction": truncated,
            "rank_tolerance": COV_RANK_TOL,
            "uncovered_rows": sparse.uncovered_rows.len(),
            "excluded_blocks": sparse.excluded_blocks.len(),
        });
        for (k, col) in cols.into_iter().enumerate() {
            shared.push((format!("mf33:factor:{k}"), "cross_section_mf33", col));
        }
    }
    let mut shared_param_counts = (0usize, 0usize);
    for (on_channel, channel, tag) in [
        (on("decay_constants"), "decay_constants", "decay"),
        (on("fission_yields"), "fission_yields", "yield"),
    ] {
        if !on_channel {
            continue;
        }
        let mut by_key: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        for (c, cell) in cells.iter().enumerate() {
            let source = if tag == "decay" {
                &cell.decay
            } else {
                &cell.yields
            };
            for (key, col) in source {
                let dst = by_key
                    .entry(key.clone())
                    .or_insert_with(|| vec![0.0; n_cells * ng]);
                dst[c * ng..(c + 1) * ng].copy_from_slice(col);
            }
        }
        if tag == "decay" {
            shared_param_counts.0 = by_key.len();
        } else {
            shared_param_counts.1 = by_key.len();
        }
        for (key, col) in by_key {
            shared.push((format!("{tag}:{key}"), channel, col));
        }
    }

    // ---- independent per-region flux blocks -------------------------------
    struct Indep {
        columns: Vec<Vec<f64>>,
        neutron_groups: usize,
        truncated: f64,
    }
    let mut indep: Vec<Indep> = Vec::with_capacity(n_cells);
    if on("flux") {
        for cell in cells {
            let mut block = vec![0.0f64; ng * ng];
            for m in cell.flux.values() {
                for (a, &x) in m.iter().enumerate() {
                    if x == 0.0 {
                        continue;
                    }
                    for (b, &y) in m.iter().enumerate() {
                        block[a * ng + b] += x * y;
                    }
                }
            }
            let (columns, truncated) = psd_factor(&block, ng, COV_RANK_TOL);
            indep.push(Indep {
                columns,
                neutron_groups: cell.flux.len(),
                truncated,
            });
        }
    }

    // ---- totals (a cheap self-check for consumers) -------------------------
    let total_photons: f64 = cells.iter().map(|c| c.mean.iter().sum::<f64>()).sum();
    let var_shared: f64 = shared
        .iter()
        .map(|(_, _, col)| col.iter().sum::<f64>().powi(2))
        .sum();
    let var_indep: f64 = indep
        .iter()
        .flat_map(|i| i.columns.iter())
        .map(|col| col.iter().sum::<f64>().powi(2))
        .sum();

    // ---- channel ledger ----------------------------------------------------
    let mut channels: Vec<Value> = Vec::new();
    let mut missing: Vec<Value> = Vec::new();
    for name in [
        "cross_section_mf33",
        "decay_constants",
        "fission_yields",
        "flux",
    ] {
        if !on(name) {
            missing.push(serde_json::json!({
                "channel": name,
                "reason": "not requested in the mesh spec's uncertainty.channels",
            }));
            continue;
        }
        let entry = match name {
            "cross_section_mf33" => serde_json::json!({
                "channel": name, "block": "shared", "detail": mf33_info,
            }),
            "decay_constants" => serde_json::json!({
                "channel": name, "block": "shared",
                "parameters": shared_param_counts.0,
                "representation": "one column per radioactive nuclide with a half-life uncertainty (diagonal)",
            }),
            "fission_yields" => serde_json::json!({
                "channel": name, "block": "shared",
                "parameters": shared_param_counts.1,
                "representation": "one column per (parent, product) independent yield with a DY uncertainty (diagonal)",
            }),
            _ => serde_json::json!({
                "channel": name, "block": "independent",
                "representation": "per region, pivoted-Cholesky factor of the photon-group block sum_j M_j M_j^T over the source neutron groups j (independent between groups and regions)",
                "regions_with_sensitivities": cells.iter().filter(|c| !c.flux.is_empty()).count(),
            }),
        };
        channels.push(entry);
    }
    missing.push(serde_json::json!({
        "channel": "photon_emission_data",
        "reason": "photon yields per decay carry no published covariance; the group spectrum of each nuclide is nominal",
    }));

    let regions: Vec<Value> = cells
        .iter()
        .map(|c| {
            serde_json::json!({
                "ordinal": c.ordinal, "id": c.id, "index": c.index, "volume_cm3": c.volume_cm3,
            })
        })
        .collect();

    header.insert("record".into(), "header".into());
    header.insert("schema".into(), COV_SCHEMA.into());
    header.insert("step".into(), step.into());
    header.insert("step_t_s".into(), Value::from(first.t_s));
    header.insert("n_regions".into(), n_cells.into());
    header.insert("n_groups".into(), ng.into());
    header.insert("group_bounds_eV".into(), first.boundaries.clone().into());
    header.insert(
        "units".into(),
        serde_json::json!({
            "mean": "photons/s of the whole region per photon group (export-r2s groups[].photons_s)",
            "shared_columns": "photons/s; one-sigma shift of the group source per unit standard deviation of the column's parameter",
            "independent_columns": "photons/s; same convention, per region",
        }),
    );
    header.insert(
        "covariance_form".into(),
        serde_json::json!({
            "C_S": "sum_k L_k L_k^T + blockdiag_c(sum_j M_{c,j} M_{c,j}^T); L_k[c][g] from 'shared' records, M_{c,j}[g] from 'independent' records",
            "dose_variance": "Var(D) = R^T C_S R + sum_c S_c^2 sigma_{R,c}^2, R[c][g] the dose per source photon born in region c, group g",
            "sensitivity": "dS[c][g]/dp = sum_n (P_{c,n,g}/A_{c,n}) dA_{c,n}/dp, first order, photon yields per decay held constant",
        }),
    );
    header.insert("channels".into(), channels.into());
    header.insert("channels_missing".into(), missing.into());

    let mut out = String::new();
    out.push_str(&to_line(&Value::Object(header))?);
    let mean_rows: Vec<Vec<f64>> = cells.iter().map(|c| c.mean.clone()).collect();
    let unbanded: Vec<f64> = cells.iter().map(|c| c.unbanded_photons_s).collect();
    out.push_str(&to_line(&serde_json::json!({
        "record": "mean",
        "regions": regions,
        "S": mean_rows,
        "unbanded_photons_s": unbanded,
    }))?);
    for (label, channel, col) in &shared {
        out.push_str(&to_line(&serde_json::json!({
            "record": "shared",
            "label": label,
            "channel": channel,
            "column": rows_of(col, ng),
        }))?);
    }
    for (cell, block) in cells.iter().zip(&indep) {
        out.push_str(&to_line(&serde_json::json!({
            "record": "independent",
            "ordinal": cell.ordinal,
            "id": cell.id,
            "channel": "flux",
            "neutron_groups_used": block.neutron_groups,
            "truncated_trace_fraction": block.truncated,
            "columns": block.columns,
        }))?);
    }
    let indep_columns: usize = indep.iter().map(|i| i.columns.len()).sum();
    out.push_str(&to_line(&serde_json::json!({
        "record": "footer",
        "n_regions": n_cells,
        "shared_columns": shared.len(),
        "independent_columns": indep_columns,
        "total_photons_s": total_photons,
        "sigma_total_shared": var_shared.sqrt(),
        "sigma_total_independent": var_indep.sqrt(),
        "totals_cover": "banded nuclides only; unbanded source is listed per region in the mean record",
    }))?);

    Ok((
        out,
        serde_json::json!({
            "regions": n_cells,
            "groups": ng,
            "shared_columns": shared.len(),
            "total_photons_s": total_photons,
            "sigma_total_shared": var_shared.sqrt(),
            "sigma_total_independent": var_indep.sqrt(),
        }),
    ))
}

/// Emit the `actinv-r2s-cov-1` document for `step`: the factored photon-source
/// covariance over (region, photon group). `mesh_bytes` is the
/// `actinv-mesh-result-1` stream; `spec_bytes` the mesh spec it was run from,
/// fingerprint-verified against the mesh header.
pub fn emit_r2s_cov(
    mesh_bytes: &[u8],
    spec_bytes: &[u8],
    step: usize,
) -> Result<(String, Value), String> {
    use crate::flux::{rebin_equal_lethargy, sha256_file, FluxStream};

    let mesh_sha = sha256_hex(mesh_bytes);
    let text =
        std::str::from_utf8(mesh_bytes).map_err(|e| format!("mesh result is not UTF-8: {e}"))?;
    let spec = parse_mesh_spec(spec_bytes)?;
    let spec_fingerprint = spec.fingerprint_sha256()?;
    if spec.self_shielding.is_some() {
        return Err(
            "covariance export does not yet support self-shielded runs (row_scale assumed 1.0)"
                .into(),
        );
    }
    let uncertainty = spec
        .uncertainty
        .as_ref()
        .ok_or("covariance export requires an uncertainty-banded mesh run")?;
    let (header, cells) = parse_cov_cells(text, step)?;
    if header
        .get("spec_fingerprint_sha256")
        .and_then(Value::as_str)
        != Some(spec_fingerprint.as_str())
    {
        return Err(format!(
            "mesh spec fingerprint mismatch: header records {}, supplied spec computes {}",
            header
                .get("spec_fingerprint_sha256")
                .and_then(Value::as_str)
                .unwrap_or("<none>"),
            spec_fingerprint
        ));
    }
    let flux_declared = header
        .pointer("/certificate/canonical_flux/sha256_computed")
        .and_then(Value::as_str)
        .ok_or("mesh header carries no canonical flux certificate")?
        .to_owned();

    let channel_list = &uncertainty.channels;
    let wanted = |name: &str| -> bool {
        (channel_list.is_empty() && name == "cross_section_mf33")
            || channel_list.iter().any(|c| c == name)
    };
    let requested: Vec<&str> = [
        "cross_section_mf33",
        "decay_constants",
        "fission_yields",
        "flux",
    ]
    .into_iter()
    .filter(|name| wanted(name))
    .collect();

    let mut base = Map::new();
    base.insert("mesh_result_sha256".into(), mesh_sha.into());
    base.insert("spec_fingerprint_sha256".into(), spec_fingerprint.into());
    base.insert("canonical_flux_sha256".into(), flux_declared.clone().into());

    let mut sparse: Option<SparseJointCollapse> = None;
    if requested.contains(&"cross_section_mf33") {
        let activation_boundaries: Vec<f64> = header
            .get("activation_energy_boundaries_eV")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_f64).collect())
            .ok_or("mesh header carries no activation_energy_boundaries_eV")?;
        let flux_actual = sha256_file(&spec.flux.path)?;
        if !flux_actual.eq_ignore_ascii_case(&spec.flux.sha256) {
            return Err(format!(
                "flux file {} sha256 does not match the spec's declared hash",
                spec.flux.path
            ));
        }
        if !flux_actual.eq_ignore_ascii_case(&flux_declared) {
            return Err(
                "flux file does not match the mesh header's canonical_flux certificate".into(),
            );
        }
        let lib_sha = sha256_file(&spec.library.path)?;
        if let Some(declared) = &spec.library.sha256 {
            if !lib_sha.eq_ignore_ascii_case(declared) {
                return Err("activation library does not match the spec's declared hash".into());
            }
        }
        let library = actinv_data::library::read_npz(&spec.library.path)?;
        let covariance_ref = uncertainty.covariance.as_ref().ok_or(
            "MF=33 propagation requires uncertainty.covariance (flux-only mode has no MF=33 block)",
        )?;
        let cov_sha = sha256_file(&covariance_ref.path)?;
        if !cov_sha.eq_ignore_ascii_case(&covariance_ref.sha256) {
            return Err("covariance sidecar does not match the spec's declared hash".into());
        }
        let covariance = actinv_data::covariance::read_npz(&covariance_ref.path)?;

        let mut stream = FluxStream::open(&spec.flux.path)?;
        let mut phis: Vec<Vec<f64>> = Vec::new();
        let source_boundaries = stream.header.energy_boundaries_eV.clone();
        loop {
            let chunk = stream.read_chunk(64)?;
            if chunk.is_empty() {
                break;
            }
            for cell in chunk {
                let rebinned = rebin_equal_lethargy(
                    &source_boundaries,
                    &cell.flux_per_group,
                    &activation_boundaries,
                )
                .map_err(|e| format!("cell '{}': {e}", cell.id))?;
                phis.push(rebinned.flux_per_group);
            }
        }
        stream.finish()?;
        if cells.len() != phis.len() {
            return Err(format!(
                "mesh carries {} cell records but the flux file carries {} cells",
                cells.len(),
                phis.len()
            ));
        }
        let selected_rows: BTreeSet<usize> =
            cells.iter().flat_map(|c| c.mf33.keys().copied()).collect();
        let selected: Vec<usize> = selected_rows.into_iter().collect();
        let phi_refs: Vec<&[f64]> = phis.iter().map(Vec::as_slice).collect();
        sparse = Some(covariance.collapse_sparse_weighted_multi(
            &library,
            &phi_refs,
            &selected,
            &|_, _| 1.0,
        )?);
        base.insert("activation_library_sha256".into(), lib_sha.into());
        base.insert("covariance_sha256".into(), cov_sha.into());
    }

    build_cov_document(&cells, step, base, &requested, sparse.as_ref())
}

#[cfg(test)]
mod cov_tests {
    use super::*;

    const RATES: [f64; 2] = [6.0, 2.0];
    const ACTIVITY: f64 = 2.0;

    /// A two-group, one-nuclide region with every channel populated.
    fn cov_test_cell(id: &str, ordinal: u64, scale: f64) -> Value {
        let groups = serde_json::json!([
            {"low_eV": 1.0e5, "high_eV": 1.0e6, "centroid_eV": 5.0e5, "photons_s": RATES[0] * scale},
            {"low_eV": 1.0e6, "high_eV": 1.0e7, "centroid_eV": 2.0e6, "photons_s": RATES[1] * scale},
        ]);
        serde_json::json!({
            "record": "cell", "ordinal": ordinal, "id": id, "volume_cm3": 1.0,
            "result": {"steps": [{
                "step": 1, "t_s": 100.0,
                "photon_source": {
                    "groups": groups,
                    "by_nuclide": [{"nuclide": "A", "groups": groups}],
                },
                "uncertainty": {"responses": {"activity:A": {
                    "nominal": ACTIVITY * scale,
                    "sensitivities": [
                        {"parameter": {"library_row": 0, "spectrum": 0, "covariance_covered": true}, "value": 0.4 * scale},
                        {"parameter": {"library_row": 1, "spectrum": 0, "covariance_covered": true}, "value": -0.3 * scale},
                        {"parameter": {"library_row": 2, "spectrum": 0, "covariance_covered": false}, "value": 9.0},
                    ],
                    "decay_sensitivities": [
                        {"parameter": {"nuclide": "A", "standard_uncertainty_s": 0.01}, "value": 3.0 * scale},
                    ],
                    "yield_sensitivities": [
                        {"parameter": {"parent_nuclide": "P", "product_nuclide": "A", "spectrum": 0,
                                       "standard_uncertainty": 0.02}, "value": 5.0 * scale},
                    ],
                    "flux_sensitivities": [
                        {"parameter": {"spectrum": 0, "group": 0, "standard_uncertainty_relative": 0.05}, "value": 0.8 * scale},
                        {"parameter": {"spectrum": 0, "group": 3, "standard_uncertainty_relative": 0.10}, "value": 0.5 * scale},
                    ],
                }}},
            }]},
        })
    }

    fn cov_test_mesh() -> String {
        let lines = [
            serde_json::json!({"record": "header"}),
            cov_test_cell("c0", 0, 1.0),
            cov_test_cell("c1", 1, 1.5),
            serde_json::json!({"record": "footer"}),
        ];
        lines
            .iter()
            .map(|l| l.to_string())
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Joint one-group covariance of (cell, row) parameters, PSD by
    /// construction: a rank-one shared part plus a positive diagonal.
    fn cov_test_sparse() -> SparseJointCollapse {
        let a = [1.0, 0.5, 0.8, 0.3];
        let d = [0.04, 0.09, 0.01, 0.16];
        let mut entries = std::collections::HashMap::new();
        for (l, &al) in a.iter().enumerate() {
            for (r, &ar) in a.iter().enumerate() {
                let v = al * ar + if l == r { d[l] } else { 0.0 };
                entries.insert((l, r), v);
            }
        }
        SparseJointCollapse {
            row_indices: vec![0, 1, 0, 1],
            param_spectrum: vec![0, 0, 1, 1],
            one_group_barns: vec![1.0; 4],
            entries,
            uncovered_rows: Vec::new(),
            absent_cross_parameter_pairs: 0,
            maximum_asymmetry_barn2: 0.0,
            excluded_blocks: Vec::new(),
        }
    }

    fn cov_records(doc: &str) -> Vec<Value> {
        doc.lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    fn row_total(rows: &Value, c: usize) -> f64 {
        rows[c]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .sum()
    }

    #[test]
    fn mf33_factor_reproduces_the_joint_total_covariance() {
        let (_, cells) = parse_cov_cells(&cov_test_mesh(), 1).unwrap();
        let sparse = cov_test_sparse();
        let (doc, summary) = build_cov_document(
            &cells,
            1,
            Map::new(),
            &["cross_section_mf33"],
            Some(&sparse),
        )
        .unwrap();
        let recs = cov_records(&doc);
        assert_eq!(recs[0]["schema"], COV_SCHEMA);
        assert_eq!(recs[0]["n_regions"], 2);
        assert_eq!(recs[0]["n_groups"], 2);
        let shared: Vec<&Value> = recs.iter().filter(|r| r["record"] == "shared").collect();
        assert!(!shared.is_empty());
        assert!(recs.iter().all(|r| r["record"] != "independent"));
        for rec in &shared {
            let col = rec["column"].as_array().unwrap();
            assert_eq!(col.len(), 2);
            for row in col {
                assert_eq!(row.as_array().unwrap().len(), 2);
                assert!(row
                    .as_array()
                    .unwrap()
                    .iter()
                    .all(|v| v.as_f64().unwrap().is_finite()));
            }
        }

        // Direct J^T Sigma J, as export-r2s-joint forms it: the photon total
        // per cell is the group sum of the activity-weighted sensitivities.
        let scale = [1.0, 1.5];
        // activity scale cancels in P/A, so J[c][row] = (sum P/A) * s_row(c)
        let j = |c: usize, row: usize| -> f64 {
            let s = [0.4, -0.3][row] * scale[c];
            let weight = (RATES[0] + RATES[1]) * scale[c] / (ACTIVITY * scale[c]);
            weight * s
        };
        let sigma = sparse.entries.clone();
        for c in 0..2 {
            for c2 in 0..2 {
                let mut expected = 0.0;
                for (&(l, r), &v) in &sigma {
                    if sparse.param_spectrum[l] == c && sparse.param_spectrum[r] == c2 {
                        expected += j(c, sparse.row_indices[l]) * v * j(c2, sparse.row_indices[r]);
                    }
                }
                let got: f64 = shared
                    .iter()
                    .map(|r| row_total(&r["column"], c) * row_total(&r["column"], c2))
                    .sum();
                assert!(
                    (got - expected).abs() <= 1e-9 * expected.abs().max(1.0),
                    "cell pair ({c},{c2}): factor {got} vs joint {expected}"
                );
            }
        }
        assert_eq!(summary["regions"], 2);
    }

    #[test]
    fn flux_block_is_independent_per_region_and_matches_direct_sum() {
        let (_, cells) = parse_cov_cells(&cov_test_mesh(), 1).unwrap();
        let (doc, _) = build_cov_document(&cells, 1, Map::new(), &["flux"], None).unwrap();
        let recs = cov_records(&doc);
        let indep: Vec<&Value> = recs
            .iter()
            .filter(|r| r["record"] == "independent")
            .collect();
        assert_eq!(indep.len(), 2);
        assert!(recs.iter().all(|r| r["record"] != "shared"));
        let scale = [1.0, 1.5];
        for (c, rec) in indep.iter().enumerate() {
            assert_eq!(rec["neutron_groups_used"], 2);
            let cols: Vec<Vec<f64>> = rec["columns"]
                .as_array()
                .unwrap()
                .iter()
                .map(|col| {
                    col.as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_f64().unwrap())
                        .collect()
                })
                .collect();
            assert!(!cols.is_empty() && cols.iter().all(|col| col.len() == 2));
            // Direct block sum_j m_j m_j^T with m_j[g] = (P_g/A) s_j e_j.
            let m: Vec<[f64; 2]> = [(0.8, 0.05), (0.5, 0.10)]
                .iter()
                .map(|&(s, e)| {
                    [
                        RATES[0] / ACTIVITY * s * scale[c] * e,
                        RATES[1] / ACTIVITY * s * scale[c] * e,
                    ]
                })
                .collect();
            for (a, b) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                let expected: f64 = m.iter().map(|mj| mj[a] * mj[b]).sum();
                let got: f64 = cols.iter().map(|col| col[a] * col[b]).sum();
                assert!(
                    (got - expected).abs() <= 1e-12 * expected.abs().max(1e-12),
                    "region {c} block ({a},{b}): {got} vs {expected}"
                );
            }
        }
    }

    #[test]
    fn decay_and_yield_columns_are_shared_and_scaled_by_sigma() {
        let (_, cells) = parse_cov_cells(&cov_test_mesh(), 1).unwrap();
        let (doc, _) = build_cov_document(
            &cells,
            1,
            Map::new(),
            &["decay_constants", "fission_yields"],
            None,
        )
        .unwrap();
        let recs = cov_records(&doc);
        let find = |label: &str| {
            recs.iter()
                .find(|r| r["label"] == label)
                .unwrap_or_else(|| panic!("no shared column {label}"))
        };
        let decay = find("decay:A");
        // region 1: dS_g/dlambda · sigma = P_g/A · 3.0·1.5 · 0.01
        let expected = RATES[0] / ACTIVITY * 3.0 * 1.5 * 0.01;
        let got = decay["column"][1][0].as_f64().unwrap();
        assert!((got - expected).abs() <= 1e-15, "{got} vs {expected}");
        let yld = find("yield:P>A");
        let expected = RATES[1] / ACTIVITY * 5.0 * 0.02;
        assert!((yld["column"][0][1].as_f64().unwrap() - expected).abs() <= 1e-15);
    }

    #[test]
    fn unrequested_channels_are_listed_as_missing() {
        let (_, cells) = parse_cov_cells(&cov_test_mesh(), 1).unwrap();
        let (doc, _) = build_cov_document(&cells, 1, Map::new(), &["flux"], None).unwrap();
        let recs = cov_records(&doc);
        let header = &recs[0];
        let missing: Vec<&str> = header["channels_missing"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["channel"].as_str().unwrap())
            .collect();
        assert!(missing.contains(&"cross_section_mf33"));
        assert!(missing.contains(&"decay_constants"));
        assert!(missing.contains(&"fission_yields"));
        assert!(missing.contains(&"photon_emission_data"));
        assert!(!missing.contains(&"flux"));
    }

    #[test]
    fn missing_step_and_truncated_stream_are_errors() {
        assert!(parse_cov_cells(&cov_test_mesh(), 2).is_err());
        let truncated: String = cov_test_mesh()
            .lines()
            .take(3)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(parse_cov_cells(&truncated, 1).is_err());
    }

    #[test]
    fn psd_factor_reproduces_a_rank_deficient_matrix() {
        // rank 2 in 3 dimensions
        let u = [1.0, 2.0, -1.0];
        let v = [0.5, 0.0, 3.0];
        let a: Vec<f64> = (0..9)
            .map(|k| u[k / 3] * u[k % 3] + v[k / 3] * v[k % 3])
            .collect();
        let (cols, truncated) = psd_factor(&a, 3, COV_RANK_TOL);
        assert_eq!(cols.len(), 2);
        assert!(truncated.abs() < 1e-12);
        for (k, &want) in a.iter().enumerate() {
            let got: f64 = cols.iter().map(|c| c[k / 3] * c[k % 3]).sum();
            assert!((got - want).abs() < 1e-12);
        }
        let (none, _) = psd_factor(&[0.0; 4], 2, COV_RANK_TOL);
        assert!(none.is_empty());
    }
}
