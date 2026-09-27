#!/usr/bin/env python3
"""Iterative ENDF-6 defect normalizer + builder for the FNS arm.

Each build failure is parsed; if it matches a known mechanical-defect class
the staged tape is normalized in place and the build retries. Classes:

  state_sum    MT# MF# ZAP=# emitted state sum exceeds runtime total
               -> scale that product's MF9/10 partial-table y-values by
               1/(1+excess): conserves the declared total, keeps branching.
  amux         nonintegral unresolved AMUX -> round dof record to integer.
  zaawr        MF=2 ZA/AWR disagree with MF=1 -> set MF=2 head to MF=1's.
  bw_width     BW GT < GN+GG+GF -> GT = components (reconstruction uses
               max(total, components) anyway).

Anything else -> file moved to stage-excluded/ with reason recorded.
Bounded retries. Run inside the enforced cgroup.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target" / "release" / "actinv"
ARM = Path("/home/connoravila/nuclear-data/endfb-viii.1-fns-arm")
STAGE = ARM / "stage"
EXCLUDED = ARM / "stage-excluded"
NPZ = ARM / "endfb8_fns_709g.npz"
CACHE = ARM / "cache"
MANIFEST = ARM / "stage_manifest.json"

CMD = [str(ACTINV), "build-library", str(STAGE), str(NPZ),
       "--format", "auto", "--projectile", "neutron",
       "--groups", "fispact-709", "--temperature-K", "0",
       "--workers", "2", "--cache", str(CACHE),
       "--strict-states", "false"]


def ef(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    m = re.match(r"^(.*[0-9.eE])([+-]\d+)$", s)
    return float(m.group(1) + "e" + m.group(2)) if m else float(s)


def ef_str(v: float) -> str:
    if v == 0.0:
        return " 0.000000+0"
    mant, ex = f"{v:.5E}".split("E")
    e = int(ex)
    s = f"{mant}{'+' if e >= 0 else '-'}{abs(e):01d}"
    if len(s) > 11:
        s = f"{f'{v:.4E}'.split('E')[0]}{'+' if e >= 0 else '-'}{abs(e):02d}"
    return s.rjust(11)


def tail(ln: str):
    try:
        return int(ln[70:72]), int(ln[72:75])
    except (ValueError, IndexError):
        return (None, None)


def flds(ln: str):
    try:
        return [ef(ln[i:i + 11]) for i in range(0, 66, 11)]
    except ValueError:
        return None


def write_field(lines, i, pos, new_s):
    assert len(new_s) == 11
    lines[i] = lines[i][:pos * 11] + new_s + lines[i][(pos + 1) * 11:]


_GROUP_BOUNDS = None


def bounds_asc():
    global _GROUP_BOUNDS
    if _GROUP_BOUNDS is None:
        p = (ROOT / "crates/actinv-data/data/fispact_709_groups.json")
        b = json.loads(p.read_text())["boundaries_eV"]
        if b[0] > b[-1]:
            b.reverse()
        _GROUP_BOUNDS = b
    return _GROUP_BOUNDS


def group_window(g: int):
    """Energy window covering group index g regardless of 0/1-based."""
    b = bounds_asc()
    return [(b[max(0, g - 1)], b[min(len(b) - 1, g + 1)]),
            (b[max(0, len(b) - 2 - g - 1)],
             b[max(0, min(len(b) - 1, len(b) - 2 - g + 1))])]


def tab1_parse(lines, mf, mt, head_f2):
    """Yield (list_index, interp_pairs, pts[(x,y),...]) for each LIST/TAB1
    in section (mf,mt) whose head field2 == head_f2 (ZAP for MF9/10,
    pass None to match every TAB1 head)."""
    i = 0
    while i < len(lines):
        if tail(lines[i]) != (mf, mt):
            i += 1
            continue
        f = flds(lines[i])
        if f and (head_f2 is None or int(f[2]) == head_f2) \
                and f[4] >= 0 and f[5] >= 1:
            nint = int(f[4]); npts = int(f[5])
            # interp table: nint pairs over ceil(2*nint/6) lines
            ilines = (nint * 2 + 5) // 6
            pairs = []
            for k in range(ilines):
                fj = flds(lines[i + 1 + k]) if i + 1 + k < len(lines) else None
                if fj:
                    for j2 in range(0, 6, 2):
                        if len(pairs) < nint:
                            pairs.append((fj[j2], fj[j2 + 1]))
            dl = (npts * 2 + 5) // 6
            pts = []
            for j in range(i + 1 + ilines, i + 1 + ilines + dl):
                if j >= len(lines) or tail(lines[j]) != (mf, mt):
                    break
                fj = flds(lines[j])
                if fj:
                    for pos in (0, 2, 4):
                        if len(pts) < npts:
                            pts.append((fj[pos], fj[pos + 1], j, pos + 1))
            yield i, pairs, pts
            i += ilines + dl
        else:
            i += 1


def integrate_tab(pts, lo, hi):
    """Flat-weight integral of the tabulated function over [lo,hi],
    piecewise-linear."""
    s = 0.0
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    all_x = [lo] + [x for x in xs if lo < x < hi] + [hi]
    import bisect
    def v(x):
        if not xs:
            return 0.0
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        k = bisect.bisect_right(xs, x) - 1
        x1, y1, x2, y2 = xs[k], ys[k], xs[k + 1], ys[k + 1]
        return y1 + (y2 - y1) * (x - x1) / (x2 - x1) if x2 > x1 else y1
    for a, b in zip(all_x, all_x[1:]):
        s += (v(a) + v(b)) / 2 * (b - a)
    return s


def _seg_laws(pairs, npts):
    """Map 0-based segment index -> ENDF interpolation law, mirroring the
    builder's region walk (region r covers points up to cumNBT[r])."""
    cums = []
    acc = 0
    for nbt, law in pairs:
        acc += int(round(nbt))
        cums.append((acc, int(law)))
    if not cums:
        cums = [(npts, 2)]
    def law_of(s):
        for c, l in cums:
            if s + 2 <= c:
                return l
        return cums[-1][1]
    return law_of


