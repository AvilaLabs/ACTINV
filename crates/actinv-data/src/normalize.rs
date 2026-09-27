//! Tape-level normalization between raw ENDF text and the fail-closed
//! semantic pipeline.
//!
//! Real evaluations routinely publish records the ENDF-6 manual treats as
//! legal "not applicable" encodings that the strict parser rejects: photon
//! exit pairs with PNT/SHF = -1, LRF=0 radius-only ranges written as
//! EL=EH=0, unresolved degrees of freedom stored with a decimal residue,
//! declared total widths below their component sum by a few last-place
//! digits, and TAB1 abscissae with a transcription ordering glitch.
//! Rather than relax the frozen validators, a profile-gated normalization
//! pass rewrites the offending fields to their documented equivalents and
//! emits a per-file ledger entry for every intervention. Anything the
//! pass does not understand still fails closed downstream.
//!
//! Applied defect classes (all scope-limited and recorded):
//!
//! - `zaawr_head`: MF=2/MT=151 head ZA/AWR disagree with MF=1/MT=451;
//!   the MF=1 values win (ZA must already agree).
//! - `mf2_lrf0_range`: LRF=0 scattering-radius-only range written as an
//!   all-zero CONT; normalized to the full neutron span (1e-5–2e7 eV).
//! - `rml_photon_pair`: RML particle pair with mass_a = 0 (photon exit)
//!   carries PNT/SHF = -1 ("not applicable"); normalized to 0, which the
//!   physics path already treats as "no penetrability computed".
//! - `unresolved_dof`: unresolved LIST degree-of-freedom fields (AMUX,
//!   AMUN, AMUF) with a fractional residue are rounded to the nearest
//!   integer 0..=4.
//! - `unresolved_zero_width`: exact-zero D/GX/GN/GG/GF values under a
//!   log-law (INT 3–5) case-C unresolved table become 1e-30; a constant
//!   field interpolates identically but the strict log path accepts it.
//! - `bw_total_width`: resolved-resonance row with GT below
//!   GN+GG+GF by a last-place amount (≤ 5e-4 relative) is normalized to
//!   the component sum — the same value the reconstruction's
//!   `max(total, components)` semantics already compute. Larger deficits
//!   are flagged in the ledger and left untouched.
//! - `tab1_order`: adjacent (x,y) pairs violating strict abscissa
//!   monotonicity in MF=2/MT=151 TAB1 records are swapped as pairs
//!   (transcription ordering glitch; each y stays attached to its x).
//!
//! `NormalizeProfile::EndfB8` enables every class; `None` preserves the
//! historical fail-closed behaviour byte-for-byte. Emitted-state sums
//! that disagree with the MF=3 totals are a separate, semantic defect
//! class handled at collapse time by `BuildOptions::state_sum_policy`,
//! not by this text pass.

/// Per-tape normalization result: rewritten text plus a human- and
/// machine-readable ledger of every intervention.
#[derive(Debug)]
pub struct NormalizedTape {
    pub text: String,
    pub entries: Vec<String>,
}

/// Which normalization profile a build opts into.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NormalizeProfile {
    /// No normalization; every defect fails closed (historical default).
    None,
    /// ENDF/B-VIII.1: the full defect table below.
    EndfB8,
    /// TENDL neutron files: no MF=2 text surgery (the ENDF-8 fixer set
    /// was developed against that library's conventions and is not
    /// trusted on TENDL layout), but opting in still enables the
    /// collapse-level MF=9/MF=10 state-sum reconciliation — TENDL's
    /// emitted state partials routinely exceed its own MF=3 totals by
    /// a few percent, and scaling them to the runtime total preserves
    /// the evaluated branching ratios instead of failing the target.
    Tendl,
}

impl NormalizeProfile {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "none" => Ok(Self::None),
            "endfb8" => Ok(Self::EndfB8),
            "tendl" => Ok(Self::Tendl),
            _ => Err(format!(
                "unknown normalization profile '{value}'; expected none, endfb8 or tendl"
            )),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::EndfB8 => "endfb8",
            Self::Tendl => "tendl",
        }
    }
}

// ---------------------------------------------------------------------------
// fixed-width line surgery
// ---------------------------------------------------------------------------

