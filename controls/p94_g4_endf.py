#!/usr/bin/env python3
"""P94 G4: independent Python ENDF-6 parser + exact flat-lethargy integrator.

Does NOT import any ACTINV Rust or Python code. Re-derives, from the raw TENDL-2025 gamma
ENDF-6 evaluations for Fe-56, Cu-63, Ta-181 and W-186, every residual group row the candidate
library builds on CCFE-162 (162 groups, 0 K), and compares against the built `.npz` library
(read only as a flat binary array; no ACTINV loader code is used).

Usage:
    python3 controls/p94_g4_endf.py --library target/p94/g3_gamma.npz --out target/p94/g4_summary.json

Physics replicated independently (see protocols/ACTINV-P94_PROTOCOL.md and the P94 implementation
in crates/actinv-data/src/{activation,builder,groups}.rs, read for understanding only -- none of
that Rust is imported or executed here):
  - Residual ZA = target + projectile(0,0 for gamma) - emitted, i.e. (per MT):
        z = Zt + dz + 0 ;  a = At + da + 0 - 1
    where (dz, da) is the vendored per-MT neutron-incidence delta (mt_products.json) when present.
  - Missing-MF=8 ground-state rule (P94 item 2): MT=4 and MT=50..91 absent from mt_products.json
    and carrying no MF=8 of their own resolve with delta (0, 0) (ground-state residual only).
  - MT=4/MT=50-91 dedup: exactly one of the two carries the single-neutron channel. MT=4 with
    its own MF=8 declaration carries it and every MT=50-91 section is skipped; MT=4 without MF=8
    is skipped when MT=50-91 detail is present.
  - MT=18/MF=10 IZAP=-1 is the fission-sentinel placeholder, not a residual row (skipped).
  - MF=6/MT=5 aggregate production: residual cross section = MF=3 MT=5 total x MF=6 yield table
    (matched to the MF=8 LMF=6 descriptor by ZAP), collapsed as a product.
  - Flat-lethargy group collapse: collapse[g] = (INT sigma(E) dE/E over [E_lo,E_g, E_hi,g]) /
    ln(E_hi,g / E_lo,g), using each table's own per-segment ENDF interpolation law (1..5).
    Evaluated here via adaptive high-precision quadrature (scipy, epsrel=1e-12) on the exact
    mathematical integrand, split at every interpolation breakpoint inside the group -- a
    different numerical method from the Rust implementation's closed-form/adaptive-Simpson
    mix, verifying the same physics by an independent computational path.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
GAMMA_DATA = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "g"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_162_groups.json"
MT_PRODUCTS_JSON = ROOT / "crates" / "actinv-data" / "data" / "mt_products.json"

TARGETS = {
    "g-Fe056.tendl": 26056,
    "g-Cu063.tendl": 29063,
    "g-Ta181.tendl": 73181,
    "g-W186.tendl": 74186,
}

# P100 G4 (protocols/ACTINV-P100_PROTOCOL.md): the second, independent run against the 8-nuclide
# TENDL-2017 set that the P98/P100 G5 library is built from (--dataset tendl2017), used together
# with --profile-normalize (the profile's threshold extension + per-group normalization).
GAMMA_DATA_2017 = Path.home() / "nuclear-data" / "tendl-2017" / "files" / "g"
TARGETS_2017 = {
    "g-Fe056.tendl": 26056,
    "g-Cu063.tendl": 29063,
    "g-Ni058.tendl": 28058,
    "g-Nb093.tendl": 41093,
    "g-W186.tendl": 74186,
    "g-Ta181.tendl": 73181,
    "g-Al027.tendl": 13027,
    "g-Pb208.tendl": 82208,
}
# Prefix every "MF=3 threshold extension" ledger line must start with (protocol G3/G4 wording,
# also matched by controls/check_p94.py's G3 comparison -- kept as one literal in both places).
EXTENSION_LEDGER_PREFIX = re.compile(r"^MT(\d+): MF=3 threshold extension:")

REL_TOL = 1e-9
FLOOR_FRACTION = 1e-12
MAX_GROUP_E = 200.0e6


def rel(value, reference):
    """|value - reference| / |reference|; inf when only the reference is zero."""
    if reference == 0.0:
        return 0.0 if value == 0.0 else math.inf
    return abs(value - reference) / abs(reference)


# ----------------------------------------------------------------- ENDF-6 primitives

def _endf_float(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    for i in range(1, len(text)):
        if text[i] in "+-" and text[i - 1] not in "eE":
            return float(text[:i] + "e" + text[i:])
    return float(text)


def _endf_int(text: str) -> int:
    text = text.strip()
    return int(text) if text else 0


def _fields(line: str):
    line = line.ljust(66)
    return [line[i * 11:(i + 1) * 11] for i in range(6)]


def _mf_mt(line: str):
    if len(line) < 75:
        return None, None
    return _endf_int(line[70:72]), _endf_int(line[72:75])


def read_cont(lines, i):
    f = _fields(lines[i])
    c1, c2 = _endf_float(f[0]), _endf_float(f[1])
    l1, l2, n1, n2 = (_endf_int(f[2]), _endf_int(f[3]), _endf_int(f[4]), _endf_int(f[5]))
    return (c1, c2, l1, l2, n1, n2), i + 1


def read_list(lines, i):
    head, i = read_cont(lines, i)
    n1 = head[4]
    values = []
    while len(values) < n1:
        for f in _fields(lines[i]):
            if len(values) == n1:
                break
            values.append(_endf_float(f))
        i += 1
    return head, values, i


def read_tab1(lines, i):
    head, i = read_cont(lines, i)
    nr, np_ = head[4], head[5]
    raw = []
    while len(raw) < 2 * nr:
        for f in _fields(lines[i]):
            if len(raw) == 2 * nr:
                break
            raw.append(_endf_int(f))
        i += 1
    interp = [(raw[2 * k], raw[2 * k + 1]) for k in range(nr)]
    pts = []
    while len(pts) < 2 * np_:
        for f in _fields(lines[i]):
            if len(pts) == 2 * np_:
                break
            pts.append(_endf_float(f))
        i += 1
    x = [pts[2 * k] for k in range(np_)]
    y = [pts[2 * k + 1] for k in range(np_)]
    return {"c1": head[0], "c2": head[1], "l1": head[2], "l2": head[3],
            "interp": interp, "x": x, "y": y}, i


def read_tab2(lines, i):
    head, i = read_cont(lines, i)
    nr = head[4]
    raw = []
    while len(raw) < 2 * nr:
        for f in _fields(lines[i]):
            if len(raw) == 2 * nr:
                break
            raw.append(_endf_int(f))
        i += 1
    return head, i


def skip_mf6_law(lines, i, law):
    """Mirrors builder.rs consume_mf6_law: never interprets the distribution data, only
    advances past it structurally so the next MF=6 product's TAB1 head is found correctly."""
    if law in (0, 3, 4):
        return i
    if law in (1, 2, 5):
        head, i = read_tab2(lines, i)
        n2 = head[5]
        for _ in range(n2):
            _, _, i = read_list(lines, i)
        return i
    if law == 6:
        _, i = read_cont(lines, i)
        return i
    if law == 7:
        ehead, i = read_tab2(lines, i)
        for _ in range(ehead[5]):
            ahead, i = read_tab2(lines, i)
            for _ in range(ahead[5]):
                _, i = read_tab1(lines, i)
        return i
    raise ValueError(f"unsupported MF=6 LAW={law}")


