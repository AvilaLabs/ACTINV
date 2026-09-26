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
    let cov_sha = sha256_file(&uncertainty.covariance.path)?;
    if !cov_sha.eq_ignore_ascii_case(&uncertainty.covariance.sha256) {
        return Err("covariance sidecar does not match the spec's declared hash".into());
    }
    let covariance = actinv_data::covariance::read_npz(&uncertainty.covariance.path)?;

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