fn tail_mf_mt(line: &str) -> Option<(i32, i32)> {
    crate::endf::tail(line).map(|(_, mf, mt)| (mf, mt))
}

/// Six field values with ENDF blank-is-zero semantics; `None` when any
/// non-blank field cannot parse — such a line is opaque to surgery and
/// left for the strict readers to reject.
fn flds(line: &str) -> Option<[f64; 6]> {
    let raw = crate::endf::fields(line);
    let mut out = [0.0; 6];
    for (i, field) in raw.iter().enumerate() {
        let t = field.trim();
        if t.is_empty() {
            continue;
        }
        out[i] = crate::endf::parse_endf_float(t).ok()?;
    }
    Some(out)
}

/// Write `value` into the `pos`-th 11-char field preserving line width.
fn write_field(lines: &mut [String], index: usize, pos: usize, value: &str) {
    debug_assert_eq!(value.len(), 11);
    let line = &mut lines[index];
    let start = pos * 11;
    line.replace_range(start..start + 11, value);
}

/// ENDF-6 11-char field: mantissa up to 7 significant digits, exponent
/// without the 'E' (` 7.318100+4`, ` 1.000000-30`).
fn ef_str(v: f64) -> String {
    if v == 0.0 {
        return " 0.000000+0".into();
    }
    let (mut mant, exp) = {
        let s = format!("{v:.6E}");
        let (m, e) = s.split_once('E').unwrap();
        (m.to_string(), e.parse::<i32>().unwrap())
    };
    let sign = if exp >= 0 { '+' } else { '-' };
    let mut body = if exp.abs() < 10 {
        format!("{mant}{sign}{}", exp.abs())
    } else {
        mant.pop(); // drop a mantissa digit to keep width <= 11
        format!("{mant}{sign}{:02}", exp.abs())
    };
    if body.len() > 11 {
        let s2 = format!("{v:.5E}");
        let m2 = s2.split('E').next().unwrap();
        body = format!("{m2}{sign}{:02}", exp.abs());
    }
    if body.len() > 11 {
        let s2 = format!("{v:.4E}");
        let m2 = s2.split('E').next().unwrap();
        body = format!("{m2}{sign}{:02}", exp.abs());
    }
    format!("{body:>11}")
}

fn is_mf2(line: &str) -> bool {
    tail_mf_mt(line) == Some((2, 151))
}

// ---------------------------------------------------------------------------
// defect classes
// ---------------------------------------------------------------------------

/// MF=2 head ZA/AWR corrected to the MF=1 head values when ZA agrees.
fn fix_zaawr(lines: &mut [String], entries: &mut Vec<String>) {
    let mf1 = lines.iter().find_map(|line| {
        if tail_mf_mt(line) == Some((1, 451)) {
            flds(line).map(|f| (f[0], f[1]))
        } else {
            None
        }
    });
    let Some((za, awr)) = mf1 else { return };
    for i in 0..lines.len() {
        if !is_mf2(&lines[i]) {
            continue;
        }
        if let Some(f) = flds(&lines[i]) {
            if f[0] as i64 == za as i64 && (f[1] - awr).abs() > 1e-7 * awr.abs().max(1.0) {
                write_field(lines, i, 1, &ef_str(awr));
                entries.push(format!(
                    "zaawr_head: MF=2 AWR {} set to MF=1 value {awr} at record {}",
                    f[1],
                    i + 1
                ));
            }
        }
        break; // only the section head
    }
}

/// LRF=0 radius-only ranges written as all-zero CONTs.
fn fix_lrf0_range(lines: &mut [String], entries: &mut Vec<String>) {
    for i in 0..lines.len() {
        if !is_mf2(&lines[i]) {
            continue;
        }
        let Some(f) = flds(&lines[i]) else { continue };
        if !f.iter().all(|v| *v == 0.0) {
            continue;
        }
        // A degenerate LRF=0 range CONT is all-zero and directly follows the
        // isotope CONT (f[0] = ZA). All-zero LIST payload rows — e.g. the
        // SAMMY zero-resonance placeholder inside an RML spin group — follow
        // LIST heads instead and must not be rewritten.
        let isotope_cont = i
            .checked_sub(1)
            .and_then(|j| flds(&lines[j]))
            .map(|p| p[0] > 1000.0)
            .unwrap_or(false);
        if !isotope_cont {
            continue;
        }
        write_field(lines, i, 0, &ef_str(1.0e-5));
        write_field(lines, i, 1, &ef_str(2.0e7));
        entries.push(format!(
            "mf2_lrf0_range: degenerate LRF=0 CONT at record {} normalized to full span",
            i + 1
        ));
    }
}

