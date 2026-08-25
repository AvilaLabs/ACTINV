#!/usr/bin/env python3
"""ACTINV P1-G1: own MF=3/MF=9/MF=10 parser on EAF-2010, FNS 709-group collapse (flat-in-lethargy intra-group),
control vs openmc.data on the same file/grid/integrator. Writes results/spectrum.json first, then results/g1_collapse.json."""
import os, sys, json, math, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_common import endf_float, fields, read_tab1, sections
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results"); os.makedirs(RES, exist_ok=True)
DATA = os.path.expanduser("~/nuclear-data/tendl-eaf-test/EAF-2010")
FILES = {"Fe56": "n_2631_26-FE-56.dat", "Ag107": "n_4725_47-AG-107.dat", "W186": "n_7443_74-W-186.dat"}
WANT = {"Fe56": [102, 103, 107, 16, 105, 104, 28, 22, 32, 111], "W186": [102], "Ag107": [102]}
# ---------------- spectrum (written before any collapse)
import pypact as pp
bounds = np.array(pp.ALL_GROUPS[709], float)
if bounds[0] > bounds[-1]: bounds = bounds[::-1]  # pypact stores the structure descending
assert bounds.size == 710 and np.all(np.diff(bounds) > 0), (bounds.size, bounds[:3])
ffile = os.path.expanduser("~/nuclear-data/conderc-fns/fns/Fe/1996exp_5min_fluxes")
vals = []
for line in open(ffile):
    try: vals += [float(x) for x in line.split()]
    except ValueError: break
flux_desc = np.array(vals[:709]); flux_asc = flux_desc[::-1]  # file lists highest-energy group first
spec = {"source": ffile, "groups": 709, "boundaries_source": "pypact.ALL_GROUPS[709] (fispact/pypact, Apache-2.0)", "boundaries_eV_min_max": [float(bounds[0]), float(bounds[-1])],
        "file_order": "descending energy (FISPACT-II fluxes format); reversed to ascending for use", "intra_group_shape": "flat in lethargy: phi(E) = phi_g / (E ln(Ehi/Elo))",
        "total_flux_file_units": float(flux_asc.sum()), "nonzero_groups": int((flux_asc > 0).sum()), "flux_ascending": flux_asc.tolist()}