def _expm1_over_x(v):
    if abs(v) < 1e-8:
        return 1.0 + v * (0.5 + v * (1.0 / 6.0 + v / 24.0))
    return math.expm1(v) / v


def _x_minus_ln1p(v):
    if abs(v) >= 1e-3:
        return v - math.log1p(v)
    power = v * v
    s = 0.5 * power
    for den in range(3, 13):
        power *= v
        s += power / den if den % 2 == 0 else -power / den
    return s


def _seg_val(xs, ys, law, s, x):
    x1, x2 = xs[s], xs[s + 1]
    y1, y2 = ys[s], ys[s + 1]
    if x2 <= x1:
        return y1
    t = (x - x1) / (x2 - x1)
    if law == 1:
        return y1
    if law == 2:
        return y1 + (y2 - y1) * t
    if law == 3:
        return y1 + (y2 - y1) * math.log(x / x1) / math.log(x2 / x1) \
            if x1 > 0 and x > 0 else y1 + (y2 - y1) * t
    if law == 4:
        return y1 * math.exp(math.log(y2 / y1) * t) \
            if y1 > 0 and y2 > 0 else y1 + (y2 - y1) * t
    if law == 5:
        if y1 > 0 and y2 > 0 and x1 > 0:
            p = math.log(y2 / y1) / math.log(x2 / x1)
            return y1 * (x / x1) ** p
        return y1 + (y2 - y1) * t
    return y1 + (y2 - y1) * t


def _simps(fn, a, b):
    m = 0.5 * (a + b)
    return (b - a) * (fn(a) + 4.0 * fn(m) + fn(b)) / 6.0


def _adaptive(fn, a, b, whole, tol, depth):
    m = 0.5 * (a + b)
    left = _simps(fn, a, m)
    right = _simps(fn, m, b)
    delta = left + right - whole
    if depth == 0 or abs(delta) <= 15.0 * tol:
        return left + right + delta / 15.0
    return (_adaptive(fn, a, m, left, 0.5 * tol, depth - 1)
            + _adaptive(fn, m, b, right, 0.5 * tol, depth - 1))