/// RML particle-pair LISTs: photon rows (mass_a == 0) with PNT/SHF = -1.
fn fix_rml_photon(lines: &mut [String], entries: &mut Vec<String>) {
    let mut ctx: Option<(i64, i64)> = None;
    for i in 0..lines.len() {
        if !is_mf2(&lines[i]) {
            continue;
        }
        range_ctx(lines, &mut ctx, i);
        let Some(f) = flds(&lines[i]) else { continue };
        let Some((lru, lrf)) = ctx else { continue };
        // pair-LIST head signature inside an LRF=7 range: NPP>=1,
        // NPL=12*NPP, N2=2*NPP
        if !(lru == 1
            && lrf == 7
            && f[4] >= 12.0
            && f[4] % 12.0 == 0.0
            && f[5] == f[4] / 6.0
            && f[2] >= 1.0)
        {
            continue;
        }
        let npp = f[2] as usize;
        let payload_lines = f[5] as usize;
        let mut vals: Vec<(usize, usize, f64)> = Vec::new();
        for j in 0..payload_lines {
            let li = i + 1 + j;
            if li >= lines.len() || !is_mf2(&lines[li]) {
                break;
            }
            let Some(fj) = flds(&lines[li]) else { break };
            for (k, v) in fj.iter().enumerate() {
                vals.push((li, k, *v));
            }
        }
        for p in 0..npp {
            let base = 12 * p;
            if base + 11 >= vals.len() {
                break;
            }
            if vals[base].2 != 0.0 {
                continue;
            }
            for (off, name) in [(7usize, "PNT"), (8, "SHF")] {
                let (li, pos, v) = vals[base + off];
                if v == -1.0 {
                    write_field(lines, li, pos, &ef_str(0.0));
                    entries.push(format!(
                        "rml_photon_pair: photon pair {} {name}=-1 normalized to 0 at record {}",
                        p + 1,
                        li + 1
                    ));
                }
            }
        }
    }
}

fn round_dof(
    lines: &mut [String],
    li: usize,
    pos: usize,
    v: f64,
    label: &str,
    entries: &mut Vec<String>,
) {
    let r = v.round();
    if (v - r).abs() <= 1e-7 || !(0.0..=4.0).contains(&r) {
        return;
    }
    write_field(lines, li, pos, &ef_str(r));
    entries.push(format!(
        "unresolved_dof: {label} {v} rounded to {r} at record {}",
        li + 1
    ));
}

/// Unresolved LIST dof fields (AMUX/AMUN/AMUF) with a decimal residue.
/// True when the CONT looks like a range header (EL, EH, LRU, LRF, NRO, NPP).
/// Returns (LRU, LRF). L-group LIST heads, dof rows, and resonance rows are
/// excluded: their second field is zero (QX/D) or their float fields are
/// nonintegral.
fn range_cont(f: &[f64; 6]) -> Option<(i64, i64)> {
    let lru = f[2];
    let lrf = f[3];
    if f[0] > 0.0
        && f[1] > f[0]
        && (lru == 1.0 || lru == 2.0)
        && matches!(lrf, 1.0 | 2.0 | 3.0 | 4.0 | 7.0)
        && f[4] == f[4].round()
        && f[4] >= 0.0
        && f[4] <= 1.0
        && f[5] == f[5].round()
        && f[5] >= 0.0
    {
        Some((lru as i64, lrf as i64))
    } else {
        None
    }
}