def split_sections(text: str):
    lines = text.splitlines()
    sections = {}
    order = []
    i = 0
    while i < len(lines):
        mf, mt = _mf_mt(lines[i])
        if mf is None or mf == 0 or mt == 0:
            i += 1
            continue
        key = (mf, mt)
        start = i
        while i < len(lines):
            mf2, mt2 = _mf_mt(lines[i])
            if mf2 != mf or mt2 != mt:
                break
            i += 1
        sections.setdefault(key, []).extend(lines[start:i])
        order.append(key)
    return sections


# ----------------------------------------------------------------- MF-level parsers

def parse_mf1_451(lines):
    head, i = read_cont(lines, 0)
    za = int(round(head[0]))
    awr = head[1]
    target, i = read_cont(lines, i)
    lis, liso, elis = target[2], target[3], target[0]
    incident, i = read_cont(lines, i)
    nsub, awi = incident[4], incident[0]
    processing, i = read_cont(lines, i)
    temperature_k = processing[0]
    return {"za": za, "awr": awr, "lis": lis, "liso": liso, "elis_eV": elis,
            "nsub": nsub, "awi": awi, "temperature_K": temperature_k}


def parse_mf3(lines):
    # MF=3 sections open with a bare CONT head (ZA, AWR, 0, 0, 0, 0) before the real TAB1
    # (QM, QI, 0, 0, NR, NP) record; mirrors builder.rs::parse_mf3's read_tab1_checked(.., 1).
    table, i = read_tab1(lines, 1)
    return table


def parse_mf8(lines):
    head, i = read_cont(lines, 0)
    n1, no = head[4], head[5]
    products = []
    for _ in range(n1):
        if no == 0:
            h, vals, i = read_list(lines, i)
            c1, c2, l1, l2 = h[0], h[1], h[2], h[3]
        else:
            (c1, c2, l1, l2, _, _), i = read_cont(lines, i)
        zap = -1 if c1 == -1.0 else int(round(c1))
        products.append({"zap": zap, "elfs_eV": c2, "lfs": l2, "lmf": l1})
    return products


def parse_mf9_or_10(lines):
    head, i = read_cont(lines, 0)
    n1 = head[4]
    products = []
    for _ in range(n1):
        table, i = read_tab1(lines, i)
        zap = -1 if table["l1"] == -1 else int(table["l1"])
        products.append({"zap": zap, "qm_eV": table["c1"], "qi_eV": table["c2"],
                          "lfs": table["l2"], "table": table})
    return products


def parse_mf6(lines):
    head, i = read_cont(lines, 0)
    n1 = head[4]
    products = []
    for _ in range(n1):
        table, i = read_tab1(lines, i)
        zap = int(round(table["c1"]))
        law = table["l2"]
        products.append({"zap": zap, "awp": table["c2"], "law": law, "yield_table": table})
        i = skip_mf6_law(lines, i, law)
    return products


