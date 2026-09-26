//! P57 — foreign-format photon-source writers (`actinv-source-adapter-1`).
//!
//! Pure text emission over an `actinv-r2s-source-1` ndjson stream: each cell's
//! discrete group strengths and Cartesian bounds are re-serialized as the
//! source constructs the transport codes natively read — OpenMC settings-XML
//! `<source>` elements, an MCNP `SDEF`/`SI`/`SP` deck, and Serpent `src`
//! cards. Uncertainty cannot ride in the native formats; the propagated band
//! survives as comment lines plus the sha pointer back to the banded
//! document. No solver semantics — an independent checker can re-derive
//! every emitted token from the same bytes.

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
                    if !(lo < hi) {
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
    let si = c
        .sigma_indep
        .map(|v| fmt(v))
        .unwrap_or_else(|| "unbanded".into());
    let sc = c
        .sigma_consv
        .map(|v| fmt(v))
        .unwrap_or_else(|| "unbanded".into());
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
}