/// Track (LRU, LRF) of the enclosing MF2 range as `i` advances; returns
/// Some((lru, lrf)) when line `i` opened a new range.
fn range_ctx(lines: &[String], ctx: &mut Option<(i64, i64)>, i: usize) -> Option<(i64, i64)> {
    if let Some(f) = flds(&lines[i]) {
        if let Some(rc) = range_cont(&f) {
            *ctx = Some(rc);
        }
    }
    *ctx
}

fn fix_unresolved_dof(lines: &mut [String], entries: &mut Vec<String>) {
    let mut ctx: Option<(i64, i64)> = None;
    for i in 0..lines.len() {
        if !is_mf2(&lines[i]) {
            continue;
        }
        range_ctx(lines, &mut ctx, i);
        let Some(f) = flds(&lines[i]) else { continue };
        let Some((lru, lrf)) = ctx else { continue };
        if lru != 2 {
            continue;
        }
        let n1 = f[4] as usize;
        let n2 = f[5] as usize;
        if n2 == 0 || n1 == 0 {
            continue;
        }
        if lrf == 2 && n1 == 6 * (n2 + 1) {
            // case-C dof LIST: first payload line carries the dof record
            let li = i + 1;
            if li >= lines.len() || !is_mf2(&lines[li]) {
                continue;
            }
            let Some(d) = flds(&lines[li]) else { continue };
            for (pos, label) in [(2usize, "AMUX"), (3, "AMUN"), (5, "AMUF")] {
                round_dof(lines, li, pos, d[pos], label, entries);
            }
        } else if lrf == 1 && n1 == 6 * n2 {
            // case-A rows: AMUN is value index 2 of each 6-value row
            for j in 0..(n1 / 6) {
                let value_index = 6 * j + 2;
                let li = i + 1 + value_index / 6;
                let pos = value_index % 6;
                if li >= lines.len() || !is_mf2(&lines[li]) {
                    break;
                }
                let Some(d) = flds(&lines[li]) else { break };
                round_dof(lines, li, pos, d[pos], "AMUN", entries);
            }
        }
    }
}

/// Case-C unresolved tables under log laws: exact-zero widths -> 1e-30.
fn fix_unresolved_zero_width(lines: &mut [String], entries: &mut Vec<String>) {
    let mut ctx: Option<(i64, i64)> = None;
    for i in 0..lines.len() {
        if !is_mf2(&lines[i]) {
            continue;
        }
        range_ctx(lines, &mut ctx, i);
        let Some(f) = flds(&lines[i]) else { continue };
        let Some((lru, lrf)) = ctx else { continue };
        if !(lru == 2 && lrf == 2) {
            continue;
        }
        let (l1, n1, n2) = (f[2] as i64, f[4] as usize, f[5] as usize);
        if !(3..=5).contains(&l1) || n2 < 1 || n1 != 6 * (n2 + 1) {
            continue;
        }
        let npayload = n1.div_ceil(6);
        let mut flat: Vec<(usize, usize, f64)> = Vec::with_capacity(n1);
        let mut ok = true;
        for k in 0..npayload {
            let li = i + 1 + k;
            if li >= lines.len() || !is_mf2(&lines[li]) {
                ok = false;
                break;
            }
            let Some(pf) = flds(&lines[li]) else {
                ok = false;
                break;
            };
            for (pos, v) in pf.iter().enumerate() {
                flat.push((li, pos, *v));
            }
        }
        if !ok || flat.len() < n1 {
            continue;
        }
        for row in 0..n2 {
            let base = 6 + row * 6;
            for col in 1..=5usize {
                let vi = base + col;
                if vi >= flat.len() {
                    break;
                }
                let (li, pos, v) = flat[vi];
                if v == 0.0 {
                    write_field(lines, li, pos, "1.000000-30");
                    entries.push(format!(
                        "unresolved_zero_width: row {row} field {col} zero width set to 1e-30 under INT={l1} at record {}",
                        li + 1
                    ));
                }
            }
        }
    }
}

