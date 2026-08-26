#!/usr/bin/env python3
"""ACTINV P4-G1: build a 709-group activation library from TENDL-2023 ENDF-6 files with the own pipeline:
MF=2 parse + resolved-resonance reconstruction (SLBW/MLBW/RM) for capture (MT102) and fission (MT18), SIGMA1 broadening
to 293.6 K on a resonance-adaptive grid, MF=3 for everything else, products from MF=8 (LMF 3/9/10) else MT arithmetic,
flat-lethargy 709-group collapse. Parallel over files (4 workers), memory-bounded. Every unsupported feature is ledgered
per target. Output: <outdir>/actinv_tendl2023_709g.npz + index JSON. Usage: tendl_build.py FILES_DIR OUT_DIR [--workers N]
[--limit K] [--dense 2] """
import os, sys, json, glob, math, time, re, hashlib, traceback, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_common import endf_float, fields, read_tab1, read_list, sections
from resonance import parse_mf2, reconstruct_range
from doppler import broaden
import pypact as pp
BOUNDS = np.array(pp.ALL_GROUPS[709], float); BOUNDS = BOUNDS[::-1] if BOUNDS[0] > BOUNDS[-1] else BOUNDS; LNW = np.log(BOUNDS[1:] / BOUNDS[:-1])
T_K = 293.6; DENSE = float(os.environ.get("ACTINV_DENSE", "1"))
PART = {"n": (0, -1), "p": (-1, -1), "d": (-1, -2), "t": (-1, -3), "3He": (-2, -3), "a": (-2, -4), "gamma": (0, 0)}
def mt_table():
    """MT -> (dZ, dA) for the residual, from openmc.data.REACTION_NAME (recorded in results/tables/mt_products.json)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "tables", "mt_products.json")
    if os.path.exists(p): return {int(k): tuple(v) for k, v in json.load(open(p))["table"].items()}
    import openmc.data; tab = {}
    for mt, name in openmc.data.REACTION_NAME.items():
        m = re.match(r"\(n,(.+)\)$", name)
        if not m: continue
        s = m.group(1)
        if s in ("n'", "elastic", "total", "level", "continuum", "anything", "disappear", "absorption") or "'" in s or s in ("heating", "damage-energy", "heating-local"): continue
        dz, da = 0, 1; ok = True
        for mult, part in re.findall(r"(\d*)(3He|n|p|d|t|a|gamma)", s):
            if part not in PART: ok = False; break
            k = int(mult) if mult else 1; dz += k * PART[part][0]; da += k * PART[part][1]
        if ok and re.fullmatch(r"(\d*(3He|n|p|d|t|a|gamma))+", s): tab[mt] = (dz, da)
    os.makedirs(os.path.dirname(p), exist_ok=True); json.dump({"source": "openmc.data.REACTION_NAME (OpenMC 0.15.3), parsed for emitted particles; residual = target + n - emitted", "table": {str(k): v for k, v in tab.items()}}, open(p, "w"), indent=1)
    return tab
MT_PROD = mt_table()
def source_fingerprint():
    """sha256 over the modules that determine a target's numbers; cached results from a different fingerprint are ignored."""
    h = hashlib.sha256(); d = os.path.dirname(os.path.abspath(__file__))
    for f in ("tendl_build.py", "resonance.py", "doppler.py", "endf_common.py", "g1_collapse.py"): h.update(open(os.path.join(d, f), "rb").read())
    h.update(("dense=%s;T=%s" % (os.environ.get("ACTINV_DENSE", "1"), T_K)).encode()); return h.hexdigest()
FINGERPRINT = None   # set in main(), inherited by forked workers
def group_avg_grid(E, s):
    """709-group flat-lethargy averages of a function given lin-lin on grid E (ascending)."""
    grid = np.union1d(E, BOUNDS); grid = grid[(grid >= BOUNDS[0]) & (grid <= BOUNDS[-1])]; sv = np.interp(grid, E, s, left=0.0, right=0.0)
    E1, E2, s1, s2 = grid[:-1], grid[1:], sv[:-1], sv[1:]; dE = E2 - E1; ok = dE > 0   # zero-length segments (ENDF double points at discontinuities) contribute nothing
    b = np.where(ok, (s2 - s1) / np.where(ok, dE, 1.0), 0.0); a = s1 - b * E1; seg = np.where(ok, a * np.log(np.where(ok, E2 / E1, 1.0)) + b * dE, 0.0)
    gi = np.searchsorted(grid, BOUNDS); sums = np.add.reduceat(np.concatenate([seg, [0.0]]), gi[:-1]); sums[gi[:-1] == gi[1:]] = 0.0; return sums / LNW