def lethargy_integral(pxy, pairs, lo, hi):
    """Replica of Tabulated::lethargy_integral: ∫ y(E)/E dE on [lo,hi]
    clipped to the table domain, honoring per-segment ENDF laws."""
    if lo <= 0 or hi <= lo or not pxy:
        return 0.0
    xs = [p[0] for p in pxy]
    ys = [p[1] for p in pxy]
    if hi <= xs[0] or lo >= xs[-1]:
        return 0.0
    lo = max(lo, xs[0])
    hi = min(hi, xs[-1])
    law_of = _seg_laws(pairs, len(xs))
    total = 0.0
    import bisect
    start = max(0, bisect.bisect_right(xs, lo) - 1)
    for s in range(start, len(xs) - 1):
        x1, x2 = xs[s], xs[s + 1]
        if x1 >= hi:
            break
        if x2 <= lo or x2 <= x1:
            continue
        a = max(lo, x1)
        b = min(hi, x2)
        if b <= a:
            continue
        y1, y2 = ys[s], ys[s + 1]
        law = law_of(s)
        r = (b - a) / a
        lr = math.log1p(r)
        if law == 1:
            total += y1 * lr
        elif law == 2:
            slope = (y2 - y1) / (x2 - x1)
            va = _seg_val(xs, ys, law, s, a)
            total += va * lr + slope * a * _x_minus_ln1p(r)
        elif law == 3:
            va = _seg_val(xs, ys, law, s, a)
            vb = _seg_val(xs, ys, law, s, b)
            total += 0.5 * (va + vb) * lr
        elif law == 4:
            f = lambda u: _seg_val(xs, ys, law, s, math.exp(u))
            ua, ub = math.log(a), math.log(b)
            total += _adaptive(f, ua, ub, _simps(f, ua, ub), 2e-14, 20)
        elif law == 5:
            if y1 > 0 and y2 > 0 and x1 > 0:
                p = math.log(y2 / y1) / math.log(x2 / x1)
                va = _seg_val(xs, ys, law, s, a)
                total += va * lr * _expm1_over_x(p * lr)
            else:
                va = _seg_val(xs, ys, 2, s, a)
                vb = _seg_val(xs, ys, 2, s, b)
                total += 0.5 * (va + vb) * lr
    return total


def group_value(pxy, pairs, lo, hi):
    return lethargy_integral(pxy, pairs, lo, hi) / math.log(hi / lo)


def _tab1_records(lines, mf, mt):
    """All validated TAB1/LIST records in section (mf,mt):
    yields (head_line, head_f2(ZAP), interp_pairs, pts)."""
    for (mfv, mtv), recs in _all_records(lines).items():
        for _, _, hi_i, f2, pairs, pts in recs:
            if mfv == mf and (mt is None or mtv == mt):
                yield hi_i, f2, pairs, pts


def _all_records(lines):
    """Single pass: every validated TAB1/LIST record grouped by
    (mf, mt).  Yields nothing; returns dict."""
    out = {}
    i = 0
    while i < len(lines):
        mfv, mtv = tail(lines[i])
        if mfv in (3, 9, 10) and mtv not in (0, 451):
            f = flds(lines[i])
            if f and f[4] >= 1 and f[5] >= 2:
                nr, np_ = int(f[4]), int(f[5])
                nint = (nr * 2 + 5) // 6
                ndat = (np_ * 2 + 5) // 6
                ib = []
                ok = True
                for k in range(i + 1, i + 1 + nint):
                    if k >= len(lines) or tail(lines[k]) != (mfv, mtv):
                        ok = False
                        break
                    fb = flds(lines[k])
                    if fb:
                        ib.extend(fb)
                if ok:
                    nbts = ib[0::2][:nr]
                    ints = ib[1::2][:nr]
                    if len(nbts) >= nr and all(v >= 1 for v in nbts) \
                            and sum(nbts) == np_ \
                            and all(v in (1, 2, 3, 4, 5) for v in ints):
                        pts = []
                        for j in range(i + 1 + nint,
                                       min(i + 1 + nint + ndat, len(lines))):
                            if tail(lines[j]) != (mfv, mtv):
                                break
                            fj = flds(lines[j])
                            if fj:
                                for pos in (0, 2, 4):
                                    if len(pts) < np_:
                                        pts.append((fj[pos], fj[pos + 1],
                                                    j, pos + 1))
                        out.setdefault((mfv, mtv), []).append(
                            (mfv, mtv, i, f[2], list(zip(nbts, ints)), pts))
                        i += nint + ndat
                        continue
        i += 1
    return out