def parse_evaluation(path: Path):
    text = path.read_text(errors="replace")
    sections = split_sections(text)
    if (1, 451) not in sections:
        raise ValueError(f"{path}: no MF=1/MT=451 header")
    meta = parse_mf1_451(sections[(1, 451)])
    mf3 = {mt: parse_mf3(lines) for (mf, mt), lines in sections.items() if mf == 3}
    mf6 = {mt: parse_mf6(lines) for (mf, mt), lines in sections.items() if mf == 6}
    mf8 = {mt: parse_mf8(lines) for (mf, mt), lines in sections.items() if mf == 8}
    mf9 = {mt: parse_mf9_or_10(lines) for (mf, mt), lines in sections.items() if mf == 9}
    mf10 = {mt: parse_mf9_or_10(lines) for (mf, mt), lines in sections.items() if mf == 10}
    return {"meta": meta, "mf3": mf3, "mf6": mf6, "mf8": mf8, "mf9": mf9, "mf10": mf10}


# ----------------------------------------------------------------- TAB1 evaluation + lethargy integral

def _law_for_segment(table, seg):
    endpoint = seg + 2
    for nbt, law in table["interp"]:
        if endpoint <= nbt:
            return law
    raise ValueError("no interpolation law for segment")


def _segment_index(table, value):
    x = table["x"]
    import bisect
    upper = bisect.bisect_right(x, value)
    seg = max(0, min(upper - 1, len(x) - 2))
    return seg


def evaluate(table, e):
    x, y = table["x"], table["y"]
    if e < x[0] or e > x[-1]:
        return 0.0
    if e == x[-1]:
        return y[-1]
    seg = _segment_index(table, e)
    x1, x2, y1, y2 = x[seg], x[seg + 1], y[seg], y[seg + 1]
    if x2 == x1:
        return y2
    law = _law_for_segment(table, seg)
    lin = (e - x1) / (x2 - x1)
    if law == 1:
        return y1
    if law == 2:
        return y1 + lin * (y2 - y1)
    if law == 3:
        return y1 + math.log(e / x1) / math.log(x2 / x1) * (y2 - y1)
    if law == 4:
        return y1 * (y2 / y1) ** lin
    if law == 5:
        return y1 * (y2 / y1) ** (math.log(e / x1) / math.log(x2 / x1))
    raise ValueError(f"unsupported INT={law}")


def table_breakpoints_in(table, lo, hi):
    return sorted({x for x in table["x"] if lo < x < hi})


def lethargy_integral(table, lo, hi):
    """INT sigma(E) dE/E over [lo, hi], independent high-precision quadrature."""
    x0, x1 = table["x"][0], table["x"][-1]
    lo = max(lo, x0)
    hi = min(hi, x1)
    if hi <= lo:
        return 0.0
    breaks = [lo] + table_breakpoints_in(table, lo, hi) + [hi]
    total = 0.0
    for a, b in zip(breaks[:-1], breaks[1:]):
        if b <= a:
            continue
        f = lambda u, a=a, b=b: evaluate(table, u) / u
        val, _ = quad(f, a, b, epsabs=0.0, epsrel=1e-12, limit=200)
        total += val
    return total


def product_lethargy_integral(tables, lo, hi):
    x0 = max(t["x"][0] for t in tables)
    x1 = min(t["x"][-1] for t in tables)
    lo = max(lo, x0)
    hi = min(hi, x1)
    if hi <= lo:
        return 0.0
    breaks = {lo, hi}
    for t in tables:
        breaks |= set(table_breakpoints_in(t, lo, hi))
    breaks = sorted(breaks)
    total = 0.0
    for a, b in zip(breaks[:-1], breaks[1:]):
        if b <= a:
            continue
        f = lambda u, a=a, b=b: math.prod(evaluate(t, u) for t in tables) / u
        val, _ = quad(f, a, b, epsabs=0.0, epsrel=1e-12, limit=200)
        total += val
    return total


def collapse(table, boundaries):
    out = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        if hi <= table["x"][0] or lo >= table["x"][-1]:
            out.append(0.0)
            continue
        out.append(lethargy_integral(table, lo, hi) / math.log(hi / lo))
    return out


def collapse_product(tables, boundaries):
    out = []
    lo_bound = max(t["x"][0] for t in tables)
    hi_bound = min(t["x"][-1] for t in tables)
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        if hi <= lo_bound or lo >= hi_bound:
            out.append(0.0)
            continue
        out.append(product_lethargy_integral(tables, lo, hi) / math.log(hi / lo))
    return out


# ------------------------------------------------- P100: MF=3 threshold extension (independent)
#
# Re-derived only from protocols/ACTINV-P100_PROTOCOL.md's "Change under test" section --
# crates/actinv-data/src/builder.rs is deliberately NOT read for this rule (the task's
# independence requirement for the new G4 checker). Everything else in this module (evaluate,
# collapse, reconcile_states, ...) was already independent of builder.rs from P94/P98 and is
# reused unchanged.