def interp_tab1(x, y, nbt, grid):
    from g1_collapse import interp_eval   # same interpolation-law code as P1/P2 (import triggers P1 work once per worker, ~10 s)
    return interp_eval(x, y, nbt, grid)
def parse_file(path):
    za = liso = awr = None; mf3 = {}; mf8 = {}; mf9 = {}; mf10 = {}
    for (mat, mf, mt), lines in sections(path):
        if mf == 1 and mt == 451: f = fields(lines[0]); za = int(round(endf_float(f[0]))); awr = endf_float(f[1]); liso = int(fields(lines[1])[3])
        elif mf == 3: (qm, qi, _, lr, nr, np_, nbt, x, y), _ = read_tab1(lines, 1); mf3[mt] = (nbt, np.array(x), np.array(y))
        elif mf == 8 and mt not in (454, 457, 459):
            f = fields(lines[0]); ns, no = int(f[4]), int(f[5]); i = 1; prods = []
            for _ in range(ns):
                if no == 0: (zap, elfs, lmf, lfs, n1, nd, vals), i = read_list(lines, i)
                else: ff = fields(lines[i]); zap, elfs, lmf, lfs = endf_float(ff[0]), endf_float(ff[1]), int(ff[2]), int(ff[3]); i += 1
                prods.append((int(round(zap)), int(lfs), int(lmf)))
            mf8[mt] = prods
        elif mf in (9, 10):
            ns = int(fields(lines[0])[4]); i = 1; items = []
            for _ in range(ns): (qm, qi, izap, lfs, nr, np_, nbt, x, y), i = read_tab1(lines, i); items.append((int(round(izap)), int(lfs), nbt, np.array(x), np.array(y)))
            (mf9 if mf == 9 else mf10)[mt] = items
    return za, liso, awr, mf3, mf8, mf9, mf10