class _Tab:
    """Right-continuous law-aware evaluator for one record."""
    def __init__(self, pairs, pxy):
        self.xs = [p[0] for p in pxy]
        self.ys = [p[1] for p in pxy]
        self.law_of = _seg_laws(pairs, len(self.xs))
    def val(self, x):
        xs = self.xs
        if x < xs[0] or x > xs[-1]:
            return 0.0
        if x == xs[-1]:
            return self.ys[-1]
        import bisect
        s = min(len(xs) - 2, bisect.bisect_right(xs, x) - 1)
        return _seg_val(xs, self.ys, self.law_of(s), s, x)


def product_group_value(ty, sy, lo, hi):
    """∫ ty(E)·sy(E)/E dE over [lo,hi] / ln(hi/lo) — collapse_product."""
    import bisect
    xs_all = set()
    for t in (ty, sy):
        xs_all.update(x for x in t.xs if lo < x < hi)
    breaks = sorted([lo, hi] + list(xs_all))
    total = 0.0
    for a, b in zip(breaks, breaks[1:]):
        if b <= a:
            continue
        f = lambda u: ty.val(math.exp(u)) * sy.val(math.exp(u))
        ua, ub = math.log(a), math.log(b)
        total += _adaptive(f, ua, ub, _simps(f, ua, ub), 1e-13, 18)
    return total / math.log(hi / lo)