def left_value_at(table, e):
    """The value of a TAB1 table at energy e, approached from below ("left limit"): at an exact
    tabulated energy that is doubled (two consecutive points sharing one x, i.e. a discontinuity),
    this returns the FIRST (lower-side) of the two y-values, not the second. Off-grid energies are
    evaluated by the table's own interpolation law via evaluate(); energies outside [x[0], x[-1]]
    are 0.0 (same convention evaluate() already uses elsewhere in this module).

    This is a different reading from evaluate()'s own doubled-point handling (which, via
    bisect_right, resolves to the LAST/upper-side value at an exact doubled point) -- evaluate()
    is used elsewhere for right-continuous group collapse, while the protocol's threshold
    extension explicitly asks for each MF=10 section's own "left limit" at E3."""
    x, y = table["x"], table["y"]
    # Nothing is tabulated below the first point, so the limit from below there is 0 (P100 run 1
    # read the first point's own value, triggering a spurious zero-width extension at MTs whose
    # states start exactly at E3; corrected after that run, disclosed in the ledger).
    if not x or e <= x[0] or e > x[-1]:
        return 0.0
    import bisect
    first = bisect.bisect_left(x, e)
    last = bisect.bisect_right(x, e) - 1
    if first <= last and x[first] == e:
        return y[first]
    return evaluate(table, e)


def apply_mf3_threshold_extension(mt, mf3_table, mf10_zap_ge0_sections):
    """protocols/ACTINV-P100_PROTOCOL.md, "Change under test" -> "MF=3 threshold extension".

    Applies (separately per MT, gamma only -- every file this module reads is gamma) when:
      - the MT has an MF=3 table, whose first energy is E3 (mf3_table["x"][0]);
      - the MT has MF=10 sections with ZAP >= 0 (mf10_zap_ge0_sections, already filtered by the
        caller);
      - at least one of those sections has a positive tabulated value below E3, or a positive
        left limit at E3 (the "trigger").
    Then the MF=3 table is extended below E3 by the summed MF=10 ZAP>=0 sections: one point at
    every energy any of those sections tabulates below E3 (summed value there), then a point at
    E3 carrying the summed left limit, followed by the original first MF=3 point (a doubled point
    when the two differ), the added region lin-lin (INT=2), original regions kept. The rule only
    applies when every one of those sections is lin-lin on every segment below E3 ("linearity
    condition"); otherwise the table is unchanged and a ledger line names the reason.

    Returns (table_to_use, applied: bool, ledger_line_or_None). A ledger line is produced on
    application (naming the MT, E3, first added energy and points added, per the protocol's
    "Ledger" bullet) and on a linearity-condition failure (naming the reason); not when the rule
    simply does not trigger (no positive value below/at E3 -- "no change when every state is zero
    below E3" per this protocol's G1 list, which does not ask for a ledger line in that case)."""
    if not mf10_zap_ge0_sections:
        return mf3_table, False, None
    e3 = mf3_table["x"][0]
    triggered = any(y > 0.0 for t in mf10_zap_ge0_sections for x, y in zip(t["x"], t["y"]) if x < e3)
    triggered = triggered or any(left_value_at(t, e3) > 0.0 for t in mf10_zap_ge0_sections)
    if not triggered:
        return mf3_table, False, None
    for t in mf10_zap_ge0_sections:
        for i in range(len(t["x"]) - 1):
            if t["x"][i] < e3 and _law_for_segment(t, i) != 2:
                reason = (f"MT{mt}: MF=3 threshold extension not applied (a MF=10 ZAP>=0 section "
                          f"is not lin-lin on a segment below E3={e3!r})")
                return mf3_table, False, reason
    added_x = sorted({x for t in mf10_zap_ge0_sections for x in t["x"] if x < e3})
    added_y = [sum(evaluate(t, x) for t in mf10_zap_ge0_sections) for x in added_x]  # the value there
    joining_y = sum(left_value_at(t, e3) for t in mf10_zap_ge0_sections)
    orig_x0, orig_y0 = mf3_table["x"][0], mf3_table["y"][0]
    new_x = added_x + [e3, orig_x0] + list(mf3_table["x"][1:])
    new_y = added_y + [joining_y, orig_y0] + list(mf3_table["y"][1:])
    offset = len(added_x) + 1  # points inserted before the original table's own first point
    new_interp = [(len(added_x) + 2, 2)] + [(nbt + offset, law) for nbt, law in mf3_table["interp"]]
    new_table = {**mf3_table, "x": new_x, "y": new_y, "interp": new_interp}
    n_added = len(added_x) + 1  # added-below-E3 points, plus the new joining point at E3
    ledger = (f"MT{mt}: MF=3 threshold extension: E3={e3!r}, "
              f"first_added_energy={(added_x[0] if added_x else e3)!r}, points_added={n_added}")
    return new_table, True, ledger