/// Resolved-resonance L-group LISTs: GT below GN+GG+GF.
///
/// The reconstruction's effective width is `max(total, components)` for
/// every LRX (`legacy_effective_total_width`), so under LRX=0 — where no
/// competitive widths are declared — a GT at or below the component sum
/// is an omitted or misprinted total and `GT = sum` reproduces the
/// evaluated physics exactly. Under LRX=1/2 (competitive widths
/// declared) only a last-place deficit (<= 5e-4 relative) is normalized;
/// larger deficits are flagged and left untouched.
fn fix_bw_gt(lines: &mut [String], entries: &mut Vec<String>) {
    const REL_TOL: f64 = 1e-6;
    const PATCH_CAP: f64 = 5e-4;
    let mut ctx: Option<(i64, i64)> = None;
    let mut i = 0;
    while i < lines.len() {
        if !is_mf2(&lines[i]) {
            i += 1;
            continue;
        }
        range_ctx(lines, &mut ctx, i);
        let Some(f) = flds(&lines[i]) else {
            i += 1;
            continue;
        };
        // L-group LIST head signature (LRF=1/2): NPL = 6*NRS, NRS >= 1,
        // LRX in {0,1,2}, inside an LRU=1 resolved range. Other records
        // (CONTs, dof LISTs) keep moving.
        let n1 = f[4] as usize;
        let nrs = f[5] as usize;
        let lrx = f[3] as i64;
        let Some((lru, lrf)) = ctx else {
            i += 1;
            continue;
        };
        if !(lru == 1
            && (lrf == 1 || lrf == 2)
            && nrs >= 1
            && n1 == 6 * nrs
            && (0..=2).contains(&lrx))
        {
            i += 1;
            continue;
        }
        let npayload = n1.div_ceil(6);
        let mut consumed = 0usize;
        'rows: for k in 0..npayload {
            let li = i + 1 + k;
            if li >= lines.len() || !is_mf2(&lines[li]) {
                break;
            }
            consumed = k + 1;
            let Some(row) = flds(&lines[li]) else {
                continue;
            };
            // every 6-field row inside a matched L-group LIST is a
            // resonance (E, AJ, GT, GN, GG, GF) — AJ may legitimately be 0
            if row[3] <= 0.0 {
                continue;
            }
            let comp = row[3] + row[4] + row[5];
            let deficit = comp - row[2];
            if deficit <= REL_TOL * row[2].abs().max(comp.abs()) {
                continue;
            }
            let rel = deficit / row[2].abs().max(comp.abs()).max(1e-30);
            if lrx == 0 || rel <= PATCH_CAP {
                write_field(lines, li, 2, &ef_str(comp));
                let class = if lrx == 0 {
                    "bw_total_width_lrx0"
                } else {
                    "bw_total_width"
                };
                entries.push(format!(
                    "{class}: GT {} normalized to GN+GG+GF={comp} (LRX={lrx}, rel deficit {rel:.3e}) at record {}",
                    row[2],
                    li + 1
                ));
            } else {
                entries.push(format!(
                    "bw_total_width_flagged: GT {} deficit {rel:.3e} exceeds normalization cap at record {}; left untouched",
                    row[2],
                    li + 1
                ));
            }
            if consumed == npayload {
                break 'rows;
            }
        }
        i += 1 + consumed;
    }
}