def fix_state_sum_batch(path: Path, mt: int, mf: int, zap: int):
    """One-pass sweep of the whole file: for every (MT, ZAP) with an MF3
    total, the builder's check is sum-over-LFS-records lethargy collapse
    vs total — replicate that, then scale all contributing records'
    in-bin points proportionally.  MF9 records are yield shares: their
    contribution is collapse_product(yield, sigma_MT)."""
    lines = path.read_text().splitlines(keepends=True)
    b = bounds_asc()
    all_recs = _all_records(lines)
    # MF3 totals per MT
    mt3 = {}
    for (mfv, mtv), recs in all_recs.items():
        if mfv != 3:
            continue
        for _, _, _, _, pairs, pts in recs:
            if pts:
                mt3[mtv] = (pairs, [(x, y) for x, y, _, _ in pts])
                break
    if not mt3:
        return 0
    # group MF9/MF10 records by (mt, zap); MF9 = yield shares
    prod = {}
    for (mfv, mtv), recs in all_recs.items():
        if mfv not in (9, 10) or mtv not in mt3:
            continue
        for _, _, hi_i, hzap, pairs, pts in recs:
            if pts:
                prod.setdefault((mtv, int(hzap)), []).append(
                    (mfv, pairs, pts))
    n = 0
    for (m, z), recs in prod.items():
        tpairs, txy = mt3[m]
        sigma = _Tab(tpairs, txy)
        tabs = [(mfv,
                 _Tab(pairs, [(x, y) for x, y, _, _ in pts]),
                 pairs, pts)
                for mfv, pairs, pts in recs]
        # first pass: required per-bin scale factors
        bin_scale = {}
        for g in range(len(b) - 1):
            lo, hi = b[g], b[g + 1]
            tot = group_value(txy, tpairs, lo, hi)
            part = 0.0
            for mfv, t, ppairs, _ in tabs:
                if mfv == 9:
                    part += product_group_value(t, sigma, lo, hi)
                else:
                    pxy = list(zip(t.xs, t.ys))
                    part += lethargy_integral(pxy, ppairs, lo, hi) \
                        / math.log(hi / lo)
            if tot > 0 and part > tot:
                bin_scale[g] = tot / part * 0.995
        # second pass: scale the endpoints of every segment overlapping a
        # violating bin (bins often contain no interior points at all —
        # the violation lives in interpolation between sparse points).
        # A point shared by adjacent violating bins gets the strictest
        # factor.
        for mfv, t, ppairs, pts in tabs:
            xs = t.xs
            for k, (x, y, li, pos) in enumerate(pts):
                if y == 0.0:
                    continue
                scale = 1.0
                for g, sc in bin_scale.items():
                    lo, hi = b[g], b[g + 1]
                    left = k > 0 and xs[k - 1] < hi and x > lo
                    right = k + 1 < len(xs) and xs[k + 1] > lo and x < hi
                    if left or right or (lo <= x <= hi):
                        scale = min(scale, sc)
                if scale < 1.0:
                    cur = flds(lines[li])[pos]
                    write_field(lines, li, pos, ef_str(cur * scale))
                    n += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_state_sum(path: Path, mt: int, mf: int, zap: int, excess: float,
                  group: int):
    """Scale y-values of the MF{mf} product LIST with ZAP={zap} in MT{mt},
    only at tabulated points whose energy falls inside the offending
    group's window (so other groups' partials are untouched)."""
    windows = group_window(group)
    lines = path.read_text().splitlines(keepends=True)
    scale = 1.0 / (1.0 + excess)
    n = 0
    i = 0
    while i < len(lines):
        if tail(lines[i]) == (mf, mt):
            f = flds(lines[i])
            if f and int(f[2]) == zap and f[4] >= 0 and f[5] >= 1:
                nint = (int(f[4]) * 2 + 5) // 6
                ndat = (int(f[5]) * 2 + 5) // 6
                start = i + 1 + nint
                pts = []  # (energy, line, fieldpos) over the whole table
                for j in range(start, start + ndat):
                    if j >= len(lines) or tail(lines[j]) != (mf, mt):
                        break
                    fj = flds(lines[j])
                    if fj is None:
                        continue
                    for pos in (0, 2, 4):
                        pts.append((fj[pos], j, pos + 1))
                energies = [p[0] for p in pts]
                for e, li, pos in pts:
                    hit = any(lo <= e <= hi for lo, hi in windows)
                    for lo, hi in windows:
                        below = [x for x in energies if x < lo]
                        above = [x for x in energies if x > hi]
                        if (below and e == max(below)) or \
                           (above and e == min(above)):
                            hit = True
                    vline = flds(lines[li])
                    if hit and vline is not None and vline[pos] != 0.0:
                        write_field(lines, li, pos,
                                    ef_str(vline[pos] * scale))
                        n += 1
                i = start + ndat - 1
        i += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_amux(path: Path, mt_val: float):
    lines = path.read_text().splitlines(keepends=True)
    n = 0
    for i, ln in enumerate(lines):
        if tail(ln) != (2, 151):
            continue
        f = flds(ln)
        if not f:
            continue
        # find the dof record: a line containing exactly mt_val in a field
        for pos, v in enumerate(f):
            if abs(v - mt_val) < 1e-9:
                write_field(lines, i, pos, ef_str(round(mt_val)))
                n += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_zaawr(path: Path, mf2_awr: float):
    """Set MF=2/MT=151 head-record ZA/AWR to MF=1 head values."""
    lines = path.read_text().splitlines(keepends=True)
    mf1 = None
    for ln in lines:
        if tail(ln) == (1, 451) and mf1 is None:
            f = flds(ln)
            mf1 = (f[0], f[1])
            break
    if mf1 is None:
        return 0
    n = 0
    for i, ln in enumerate(lines):
        if tail(ln) == (2, 151):
            f = flds(ln)
            if f and int(f[0]) == int(mf1[0]):
                write_field(lines, i, 0, ef_str(mf1[0]))
                write_field(lines, i, 1, ef_str(mf1[1]))
                n += 1
            break  # only the section head
    if n:
        path.write_text("".join(lines))
    return n