def classify_mt18(ev):
    """P98's IZAP=0 photofission rule (P98 protocol, "Why this protocol exists"), re-applied here
    under --profile-normalize: an MT18 with no MF=3 MT18, exactly one MF=10 MT18 section with
    IZAP=0 LFS=0, and MF=8 MT18 declaring nothing or only ZAP=0 LMF=10 LFS=0, is the total-
    photofission sentinel -- not a residual, produces no inventory row. This mirrors how MT18's
    TENDL-2025 IZAP=-1 sentinel is already treated by build_rows (`if mt == 18: continue`, reached
    only when MT18 HAS an MF=3 entry -- that path is untouched by this function). When MT18 has no
    MF=3 entry, build_rows never visits it at all (the mf3-keyed loop simply never reaches MT18),
    so no row-suppression code is needed here; this function only validates the shape so an
    unrecognized MT18 encoding fails loud instead of silently producing zero rows.

    Returns a dict describing the shape found (or "absent"); raises ValueError for a shape that
    matches neither the TENDL-2025 nor the TENDL-2017 sentinel pattern."""
    has_mf3 = 18 in ev["mf3"]
    mf10_18 = ev["mf10"].get(18, [])
    mf8_18 = ev["mf8"].get(18, [])
    if has_mf3:
        izap_neg1 = [p for p in mf10_18 if p["zap"] == -1]
        return {"shape": "mf3_present_tendl2025_style", "izap_neg1_sections": len(izap_neg1)}
    if not mf10_18 and not mf8_18:
        return {"shape": "absent"}
    izap0 = [p for p in mf10_18 if p["zap"] == 0 and p["lfs"] == 0]
    mf8_only_sentinel = all(d["zap"] == 0 and d["lmf"] == 10 and d["lfs"] == 0 for d in mf8_18)
    if len(mf10_18) == 1 and len(izap0) == 1 and mf8_only_sentinel:
        return {"shape": "izap0_total_photofission_sentinel"}
    raise ValueError(
        f"MT18 declared photofission product yield present: n_mf10_sections={len(mf10_18)}, "
        f"n_izap0_lfs0={len(izap0)}, mf8_mt18={mf8_18}")


# ----------------------------------------------------------------- residual rows

def residual_product(target_za, neutron_delta):
    z, a = target_za // 1000, target_za % 1000
    dz, da = neutron_delta
    rz = z + dz + 0
    ra = a + da + 0 - 1
    if rz > 0 and ra > 0 and ra >= rz:
        return rz * 1000 + ra
    return None


def missing_mf8_ground_state_delta(mt):
    if mt == 4 or 50 <= mt <= 91:
        return (0, 0)
    return None


def build_rows(ev, target_za, mt_products, boundaries, profile_normalize=False):
    """Returns {(mt, zap, lfs): [group values]} replicating the P94 gamma rules.

    profile_normalize=False (the default) preserves P94/P98 behaviour exactly -- the P100 MF=3
    threshold extension and the profile's per-group state-sum normalization (reconcile_states)
    are both gated behind this flag and never run otherwise."""
    rows = {}
    ledger = []
    for mt, table in sorted(ev["mf3"].items()):
        if mt in (1, 2, 3, 27, 101) or mt >= 500:
            continue  # totals/non-physical/redundant summary MTs, not activation products
        if profile_normalize:
            mf10_zap_ge0 = [p["table"] for p in ev["mf10"].get(mt, []) if p["zap"] >= 0]
            table, _applied, ext_line = apply_mf3_threshold_extension(mt, table, mf10_zap_ge0)
            if ext_line:
                ledger.append(ext_line)
        has_mf8 = mt in ev["mf8"] and ev["mf8"][mt]
        # MT=4 dedup: only when MT=4 itself has no MF=8 of its own.
        if mt == 4 and not has_mf8 and any(dmt in ev["mf3"] for dmt in range(50, 92)):
            ledger.append(f"MT4 skipped (dedup with MT50-91 detail, no MF=8 of its own)")
            continue
        if 50 <= mt <= 91 and ev["mf8"].get(4):
            ledger.append(f"MT{mt} skipped (MT4 carries its own MF=8 state-resolved declaration)")
            continue
        if mt == 18:
            # MF=10 IZAP=-1 sentinel only; no real residual row from MT=18 in v1.
            continue
        if not has_mf8:
            delta = mt_products.get(str(mt))
            if delta is None:
                delta = missing_mf8_ground_state_delta(mt)
            if delta is None:
                continue  # unmapped leakage -- not in scope for this comparison
            zap = residual_product(target_za, tuple(delta))
            if zap is None:
                continue
            rows[(mt, zap, 0)] = collapse(table, boundaries)
            continue
        # LMF=6 descriptors are matched against MF=6 yields in declaration order, each yield
        # used at most once (duplicate ZAP values across isomeric states are disambiguated by
        # position, not by ZAP alone) -- mirrors builder.rs::match_mf6 exactly. ZAP=1 (the free
        # emitted neutron) and ZAP=0 are consumed but never become inventory rows.
        yields = ev["mf6"].get(mt, [])
        yield_used = [False] * len(yields)
        for descriptor in ev["mf8"][mt]:
            if descriptor["zap"] <= 0:
                continue
            key = (mt, descriptor["zap"], descriptor["lfs"])
            if descriptor["lmf"] == 6:
                match_i = next((i for i, y in enumerate(yields)
                                 if not yield_used[i] and y["zap"] == descriptor["zap"]), None)
                if match_i is None:
                    continue
                yield_used[match_i] = True
                if descriptor["zap"] == 1:
                    continue  # free emitted neutron, not a residual product
                rows[key] = collapse_product([table, yields[match_i]["yield_table"]], boundaries)
            elif descriptor["lmf"] == 10:
                # LMF=10: the declared product's OWN state-resolved MF=10 TAB1 cross section is
                # collapsed directly (not the shared MF=3 total) -- matches builder.rs's
                # collapsed_mf10 per-(zap,lfs) row. The documented runtime state-sum
                # reconciliation is applied after this loop (reconcile_states below).
                match = next((p for p in ev["mf10"].get(mt, [])
                              if p["zap"] == descriptor["zap"] and p["lfs"] == descriptor["lfs"]), None)
                if match is None:
                    continue
                rows[key] = collapse(match["table"], boundaries)
            elif descriptor["lmf"] == 3:
                # LMF=3: the declared product uses the MF=3 total directly (single product
                # channel, no per-state MF=9/10 table to disambiguate).
                rows[key] = collapse(table, boundaries)
            # LMF=9 (multiplicity vs energy, paired with an MF=3 total) does not occur for any
            # MT8 descriptor across Fe-56/Cu-63/Ta-181/W-186 (surveyed directly) and is left
            # unhandled here rather than guessed at.
        if ev["mf10"].get(mt):
            reconcile_states(mt, table, ev["mf10"][mt], rows, ledger, boundaries,
                              profile_normalize=profile_normalize)
    return rows, ledger