def resonant_pointwise(path, awr, mf3, led):
    """Return dict MT -> (E, sigma) for MT 102 and 18 with resolved-resonance contribution reconstructed and broadened to 293.6 K,
    combined with MF=3 background inside the range and MF=3 as-is above it. None where no supported resolved range."""
    r2 = parse_mf2(path)
    if r2 is None or not r2["isotopes"]: return {}
    iso = r2["isotopes"][0]
    if len(r2["isotopes"]) > 1: led.append("NIS>1: only the first isotope's resonances used")
    if iso.get("truncated"): led.append(iso["truncated"])
    rr = [rg for rg in iso["ranges"] if rg["LRU"] == 1 and rg["LRF"] in (1, 2, 3) and not rg.get("unsupported")]
    for rg in iso["ranges"]:
        if rg.get("unsupported"): led.append(f"unsupported range {rg['EL']:.3g}-{rg['EH']:.3g} eV: {rg['unsupported']}")
        if rg["LRU"] == 2 and rg.get("LSSF", 1) == 0: led.append(f"INCOMPLETE-URR {rg['EL']:.3g}-{rg['EH']:.3g} eV: LSSF=0, MF=3 background only")
        if rg["LRU"] == 1 and rg["LRF"] not in (1, 2, 3): led.append(f"unsupported resolved formalism LRF={rg['LRF']} {rg['EL']:.3g}-{rg['EH']:.3g} eV: MF=3 background only")
    narrow = sum(int(((Lg["GN"] + Lg["GG"]) < 1e-5).sum()) for rg in rr for Lg in rg["L"]); nres = sum(int(Lg["ER"].size) for rg in rr for Lg in rg["L"])
    if narrow: led.append(f"DATA-QUALITY: {narrow}/{nres} resolved resonances narrower than 1e-5 eV (synthetic placeholders; integrated at two scales)")
    if not rr: return {}
    out = {}
    for mt, key in ((102, "capture"), (18, "fission")):
        if mt not in mf3 and mt == 18: continue
        pieces_E = []; pieces_s = []
        for rg in rr:
            Er_all = np.concatenate([Lg["ER"] for Lg in rg["L"]]); widths = np.concatenate([(Lg["GN"] + Lg["GG"] + Lg.get("GF", 0 * Lg["GN"]) + np.abs(Lg.get("GFA", 0 * Lg["GN"])) + np.abs(Lg.get("GFB", 0 * Lg["GN"]))) for Lg in rg["L"]])
            npts = int(201 * DENSE); th = np.linspace(-np.arctan(200.0), np.arctan(200.0), npts)
            lo = max(rg["EL"], 1e-5); split = min(1.0, rg["EH"])   # sparse backbone below 1 eV (smooth 1/v region), dense above
            dense = [np.logspace(np.log10(lo), np.log10(split), 300)]
            if rg["EH"] > 1.0: dense.append(np.logspace(0.0, np.log10(rg["EH"]), int(3000 * DENSE * max(1.0, np.log10(rg["EH"])))))
            kT_A = 8.617333262e-5 * T_K / awr
            for e, g in zip(Er_all, widths):   # every resonance whose wings reach into the range, including those at or beyond its bounds
                gD = np.sqrt(4 * kT_A * abs(e)); g0 = max(g, 1e-9); gw = max(g0, gD)
                if rg["EL"] - 200 * gw < e < rg["EH"] + 200 * gw:
                    dense.append(e + g0 / 2 * np.tan(th))                       # 0 K peak: resolves Gamma, so the area is right
                    if gD > 3 * g0: dense.append(e + gD / 2 * np.tan(th))       # broadened line: resolves the Doppler width
            dense.append(np.array([rg["EL"] * (1 + 1e-9), rg["EH"] * (1 - 1e-9)]))   # explicit end points so the range boundary is not bridged by a grid-dependent segment
            Ed = np.unique(np.concatenate(dense)); Ed = Ed[(Ed > rg["EL"]) & (Ed < rg["EH"])]
            rec = reconstruct_range(rg, Ed, awr)
            if rec is None: continue
            s0 = rec[key]
            if mt in mf3: nbt, x, y = mf3[mt]; s0 = s0 + interp_tab1(x, y, nbt, Ed)
            # iterative midpoint refinement (RECONR-style) until no segment's linear interpolation errs > 1e-3, max 8 passes
            for _pass in range(8):
                Em = 0.5 * (Ed[:-1] + Ed[1:]); mid = reconstruct_range(rg, Em, awr)[key]
                if mt in mf3: mid = mid + interp_tab1(x, y, nbt, Em)
                need = np.abs(mid - 0.5 * (s0[:-1] + s0[1:])) > 2e-4 * np.maximum(np.abs(mid), 1e-6)
                if not need.any(): break
                E2 = np.concatenate([Ed, Em[need]]); o = np.argsort(E2); Ed = E2[o]; s0 = np.concatenate([s0, mid[need]])[o]
            else: led.append(f"MT{mt}: linearisation not converged in 8 passes ({int(need.sum())} segments > 2e-4)")
            # broaden with the MF=3 points above the range included as kernel input, so the top of the range is not a constant tail
            gD = np.sqrt(4 * kT_A * rg["EH"])   # Doppler width at the range boundary
            if mt in mf3:
                hi_ = x >= rg["EH"]; E_in = np.concatenate([Ed, x[hi_]]); s_in = np.concatenate([s0, y[hi_]])
                # broaden across the boundary: dense output points through EH + 10 Gamma_D, then splice to unbroadened MF=3
                E_over = rg["EH"] + gD * np.linspace(0.0, 10.0, 81)[1:]; E_over = E_over[E_over < x[-1]]
                E_under = rg["EH"] - gD * np.linspace(0.0, 10.0, 81)[1:]; E_under = E_under[E_under > rg["EL"]]   # dense on both sides of the step
                Eout = np.unique(np.concatenate([Ed, E_under, E_over])); order_in = np.argsort(E_in); E_in, s_in = E_in[order_in], s_in[order_in]
                sT = np.maximum(broaden(E_in, s_in, T_K, awr, Eout=Eout), 0.0)
                pieces_E.append(Eout); pieces_s.append(sT); rg_top = rg["EH"] + 10 * gD
            else:
                sT = np.maximum(broaden(Ed, s0, T_K, awr), 0.0); pieces_E.append(Ed); pieces_s.append(sT); rg_top = rg["EH"]
        if not pieces_E: continue
        Ehi = max(pe.max() for pe in pieces_E)   # top of the broadened region (EH + 10 Gamma_D of the last range)
        if mt in mf3:
            nbt, x, y = mf3[mt]; hi = x > Ehi
            E_all = np.concatenate(pieces_E + [x[hi]]); s_all = np.concatenate(pieces_s + [y[hi]])
        else: E_all = np.concatenate(pieces_E); s_all = np.concatenate(pieces_s)
        o = np.argsort(E_all); out[mt] = (E_all[o], s_all[o])
    return out
