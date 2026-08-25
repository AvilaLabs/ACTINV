#!/usr/bin/env python3
"""P2-G4: run every FNS experiment through ACTINV (EAF-2010 709-group library, ENDF/B-VIII.0 decay, actinv-solve with
pruning), evaluate decay heat with the code-agnostic evaluator, compute C/E vs measurement for ACTINV and for the
FISPACT-II/TENDL-2017 reference, write per-experiment records (inventories in the interchange schema) and a summary."""
import os, sys, json, glob, math, subprocess, time, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain import build
from harness import fispact_io as fio, composition as comp, decayheat as dh
from harness.elements import SYM_OF
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results"); FNS = os.path.join(RES, os.environ.get("ACTINV_FNS_DIR", "fns")); os.makedirs(FNS, exist_ok=True)
BIN = os.path.join(ROOT, "target", "release", "actinv-solve"); LIB = os.path.expanduser("~/nuclear-data/eaf-2010")
DATA = os.path.expanduser("~/nuclear-data/conderc-fns/fns")
cc = json.load(open(os.path.expanduser("~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json")))["Cram16Solver"]
# ---- library
L = np.load(os.path.join(LIB, "actinv_eaf2010_709g.npz")); ROWS, SIG, BOUNDS = L["rows"], L["sig"], L["bounds"]; IDXJ = json.load(open(os.path.join(LIB, "actinv_eaf2010_709g_index.json")))
TARGETS = [(t["za"], t["liso"]) for t in IDXJ["targets"]]; T_INDEX = {t: k for k, t in enumerate(TARGETS)}
# ---- decay chain (once)
keys, recs, idx, lam, entries, leak = build(); N = len(keys) + 2; LEAK = N - 2; UNIT = N - 1   # Amendment B: constant unit state
NAME = {k: (int(round(recs[m]["za"])), recs[m]["liso"]) for k, m in enumerate(keys)}
def nuc_name(za, liso): z, a = divmod(za, 1000); return f"{SYM_OF.get(z, 'Z%d' % z)}{a}" + ("m%d" % liso if liso else "")
def flux_from_file(path, total):
    vals = []
    for line in open(path):
        try: vals += [float(x) for x in line.split()]
        except ValueError: break
    f = np.array(vals[:709])[::-1]; return f * (total / f.sum())
def reaction_matrix(phi):
    """Per-atom rates (s^-1) for every library target under group flux phi (ascending). Returns dict (row, col)->rate, ledger."""
    sbar = SIG @ phi / phi.sum() * 1e-24 * phi.sum()   # = sum_g sig_g phi_g * 1e-24  (rate per atom, s^-1)
    R = {}; led = {"products_no_evaluated_decay_data_ENDFB80_JEFF33": {}, "targets_absent_from_decay_lib": []}
    for (tk, mt, zap, lfs, lmf), rate in zip(ROWS, sbar):
        if rate == 0.0: continue
        za, liso = TARGETS[tk]; col = idx.get((za, liso))
        if col is None:
            if (za, liso) not in led["targets_absent_from_decay_lib"]: led["targets_absent_from_decay_lib"].append((za, liso))
            continue
        if zap == -1: R[(col, col)] = R.get((col, col), 0.0) - rate; continue
        if mt == 18 and zap == 0:   # fission: no yields in ACTINV yet -> leakage, own ledger category
            led.setdefault("fission_no_yields_to_leakage", {})[f"{za}_{liso}"] = rate; R[(LEAK, col)] = R.get((LEAK, col), 0.0) + rate; continue
        row = idx.get((int(zap), int(lfs)))
        if row is None:
            row = idx.get((int(zap), 0))
            if row is None: led["products_no_evaluated_decay_data_ENDFB80_JEFF33"][f"{int(zap)}_{int(lfs)}"] = led["products_no_evaluated_decay_data_ENDFB80_JEFF33"].get(f"{int(zap)}_{int(lfs)}", 0.0) + rate; row = LEAK
        R[(row, col)] = R.get((row, col), 0.0) + rate
    return R, led
def write_problem(path, n0, R, sched, Dm):
    with open(path, "w") as f:
        f.write("ACTINV-PROBLEM 1\nn %d\n" % N); f.write("decay %d\n" % len(Dm))
        for (i, j), v in Dm.items(): f.write("%d %d %.17e\n" % (i, j, v))
        f.write("reaction %d\n" % len(R))
        for (i, j), v in R.items(): f.write("%d %d %.17e\n" % (i, j, v))   # already per second at this experiment's flux; schedule phi = 1 or 0
        f.write("n0 %d\n" % len(n0))
        for i, v in n0.items(): f.write("%d %.17e\n" % (i, v))
        f.write("cram %.17e %d\n" % (cc["alpha0"], len(cc["theta_re"])))
        for a, b, c, d in zip(cc["theta_re"], cc["theta_im"], cc["alpha_re"], cc["alpha_im"]): f.write("%.17e %.17e %.17e %.17e\n" % (a, b, c, d))
        f.write("schedule %d\n" % len(sched))
        for dt, on in sched: f.write("%.17e %.17e\n" % (dt, on))
        f.write("prune %s %s\n" % (os.environ.get("ACTINV_PRUNE", "2"), os.environ.get("ACTINV_BMIN", "1e-8")))
