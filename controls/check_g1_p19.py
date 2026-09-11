#!/usr/bin/env python3
"""Independent checker for the P19 G1 shielding-table gate.

Imports no ACTINV production module. Verifies the frozen protocol, the G0
verdict chain, the emitted artifact's schema/provenance/invariants, and
re-derives the W-186 node-0 infinite-dilution cross sections from the ENDF-6
file itself — an independent MF=2 LRU=2 case-C parser plus the published
MC2-2 ten-point fluctuation quadrature (PURR's gnrx tables, transcribed here).
It also recomputes the Bondarenko moments from the artifact's own emitted
probability table, exercising the full sigma0-grid arithmetic independently.
With ``--self-test`` it mutates copies of the evidence and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P19_PROTOCOL.md"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
RECORD = ROOT / "results/g1_p19_shield_table.json"
G0_CHECK = ROOT / "results/g0_p19_check.json"
OUTPUT = ROOT / "results/g1_p19_check.json"
MATERIALS = ROOT / "controls/p19_core/inputs/materials.json"
DATA_ROOT = (
    Path(__import__("os").environ.get("ACTINV_P19_DATA_ROOT", Path.home() / "nuclear-data"))
    / "tendl-2025/files/n"
)

PROTOCOL_SHA256 = "8ee3d561fec513b3038fcbabfc8b57a6cce25e36c992f2af311afb6740acce16"
OPENING_COMMIT = "d463d57db4bcd87715eaf0d2082d07a71ec36424"
TEST_SET = ("W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56")
SIGMA0_B = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
TEMPERATURES_K = [293.6, 600.0, 900.0, 1200.0]

# Published MC2-2 ten-point chi-square quadrature (columns = dof 1..4).
QW = [
    [0.11120413, 0.033773418, 3.3376214e-4, 1.7623788e-3],
    [0.23546798, 0.079932171, 0.018506108, 0.021517749],
    [0.28440987, 0.12835937, 0.12309946, 0.080979849],
    [0.22419127, 0.17652616, 0.29918923, 0.18797998],
    [0.10967668, 0.21347043, 0.33431475, 0.30156335],
    [0.030493789, 0.21154965, 0.17766657, 0.29616091],
    [0.0042930874, 0.13365186, 0.042695894, 0.10775649],
    [2.5827047e-4, 0.022630659, 4.0760575e-3, 2.5171914e-3],
    [4.9031965e-6, 1.6313638e-5, 1.1766115e-4, 8.9630388e-10],
    [1.4079206e-8, 2.745383e-31, 5.0989546e-7, 0.0],
]
QP = [
    [3.0013465e-3, 1.3219203e-2, 1.0004488e-3, 0.013219203],
    [7.8592886e-2, 7.2349624e-2, 0.026197629, 0.072349624],
    [0.43282415, 0.19089473, 0.14427472, 0.19089473],
    [1.3345267, 0.39528842, 0.44484223, 0.39528842],
    [3.0481846, 0.74083443, 1.0160615, 0.74083443],
    [5.8263198, 1.3498293, 1.9421066, 1.3498293],
    [9.9452656, 2.5297983, 3.3150885, 2.5297983],
    [15.782128, 5.2384894, 5.2607092, 5.2384894],
    [23.996824, 13.821772, 7.9989414, 13.821772],
    [36.216208, 75.647525, 12.072069, 75.647525],
]

K_WAVE = 2.196771e-3  # fm^-1 eV^-1/2 coefficient (PURR cwaven convention)
AMASSN_AMU = 1.00866491588
ENDF_FLOAT = re.compile(r"^([+-]?\d+\.?\d*)([+-]\d+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value
    )


def endf_float(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    if "e" not in text.lower():
        text = ENDF_FLOAT.sub(r"\1e\2", text)
    return float(text)


def fields(line: str):
    return [endf_float(line[k * 11:(k + 1) * 11]) for k in range(6)]


def parse_mf2_lru2(path: Path):
    """Minimal independent MF=2/MT=151 LRU=2 case-C parser.

    Returns a list of sequences: {l, spin(J), awri, int, dofs(amux,amun,amuf),
    points [(E, D, GX, GN, GG, GF)]} for the single-isotope case plus the
    section SPI/AP/LSSF and range bounds.
    """
    lines = path.read_text(errors="replace").splitlines()
    seqs = []
    out = None
    i = 0
    while i < len(lines):
        line = lines[i]
        try:
            mf = int(line[70:72])
            mt = int(line[72:75])
        except ValueError:
            i += 1
            continue
        if mf != 2 or mt != 151:
            i += 1
            continue
        # Section head [ZA,AWR,LIS,0,NIS,0]
        h = fields(line)
        nis = int(h[4])
        i += 1
        for _ in range(nis):
            c = fields(lines[i]); i += 1
            lfw, ner = int(c[3]), int(c[4])
            for _ in range(ner):
                c = fields(lines[i]); i += 1
                el, eh, lru, lrf, nro, naps = c[0], c[1], int(c[2]), int(c[3]), int(c[4]), int(c[5])
                if nro != 0:  # TAB1 AP(E)
                    c = fields(lines[i]); i += 1
                    n1 = int(c[4])
                    nlines = (n1 + 5) // 6
                    i += nlines
                def skip_list(idx):
                    c = fields(lines[idx]); nw = int(c[4])
                    return idx + 1 + (nw + 5) // 6

                if lru == 1 and lrf in (1, 2):
                    c = fields(lines[i]); i += 1
                    nls = int(c[4])
                    for _ in range(nls):
                        i = skip_list(i)
                    continue
                if lru == 1 and lrf == 3:
                    c = fields(lines[i]); i += 1
                    nls = int(c[4])
                    for _ in range(nls):
                        i = skip_list(i)
                    continue
                if lru == 2 and lrf == 1:
                    c = fields(lines[i]); i += 1
                    nls = int(c[4])
                    if lfw == 0:
                        # LFW=0: one LIST per l-state, no shared energy grid.
                        for _ in range(nls):
                            i = skip_list(i)
                    else:
                        # LFW=1: one energies LIST then per (l,J) LISTs.
                        i = skip_list(i)
                        for _ in range(nls):
                            c = fields(lines[i]); i += 1
                            njs = int(c[4])
                            for _ in range(njs):
                                i = skip_list(i)
                    continue
                if lru != 2 or lrf != 2:
                    continue
                c = fields(lines[i]); i += 1
                spi, ap, lssf, nls = c[0], c[1], int(c[2]), int(c[4])
                for _ in range(nls):
                    c = fields(lines[i]); i += 1
                    awri, l, njs = c[0], int(c[2]), int(c[4])
                    for _ in range(njs):
                        c = fields(lines[i]); i += 1
                        aj, law, nw, ne = c[0], int(c[2]), int(c[4]), int(c[5])
                        vals = []
                        while len(vals) < nw:
                            vals.extend(fields(lines[i])); i += 1
                        vals = vals[:nw]
                        dof = vals[:6]
                        pts = [
                            tuple(vals[j:j + 6]) for j in range(6, len(vals), 6)
                        ]
                        seqs.append({
                            "l": l, "spin": aj, "awri": awri, "int": law,
                            "amux": int(dof[2]), "amun": int(dof[3]),
                            "amuf": int(dof[5]), "points": pts,
                        })
                out = {"EL": el, "EH": eh, "SPI": spi, "AP": ap,
                       "LSSF": lssf, "NAPS": naps, "seqs": seqs}
        break
    return out


def interp(points, energy, law):
    """Energy interpolation of the 6-field case-C rows at `energy`."""
    if energy <= points[0][0]:
        return points[0][1:]
    if energy >= points[-1][0]:
        return points[-1][1:]
    i = 1
    while i < len(points) and points[i][0] <= energy:
        i += 1
    x1, x2 = points[i - 1][0], points[i][0]
    frac = (energy - x1) / (x2 - x1)
    row = []
    for col in range(1, 6):
        y1, y2 = points[i - 1][col], points[i][col]
        if law == 1:
            v = y1
        elif law == 2:
            v = y1 + frac * (y2 - y1)
        elif law == 3:
            v = y1 + math.log(energy / x1) / math.log(x2 / x1) * (y2 - y1)
        elif law == 4:
            v = (max(y1, 0.0) + frac * (max(y2, 0.0) - max(y1, 0.0))) if (
                y1 <= 0 or y2 <= 0) else y1 * (y2 / y1) ** frac
        elif law == 5:
            v = (max(y1, 0.0) + frac * (max(y2, 0.0) - max(y1, 0.0))) if (
                y1 <= 0 or y2 <= 0) else y1 * (y2 / y1) ** (
                math.log(energy / x1) / math.log(x2 / x1))
        else:
            raise ValueError(f"INT={law}")
        row.append(v)
    return tuple(row)


def penetration(l, rho):
    r2 = rho * rho
    if l == 0:
        return 1.0, rho  # P_0/rho = 1, phase = rho
    if l == 1:
        return r2 / (1 + r2), rho - math.atan(rho)
    if l == 2:
        return r2 * r2 / (9 + 3 * r2 + r2 * r2), rho - math.atan2(
            3 * rho, 3 - r2)
    raise ValueError(f"l={l}")


def gnrx(gn, gf, gg, nu, mu, lam, gx, ident):
    """MC2-2 ten-point fluctuation integral (PURR gnrx port, numpy-free)."""
    if gn <= 0 or gg <= 0 or gf < 0 or (gf <= 0 and gx < 0):
        return 0.0
    col = lambda d: min(max(d, 1), 4) - 1
    s = 0.0
    if gf <= 0:
        for j in range(10):
            xj, wj = QP[j][col(nu)], QW[j][col(nu)]
            if gx <= 0:
                if ident == 1:
                    s += wj * xj * xj / (gn * xj + gg)
                elif ident == 2:
                    s += wj * xj / (gn * xj + gg)
            else:
                for k in range(10):
                    xk, wk = QP[k][col(lam)], QW[k][col(lam)]
                    if ident == 1:
                        s += wj * wk * xj * xj / (gn * xj + gg + gx * xk)
                    elif ident == 2:
                        s += wj * wk * xj / (gn * xj + gg + gx * xk)
    elif gx <= 0:
        for j in range(10):
            xj, wj = QP[j][col(nu)], QW[j][col(nu)]
            for k in range(10):
                xk, wk = QP[k][col(mu)], QW[k][col(mu)]
                if ident == 1:
                    s += wj * wk * xj * xj / (gn * xj + gf * xk + gg)
                elif ident == 2:
                    s += wj * wk * xj / (gn * xj + gf * xk + gg)
                elif ident == 3:
                    s += wj * wk * xj * xk / (gn * xj + gf * xk + gg)
    else:
        for j in range(10):
            xj, wj = QP[j][col(nu)], QW[j][col(nu)]
            for k in range(10):
                xk, wk = QP[k][col(mu)], QW[k][col(mu)]
                for l_ in range(10):
                    xl, wl = QP[l_][col(lam)], QW[l_][col(lam)]
                    den = gn * xj + gf * xk + gg + gx * xl
                    if ident == 1:
                        s += wj * wk * wl * xj * xj / den
                    elif ident == 2:
                        s += wj * wk * wl * xj / den
                    elif ident == 3:
                        s += wj * wk * wl * xj * xk / den
    return s


def rederive_infinite(eval_path: Path, energy_ev: float, awr: float):
    """Analytic infinite-dilution xs at `energy_ev` from the ENDF file."""
    parsed = parse_mf2_lru2(eval_path)
    if parsed is None or not parsed["seqs"]:
        raise ValueError("no LRU=2 case-C block found")
    abn = 1.0  # single-isotope nuclide (test set are elemental isotopes)
    e2 = math.sqrt(energy_ev)
    awri0 = parsed["seqs"][0]["awri"]
    rat = awri0 / (awri0 + 1.0)
    k = K_WAVE * rat * e2
    ab = 4.0 * math.pi / (k * k)
    aw = awri0 * AMASSN_AMU
    spot = 0.0
    seen_l = set()
    sigi = [0.0, 0.0, 0.0, 0.0]  # total, elastic, fission, capture
    for seq in parsed["seqs"]:
        d, gx0, gn0, gg0, gf0 = interp(seq["points"], energy_ev, seq["int"])
        aa = 0.123 * aw ** (1.0 / 3.0) + 0.08 if parsed["NAPS"] == 0 else parsed["AP"]
        ay = parsed["AP"]
        rho, rhoc = k * aa, k * ay
        vl = penetration(seq["l"], rho)[0] * seq["amun"]
        phase = penetration(seq["l"], rhoc)[1]
        gj = (2 * abs(seq["spin"]) + 1) / (4 * abs(parsed["SPI"]) + 2)
        gnx = gn0 * vl * e2 * seq["amun"] / max(seq["amun"], 1)
        gfx = gf0 if seq["amuf"] > 0 else 0.0
        gxx = gx0 if seq["amux"] > 0 else 0.0
        if gxx < 1e-8:
            gxx = 0.0
        if gfx < 1e-8:
            gfx = 0.0
        gs = gnrx(gnx, gfx, gg0, seq["amun"], seq["amuf"], seq["amux"], gxx, 1)
        gc = gnrx(gnx, gfx, gg0, seq["amun"], seq["amuf"], seq["amux"], gxx, 2)
        gfi = gnrx(gnx, gfx, gg0, seq["amun"], seq["amuf"], seq["amux"], gxx, 3)
        temp = abn * math.pi * ab * gj * gnx / (2 * d)
        if seq["l"] not in seen_l:
            seen_l.add(seq["l"])
            spot += abn * ab * (2 * seq["l"] + 1) * math.sin(phase) ** 2
        sigi[1] += temp * (gs * gnx - 2 * math.sin(phase) ** 2)
        sigi[2] += temp * gfi * gfx
        sigi[3] += temp * gc * gg0
    sigi[0] = sigi[1] + sigi[2] + sigi[3] + spot
    sigi[1] += spot
    return sigi, spot


def bondarenko_from_ptable(ptable_row, sig0):
    """Recompute Bondarenko moments from an emitted probability-table row."""
    bounds, prob, st = ptable_row["bounds_b"], ptable_row["prob"], ptable_row["total_b"]
    se, sf, sc = ptable_row["elastic_b"], ptable_row["fission_b"], ptable_row["capture_b"]
    num = [0.0] * 5
    den6, den7 = 0.0, 0.0
    for j in range(len(prob)):
        if prob[j] == 0:
            continue
        den = sig0 / (sig0 + st[j])
        num[0] += prob[j] * st[j] * den
        num[1] += prob[j] * se[j] * den
        num[2] += prob[j] * sf[j] * den
        num[3] += prob[j] * sc[j] * den
        num[4] += prob[j] * st[j] * den * den
        den6 += prob[j] * den
        den7 += prob[j] * den * den
    return [num[j] / (den7 if j == 4 else den6) for j in range(5)]


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE).stdout.strip()


def check_artifact(failures):
    if not ARTIFACT.exists():
        failures.append("g1 shield artifact is missing")
        return None
    art = json.loads(ARTIFACT.read_text())
    if art.get("format") != "actinv-shield-table-1":
        failures.append("artifact format tag is not actinv-shield-table-1")
    if art.get("sigma0_b") != SIGMA0_B:
        failures.append("sigma0 grid does not match the frozen grid")
    if art.get("temperatures_K") != TEMPERATURES_K:
        failures.append("temperature grid does not match the frozen grid")
    names = {e["material"]: e["file"] for e in
             json.loads(MATERIALS.read_text())["materials"]}
    recorded = {f["path"]: f["sha256"] for f in art.get("files", [])}
    for m in TEST_SET:
        f = names[m]
        if f not in recorded:
            failures.append(f"artifact files omit {f}")
            continue
        actual = sha256(DATA_ROOT / f)
        if recorded[f] != actual:
            failures.append(f"{f} sha256 mismatch in artifact provenance")
    for m in TEST_SET:
        key = m.replace("-", "")
        if key not in art["nuclides"]:
            failures.append(f"artifact nuclides omit {key}")
            continue
        nuc = art["nuclides"][key]
        if not nuc.get("unresolved_ranges_ev"):
            failures.append(f"{key} records no unresolved ranges")
        for node in nuc["nodes"]:
            if not node.get("covered"):
                continue
            for ch, mat in node["bondarenko_b"].items():
                for i, row in enumerate(mat):
                    for t, v in enumerate(row):
                        if not (v == v) or v is None:
                            failures.append(f"{key} {ch} NaN/null at node "
                                            f"{node['energy_ev']} i={i} t={t}")
            total = node["bondarenko_b"]["total"]
            for i in range(len(SIGMA0_B) - 1):
                if total[i + 1][0] > total[i][0] * (1 + 1e-6):
                    failures.append(
                        f"{key} total not monotone in sigma0 at "
                        f"{node['energy_ev']} index {i}")
                    break
            inf = node["infinite_dilution_b"][0]
            if abs(total[0][0] / inf - 1.0) > 1e-6:
                failures.append(
                    f"{key} infinite-dilution factor != 1 at {node['energy_ev']}")
    return art


def check_rederivation(art, failures):
    """Re-derive W-186 node-0 infinite-dilution from the ENDF file, and the
    sigma0-grid Bondarenko values from the emitted probability table."""
    w186 = art["nuclides"]["W186"]
    node = w186["nodes"][0]
    e = node["energy_ev"]
    try:
        sigi, spot = rederive_infinite(
            DATA_ROOT / "n-W186.tendl", e, awr=184.4022)
    except Exception as exc:
        failures.append(f"independent re-derivation failed: {exc}")
        return
    emitted = node["infinite_dilution_b"]
    names = ["total", "elastic", "fission", "capture"]
    for c, name in enumerate(names):
        if emitted[c] is None or emitted[c] == 0:
            continue
        rel = abs(sigi[c] - emitted[c]) / emitted[c]
        if rel > 1e-3:
            failures.append(
                f"W-186 {name} infinite-dilution re-derivation differs by "
                f"{rel:.4f} (checker {sigi[c]:.6f} vs artifact {emitted[c]:.6f})")
    if abs(node["sigma_p_b"] - spot) / spot > 1e-3:
        failures.append("W-186 sigma_p does not match the re-derived potential")

    # Recompute the ptable-derived Bondarenko column at sigma0=1e3 and 0.1.
    pt0 = node["ptable"][0]
    for si, s0 in ((3, 1e3), (9, 0.1)):
        recomputed = bondarenko_from_ptable(pt0, s0)
        sigi_em = node["infinite_dilution_b"]
        renorm = [sigi_em[c] / recomputed[c] if recomputed[c] else 1.0
                  for c in range(4)]
        # The emitted table applies the same sigi/sigf(1e10) renorm; recompute
        # the reference column first, then scale.
        ref = bondarenko_from_ptable(pt0, 1e10)
        for c, name in enumerate(names):
            expected = recomputed[c] * (sigi_em[c] / ref[c]) if ref[c] else 0.0
            got = node["bondarenko_b"][name][si][0]
            if got and abs(expected - got) / got > 1e-6:
                failures.append(
                    f"ptable-recomputed {name} at sig0={s0} differs "
                    f"{expected:.6f} vs {got:.6f}")

    # Independently re-derive one nuclide x group x sigma0 factor: pick the
    # group holding node 0, collapse the (already node-verified) per-node
    # Bondarenko values over the group's unresolved segments with an
    # independent lethargy-trapezoid implementation.
    gbounds = art["group_structure"]["boundaries_eV"]
    e0 = node["energy_ev"]
    gi = next(
        i for i in range(len(gbounds) - 1)
        if min(gbounds[i], gbounds[i + 1]) <= e0 <= max(gbounds[i], gbounds[i + 1])
    )
    glo, ghi = min(gbounds[gi], gbounds[gi + 1]), max(gbounds[gi], gbounds[gi + 1])
    segs = [
        (max(glo, r[0]), min(ghi, r[1]))
        for r in w186["unresolved_ranges_ev"]
        if max(glo, r[0]) < min(ghi, r[1])
    ]
    covered = sum(math.log(b) - math.log(a) for a, b in segs)
    group_width = math.log(ghi) - math.log(glo)

    def trapezoid(xs, ys, lo, hi):
        def interp(e):
            if e <= xs[0]:
                return ys[0]
            if e >= xs[-1]:
                return ys[-1]
            i = max(k for k in range(len(xs) - 1) if xs[k] <= e)
            f = (math.log(e) - math.log(xs[i])) / (math.log(xs[i + 1]) - math.log(xs[i]))
            return ys[i] + f * (ys[i + 1] - ys[i])
        pts = [lo] + [x for x in xs if lo < x < hi] + [hi]
        area = sum(
            0.5 * (interp(a) + interp(b)) * (math.log(b) - math.log(a))
            for a, b in zip(pts, pts[1:])
        )
        return area / (math.log(hi) - math.log(lo))

    for si in (0, 3, 9):
        for c, name in enumerate(names):
            derived = 0.0
            saw = False
            for a, b in segs:
                xs = [n["energy_ev"] for n in w186["nodes"]
                      if n["covered"] and a <= n["energy_ev"] <= b]
                if not xs:
                    continue
                svals = [n["bondarenko_b"][name][si][0] for n in w186["nodes"]
                         if n["covered"] and a <= n["energy_ev"] <= b]
                ivals = [n["infinite_dilution_b"][c] for n in w186["nodes"]
                         if n["covered"] and a <= n["energy_ev"] <= b]
                w = (math.log(b) - math.log(a)) / covered
                sc = svals[0] if len(xs) == 1 else trapezoid(xs, svals, a, b)
                ic = ivals[0] if len(xs) == 1 else trapezoid(xs, ivals, a, b)
                if ic:
                    saw = True
                    derived += w * sc / ic
            got = next(g for g in w186["groups"] if g["group"] == gi)
            f = got["factors"][name][si][0]
            if saw and f and abs(derived - f) / f > 1e-6:
                failures.append(
                    f"independent group {gi} {name} factor at sig0 index {si}: "
                    f"{derived:.6f} vs artifact {f:.6f}")
    overlap_expected = covered / group_width if group_width > 0 else 0.0
    got = next(g for g in w186["groups"] if g["group"] == gi)
    if abs(got["overlap_fraction"] - overlap_expected) > 1e-9:
        failures.append(
            f"group {gi} overlap fraction {got['overlap_fraction']} != "
            f"derived {overlap_expected}")

    # Group-collapse arithmetic: factor * unshielded == shielded, inf factor==1.
    for g in w186["groups"]:
        for c, name in enumerate(names):
            for si in range(len(SIGMA0_B)):
                for t in range(len(TEMPERATURES_K)):
                    f = g["factors"][name][si][t]
                    s = g["shielded_b"][name][si][t]
                    u = g["infinite_dilution_b"][c]
                    if u and abs(s / u - f) > 1e-9 * max(1.0, abs(f)):
                        failures.append(
                            f"group {g['group']} {name} factor*inf != shielded")
                        return
        for c, name in enumerate(names):
            if g["infinite_dilution_b"][c] == 0.0:
                continue  # zero-width channel: factor is undefined, skip
            if any(
                g["factors"][name][0][t] != 1.0
                for t in range(len(TEMPERATURES_K))
            ):
                failures.append(f"group {g['group']} {name} inf factor != 1")
                return


def run_checks():
    failures = []
    observed = sha256(PROTOCOL)
    if observed != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed} != frozen {PROTOCOL_SHA256}")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor")
    if not G0_CHECK.exists() or not json.loads(G0_CHECK.read_text()).get("pass"):
        failures.append("G0 check is missing or failing")
    if not RECORD.exists():
        failures.append("g1 producer record is missing")
    else:
        rec = json.loads(RECORD.read_text())
        if rec.get("schema") != "actinv-p19-g1-shield-1":
            failures.append("producer record schema mismatch")
        if not is_hex64(rec.get("artifact_sha256")):
            failures.append("producer record lacks artifact sha256")
        elif ARTIFACT.exists() and rec["artifact_sha256"] != sha256(ARTIFACT):
            failures.append("artifact sha256 does not match producer record")
        for m in TEST_SET:
            probe = (rec.get("probes") or {}).get(m)
            if not probe or probe.get("monotone_in_sigma0") is not True:
                failures.append(f"producer probe for {m} not monotone")
            if probe and abs(probe.get("inf_factor", 0) - 1.0) > 1e-6:
                failures.append(f"producer probe for {m} inf factor != 1")
    art = check_artifact(failures)
    if art is not None:
        check_rederivation(art, failures)
    return {
        "schema": "actinv-p19-g1-check-1",
        "protocol_sha256": observed,
        "head_commit": head,
        "failures": failures,
        "pass": not failures,
    }


def self_test():
    if not ARTIFACT.exists():
        raise SystemExit("self-test requires the g1 artifact")
    original_bytes = ARTIFACT.read_bytes()
    original = json.loads(original_bytes)
    mutations = {
        "sigma0_grid": lambda a: a.__setitem__("sigma0_b", [1.0] * 10),
        "factor_gt_one": lambda a: a["nuclides"]["W186"]["nodes"][0][
            "bondarenko_b"]["total"].__setitem__(9, [99.0] * 4),
        "inf_column": lambda a: a["nuclides"]["W186"]["nodes"][0][
            "bondarenko_b"]["total"].__setitem__(0, [0.5] * 4),
        "source_hash": lambda a: a["files"][0].__setitem__("sha256", "0" * 64),
        "ptable": lambda a: a["nuclides"]["W186"]["nodes"][0]["ptable"][0][
            "total_b"].__setitem__(0, 9e9),
    }
    rejected = []
    try:
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(original)
            mutate(candidate)
            ARTIFACT.write_text(json.dumps(candidate))
            try:
                result = run_checks()
                if result["pass"]:
                    rejected.append(name)
            finally:
                ARTIFACT.write_bytes(original_bytes)
    finally:
        ARTIFACT.write_bytes(original_bytes)
    if rejected:
        raise SystemExit(f"self-test mutations not rejected: {rejected}")
    print(f"self-test: all {len(mutations)} mutations rejected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    OUTPUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