/// MF=2 TAB1 records: swap adjacent out-of-order (x,y) pairs as pairs.
fn fix_tab1_order(lines: &mut [String], entries: &mut Vec<String>) {
    let mut i = 0;
    while i < lines.len() {
        let f = if is_mf2(&lines[i]) {
            flds(&lines[i])
        } else {
            None
        };
        let Some(f) = f else {
            i += 1;
            continue;
        };
        if !(f[4] >= 1.0 && f[5] >= 2.0) {
            i += 1;
            continue;
        }
        let (nr, np) = (f[4] as usize, f[5] as usize);
        let nint = (2 * nr).div_ceil(6);
        // the interp payload must be a valid TAB1 shape before we trust it
        let mut ib: Vec<f64> = Vec::new();
        for k in 0..nint {
            let li = i + 1 + k;
            if li >= lines.len() || !is_mf2(&lines[li]) {
                break;
            }
            if let Some(fb) = flds(&lines[li]) {
                ib.extend(fb);
            }
        }
        let nbts: Vec<f64> = ib.iter().step_by(2).copied().take(nr).collect();
        let ints: Vec<f64> = ib.iter().skip(1).step_by(2).copied().take(nr).collect();
        let valid = nbts.len() == nr
            && nbts.iter().all(|v| *v >= 1.0)
            && nbts.iter().sum::<f64>() as usize == np
            && ints.iter().all(|v| (1.0..=5.0).contains(v));
        if !valid {
            i += 1;
            continue;
        }
        let ndat = (2 * np).div_ceil(6);
        let start = i + 1 + nint;
        // points as (x, y, line, field)
        let mut pts: Vec<(f64, f64, usize, usize)> = Vec::with_capacity(np);
        for (j, line) in lines[start..(start + ndat).min(lines.len())]
            .iter()
            .enumerate()
            .map(|(k, l)| (start + k, l))
        {
            if !is_mf2(line) {
                break;
            }
            let Some(fj) = flds(line) else { continue };
            for pos in [0usize, 2, 4] {
                if pts.len() < np {
                    pts.push((fj[pos], fj[pos + 1], j, pos));
                }
            }
        }
        for k in 0..pts.len().saturating_sub(1) {
            if pts[k].0 > pts[k + 1].0 {
                let (xa, ya, la, pa) = pts[k];
                let (xb, yb, lb, pb) = pts[k + 1];
                write_field(lines, la, pa, &ef_str(xb));
                write_field(lines, la, pa + 1, &ef_str(yb));
                write_field(lines, lb, pb, &ef_str(xa));
                write_field(lines, lb, pb + 1, &ef_str(ya));
                pts[k] = (xb, yb, la, pa);
                pts[k + 1] = (xa, ya, lb, pb);
                entries.push(format!(
                    "tab1_order: out-of-order abscissa pair {xa:.6e}/{xb:.6e} swapped at record {}",
                    la + 1
                ));
            }
        }
        i = start + ndat;
    }
}

// ---------------------------------------------------------------------------
// entry point
// ---------------------------------------------------------------------------