# Documented runtime rule (SPEC / P18b, P25 Amendment B), re-derived here from its
# specification rather than from the Rust code: per (MT, ZAP), every emitted MF=10 state row of
# that ZAP is summed group by group and compared against the collapsed MF=3 total of the same MT.
# A sum at or below the total is unchanged; an excess at or below FLOOR_EXCESS_ABS_B barn is
# accepted unchanged; an excess inside the 0.001 standard envelope scales every state row of
# that ZAP in that group by the common factor T/S. The builder's at-most-few-ULP closure
# correction is below this gate's 1e-9 tolerance and is not replicated. An excess outside the
# envelope would have failed construction closed, so it is reported as a G4 failure here --
# UNLESS profile_normalize is set (P100's --profile-normalize), re-deriving the profile's own
# per-group normalization from its documented specification (task instructions, not builder.rs):
# under a profile, that same out-of-envelope excess is instead scaled by T/S exactly like the
# in-envelope case, and recorded as "state_sum_normalized" rather than an envelope violation.
FLOOR_EXCESS_ABS_B = 1e-15
STANDARD_ENVELOPE_REL = 1e-3
STANDARD_ZERO_TOTAL_ABS_B = 1e-3


def reconcile_states(mt, mf3_table, mf10_products, rows, ledger, boundaries, profile_normalize=False):
    total = collapse(mf3_table, boundaries)
    by_zap = {}
    for product in mf10_products:
        if product["zap"] >= 0:
            by_zap.setdefault(product["zap"], []).append(product)
    for zap, products in sorted(by_zap.items()):
        states = [collapse(p["table"], boundaries) for p in products]
        scaled = floor = normalized = 0
        max_excess = 0.0
        for g, t in enumerate(total):
            s = sum(state[g] for state in states)
            if s <= t:
                continue
            if s - t <= FLOOR_EXCESS_ABS_B:
                floor += 1
                continue
            inside = (s - t <= STANDARD_ENVELOPE_REL * t) if t > 0 else (s <= STANDARD_ZERO_TOTAL_ABS_B)
            if not inside:
                if profile_normalize and s > 0:
                    scale = t / s
                    for state in states:
                        state[g] *= scale
                    normalized += 1
                    if t > 0:
                        max_excess = max(max_excess, (s - t) / t)
                    # One line per normalized group (not one summary line per ZAP), matching the
                    # granularity the candidate's --profile tendl build is documented to use
                    # (controls/p94_g5_actinv.py: "records every group as state_sum_normalized").
                    ledger.append(f"MT{mt}/MF=10 ZAP={zap} group {g}: state_sum_normalized "
                                  f"(state sum {s!r} exceeds MF=3 total {t!r})")
                    continue
                ledger.append(f"MT{mt}/MF=10 ZAP={zap} group {g}: state sum {s!r} exceeds MF=3 total "
                              f"{t!r} outside the standard envelope (builder would fail closed)")
                continue
            scale = t / s
            for state in states:
                state[g] *= scale
            scaled += 1
            if t > 0:
                max_excess = max(max_excess, (s - t) / t)
        for product, state in zip(products, states):
            key = (mt, zap, product["lfs"])
            if key in rows:
                rows[key] = state
        if scaled or floor:
            ledger.append(f"MT{mt}/MF=10 ZAP={zap}: {scaled} group(s) scaled by T/S "
                          f"(max relative excess {max_excess:.6e}), {floor} floor-accepted")


# ----------------------------------------------------------------- npz reading (no ACTINV code)
#
# Layout written by crates/actinv-data/src/library.rs::write_npz (read here only as a plain
# zip-of-npy, via zipfile + numpy.lib.format -- no ACTINV reader code is imported or run):
#   rows.npy:   <i8, shape (n_rows, 5) = (target_index, mt, zap, lfs, lmf)
#   sig.npy:    <f8, shape (n_rows, ngroups), barns
#   bounds.npy: <f8, shape (ngroups+1,), ascending eV
# target_index indexes the SAME order as the sibling index.json's "targets" array (0-based,
# enumeration order of BuiltSource.targets in builder.rs::source_library).

def read_npz_library(npz_path: Path):
    import numpy.lib.format as npfmt
    with zipfile.ZipFile(npz_path) as zf:
        with zf.open("rows.npy") as f:
            rows = npfmt.read_array(f)
        with zf.open("sig.npy") as f:
            sig = npfmt.read_array(f)
        with zf.open("bounds.npy") as f:
            bounds = npfmt.read_array(f)
    return rows, sig, bounds


def load_index_target_za(npz_path: Path):
    # builder.rs::index_path: OUT.npz -> OUT_index.json (stem + "_index.json"), sibling file.
    index_path = npz_path.with_name(npz_path.stem + "_index.json")
    idx = json.loads(index_path.read_text())
    return [t["za"] for t in idx["targets"]]