json.dump(spec, open(os.path.join(RES, "spectrum.json"), "w"), indent=1)
# ---------------- own parser
def interp_eval(x, y, nbt, xs):
    """Evaluate a TAB1 (x,y, interpolation regions) at points xs (numpy), honoring INT 1-5."""
    x = np.asarray(x); y = np.asarray(y); out = np.zeros_like(xs, float)
    idx = np.searchsorted(x, xs, side="right") - 1; idx = np.clip(idx, 0, len(x) - 2)
    x1, x2, y1, y2 = x[idx], x[idx + 1], y[idx], y[idx + 1]
    # interpolation law per point: region r covers points up to NBT_r (1-based) -> segment i uses law of first region with NBT > i+1
    laws = np.ones(len(x) - 1, int)
    start = 0
    for nb, law in nbt:
        laws[start:nb - 1] = law; start = nb - 1
    law = laws[idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        t_lin = np.where(x2 != x1, (xs - x1) / (x2 - x1), 0.0)
        t_log = np.where((x2 > 0) & (x1 > 0) & (x2 != x1), np.log(xs / x1) / np.log(x2 / x1), 0.0)
        out = np.where(law == 1, y1, out)
        out = np.where(law == 2, y1 + t_lin * (y2 - y1), out)
        out = np.where(law == 3, y1 + t_log * (y2 - y1), out)
        out = np.where(law == 4, np.where((y1 > 0) & (y2 > 0), y1 * (y2 / y1) ** t_lin, y1 + t_lin * (y2 - y1)), out)
        out = np.where(law == 5, np.where((y1 > 0) & (y2 > 0), y1 * (y2 / y1) ** t_log, y1 + t_log * (y2 - y1)), out)
    out[(xs < x[0]) | (xs > x[-1])] = 0.0
    return out
def parse_file(path):
    mf3, mf9, mf10 = {}, {}, {}
    for (mat, mf, mt), lines in sections(path):
        if mf == 3:
            (qm, qi, _, lr, nr, np_, nbt, x, y), _ = read_tab1(lines, 1)
            mf3[mt] = {"qm": qm, "qi": qi, "nbt": nbt, "x": x, "y": y, "laws": sorted({l for _, l in nbt})}
        elif mf in (9, 10):
            ns = int(fields(lines[0])[4]); i = 1; items = []
            for _ in range(ns):
                (qm, qi, izap, lfs, nr, np_, nbt, x, y), i = read_tab1(lines, i)
                items.append({"izap": izap, "lfs": lfs, "qi": qi, "nbt": nbt, "x": x, "y": y})
            (mf9 if mf == 9 else mf10)[mt] = items
    return mf3, mf9, mf10
def collapse(sig_on_grid, grid):
    """sigma given on 'grid' (ascending eV, includes all group boundaries); flat-lethargy flux; returns one-group sigma (barns)."""
    num = 0.0; den = flux_asc.sum()
    gi = np.searchsorted(grid, bounds)  # positions of boundaries in grid (exact members)
    for g in range(709):
        phi = flux_asc[g]
        if phi == 0: continue
        lo, hi = gi[g], gi[g + 1]
        E = grid[lo:hi + 1]; s = sig_on_grid[lo:hi + 1]
        E1, E2, s1, s2 = E[:-1], E[1:], s[:-1], s[1:]
        # sigma linear in E on each segment: s = a + b E ; integral of s/E dE = a ln(E2/E1) + b (E2-E1)
        b = (s2 - s1) / (E2 - E1); a = s1 - b * E1
        integ = (a * np.log(E2 / E1) + b * (E2 - E1)).sum()
        num += phi * integ / math.log(bounds[g + 1] / bounds[g])
    return num / den
def union_grid(x):
    g = np.union1d(np.asarray(x, float), bounds); g = g[(g >= bounds[0]) & (g <= bounds[-1])]; return g
# ---------------- openmc control
import openmc.data
out = {"route": "ROUTE-B′", "library": "EAF-2010 (IAEA mirror)", "spectrum": "results/spectrum.json", "tests": []}
for nuc, fn in FILES.items():
    path = os.path.join(DATA, fn); mf3, mf9, mf10 = parse_file(path)
    try:
        inc = openmc.data.IncidentNeutron.from_endf(path); omc_ok = True
    except Exception as e:
        inc = None; omc_ok = False; out.setdefault("openmc_errors", {})[nuc] = repr(e)[:300]
    for mt in WANT[nuc]:
        if mt not in mf3: out["tests"].append({"nuclide": nuc, "mt": mt, "status": "absent in file"}); continue
        r = mf3[mt]; grid = union_grid(r["x"]); s_own = interp_eval(r["x"], r["y"], r["nbt"], grid)
        sig_own = collapse(s_own, grid)
        t = {"nuclide": nuc, "mt": mt, "laws": r["laws"], "npoints": len(r["x"]), "sigma_own_b": sig_own}
        if omc_ok and mt in inc.reactions:
            xs = inc.reactions[mt].xs[list(inc.reactions[mt].xs)[0]]; s_omc = xs(grid); sig_omc = collapse(s_omc, grid)
            t.update({"sigma_openmc_b": sig_omc, "max_rel_diff": abs(sig_own - sig_omc) / max(abs(sig_omc), 1e-300), "pointwise_max_rel": float(np.max(np.abs(s_own - s_omc) / np.maximum(np.abs(s_omc), 1e-300)))})
        else:
            # low-level control: openmc's TAB1 reader on the same section
            ev = openmc.data.endf.Evaluation(path); sec = ev.section[(3, mt)]
            from openmc.data.endf import get_head_record, get_tab1_record
            import io; fh = io.StringIO(sec); get_head_record(fh); params, tab = get_tab1_record(fh)
            s_omc = tab(grid); sig_omc = collapse(s_omc, grid)
            t.update({"control": "openmc.data.endf.get_tab1_record", "sigma_openmc_b": sig_omc, "max_rel_diff": abs(sig_own - sig_omc) / max(abs(sig_omc), 1e-300), "pointwise_max_rel": float(np.max(np.abs(s_own - s_omc) / np.maximum(np.abs(s_omc), 1e-300)))})
        out["tests"].append(t)
    # isomer sections: own MF=9 (yields) / MF=10 (partial xs) vs openmc's low-level TAB1 reader on the same section
    from openmc.data.endf import get_head_record, get_tab1_record
    import io
    ev = openmc.data.endf.Evaluation(path)
    def omc_blocks(mf, mt):
        fh = io.StringIO(ev.section[(mf, mt)]); head = get_head_record(fh); ns = head[4]; out_ = []
        for _ in range(ns): params, tab = get_tab1_record(fh); out_.append((int(params[3]), tab))  # (LFS, Tabulated1D)
        return out_
    for mf, store, mt in ((9, mf9, 102), (10, mf10, 16)):
        if mt not in store: continue
        if nuc == "Ag107" and mf != 9: continue
        if nuc == "W186" and mf != 10: continue
        if nuc == "Fe56": continue
        blocks = dict(omc_blocks(mf, mt)) if (mf, mt) in ev.section else {}
        tot = mf3.get(mt)
        for it in store[mt]:
            grid = union_grid(it["x"] if tot is None else tot["x"] + it["x"]); f_own = interp_eval(it["x"], it["y"], it["nbt"], grid)
            if mf == 9:
                s_tot = interp_eval(tot["x"], tot["y"], tot["nbt"], grid); s_own = s_tot * f_own
                s_tot_omc = inc.reactions[mt].xs[list(inc.reactions[mt].xs)[0]](grid) if omc_ok and mt in inc.reactions else s_tot
            else:
                s_own = f_own; s_tot_omc = None
            t = {"nuclide": nuc, "mt": mt, "mf": mf, "isomer_lfs": it["lfs"], "izap": it["izap"], "sigma_own_b": collapse(s_own, grid), "control": "openmc.data.endf.get_tab1_record on MF=%d" % mf}
            if it["lfs"] in blocks:
                f_omc = blocks[it["lfs"]](grid); s_omc = (s_tot_omc * f_omc) if mf == 9 else f_omc
                t["sigma_openmc_b"] = collapse(s_omc, grid); t["max_rel_diff"] = abs(t["sigma_own_b"] - t["sigma_openmc_b"]) / max(abs(t["sigma_openmc_b"]), 1e-300)
            else: t["status"] = "openmc section missing"
            out["tests"].append(t)
# TENDL-2023 Fe-56 resonance report (control 3)
tpath = os.path.expanduser("~/nuclear-data/tendl-eaf-test/TENDL-2023/n_026-Fe-56_2631.dat")
for (mat, mf, mt), lines in sections(tpath):
    if mf == 1 and mt == 451:
        lrp = int(fields(lines[0])[2]); out["tendl2023_Fe56"] = {"LRP": lrp}
    if mf == 2 and mt == 151:
        f = fields(lines[2]); out["tendl2023_Fe56"].update({"EL_eV": endf_float(f[0]), "EH_eV": endf_float(f[1]), "LRU": int(f[2]), "LRF": int(f[3]), "note": "resolved-resonance reconstruction required before collapse; not attempted in P1"}); break
json.dump(out, open(os.path.join(RES, "g1_collapse.json"), "w"), indent=1)
for t in out["tests"]:
    print(f"{t['nuclide']:6s} MT{t['mt']:<4d}" + (f" lfs={t['isomer_lfs']}" if 'isomer_lfs' in t else "      ") + f" own={t.get('sigma_own_b', float('nan')):.6e} b  omc={t.get('sigma_openmc_b', float('nan')):.6e} b  rel={t.get('max_rel_diff', float('nan')):.2e}  {t.get('status', '')}{t.get('openmc_product', '')}")
print("openmc errors:", out.get("openmc_errors")); print("TENDL:", out.get("tendl2023_Fe56"))