def build_one(args):
    global DENSE; DENSE = float(os.environ.get("ACTINV_DENSE", "1"))
    path, tk, cache_dir = args if len(args) == 3 else (args[0], args[1], None); led = []; rows = []; sig = []; t0 = time.time()
    cf = os.path.join(cache_dir, os.path.basename(path) + ".npz") if cache_dir else None
    if cf and os.path.exists(cf):
        try:
            z = np.load(cf, allow_pickle=False); info = json.loads(str(z["info"])); 
            if info.get("fingerprint") == FINGERPRINT:
                r = z["rows"]; info["cached"] = True
                return info, [(tk, int(a), int(b), int(c), int(d)) for a, b, c, d in r.tolist()] if r.size else [], list(z["sig"])
        except Exception: pass
    try:
        za, liso, awr, mf3, mf8, mf9, mf10 = parse_file(path)
        res = resonant_pointwise(path, awr, mf3, led)
        for mt in sorted(set(mf3) | set(mf10)):
            if mt in (1, 2, 3, 4, 5, 27, 101, 444) or (51 <= mt <= 91) or (201 <= mt <= 207) or (600 <= mt <= 849) or mt >= 1000 or (mt in (18, 19, 20, 21, 38) and mt != 18):
                if mt == 5 and mf8.get(5): led.append("MT=5 (n,anything) products not tracked")
                continue
            if mt in res: E, s = res[mt]; g_tot = group_avg_grid(E, s)
            elif mt in mf3: nbt, x, y = mf3[mt]; grid = np.union1d(x, BOUNDS); grid = grid[(grid >= BOUNDS[0]) & (grid <= BOUNDS[-1])]; g_tot = group_avg_grid(grid, interp_tab1(x, y, nbt, grid))
            else: g_tot = None
            prods = mf8.get(mt, [])
            if g_tot is None and mt not in mf10: continue
            if g_tot is None: g_tot = sum(group_avg_grid(*(lambda nbt, x, y: (np.union1d(x, BOUNDS)[(np.union1d(x, BOUNDS) >= BOUNDS[0]) & (np.union1d(x, BOUNDS) <= BOUNDS[-1])], None))(nbt, x, y)[:1], interp_tab1(x, y, nbt, np.union1d(x, BOUNDS)[(np.union1d(x, BOUNDS) >= BOUNDS[0]) & (np.union1d(x, BOUNDS) <= BOUNDS[-1])])) for (_, _, nbt, x, y) in mf10[mt])
            rows.append((tk, mt, -1, -1, 0)); sig.append(g_tot); done = set()
            if mt in mf10:
                for (izap, lfs, nbt, x, y) in mf10[mt]:
                    grid = np.union1d(x, BOUNDS); grid = grid[(grid >= BOUNDS[0]) & (grid <= BOUNDS[-1])]
                    rows.append((tk, mt, izap, lfs, 10)); sig.append(group_avg_grid(grid, interp_tab1(x, y, nbt, grid))); done.add((izap, lfs))
            if mt in mf9 and (mt in res or mt in mf3):
                E, s = res[mt] if mt in res else (None, None)
                for (izap, lfs, nbt, x, y) in mf9[mt]:
                    if E is None: nbt3, x3, y3 = mf3[mt]; grid = np.union1d(np.union1d(x3, x), BOUNDS); grid = grid[(grid >= BOUNDS[0]) & (grid <= BOUNDS[-1])]; prod = interp_tab1(x3, y3, nbt3, grid) * interp_tab1(x, y, nbt, grid)
                    else:
                        xa, ya = np.asarray(x, float), np.asarray(y, float); ramp = [np.geomspace(max(xa[i], 1e-5), xa[i + 1], 65)[1:-1] for i in range(len(xa) - 1) if ya[i] != ya[i + 1] and xa[i + 1] > xa[i] > 0]
                        grid = np.union1d(np.union1d(np.union1d(E, xa), BOUNDS), np.concatenate(ramp) if ramp else np.array([])); grid = grid[(grid >= BOUNDS[0]) & (grid <= BOUNDS[-1])]
                        prod = np.interp(grid, E, s, left=0.0, right=0.0) * interp_tab1(x, y, nbt, grid)   # yield points + dense sampling across every yield ramp
                    rows.append((tk, mt, izap, lfs, 9)); sig.append(group_avg_grid(grid, prod)); done.add((izap, lfs))
            for (zap, lfs, lmf) in prods:
                if lmf == 3 and (zap, lfs) not in done: rows.append((tk, mt, zap, lfs, 3)); sig.append(g_tot); done.add((zap, lfs))
            if not done:   # no MF=8 product: fission -> fission-products row (zap 0, the runner's fission category); else MT arithmetic; else leakage
                if mt == 18: rows.append((tk, 18, 0, 0, 0)); sig.append(g_tot)
                elif mt in MT_PROD: dz, da = MT_PROD[mt]; rows.append((tk, mt, za + dz * 1000 + da, 0, -1)); sig.append(g_tot)
                else: rows.append((tk, mt, 0, 0, -2)); sig.append(g_tot); led.append(f"MT{mt}: product unmapped -> leakage")
        info = {"file": os.path.basename(path), "za": za, "liso": liso, "awr": awr, "n_mf3": len(mf3), "resonant_mts": sorted(res), "ledger": led, "seconds": time.time() - t0, "fingerprint": FINGERPRINT}
        if cf:
            tmp = cf + ".tmp.npz"; np.savez_compressed(tmp, rows=np.array([r[1:] for r in rows], dtype=np.int64) if rows else np.zeros((0, 4), np.int64), sig=np.array(sig) if sig else np.zeros((0, 709)), info=json.dumps(info)); os.replace(tmp, cf)
        return info, rows, sig
    except Exception:
        info = {"file": os.path.basename(path), "za": None, "liso": None, "error": traceback.format_exc()[-600:], "ledger": led, "seconds": time.time() - t0, "fingerprint": FINGERPRINT}
        if cf:
            tmp = cf + ".tmp.npz"; np.savez_compressed(tmp, rows=np.zeros((0, 4), np.int64), sig=np.zeros((0, 709)), info=json.dumps(info)); os.replace(tmp, cf)
        return info, [], []