/// Run the profile's tape-level fixes.
///
/// Returns the rewritten tape and the per-file normalization ledger. The
/// returned text always round-trips: with `NormalizeProfile::None` it is
/// byte-identical to the input.
pub fn normalize_tape(text: &str, profile: NormalizeProfile) -> NormalizedTape {
    // None preserves every defect byte-for-byte; Tendl performs no
    // text surgery (its normalization is collapse-level state-sum
    // reconciliation, applied in the builder).
    if matches!(profile, NormalizeProfile::None | NormalizeProfile::Tendl) {
        return NormalizedTape {
            text: text.to_string(),
            entries: Vec::new(),
        };
    }
    let mut entries = Vec::new();
    // preserve line endings; ENDF tapes are \n-terminated
    let mut lines: Vec<String> = text.split_inclusive('\n').map(str::to_string).collect();
    if let Some(last) = lines.last() {
        if last.is_empty() {
            lines.pop();
        }
    }

    fix_zaawr(&mut lines, &mut entries);
    fix_lrf0_range(&mut lines, &mut entries);
    fix_rml_photon(&mut lines, &mut entries);
    fix_unresolved_dof(&mut lines, &mut entries);
    fix_unresolved_zero_width(&mut lines, &mut entries);
    fix_bw_gt(&mut lines, &mut entries);
    fix_tab1_order(&mut lines, &mut entries);

    NormalizedTape {
        text: lines.concat(),
        entries,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(fields: &[f64], tail_str: &str) -> String {
        let mut line = String::new();
        for v in fields {
            line.push_str(&ef_str(*v));
        }
        line.push_str(tail_str);
        line.push('\n');
        line
    }

    /// Minimal MF2/MF1 skeleton with one L-group LIST carrying a GT=0
    /// resonance under LRX=0 and a small deficit row.
    fn mf2_fixture() -> String {
        let mut t = String::new();
        t.push_str(&row(&[28063.0, 62.0, 0.0, 0.0, 1.0, 0.0], "2840 1451    1"));
        t.push_str(&row(&[28063.0, 62.0, 0.0, 0.0, 1.0, 0.0], "2840 2151    2"));
        t.push_str(&row(&[1e-5, 28137.0, 1.0, 2.0, 0.0, 1.0], "2840 2151    3"));
        t.push_str(&row(&[0.5, 0.54, 0.0, 0.0, 2.0, 0.0], "2840 2151    4"));
        // L-group head: AWRI, QX, L, LRX=0, NPL=12, NRS=2
        t.push_str(&row(&[62.0, 0.0, 0.0, 0.0, 12.0, 2.0], "2840 2151    5"));
        // resonance rows: E, AJ, GT, GN, GG, GF
        t.push_str(&row(&[-100.0, 0.0, 0.0, 0.85, 0.45, 0.0], "2840 2151    6"));
        t.push_str(&row(
            &[500.0, 1.0, 1.30832, 0.8542891, 0.4540326, 0.0],
            "2840 2151    7",
        ));
        t.push_str(&row(&[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "2840 2151    8"));
        t
    }

    #[test]
    fn bw_lrx0_omitted_and_deficit_totals_normalize() {
        let out = normalize_tape(&mf2_fixture(), NormalizeProfile::EndfB8);
        let rows: Vec<&str> = out.text.lines().collect();
        assert!(rows[5].contains("1.300000+0"), "{}", rows[5]);
        let gt_field = &rows[5][22..33];
        assert_eq!(gt_field.trim(), "1.300000+0");
        assert!(rows[6].contains("1.308322+0"), "{}", rows[6]);
        assert!(out
            .entries
            .iter()
            .any(|e| e.starts_with("bw_total_width_lrx0")));
    }

    #[test]
    fn none_profile_is_byte_identical() {
        let t = mf2_fixture();
        let out = normalize_tape(&t, NormalizeProfile::None);
        assert_eq!(out.text, t);
        assert!(out.entries.is_empty());
    }

    #[test]
    fn lrf0_range_normalizes() {
        let mut t = String::new();
        t.push_str(&row(&[28063.0, 62.0, 0.0, 0.0, 1.0, 0.0], "2840 1451    1"));
        t.push_str(&row(&[28063.0, 62.0, 0.0, 0.0, 1.0, 0.0], "2840 2151    2"));
        t.push_str(&row(&[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "2840 2151    3"));
        let out = normalize_tape(&t, NormalizeProfile::EndfB8);
        assert!(out.text.contains(" 1.000000-5 2.000000+7"));
        assert!(out.entries.iter().any(|e| e.starts_with("mf2_lrf0_range")));
    }

    #[test]
    #[ignore]
    fn dump_real_tape_entries() {
        let path = std::env::var("NTAPE").unwrap();
        let t = std::fs::read_to_string(path).unwrap();
        let out = normalize_tape(&t, NormalizeProfile::EndfB8);
        eprintln!("entries: {}", out.entries.len());
        for e in &out.entries {
            eprintln!("  {e}");
        }
        if out.text != t {
            std::fs::write("/tmp/normalized.endf", &out.text).unwrap();
            eprintln!("normalized text written to /tmp/normalized.endf");
        }
    }

    #[test]
    fn rml_photon_pair_normalizes() {
        let mut t = String::new();
        // Range CONT (EL, EH, LRU=1, LRF=7, NRO, NPP) gates the fixer on.
        t.push_str(&row(&[1.0, 1.0e7, 1.0, 7.0, 0.0, 1.0], "2925 2151    7"));
        t.push_str(&row(&[0.0, 0.0, 2.0, 0.0, 24.0, 4.0], "2925 2151    8"));
        t.push_str(&row(&[0.0, 63.4, 0.0, 0.0, 1.0, 0.0], "2925 2151    9"));
        t.push_str(&row(&[0.0, -1.0, 0.0, 102.0, 0.0, 0.0], "2925 2151   10"));
        t.push_str(&row(&[1.0, 62.4, 0.0, 0.0, 0.5, -1.5], "2925 2151   11"));
        t.push_str(&row(&[0.0, 1.0, 1.0, 2.0, 0.0, 0.0], "2925 2151   12"));
        let out = normalize_tape(&t, NormalizeProfile::EndfB8);
        let rows: Vec<&str> = out.text.lines().collect();
        assert_eq!(rows[3][11..22].trim(), "0.000000+0", "{}", rows[3]);
        assert!(out.entries.iter().any(|e| e.starts_with("rml_photon_pair")));
    }
}