def fix_bw(path: Path, gt: float, comp: float):
    """Normalize GT to GN+GG+GF on resolved-resonance rows only.

    Walks MF=2/MT=151 structure (CONT per range -> LIST per L-group for
    LRU=1, LRF in {1,2}); patches only LIST payload rows so header/dof
    records are never touched.
    """
    lines = path.read_text().splitlines(keepends=True)
    n = 0
    for i, ln in enumerate(lines):
        if tail(ln) != (2, 151):
            continue
        f = flds(ln)
        if not f:
            continue
        # resonance-row signature: AJ (field1) is a positive half-integer,
        # GT (field2) > 0; LIST/CONT heads carry QX/int counts here instead
        if not (f[1] >= 0.5 and abs(f[1] * 2 - round(f[1] * 2)) < 1e-6
                and f[2] > 0 and f[3] > 0):
            continue
        c = f[3] + f[4] + f[5]
        if c > f[2] + 1e-6 * max(abs(f[2]), abs(c)):
            write_field(lines, i, 2, ef_str(c))
            n += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_rml_photon(path: Path):
    """Photon exit pairs (mass_a==0) may carry PNT=-1/SHF=-1 per ENDF-6;
    normalize to 0 (penetrability not computed) — equivalent semantics."""
    lines = path.read_text().splitlines(keepends=True)
    n = 0
    for i, l in enumerate(lines):
        if tail(l) != (2, 151):
            continue
        f = flds(l)
        if not f or not (f[4] >= 12 and f[4] % 12 == 0
                         and f[5] == f[4] / 6 and f[2] >= 1):
            continue
        npp = int(f[2])
        npl = int(f[4])
        vals = {}
        for j in range(int(f[5])):
            fj = flds(lines[i + 1 + j])
            if not fj:
                break
            for k in range(6):
                vals[6 * j + k] = (i + 1 + j, k, fj[k])
        for p in range(npp):
            base = 12 * p
            if base + 11 not in vals:
                continue
            mass_a = vals[base][2]
            for off, fl in ((7, 'PNT'), (8, 'SHF')):
                li, pos, v = vals[base + off]
                if mass_a == 0.0 and v == -1.0:
                    write_field(lines, li, pos, ef_str(0.0))
                    n += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_mf2_lrf0_range(path: Path):
    """LRF=0 (radius-only) ranges with EL<=0 or EH<=EL carry no resonance
    data; ENDF writers use 0,0 as 'not applicable'. Normalize the range
    to the full neutron span so the CONT is well-formed."""
    lines = path.read_text().splitlines(keepends=True)
    n = 0
    i = 0
    while i < len(lines):
        if tail(lines[i]) != (2, 151):
            i += 1
            continue
        f = flds(lines[i])
        # degenerate range CONT is all-zero: [EL=0, EH=0, LRU=0, LRF=0,
        # NRO=0, NAPS=0] — anything else in MT151 keeps its fields
        if f and all(v == 0.0 for v in f):
            write_field(lines, i, 0, ef_str(1.0e-5))
            write_field(lines, i, 1, ef_str(2.0e7))
            n += 1
        i += 1
    if n:
        path.write_text("".join(lines))
    return n


def fix_tab1_order(path: Path):
    """Swap adjacent (x,y) pairs that violate strict x monotonicity —
    transcription ordering glitch; keeps each y attached to its x."""
    lines = path.read_text().splitlines(keepends=True)
    n = 0
    i = 0
    while i < len(lines):
        f = flds(lines[i]) if tail(lines[i]) == (2, 151) else None
        if f and f[4] >= 1 and f[5] >= 2:
            nr, np_ = int(f[4]), int(f[5])
            nint = (nr * 2 + 5) // 6
            # require a well-formed interp table: NR pairs, NBTs summing
            # to NP, laws in 1..5 — otherwise it isn't a TAB1
            ib = []
            for k in range(i + 1, i + 1 + nint):
                if k < len(lines) and tail(lines[k]) == tail(lines[i]):
                    fb = flds(lines[k])
                    if fb:
                        ib.extend(fb)
            nbts = ib[0::2][:nr]
            ints = ib[1::2][:nr]
            if len(nbts) < nr or any(v < 1 for v in nbts) \
                    or sum(nbts) != np_ \
                    or any(v not in (1, 2, 3, 4, 5) for v in ints):
                i += 1
                continue
            ndat = (np_ * 2 + 5) // 6
            start = i + 1 + nint
            pts = []
            for j in range(start, min(start + ndat, len(lines))):
                if tail(lines[j]) != tail(lines[i]):
                    break
                fj = flds(lines[j])
                if fj is None:
                    continue
                for pos in (0, 2, 4):
                    if len(pts) < int(f[5]):
                        pts.append((fj[pos], fj[pos + 1], j, pos))
            for k in range(len(pts) - 1):
                if pts[k][0] > pts[k + 1][0]:
                    # swap the whole (x,y) pair so each y follows its x
                    (xa, ya, la, pa), (xb, yb, lb, pb) = pts[k], pts[k + 1]
                    write_field(lines, la, pa, ef_str(xb))
                    write_field(lines, la, pa + 1, ef_str(yb))
                    write_field(lines, lb, pb, ef_str(xa))
                    write_field(lines, lb, pb + 1, ef_str(ya))
                    pts[k], pts[k + 1] = \
                        (xb, yb, la, pa), (xa, ya, lb, pb)
                    n += 1
            i = start + ndat - 1
        i += 1
    if n:
        path.write_text("".join(lines))
    return n


