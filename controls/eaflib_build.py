#!/usr/bin/env python3
"""P2-G1: parse the full EAF-2010 neutron library (own parser: MF=3, MF=8, MF=9, MF=10) and pre-collapse every
(target, MT, product, LFS) to the FISPACT 709-group structure with flat-lethargy weighting. Output (outside the repo):
~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz (+ index JSON). Every file/section failure is ledgered."""
import os, sys, json, glob, math, time, re, hashlib, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_common import endf_float, fields, read_tab1, read_list, sections
from g1_collapse import interp_eval  # same interpolation code as P1 (module runs its P1 work on import; acceptable, ~10 s)

def _group_boundaries(name="fispact-709"):
    """709-group boundaries, ascending (eV), from the vendored table — no runtime package dependency."""
    import json as _json, os as _os
    p = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "..",
        "crates",
        "actinv-data",
        "data",
        "fispact_709_groups.json",
    )
    b = _json.load(open(p))["boundaries_eV"]
    return b[::-1] if b[0] > b[-1] else b

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
LIB = os.path.expanduser("~/nuclear-data/eaf-2010"); OUT = os.path.join(LIB, "actinv_eaf2010_709g.npz"); IDX = os.path.join(LIB, "actinv_eaf2010_709g_index.json")
bounds = np.array(_group_boundaries(), float); LNW = np.log(bounds[1:] / bounds[:-1])
def group_avg(x, y, nbt):
    """709-group flat-lethargy averages of a TAB1 function: (1/ln(E2/E1)) ∫ σ(E) dE/E with lin-lin between union points."""
    grid = np.union1d(np.asarray(x, float), bounds); grid = grid[(grid >= bounds[0]) & (grid <= bounds[-1])]
    s = interp_eval(x, y, nbt, grid); E1, E2, s1, s2 = grid[:-1], grid[1:], s[:-1], s[1:]
    b = (s2 - s1) / (E2 - E1); a = s1 - b * E1; seg = a * np.log(E2 / E1) + b * (E2 - E1)
    gi = np.searchsorted(grid, bounds)  # direct per-group sums (P2 G1 repair: cumsum differencing lost ~1e-9)
    sums = np.add.reduceat(np.concatenate([seg, [0.0]]), gi[:-1]); sums[gi[:-1] == gi[1:]] = 0.0
    return sums / LNW
def parse_mf8(lines):
    f = fields(lines[0]); ns, no = int(f[4]), int(f[5]); i = 1; prods = []
    for _ in range(ns):
        if no == 0: (zap, elfs, lmf, lfs, n1, nd, vals), i = read_list(lines, i)
        else: ff = fields(lines[i]); zap, elfs, lmf, lfs = endf_float(ff[0]), endf_float(ff[1]), int(ff[2]), int(ff[3]); i += 1
        prods.append((int(round(zap)), int(lfs), int(lmf)))
    return prods
def parse_file(path):
    mf3, mf8, mf9, mf10 = {}, {}, {}, {}; za = liso = None
    for (mat, mf, mt), lines in sections(path):
        if mf == 1 and mt == 451:
            f = fields(lines[0]); za = int(round(endf_float(f[0]))); f2 = fields(lines[1]); liso = int(f2[3])
        elif mf == 3:
            (qm, qi, _, lr, nr, np_, nbt, x, y), _ = read_tab1(lines, 1); mf3[mt] = (nbt, x, y)
        elif mf == 8 and mt not in (454, 457, 459): mf8[mt] = parse_mf8(lines)
        elif mf in (9, 10):
            ns = int(fields(lines[0])[4]); i = 1; items = []
            for _ in range(ns):
                (qm, qi, izap, lfs, nr, np_, nbt, x, y), i = read_tab1(lines, i); items.append((int(round(izap)), int(lfs), nbt, x, y))
            (mf9 if mf == 9 else mf10)[mt] = items
    return za, liso, mf3, mf8, mf9, mf10
def main():
    files = sorted(glob.glob(os.path.join(LIB, "files", "*.dat"))); t0 = time.time()
    targets = []; rows = []; sig = []; ledger = {"parse_failures": [], "mf8_header_mismatch": [], "sections": 0}
    for k, path in enumerate(files):
        try: za, liso, mf3, mf8, mf9, mf10 = parse_file(path)
        except Exception as e: ledger["parse_failures"].append({"file": os.path.basename(path), "error": repr(e)[:200]}); continue
        tk = len(targets); targets.append({"za": za, "liso": liso, "file": os.path.basename(path), "n_mf3": len(mf3), "n_mf8": len(mf8), "n_mf9": len(mf9), "n_mf10": len(mf10)})
        for mt in sorted(set(mf3) | set(mf10)):
            prods = mf8.get(mt, [])
            # total loss cross section for the diagonal: MF3 if present else sum of MF10 partials
            if mt in mf3:
                nbt, x, y = mf3[mt]; g_tot = group_avg(x, y, nbt)
            else:
                g_tot = sum(group_avg(x, y, nbt) for (_, _, nbt, x, y) in mf10[mt])
            rows.append((tk, mt, -1, -1, 0)); sig.append(g_tot); ledger["sections"] += 1   # zap=-1: the loss term
            done = set()
            if mt in mf10:
                for (izap, lfs, nbt, x, y) in mf10[mt]:
                    hdr = [(z, l) for (z, l, lmf) in prods if lmf == 10]
                    if hdr and (izap, lfs) not in hdr: ledger["mf8_header_mismatch"].append({"file": os.path.basename(path), "mt": mt, "mf10": [izap, lfs], "mf8": hdr})
                    rows.append((tk, mt, izap, lfs, 10)); sig.append(group_avg(x, y, nbt)); done.add((izap, lfs))
            if mt in mf9 and mt in mf3:
                nbt3, x3, y3 = mf3[mt]
                for (izap, lfs, nbt, x, y) in mf9[mt]:
                    grid = np.union1d(np.union1d(np.asarray(x3, float), np.asarray(x, float)), bounds); grid = grid[(grid >= bounds[0]) & (grid <= bounds[-1])]
                    prod = interp_eval(x3, y3, nbt3, grid) * interp_eval(x, y, nbt, grid)
                    rows.append((tk, mt, izap, lfs, 9)); sig.append(group_avg(grid, prod, [(len(grid), 2)])); done.add((izap, lfs))
            for (zap, lfs, lmf) in prods:
                if lmf == 3 and (zap, lfs) not in done: rows.append((tk, mt, zap, lfs, 3)); sig.append(g_tot); done.add((zap, lfs))
        if k % 100 == 0: print(f"  {k}/{len(files)} files, {len(rows)} rows, {time.time() - t0:.0f} s", file=sys.stderr)
    S = np.array(sig); R = np.array(rows, dtype=np.int64)
    np.savez_compressed(OUT, rows=R, sig=S, bounds=bounds)
    json.dump({"targets": targets, "n_rows": len(rows), "columns": "rows: (target_index, MT, product_ZA or -1 for the loss term, LFS, LMF source)", "groups": 709, "weighting": "flat lethargy", "ledger": ledger,
               "sha256_npz": hashlib.sha256(open(OUT, "rb").read()).hexdigest(), "build_seconds": time.time() - t0}, open(IDX, "w"), indent=1)
    print(f"targets {len(targets)}, rows {len(rows)}, parse failures {len(ledger['parse_failures'])}, mf8 mismatches {len(ledger['mf8_header_mismatch'])}, {time.time() - t0:.0f} s -> {OUT}")
if __name__ == "__main__": main()
