//! P57/P102 — foreign-format photon-source writers (`actinv-source-adapter-1`).
//!
//! Pure text emission over an `actinv-r2s-source-1` ndjson stream: each cell's
//! discrete group strengths and Cartesian bounds are re-serialized as the
//! source constructs the transport codes natively read — OpenMC settings-XML
//! `<source>` elements, an MCNP `SDEF`/`SI`/`SP` deck, Serpent `src` cards,
//! and (P102) one ALARA `.photonSrc` file per cell plus a provenance
//! sidecar, for `nucleide`'s and PyNE's own ALARA readers. Uncertainty
//! cannot ride in the native formats; the propagated band survives as
//! comment lines (or the sidecar, for ALARA) plus the sha pointer back to
//! the banded document. No solver semantics — an independent checker can
//! re-derive every emitted token from the same bytes.

use serde_json::Value;
use sha2::{Digest, Sha256};

const EMITTER: &str = "actinv-source-adapter-1";
const R2S_SCHEMA: &str = "actinv-r2s-source-1";

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn f(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

fn fmt(x: f64) -> String {
    format!("{x}")
}

struct Cell {
    id: String,
    bounds: [[f64; 2]; 3],
    volume: f64,
    photons_s: f64,
    sigma_indep: Option<f64>,
    sigma_consv: Option<f64>,
    groups: Vec<(f64, f64)>, // (centroid_eV, photons_s) nonzero-strength only
    step_t_s: Option<f64>,   // P102: irradiation-step time, for --shutdown-t-s cooling
}

struct Source {
    input_sha: String,
    step: u64,
    cells: Vec<Cell>,
}

fn parse_r2s(bytes: &[u8]) -> Result<Source, String> {
    let input_sha = sha256_hex(bytes);
    let text = std::str::from_utf8(bytes).map_err(|e| format!("r2s source is not UTF-8: {e}"))?;
    let mut step: Option<u64> = None;
    let mut cells = Vec::new();
    let mut header_seen = false;
    let mut footer_seen = false;

    for (line_no, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let rec: Value = serde_json::from_str(line)
            .map_err(|e| format!("line {}: not JSON: {e}", line_no + 1))?;
        match rec.get("record").and_then(Value::as_str) {
            Some("header") => {
                if header_seen {
                    return Err("r2s source carries more than one header".into());
                }
                header_seen = true;
                let schema = rec.get("schema").and_then(Value::as_str).unwrap_or("");
                if schema != R2S_SCHEMA {
                    return Err(format!(
                        "export-source: schema '{schema}' is not {R2S_SCHEMA} — \
                         run actinv export-r2s first"
                    ));
                }
                step = rec.get("step").and_then(Value::as_u64);
                if step.is_none() {
                    return Err("r2s source header carries no step".into());
                }
            }
            Some("cell") => {
                if !header_seen {
                    return Err("cell record before header".into());
                }
                let id = rec
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("<unnamed>")
                    .to_owned();
                let b = rec
                    .get("bounds_cm")
                    .and_then(Value::as_array)
                    .ok_or_else(|| format!("cell '{id}' carries no bounds_cm"))?;
                if b.len() != 3 {
                    return Err(format!("cell '{id}' bounds_cm is not three axis pairs"));
                }
                let mut bounds = [[0.0; 2]; 3];
                for (a, pair) in b.iter().enumerate() {
                    let pair = pair
                        .as_array()
                        .filter(|p| p.len() == 2)
                        .ok_or_else(|| format!("cell '{id}' bounds axis {a} is not a pair"))?;
                    let lo = pair[0]
                        .as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or_else(|| format!("cell '{id}' bounds axis {a} low not finite"))?;
                    let hi = pair[1]
                        .as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or_else(|| format!("cell '{id}' bounds axis {a} high not finite"))?;
                    if lo >= hi {
                        return Err(format!(
                            "cell '{id}' bounds axis {a} is not a positive interval \
                             ({lo}..{hi}) — non-Cartesian bounds are outside the pinned subset"
                        ));
                    }
                    bounds[a] = [lo, hi];
                }
                let photons_s = f(&rec, "photons_s")
                    .filter(|v| v.is_finite() && *v >= 0.0)
                    .ok_or_else(|| format!("cell '{id}' photons_s missing or invalid"))?;
                let volume = f(&rec, "volume_cm3").unwrap_or(0.0);
                let groups: Vec<(f64, f64)> = rec
                    .get("groups")
                    .and_then(Value::as_array)
                    .map(|gs| {
                        gs.iter()
                            .filter_map(|g| {
                                let e = f(g, "centroid_eV")?;
                                let s = f(g, "photons_s")?;
                                (e.is_finite() && e > 0.0 && s.is_finite() && s > 0.0)
                                    .then_some((e, s))
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                cells.push(Cell {
                    id,
                    bounds,
                    volume,
                    photons_s,
                    sigma_indep: f(&rec, "sigma_photons_s_independent"),
                    sigma_consv: f(&rec, "sigma_photons_s_conservative"),
                    groups,
                    step_t_s: f(&rec, "step_t_s"),
                });
            }
            Some("footer") => footer_seen = true,
            Some(other) => {
                return Err(format!("unknown record '{other}' on line {}", line_no + 1));
            }
            None => return Err(format!("line {}: missing 'record'", line_no + 1)),
        }
    }
    if !header_seen {
        return Err("r2s source carries no header".into());
    }
    if !footer_seen {
        return Err("r2s source carries no footer — input is truncated".into());
    }
    if cells.is_empty() {
        return Err("r2s source carries no cells".into());
    }
    Ok(Source {
        input_sha,
        step: step.unwrap(),
        cells,
    })
}

/// Group probabilities: the exact ratios `sᵢ/Σs`. No residual fudging —
/// the emitted values are the true shares; their double-precision sum sits
/// within a few ulp of 1.0 and every target format normalizes the
/// distribution internally (OpenMC `Discrete`, MCNP `SP`, Serpent `sb`).
fn normalized_probs(groups: &[(f64, f64)]) -> Vec<f64> {
    let total: f64 = groups.iter().map(|g| g.1).sum();
    groups.iter().map(|g| g.1 / total).collect()
}

fn sigma_comment(c: &Cell, prefix: &str) -> String {
    let si = c.sigma_indep.map(fmt).unwrap_or_else(|| "unbanded".into());
    let sc = c.sigma_consv.map(fmt).unwrap_or_else(|| "unbanded".into());
    format!(
        "{prefix} cell '{id}' photons_s={ps} sigma_independent={si} sigma_conservative={sc} \
         volume_cm3={vol} nonzero_groups={ng}",
        id = c.id,
        ps = fmt(c.photons_s),
        vol = fmt(c.volume),
        ng = c.groups.len(),
    )
}

fn provenance_openmc(src: &Source, total: f64) -> String {
    let mut s = String::from("<!--\n");
    s.push_str(&format!(
        "{EMITTER}\ninput_sha256={}\nstep={}\ntotal_photons_s={}\ncells={}\n",
        src.input_sha,
        src.step,
        fmt(total),
        src.cells.len()
    ));
    for c in &src.cells {
        s.push_str(&sigma_comment(c, ""));
        s.push('\n');
    }
    s.push_str("-->\n");
    s
}

fn provenance_text(src: &Source, total: f64, prefix: &str) -> String {
    let mut s = format!(
        "{prefix} {EMITTER}\n{prefix} input_sha256={}\n{prefix} step={}\n\
         {prefix} total_photons_s={}\n{prefix} cells={}\n",
        src.input_sha,
        src.step,
        fmt(total),
        src.cells.len()
    );
    for c in &src.cells {
        s.push_str(&sigma_comment(c, prefix));
        s.push('\n');
    }
    s
}

fn emit_openmc(src: &Source) -> Result<String, String> {
    let total: f64 = src.cells.iter().map(|c| c.photons_s).sum();
    let mut out = String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<sources>\n");
    out.push_str(&provenance_openmc(src, total));
    for c in &src.cells {
        let probs = normalized_probs(&c.groups);
        out.push_str(&format!(
            "<source type=\"independent\" particle=\"photon\" strength=\"{}\">\n",
            fmt(c.photons_s)
        ));
        out.push_str("  <space type=\"cartesian\">\n");
        for (axis, letter) in c.bounds.iter().zip(["x", "y", "z"]) {
            out.push_str(&format!(
                "    <{letter} type=\"uniform\" parameters=\"{} {}\"/>\n",
                fmt(axis[0]),
                fmt(axis[1])
            ));
        }
        out.push_str("  </space>\n");
        out.push_str("  <angle type=\"isotropic\"/>\n");
        if !c.groups.is_empty() {
            let xs: Vec<String> = c.groups.iter().map(|g| fmt(g.0)).collect();
            let ps: Vec<String> = probs.iter().map(|p| fmt(*p)).collect();
            out.push_str(&format!(
                "  <energy type=\"discrete\"><parameters>{} {}</parameters></energy>\n",
                xs.join(" "),
                ps.join(" ")
            ));
        }
        out.push_str("</source>\n");
    }
    out.push_str("</sources>\n");
    Ok(out)
}

fn emit_mcnp(src: &Source) -> Result<String, String> {
    let total: f64 = src.cells.iter().map(|c| c.photons_s).sum();
    let mut out = String::new();
    out.push_str(&provenance_text(src, total, "c"));
    for (i, c) in src.cells.iter().enumerate() {
        // four distributions per cell: x, y, z, energy — card indices are
        // sequential so a checker can reconstruct the mapping exactly
        let base = 1 + i * 4;
        let [dx, dy, dz, de] = [base, base + 1, base + 2, base + 3];
        let w = if total > 0.0 {
            c.photons_s / total
        } else {
            0.0
        };
        out.push_str(&format!("c cell '{}'\n", c.id));
        out.push_str(&format!(
            "SDEF X=D{dx} Y=D{dy} Z=D{dz} ERG=D{de} WGT={}\n",
            fmt(w)
        ));
        for (d, pair) in [(dx, c.bounds[0]), (dy, c.bounds[1]), (dz, c.bounds[2])] {
            out.push_str(&format!("SI{d} H {} {}\n", fmt(pair[0]), fmt(pair[1])));
            out.push_str(&format!("SP{d} 0 1\n"));
        }
        let probs = normalized_probs(&c.groups);
        if !c.groups.is_empty() {
            // MCNP energies are MeV; centroids arrive in eV
            let xs: Vec<String> = c.groups.iter().map(|g| fmt(g.0 / 1.0e6)).collect();
            let ps: Vec<String> = probs.iter().map(|p| fmt(*p)).collect();
            out.push_str(&format!("SI{de} L {}\n", xs.join(" ")));
            out.push_str(&format!("SP{de} {}\n", ps.join(" ")));
        } else {
            // zero-strength cell: degenerate single-line distribution keeps
            // the deck syntactically complete; WGT=0 means it never samples
            out.push_str(&format!("SI{de} L 1000000\n"));
            out.push_str(&format!("SP{de} 1\n"));
        }
    }
    Ok(out)
}

fn serpent_id(raw: &str, ordinal: usize) -> String {
    let mut s: String = raw
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    if s.is_empty() || s.chars().next().unwrap().is_ascii_digit() {
        s = format!("actinv_{s}");
    }
    format!("s_{s}_{ordinal}")
}

fn emit_serpent(src: &Source) -> Result<String, String> {
    let total: f64 = src.cells.iter().map(|c| c.photons_s).sum();
    let mut out = String::new();
    out.push_str(&provenance_text(src, total, "%"));
    for (i, c) in src.cells.iter().enumerate() {
        let id = serpent_id(&c.id, i);
        let w = if total > 0.0 {
            c.photons_s / total
        } else {
            0.0
        };
        let [[x0, x1], [y0, y1], [z0, z1]] = c.bounds;
        out.push_str(&format!("% cell '{}'\n", c.id));
        if c.groups.is_empty() {
            out.push_str(&format!(
                "src {id} p sx {} {} sy {} {} sz {} {} sw {} se 1.0\n",
                fmt(x0),
                fmt(x1),
                fmt(y0),
                fmt(y1),
                fmt(z0),
                fmt(z1),
                fmt(w)
            ));
        } else {
            let probs = normalized_probs(&c.groups);
            let pairs: Vec<String> = c
                .groups
                .iter()
                .zip(&probs)
                .map(|(g, p)| format!("{} {}", fmt(g.0 / 1.0e6), fmt(*p)))
                .collect();
            out.push_str(&format!(
                "src {id} p sx {} {} sy {} {} sz {} {} sw {} sb {} 0 {}\n",
                fmt(x0),
                fmt(x1),
                fmt(y0),
                fmt(y1),
                fmt(z0),
                fmt(z1),
                fmt(w),
                c.groups.len(),
                pairs.join(" ")
            ));
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------
// P102: ALARA photon-source export (`nucleide` / PyNE R2S reader surface)
// ---------------------------------------------------------------------

/// One ALARA `.photonSrc` file, as it will be written under `OUT_DIR`.
#[derive(Debug)]
pub struct AlaraFile {
    pub name: String,
    pub text: String,
}

/// The complete ALARA export: one file per cell plus the provenance index
/// (`actinv-alara-index.json`). No filesystem access happens here — the
/// caller (the CLI) decides `OUT_DIR` semantics and performs the writes.
#[derive(Debug)]
pub struct AlaraExport {
    pub files: Vec<AlaraFile>,
    pub index: Value,
}

/// Sanitize a cell id into the ASCII subset ALARA/PyNE/nucleide file names
/// tolerate: `[A-Za-z0-9._-]`, every other **byte** replaced by `_` (decision
/// 1 operates byte-wise, so a multi-byte UTF-8 character becomes one `_` per
/// byte, not one `_` per character).
fn sanitize_alara_id(id: &str) -> String {
    id.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-' {
                b as char
            } else {
                '_'
            }
        })
        .collect()
}

/// Decimal width needed so zero-padded ordinals `0..n` sort lexicographically
/// in the same order as numerically (decision 1: "the ordinal prefix keeps
/// names unique and sorted in document order").
fn ordinal_width(n: usize) -> usize {
    if n <= 1 {
        1
    } else {
        (n - 1).to_string().len()
    }
}

/// Build the ALARA photon-source export: one `OUT/<ordinal>_<id>.photonSrc`
/// per cell (a single `TOTAL\t<time>\t<densities…>` row each) plus
/// `OUT/actinv-alara-index.json`. Pure function — fails closed with no
/// output produced at all if any cell fails a check, so the caller never
/// writes a partial directory.
pub fn export_source_alara(r2s_bytes: &[u8], shutdown_t_s: f64) -> Result<AlaraExport, String> {
    if !shutdown_t_s.is_finite() {
        return Err("export-source alara: --shutdown-t-s must be a finite number".into());
    }
    let src = parse_r2s(r2s_bytes)?;

    // decision 5: one step_t_s basis, finite and identical across cells
    let mut step_t_s: Option<f64> = None;
    for c in &src.cells {
        let t = c
            .step_t_s
            .ok_or_else(|| format!("cell '{}' carries no step_t_s", c.id))?;
        if !t.is_finite() {
            return Err(format!("cell '{}' step_t_s is not finite", c.id));
        }
        match step_t_s {
            None => step_t_s = Some(t),
            Some(prev) if prev == t => {}
            Some(prev) => {
                return Err(format!(
                    "export-source alara: cells carry different step_t_s ({prev} vs {t} \
                     for cell '{}') — one irradiation-step basis is required",
                    c.id
                ))
            }
        }
    }
    let step_t_s = step_t_s.expect("parse_r2s refuses an empty cell list");

    let cooling = step_t_s - shutdown_t_s;
    if cooling < 0.0 {
        return Err(format!(
            "export-source alara: cooling time is negative (step_t_s={step_t_s}, \
             shutdown_t_s={shutdown_t_s})"
        ));
    }
    let time_token = if cooling == 0.0 {
        "shutdown".to_string()
    } else {
        format!("{} s", fmt(cooling))
    };

    // decision 3: every cell needs a finite, positive volume
    for c in &src.cells {
        if !(c.volume.is_finite() && c.volume > 0.0) {
            return Err(format!(
                "cell '{}' volume_cm3 is missing, zero or negative — refused",
                c.id
            ));
        }
    }

    // decision 4: the group grid is the union of centroid_eV over every
    // cell's (already nonzero-filtered) groups, sorted ascending, exact
    // float equality defining "same group".
    let mut grid: Vec<f64> = Vec::new();
    for c in &src.cells {
        for (e, _) in &c.groups {
            if !grid.contains(e) {
                grid.push(*e);
            }
        }
    }
    grid.sort_by(|a, b| a.partial_cmp(b).expect("centroids are finite"));

    let width = ordinal_width(src.cells.len());
    let mut files = Vec::with_capacity(src.cells.len());
    let mut index_cells = Vec::with_capacity(src.cells.len());
    for (ordinal, c) in src.cells.iter().enumerate() {
        let densities: Vec<f64> = grid
            .iter()
            .map(|g| {
                c.groups
                    .iter()
                    .find(|(e, _)| *e == *g)
                    .map(|(_, s)| s / c.volume)
                    .unwrap_or(0.0)
            })
            .collect();
        let name = format!(
            "{:0width$}_{}.photonSrc",
            ordinal,
            sanitize_alara_id(&c.id),
            width = width
        );
        let row = format!(
            "TOTAL\t{time_token}\t{}\n",
            densities
                .iter()
                .map(|d| fmt(*d))
                .collect::<Vec<_>>()
                .join("\t")
        );
        index_cells.push(serde_json::json!({
            "file": name,
            "id": c.id,
            "ordinal": ordinal,
            "bounds_cm": c.bounds,
            "volume_cm3": c.volume,
            "photons_s": c.photons_s,
            "sigma_photons_s_independent": c.sigma_indep,
            "sigma_photons_s_conservative": c.sigma_consv,
            "nonzero_groups": c.groups.len(),
            "sha256": sha256_hex(row.as_bytes()),
        }));
        files.push(AlaraFile { name, text: row });
    }

    let index = serde_json::json!({
        "emitter": EMITTER,
        "format": "alara",
        "input_sha256": src.input_sha,
        "step": src.step,
        "step_t_s": step_t_s,
        "shutdown_t_s": shutdown_t_s,
        "cooling_s": cooling,
        "time_token": time_token,
        "units": "photons/s/cm3",
        "group_order": "ascending centroid_eV",
        "group_centroids_eV": grid,
        "cells": index_cells,
    });

    Ok(AlaraExport { files, index })
}

/// Emit a foreign-format photon source. `format` ∈ {openmc, mcnp, serpent}.
/// Returns `(file_text, summary)`.
pub fn export_source(format: &str, r2s_bytes: &[u8]) -> Result<(String, Value), String> {
    let src = parse_r2s(r2s_bytes)?;
    let text = match format {
        "openmc" => emit_openmc(&src)?,
        "mcnp" => emit_mcnp(&src)?,
        "serpent" => emit_serpent(&src)?,
        other => {
            return Err(format!(
                "export-source: unknown format '{other}' — expected openmc, mcnp or serpent"
            ))
        }
    };
    let total: f64 = src.cells.iter().map(|c| c.photons_s).sum();
    Ok((
        text,
        serde_json::json!({
            "format": format,
            "cells": src.cells.len(),
            "step": src.step,
            "total_photons_s": total,
            "input_sha256": src.input_sha,
        }),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> String {
        let lines = [
            serde_json::json!({"record": "header",
                "schema": "actinv-r2s-source-1", "step": 2}),
            serde_json::json!({"record": "cell", "id": "a",
                "bounds_cm": [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
                "volume_cm3": 1.0, "photons_s": 3.0,
                "groups": [{"centroid_eV": 1.0e5, "photons_s": 1.0},
                           {"centroid_eV": 2.0e5, "photons_s": 2.0}]}),
            serde_json::json!({"record": "footer", "cell_count": 1}),
        ];
        lines
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn probs_are_exact_ratios() {
        let p = normalized_probs(&[(1.0, 1.0), (2.0, 1.0), (3.0, 1.0)]);
        assert_eq!(p, vec![1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]);
        assert_eq!(normalized_probs(&[]), Vec::<f64>::new());
        assert_eq!(normalized_probs(&[(5.0, 3.0)]), vec![1.0]);
    }

    #[test]
    fn rejects_degenerate_bounds() {
        let doc = fixture().replace(
            "\"bounds_cm\":[[0.0,1.0],[0.0,1.0],[0.0,1.0]]",
            "\"bounds_cm\":[[0.0,1.0],[2.0,1.0],[0.0,1.0]]",
        );
        let e = export_source("mcnp", doc.as_bytes()).unwrap_err();
        assert!(e.contains("positive interval"), "{e}");
    }

    #[test]
    fn rejects_wrong_schema_and_missing_footer() {
        let bad = fixture().replace("actinv-r2s-source-1", "other-1");
        assert!(export_source("openmc", bad.as_bytes()).is_err());
        let nofoot = fixture()
            .lines()
            .filter(|l| !l.contains("\"footer\""))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(export_source("serpent", nofoot.as_bytes()).is_err());
        assert!(export_source("nonsense", fixture().as_bytes()).is_err());
    }

    #[test]
    fn emitted_mev_and_probability_exact() {
        let (text, _) = export_source("mcnp", fixture().as_bytes()).unwrap();
        // centroids 1e5 and 2e5 eV -> 0.1 and 0.2 MeV; probs 1/3, 2/3
        assert!(text.contains("SI4 L 0.1 0.2"));
        assert!(text.contains("SP4 0.3333333333333333 0.6666666666666666"));
    }

    // ---- P102: ALARA export ----

    fn fixture_alara() -> String {
        let lines = [
            serde_json::json!({"record": "header",
                "schema": "actinv-r2s-source-1", "step": 4}),
            serde_json::json!({"record": "cell", "id": "cell-a",
                "bounds_cm": [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
                "volume_cm3": 2.0, "photons_s": 3.0, "step_t_s": 5000.0,
                "sigma_photons_s_independent": 0.1, "sigma_photons_s_conservative": 0.2,
                "groups": [{"centroid_eV": 1.0e5, "photons_s": 1.0},
                           {"centroid_eV": 2.0e5, "photons_s": 2.0}]}),
            serde_json::json!({"record": "cell", "id": "cell-b/β",
                "bounds_cm": [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
                "volume_cm3": 4.0, "photons_s": 2.0, "step_t_s": 5000.0,
                "groups": [{"centroid_eV": 2.0e5, "photons_s": 2.0}]}),
            serde_json::json!({"record": "cell", "id": "cell-empty",
                "bounds_cm": [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
                "volume_cm3": 1.0, "photons_s": 0.0, "step_t_s": 5000.0,
                "groups": []}),
            serde_json::json!({"record": "footer", "cell_count": 3}),
        ];
        lines
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn alara_sanitizes_ids_byte_wise() {
        assert_eq!(sanitize_alara_id("cell-a"), "cell-a");
        // '/' -> one underscore; 'β' is two UTF-8 bytes, neither ASCII-safe
        // -> two more underscores (byte-wise, not char-wise)
        assert_eq!(sanitize_alara_id("cell-b/β"), "cell-b___");
    }

    #[test]
    fn alara_ordinal_width_sorts_lexicographically() {
        assert_eq!(ordinal_width(1), 1);
        assert_eq!(ordinal_width(8), 1);
        assert_eq!(ordinal_width(11), 2);
        assert_eq!(ordinal_width(101), 3);
    }

    #[test]
    fn alara_emits_one_row_per_file_with_union_grid() {
        let export = export_source_alara(fixture_alara().as_bytes(), 0.0).unwrap();
        assert_eq!(export.files.len(), 3);
        assert_eq!(
            export.index["group_centroids_eV"],
            serde_json::json!([1.0e5, 2.0e5])
        );
        // cell-a: densities 1/2, 2/2; cooling=5000-0=5000 -> "5000 s"
        assert_eq!(export.files[0].text, "TOTAL\t5000 s\t0.5\t1\n");
        // cell-b: no group at 1e5 -> 0; group at 2e5 -> 2/4 = 0.5
        assert_eq!(export.files[1].text, "TOTAL\t5000 s\t0\t0.5\n");
        // zero-strength cell: zeros, not silence
        assert_eq!(export.files[2].text, "TOTAL\t5000 s\t0\t0\n");
        assert!(export.files[1].name.starts_with("1_cell-b_"));
    }

    #[test]
    fn alara_shutdown_token_at_zero_cooling() {
        let export = export_source_alara(fixture_alara().as_bytes(), 5000.0).unwrap();
        assert!(export.files[0].text.starts_with("TOTAL\tshutdown\t"));
        assert_eq!(export.index["cooling_s"], 0.0);
    }

    #[test]
    fn alara_rejects_negative_cooling() {
        let e = export_source_alara(fixture_alara().as_bytes(), 5001.0).unwrap_err();
        assert!(e.contains("negative"), "{e}");
    }

    #[test]
    fn alara_rejects_missing_step_t_s() {
        let doc = fixture_alara().replace(",\"step_t_s\":5000.0", "");
        let e = export_source_alara(doc.as_bytes(), 0.0).unwrap_err();
        assert!(e.contains("step_t_s"), "{e}");
    }

    #[test]
    fn alara_rejects_unequal_step_t_s() {
        let doc = fixture_alara().replacen("\"step_t_s\":5000.0", "\"step_t_s\":9000.0", 1);
        let e = export_source_alara(doc.as_bytes(), 0.0).unwrap_err();
        assert!(e.contains("different step_t_s"), "{e}");
    }

    #[test]
    fn alara_rejects_bad_volume() {
        let doc = fixture_alara().replace("\"volume_cm3\":2.0", "\"volume_cm3\":0.0");
        let e = export_source_alara(doc.as_bytes(), 0.0).unwrap_err();
        assert!(e.contains("volume_cm3"), "{e}");
    }

    #[test]
    fn alara_rejects_non_finite_shutdown_flag() {
        let e = export_source_alara(fixture_alara().as_bytes(), f64::NAN).unwrap_err();
        assert!(e.contains("--shutdown-t-s"), "{e}");
    }
}
