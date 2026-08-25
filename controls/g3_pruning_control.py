#!/usr/bin/env python3
"""P3-G3 control: heat with rate-significance pruning (results/fns, prune 2) vs reachable-set pruning (results/fns_p1, prune 1)
on all 132 FNS experiments, <= 1e-10 relative at every matched point; timing and dropped bounds reported. Also P3-G1 (b):
products_no_decay_record counts after the fallback. Writes results/g3_pruning.json."""
import os, sys, json, glob, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import decayheat as dh
D = dh.decay_table(); LAME = max(d["lambda"] * (d["E_light"] + d["E_EM"] + d["E_heavy"]) * dh.EV for d in D.values())  # max lambda*E over the library (W per atom)
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
worst = 0.0; rows = []; ms1 = []; ms2 = []; states1 = []; states2 = []; bounds = []; nodecay = []; fission = 0; sources = set()
for f in sorted(glob.glob(os.path.join(RES, "fns", "*.json"))):
    r2 = json.load(open(f)); r1 = json.load(open(os.path.join(RES, "fns_p1", os.path.basename(f))))
    if r2.get("error") or r1.get("error"): rows.append({"exp": os.path.basename(f), "error": True}); continue
    h1 = np.array(r1["heat_uW_g_actinv"]); h2 = np.array(r2["heat_uW_g_actinv"]); k = min(h1.size, h2.size)
    rel = float(np.max(np.abs(h2[:k] - h1[:k]) / np.maximum(np.abs(h1[:k]), 1e-300))); worst = max(worst, rel)
    dl = r2["ledger"]["rate_pruning"].get("dropped", [])   # per-nuclide bound × that nuclide's own lambda*E (Amendment B §4 ii)
    def hb(x):
        d = D.get((x["za"], x["liso"]))
        if d is None: return 0.0
        E_ = (d["E_light"] + d["E_EM"] + d["E_heavy"]) * dh.EV; return E_ * min(d["lambda"] * x["bound_atoms_per_g"], x.get("feed_bound_atoms_per_s_g", float("inf")))
    bnd = sum(hb(x) for x in dl) * 1e6   # uW/g: E * min(lambda*B, F) per dropped nuclide (Amendment B §4 ii, feed-rate form)
    bfrac = float(np.max(bnd / np.maximum(h2[:k], 1e-300))); worst_bound = max(globals().get("worst_bound", 0.0), bfrac); globals()["worst_bound"] = worst_bound
    ms1.append(r1["ms_total"]); ms2.append(r2["ms_total"]); states1.append(r1["pruned_size"]); states2.append(r2["pruned_size"]); bounds.append(r2["ledger"]["rate_pruning"]["dropped_bound_atoms_per_g"])
    nodecay.append(len(r2["ledger"].get("products_no_evaluated_decay_data_ENDFB80_JEFF33", r2["ledger"].get("products_no_decay_record", {})))); fission += len(r2["ledger"].get("fission_no_yields_to_leakage", {})); sources |= set(r2["ledger"].get("decay_data_sources_used", []))
    rows.append({"exp": os.path.basename(f)[:-5], "max_rel": rel, "states_p1": r1["pruned_size"], "states_p2": r2["pruned_size"], "ms_p1": r1["ms_total"], "ms_p2": r2["ms_total"]})
worst_bound = globals().get("worst_bound", 0.0)
out = {"n": len(rows), "worst_rel": worst, "worst_dropped_bound_fraction_of_heat": worst_bound, "max_lambdaE_W_per_atom": LAME, "criterion": "P3 Amendment B §4: rel<=1e-8 and bound<=1e-12", "pass": bool(worst <= 1e-8 and worst_bound <= 1e-12 and all("error" not in x for x in rows)),
       "median_states": [float(np.median(states1)), float(np.median(states2))], "median_ms": [float(np.median(ms1)), float(np.median(ms2))], "sum_ms": [float(np.sum(ms1)), float(np.sum(ms2))],
       "max_dropped_bound_atoms_per_g": float(np.max(bounds)), "g1b_products_no_decay_record_per_experiment": {"min": int(min(nodecay)), "max": int(max(nodecay))}, "fission_categories_total": fission, "decay_sources_used": sorted(sources), "rows": rows}
json.dump(out, open(os.path.join(RES, "g3_pruning.json"), "w"), indent=1); print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