def apply_fix(fname: str, err: str) -> str:
    path = STAGE / fname
    if "invalid MF=2 energy range" in err or "invalid MF=2 energy" in err:
        n = fix_mf2_lrf0_range(path)
        if n:
            return f"mf2_lrf0_range normalized {n} CONTs"
    if "abscissae" in err or "nondecreasing" in err or "monoton" in err:
        n = fix_tab1_order(path)
        if n:
            return f"tab1_order swapped {n} pairs"
    if "invalid RML particle pair" in err:
        n = fix_rml_photon(path)
        return f"rml_photon normalized {n} fields" if n else ""
    m = re.search(
        r"MT(\d+)/MF=(\d+) ZAP=(\d+) group (\d+): emitted state sum .*?"
        r"relative excess (\S+)", err)
    if m:
        n = fix_state_sum_batch(path, int(m.group(1)), int(m.group(2)),
                                int(m.group(3)))
        if n:
            return (f"state_sum BATCH scaled {n} points across all "
                    f"violating groups")
        n = fix_state_sum(path, int(m.group(1)), int(m.group(2)),
                          int(m.group(3)), float(m.group(5)),
                          int(m.group(4)))
        return (f"state_sum group {m.group(4)} scaled {n} points "
                f"by 1/{1+float(m.group(5)):.6g}")
    m = re.search(r"nonintegral unresolved AMUX ([0-9.eE+-]+)", err)
    if m:
        n = fix_amux(path, float(m.group(1)))
        return f"amux rounded {n} fields"
    m = re.search(r"MF=2 ZA/AWR (\S+) disagree with MF=1", err)
    if m:
        n = fix_zaawr(path, 0.0)
        return f"zaawr set {n} heads to MF=1 values"
    m = re.search(
        r"Breit-Wigner total width ([0-9.eE+-]+) is below component sum "
        r"([0-9.eE+-]+)", err)
    if m:
        n = fix_bw(path, float(m.group(1)), float(m.group(2)))
        return f"bw_width GT->{m.group(2)} on {n} rows"
    return ""


def main() -> int:
    EXCLUDED.mkdir(exist_ok=True)
    fixes, excluded = [], []
    for _ in range(800):
        r = subprocess.run(CMD, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode == 0:
            m = json.loads(MANIFEST.read_text())
            m["fixloop"] = {"fixes": fixes, "excluded": excluded,
                            "outcome": "ok"}
            MANIFEST.write_text(json.dumps(m, indent=1))
            print(f"BUILD OK; fixes={len(fixes)} excluded={len(excluded)}")
            if excluded:
                print("excluded:", [e['file'] for e in excluded])
            return 0
        err = r.stderr
        fm = re.search(r"stage/([A-Za-z0-9_.-]+\.endf)", err)
        if not fm:
            print("FAILED w/o file:\n" + err[-2000:])
            return 1
        fname = fm.group(1)
        desc = apply_fix(fname, err)
        if desc:
            fixes.append({"file": fname, "fix": desc})
            print(f"fixed {fname}: {desc}", flush=True)
            continue
        src = STAGE / fname
        reason = err.strip().splitlines()[-1][:250]
        shutil.move(str(src), EXCLUDED / fname)
        excluded.append({"file": fname, "reason": reason})
        print(f"EXCLUDED {fname}: {reason[:110]}", flush=True)
    print("hit 200-iteration cap")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