def read_result(path):
    lines = open(path).read().splitlines(); hdr = dict(zip(lines[1].split()[0::2], lines[1].split()[1::2])); steps = []; i = 2
    dropped = []
    while i < len(lines):
        p = lines[i].split()
        if p[0] == "dropped":
            for l in lines[i + 1:i + 1 + int(p[1])]: a, b, fr = l.split(); dropped.append((int(a), float(b), float(fr)))
            break
        k = int(p[5]); vec = {}
        for l in lines[i + 1:i + 1 + k]: a, b = l.split(); vec[int(a)] = float(b)
        steps.append({"t": float(p[3]), "vec": vec}); i += 1 + k
    hdr["_dropped"] = dropped; return hdr, steps
def run_experiment(mat, tag):
    d = os.path.join(DATA, mat); ifile = os.path.join(d, f"TENDL-2017_{tag}.i"); ef = os.path.join(d, f"{tag}.exp"); ff = os.path.join(d, f"{tag}_fluxes")
    of = os.path.join(d, f"TENDL-2017_{tag}.out"); nf = os.path.join(d, f"TENDL-2017_{tag}.nuclides")
    rec = {"material": mat, "experiment": tag, "ledger": {}}
    inp = fio.read_i(ifile); exp = fio.read_exp(ef); nuc = fio.read_nuclides(nf) if os.path.exists(nf) else None
    phi = flux_from_file(ff, inp["flux_total"]); rec["flux_total"] = inp["flux_total"]; rec["t_irr_s"] = inp["t_irr_s"]; rec["cooling_cum_s"] = inp["cooling_cum_s"]
    inv0, cdiag = comp.atoms_per_gram(inp["elements"]); rec["composition"] = {"elements_wt": inp["elements"], "diag": cdiag["elements"]}
    R, led = reaction_matrix(phi); rec["ledger"].update(led)
    # ---- Amendment B: bulk isotopes are constant sources through the unit state
    bulk = {}; absent = []
    for (za, liso), atoms in inv0.items():
        col = idx.get((za, liso))
        if col is None: absent.append({"za": za, "liso": liso, "atoms_per_g": atoms}); continue
        bulk[col] = bulk.get(col, 0.0) + atoms
        if (za, liso) not in T_INDEX: absent.append({"za": za, "liso": liso, "atoms_per_g": atoms, "note": "in decay lib but not an EAF-2010 target (no activation)"})
    rec["ledger"]["composition_isotopes_absent"] = absent
    Rsrc = {}; dropped_bulk_production = []; burnup = {}; bulk_heat_W_g = 0.0; D = dh.decay_table()
    for (row, col), r in list(R.items()):
        if col in bulk:
            if row == col: burnup[col] = burnup.get(col, 0.0) + (-r) * inp["t_irr_s"]; continue
            if row in bulk: dropped_bulk_production.append({"from": NAME[col], "to": NAME[row], "rate_per_s_per_atom": r}); continue
            Rsrc[(row, UNIT)] = Rsrc.get((row, UNIT), 0.0) + r * bulk[col]
        elif row in bulk:
            dropped_bulk_production.append({"from": NAME[col] if col < LEAK else "leak", "to": NAME[row], "rate_per_s_per_atom": r}); continue
        else: Rsrc[(row, col)] = Rsrc.get((row, col), 0.0) + r
    Dsrc = {}
    for (row, col), v in entries.items():
        if col in bulk:
            if row == col: continue
            if row in bulk: continue
            Dsrc[(row, UNIT)] = Dsrc.get((row, UNIT), 0.0) + v * bulk[col]   # natural radioactivity of the bulk as a source
        elif row in bulk: continue
        else: Dsrc[(row, col)] = Dsrc.get((row, col), 0.0) + v
    for col, nb in bulk.items():
        za, liso = NAME[col]; d = D.get((za, liso))
        if d and d["lambda"] > 0: bulk_heat_W_g += d["lambda"] * nb * (d["E_light"] + d["E_EM"] + d["E_heavy"]) * dh.EV
    rec["bulk_background_heat_uW_g"] = bulk_heat_W_g * 1e6; rec["max_burnup_fraction"] = max(burnup.values()) if burnup else 0.0
    rec["ledger"]["bulk_production_dropped"] = dropped_bulk_production; rec["ledger"]["burnup_flag"] = rec["max_burnup_fraction"] > 1e-6
    n0 = {UNIT: 1.0}
    cool = inp["cooling_cum_s"]; sched = [(inp["t_irr_s"], 1.0)] + [(cool[0], 0.0)] + [(cool[k] - cool[k - 1], 0.0) for k in range(1, len(cool))]
    pf = os.path.join(FNS, f"{mat}_{tag}.problem"); rf = os.path.join(FNS, f"{mat}_{tag}.result"); write_problem(pf, n0, Rsrc, sched, Dsrc)
    t0 = time.time(); p = subprocess.run([BIN, pf, rf], capture_output=True, text=True); rec["solve_wall_s"] = time.time() - t0
    if p.returncode != 0: rec["error"] = p.stderr[-500:]; return rec
    hdr, steps = read_result(rf); rec["pruned_size"] = int(hdr["pruned"]); rec["ms_total"] = float(hdr["ms_total"]); rec["prune_mode"] = int(hdr.get("prune_mode", 1)); rec["ledger"]["rate_pruning"] = {"dropped_nodes": int(hdr.get("dropped_nodes", 0)), "dropped_bound_atoms_per_g": float(hdr.get("dropped_bound_atoms", 0.0)), "dropped": [{"za": NAME[i][0], "liso": NAME[i][1], "bound_atoms_per_g": b, "feed_bound_atoms_per_s_g": fr} for i, b, fr in hdr.get("_dropped", []) if i in NAME]}
    # heats at each cooling step (steps[1:]), interchange records
    inventories = []; heats = []; top = []; missing_energy = set(); zeroed = []
    for s in steps[1:]:
        neg = sum(-v for i, v in s["vec"].items() if v < 0 and i not in (LEAK, UNIT)); zeroed.append(neg)
        inv = {NAME[i]: v for i, v in s["vec"].items() if i not in (LEAK, UNIT) and v > 0}
        tot, per, miss = dh.heat_W_per_g(inv); heats.append((tot + bulk_heat_W_g) * 1e6)
        for m_ in miss: missing_energy.add((m_["za"], m_["liso"]))
        top.append(sorted(((nuc_name(*k), v * 1e6) for k, v in per.items()), key=lambda kv: -kv[1])[:5])
        inventories.append({"t_s": s["t"] - inp["t_irr_s"], "nuclides": [{"Z": k[0] // 1000, "A": k[0] % 1000, "LISO": k[1], "atoms_per_g": v} for k, v in inv.items()], "leakage_atoms_per_g": s["vec"].get(LEAK, 0.0), "source": "ACTINV P2 (Amendment B)"})
    rec["ledger"]["nuclides_without_decay_energy_data"] = sorted(missing_energy); rec["ledger"]["negative_atoms_zeroed_per_step"] = zeroed
    D_ = dh.decay_table(); rec["ledger"]["decay_data_sources_used"] = sorted({D_[k]["source"] for inv_ in inventories for x in inv_["nuclides"] for k in [(x["Z"] * 1000 + x["A"], x["LISO"])] if k in D_})
    rec["inventories"] = inventories; rec["heat_uW_g_actinv"] = heats; rec["top_contributors_actinv"] = {"first": top[0], "last": top[-1]}
    # measured and reference alignment — Amendment C: by time, unit inferred; non-positive rows excluded
    cool = np.array(cool); traw = np.array(exp["t_raw"]); units = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0, "y": 365.25 * 86400.0}
    valid = (traw > 0) & (exp["heat_uW_g"] > 0)   # padded zero rows must not poison the unit inference
    def mismatch(u):
        ts = traw[valid] * u; return float(np.median([np.min(np.abs(cool - t) / t) for t in ts])) if valid.any() else float("inf")
    unit = min(units, key=lambda u: mismatch(units[u])); ts = traw * units[unit]
    matched = []; unmatched = []
    for j, t in enumerate(ts):
        k_ = int(np.argmin(np.abs(cool - t)))
        if abs(cool[k_] - t) <= max(0.02 * t, 1.0) and exp["heat_uW_g"][j] > 0: matched.append((j, k_))
        else: unmatched.append({"row": j, "t_raw": float(traw[j]), "t_s": float(t), "nearest_step_s": float(cool[k_]), "heat": float(exp["heat_uW_g"][j]), "reason": "no step within 2%" if abs(cool[k_] - t) > max(0.02 * t, 1.0) else "heat <= 0"})
    rec["alignment"] = {"exp_time_unit": unit, "median_rel_mismatch": mismatch(units[unit]), "n_matched": len(matched), "n_measured": int(len(traw)), "n_steps": int(len(cool))}
    rec["ledger"]["measured_rows_excluded"] = unmatched; rec["n_cooling_steps"] = int(len(cool)); rec["n_measured"] = int(len(traw))
    jj = [j for j, _ in matched]; kk = [k_ for _, k_ in matched]
    if not matched:   # instrument must not raise: record and return (Amendment C)
        rec["disposition"] = {"NO_MATCHED_ROWS": True}; rec["summary"] = {}; rec["measured"] = {"t_raw": [], "heat_uW_g": [], "sigma_uW_g": [], "steps": []}; rec["CE_actinv"] = []; return rec
    E = exp["heat_uW_g"][jj]; S = exp["sigma_uW_g"][jj]; C = np.array(heats)[kk]
    rec["measured"] = {"t_raw": traw[jj].tolist(), "t_s": ts[jj].tolist(), "heat_uW_g": E.tolist(), "sigma_uW_g": S.tolist(), "steps": kk}
    rec["CE_actinv"] = (C / E).tolist()
    if nuc is not None:
        ref_all = nuc["total_kW_kg"][1:] * 1e6; ref = ref_all[kk]  # kW/kg -> uW/g ; index 0 of ref_all is the first cooling step
        rec["heat_uW_g_fispact"] = ref.tolist(); rec["CE_fispact"] = (ref / E).tolist()
        names = list(nuc["nuclides"]); rec["top_contributors_fispact"] = {"first": sorted(((nm, nuc["nuclides"][nm][1 + kk[0]] * 1e6) for nm in names), key=lambda kv: -kv[1])[:5], "last": sorted(((nm, nuc["nuclides"][nm][1 + kk[-1]] * 1e6) for nm in names), key=lambda kv: -kv[1])[:5]}
        ty = nuc["t_y"][1:] * 365.25 * 86400.0; rec["ref_time_max_rel_mismatch"] = float(np.max(np.abs(ty[:len(cool)] - cool[:len(ty)]) / cool[:len(ty)]))
    def summ(ce):
        ce = np.array(ce); ln = np.log(ce); return {"geomean_CE": float(np.exp(ln.mean())), "max_abs_lnCE": float(np.max(np.abs(ln))), "n": int(len(ce))}
    rec["summary"] = {"actinv": summ(rec["CE_actinv"])}
    if "CE_fispact" in rec: rec["summary"]["fispact"] = summ(rec["CE_fispact"])
    # dispositions
    within = np.abs(C - E) <= np.maximum(2 * S, 0.1 * E); rec["disposition"] = {"AGREE_MEAS": bool(within.all())}
    if "CE_fispact" in rec: rec["disposition"]["AGREE_REF"] = bool(np.max(np.abs(np.log(rec["CE_actinv"]) - np.log(rec["CE_fispact"]))) <= 0.1)
    return rec
def main():
    exps = []
    for mat in sorted(os.listdir(DATA)):
        for ef in sorted(glob.glob(os.path.join(DATA, mat, "*.exp"))): exps.append((mat, os.path.basename(ef)[:-4]))
    summary = []; t0 = time.time()
    for k, (mat, tag) in enumerate(exps):
        try: rec = run_experiment(mat, tag)
        except Exception as e:
            import traceback; rec = {"material": mat, "experiment": tag, "error": traceback.format_exc()[-800:]}
        json.dump(rec, open(os.path.join(FNS, f"{mat}_{tag}.json"), "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
        s = {"material": mat, "experiment": tag, "error": rec.get("error"), "pruned": rec.get("pruned_size"), "ms": rec.get("ms_total"), "summary": rec.get("summary"), "disposition": rec.get("disposition"),
             "ledger_counts": {kk: (len(v) if hasattr(v, "__len__") else v) for kk, v in rec.get("ledger", {}).items()}}
        summary.append(s); print(f"{k+1:3d}/{len(exps)} {mat:7s} {tag:16s} " + (f"ERR {rec['error'][-120:]}" if rec.get("error") else f"pruned {rec['pruned_size']:4d} {rec['ms_total']:7.2f} ms  ACTINV gm C/E {rec['summary']['actinv']['geomean_CE']:.3f} max|ln| {rec['summary']['actinv']['max_abs_lnCE']:.3f}" + (f" | FISPACT gm {rec['summary']['fispact']['geomean_CE']:.3f} max|ln| {rec['summary']['fispact']['max_abs_lnCE']:.3f}" if 'fispact' in rec['summary'] else "")), file=sys.stderr)
    json.dump({"n_experiments": len(exps), "wall_s": time.time() - t0, "experiments": summary}, open(os.path.join(RES, os.environ.get("ACTINV_FNS_DIR", "fns") + "_summary.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
if __name__ == "__main__": main()