def main():
    import argparse, multiprocessing as mp
    ap = argparse.ArgumentParser(); ap.add_argument("files_dir"); ap.add_argument("out_dir"); ap.add_argument("--workers", type=int, default=8); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--name", default="actinv_tendl2023_709g"); ap.add_argument("--dense", type=float, default=None)
    a = ap.parse_args(); files = sorted(glob.glob(os.path.join(a.files_dir, "*.dat")))
    if a.dense: os.environ["ACTINV_DENSE"] = str(a.dense)
    global FINGERPRINT; FINGERPRINT = source_fingerprint()
    if a.limit: files = files[:a.limit]
    os.makedirs(a.out_dir, exist_ok=True); cache = os.path.join(a.out_dir, "cache_" + a.name); os.makedirs(cache, exist_ok=True)
    have = sum(1 for f in files if os.path.exists(os.path.join(cache, os.path.basename(f) + ".npz")))
    print(f"fingerprint {FINGERPRINT[:12]}; {have}/{len(files)} targets already cached in {cache}", file=sys.stderr, flush=True)
    t0 = time.time(); targets = []; ROWS = []; SIG = []; nerr = 0
    with mp.Pool(a.workers, maxtasksperchild=20) as pool:
        for k, (info, rows, sig) in enumerate(pool.imap(build_one, [(f, i, cache) for i, f in enumerate(files)], chunksize=1)):
            targets.append(info); ROWS += rows; SIG += sig; nerr += 1 if info.get("error") else 0
            if k % 50 == 0 or info.get("error"): print(f"  {k+1}/{len(files)} {info['file']} {info.get('seconds', 0):.1f}s rows={len(rows)} led={len(info['ledger'])}{' cached' if info.get('cached') else ''}{' ERROR' if info.get('error') else ''}  total {time.time()-t0:.0f}s", file=sys.stderr, flush=True)
    R = np.array(ROWS, dtype=np.int64); S = np.array(SIG); out = os.path.join(a.out_dir, a.name + ".npz"); np.savez_compressed(out, rows=R, sig=S, bounds=BOUNDS)
    json.dump({"targets": targets, "n_rows": len(ROWS), "n_errors": nerr, "columns": "rows: (target_index, MT, product ZA (-1 loss term, 0 unmapped), LFS, LMF source: 3/9/10, -1 MT arithmetic, -2 leakage)", "groups": 709, "weighting": "flat lethargy", "temperature_K": T_K, "dense_factor": DENSE, "fingerprint": FINGERPRINT, "n_from_cache": sum(1 for t in targets if t.get("cached")), "sha256_npz": hashlib.sha256(open(out, "rb").read()).hexdigest(), "build_seconds": time.time() - t0}, open(os.path.join(a.out_dir, a.name + "_index.json"), "w"), indent=1)
    print(f"targets {len(targets)} rows {len(ROWS)} errors {nerr} {time.time()-t0:.0f}s -> {out}")
if __name__ == "__main__": main()