def load_state_labels(npz_path: Path) -> dict:
    """{target_za: {(mt, zap, raw_lfs): canonical_liso}} from the index's recorded state mappings.

    The group values in this script are computed independently. Only the state labels are joined
    through the builder's recorded decisions, because the library labels a state by its isomeric
    ordinal from decay data, while the evaluation labels it by level number (LFS)."""
    index_path = npz_path.with_name(npz_path.stem + "_index.json")
    idx = json.loads(index_path.read_text())
    out = {}
    for target in idx["targets"]:
        labels = out.setdefault(target["za"], {})
        for mapping in target.get("state_mappings", []):
            labels[(mapping["mt"], mapping["zap"], mapping["raw_lfs"])] = mapping["canonical_liso"]
    return out


def load_target_ledgers(npz_path: Path) -> dict:
    """{target_za: [ledger lines]} from the index -- P100's extension/state_sum_normalized count
    comparison reads the candidate's own claims from here; it is never used to decide the group
    values themselves, only to cross-check counts (see main())."""
    index_path = npz_path.with_name(npz_path.stem + "_index.json")
    idx = json.loads(index_path.read_text())
    return {t["za"]: t.get("ledger", []) for t in idx["targets"]}


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dataset", choices=["tendl2025", "tendl2017"], default="tendl2025",
                     help="Raw ENDF-6 source set (default tendl2025: P94/P98's 4-nuclide set). "
                          "tendl2017 selects the 8-nuclide P98/P100 G5 set.")
    ap.add_argument("--profile-normalize", action="store_true",
                     help="Apply the P100 MF=3 threshold extension and the profile's per-group "
                          "state-sum normalization (protocols/ACTINV-P100_PROTOCOL.md), matching "
                          "a candidate library built with --profile tendl. Default off, which "
                          "preserves P94/P98 behaviour exactly.")
    args = ap.parse_args()

    boundaries = json.loads(GROUPS_JSON.read_text())["boundaries_eV"]
    if boundaries[0] > boundaries[-1]:
        boundaries = list(reversed(boundaries))
    mt_products = json.loads(MT_PRODUCTS_JSON.read_text())["table"]

    dataset_dir = GAMMA_DATA_2017 if args.dataset == "tendl2017" else GAMMA_DATA
    dataset_targets = TARGETS_2017 if args.dataset == "tendl2017" else TARGETS

    npz_path = Path(args.library)
    lib_rows, lib_sig, lib_bounds = read_npz_library(npz_path)
    target_za_by_index = load_index_target_za(npz_path)
    lib_bounds = list(lib_bounds)
    if lib_bounds != boundaries:
        # Still compare numerically (both ascending eV); report if they disagree in length/values.
        print(f"WARNING: library bounds differ from vendored fispact_162_groups.json "
              f"(len {len(lib_bounds)} vs {len(boundaries)})", file=sys.stderr)

    # Index library rows by (target_za, mt, zap, lfs) -> sig row (ndarray of ngroups).
    # Rows sharing a label (a non-isomeric excited level merged into the ground state) are summed,
    # as the runtime feeds both into the same inventory nuclide.
    lib_by_key = {}
    lib_duplicate_keys = []
    for i in range(lib_rows.shape[0]):
        ti, mt, zap, lfs, lmf = (int(x) for x in lib_rows[i])
        za = target_za_by_index[ti]
        key = (za, int(mt), int(zap), int(lfs))
        if key in lib_by_key:
            lib_duplicate_keys.append(key)
            lib_by_key[key] = [a + float(b) for a, b in zip(lib_by_key[key], lib_sig[i])]
        else:
            lib_by_key[key] = [float(v) for v in lib_sig[i]]
    # The 2017 library is built with --decay (decay-data isomer mapping); labels are joined
    # through the index's state_mappings exactly as for the 2025 G3 library -- load_state_labels
    # is dataset-agnostic and already does this.
    state_labels = load_state_labels(npz_path)
    target_ledgers = load_target_ledgers(npz_path) if args.profile_normalize else {}

    report = {"nuclides": {}, "pass": True, "dataset": args.dataset,
              "profile_normalize": args.profile_normalize,
              "tolerance": {"rel": REL_TOL, "floor_fraction": FLOOR_FRACTION,
              "max_group_energy_eV": MAX_GROUP_E}, "worst_rel_overall": 0.0, "worst_rel_where": None}

    for filename, target_za in dataset_targets.items():
        path = dataset_dir / filename
        if not path.exists():
            report["nuclides"][filename] = {"pass": False, "error": f"missing {path}"}
            report["pass"] = False
            continue
        ev = parse_evaluation(path)
        if ev["meta"]["za"] != target_za:
            report["nuclides"][filename] = {"pass": False,
                                             "error": f"ZA mismatch {ev['meta']['za']} != {target_za}"}
            report["pass"] = False
            continue
        mt18_info, mt18_error = None, None
        if args.profile_normalize:
            # P98's IZAP=0 photofission rule, treated the same way as the IZAP=-1 sentinel.
            try:
                mt18_info = classify_mt18(ev)
            except ValueError as exc:
                mt18_error = str(exc)
        raw_rows, ledger = build_rows(ev, target_za, mt_products, boundaries,
                                       profile_normalize=args.profile_normalize)
        labels = state_labels.get(target_za, {})
        rows = {}
        unlabelled = []
        for (mt, zap, raw_lfs), values in raw_rows.items():
            label = labels.get((mt, zap, raw_lfs), 0 if raw_lfs == 0 else None)
            if label is None:
                unlabelled.append([mt, zap, raw_lfs])
                continue
            key = (mt, zap, label)
            rows[key] = [a + b for a, b in zip(rows[key], values)] if key in rows else list(values)
        non_ground_merges = sorted({(mt, zap, lab) for (mt, zap, raw), lab in labels.items()
                                    if lab and sum(1 for (m2, z2, r2), l2 in labels.items()
                                                   if (m2, z2, l2) == (mt, zap, lab)) > 1})
        n = {"n_rows_independent": len(rows), "ledger": ledger, "unlabelled_independent_rows": unlabelled,
             "non_ground_label_merges": [list(k) for k in non_ground_merges],
             "rows_compared": 0, "rows_missing_in_library": [], "rows_missing_in_independent": [],
             "worst_rel": 0.0, "worst_rel_where": None, "above_200MeV_nonzero_independent": [],
             "above_200MeV_nonzero_library": [],
             "envelope_violations": [line for line in ledger if "builder would fail closed" in line]}
        if args.profile_normalize:
            n["mt18"] = mt18_info
            if mt18_error:
                n["mt18_error"] = mt18_error
            cand_ledger = target_ledgers.get(target_za, [])
            n["extension_count_independent"] = sum(1 for line in ledger if EXTENSION_LEDGER_PREFIX.match(line))
            n["extension_count_candidate_ledger"] = sum(
                1 for line in cand_ledger if EXTENSION_LEDGER_PREFIX.match(line))
            n["state_sum_normalized_count_independent"] = sum(1 for line in ledger if "state_sum_normalized" in line)
            # The builder writes one line per (MT, ZAP) ending "... in N of 162 group(s) ..."; count
            # groups, matching the independent per-group lines above.
            n["state_sum_normalized_count_candidate_ledger"] = sum(
                int(m.group(1)) for line in cand_ledger if "state_sum_normalized" in line
                for m in [re.search(r" in (\d+) of \d+ group\(s\)", line)] if m)
            n["ledger_counts_match"] = (
                n["extension_count_independent"] == n["extension_count_candidate_ledger"]
                and n["state_sum_normalized_count_independent"] == n["state_sum_normalized_count_candidate_ledger"])
        ok = True

        for (mt, zap, lfs), values in rows.items():
            key = (target_za, mt, zap, lfs)
            lib_row = lib_by_key.get(key)
            if lib_row is None:
                n["rows_missing_in_library"].append([mt, zap, lfs])
                ok = False
                continue
            n["rows_compared"] += 1
            max_v = max(abs(v) for v in values) if values else 0.0
            floor = max_v * FLOOR_FRACTION
            for g, (lo, hi, v_ind, v_lib) in enumerate(zip(boundaries[:-1], boundaries[1:], values, lib_row)):
                v_lib = float(v_lib)
                if lo >= MAX_GROUP_E:
                    if abs(v_ind) > 0.0:
                        n["above_200MeV_nonzero_independent"].append([mt, zap, lfs, g, v_ind])
                        ok = False
                    if abs(v_lib) > 0.0:
                        n["above_200MeV_nonzero_library"].append([mt, zap, lfs, g, v_lib])
                        ok = False
                    continue
                if max(abs(v_ind), abs(v_lib)) < floor:
                    continue
                r = rel(v_lib, v_ind)
                if r is not None and r > n["worst_rel"]:
                    n["worst_rel"] = r
                    n["worst_rel_where"] = f"MT{mt} ZAP{zap} LFS{lfs} group{g}"
                if r is not None and r > report["worst_rel_overall"]:
                    report["worst_rel_overall"] = r
                    report["worst_rel_where"] = f"{filename} MT{mt} ZAP{zap} LFS{lfs} group{g}"
                if r is not None and r > REL_TOL:
                    ok = False

        # Library rows for this target not reproduced independently. G4 requires the independent
        # code to compute every residual group row, so any such row fails the nuclide.
        # Loss rows (ZAP -1) and the photofission total placeholder (ZAP 0) are not residual rows.
        for (za, mt, zap, lfs) in lib_by_key:
            if za == target_za and zap > 0 and (mt, zap, lfs) not in rows:
                n["rows_missing_in_independent"].append([mt, zap, lfs])

        n["pass"] = (ok and not n["rows_missing_in_library"] and not n["rows_missing_in_independent"]
                     and not unlabelled and not n["envelope_violations"]
                     and (not args.profile_normalize or (mt18_error is None and n["ledger_counts_match"])))
        if not n["pass"]:
            ok = False
        report["nuclides"][filename] = n
        report["pass"] = report["pass"] and n["pass"]

    report["library_duplicate_keys_summed"] = len(lib_duplicate_keys)
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    print(json.dumps({"pass": report["pass"], "worst_rel_overall": report["worst_rel_overall"],
                       "worst_rel_where": report["worst_rel_where"],
                       "per_nuclide_pass": {k: v.get("pass") for k, v in report["nuclides"].items()}},
                      indent=1))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
