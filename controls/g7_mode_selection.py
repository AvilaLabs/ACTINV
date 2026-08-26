#!/usr/bin/env python3
"""P5-G7: mode selection.
  (a) `auto` selects trace below a burn-up fraction of 1e-6 and coupled above it.
  (b) At low burn-up the two modes must agree to within COUPLED's own numerical floor, not to an arbitrary tolerance.
      This is the P2 finding restated: in coupled mode the largest population is the bulk material (~1e22 atoms/g), so
      CRAM's absolute floor is alpha0 * 1e22 ~ 2e6 atoms/g, which is a ~1e-3 relative error on a product at 1e9. The
      trace formulation exists precisely to make the round-off relative to the products rather than the bulk, so the
      correct statement is that trace is the more accurate mode there — not that the two agree.
  (c) A high-fluence spec must flip the selection to coupled.
"""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONWARNINGS"] = "ignore"
import actinv
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
spec = json.load(open(os.path.join(ROOT, "examples", "fns_fe_5min.json")))
def solve(sp, mode=None):
    s = json.loads(json.dumps(sp))
    if mode: s["options"]["mode"] = mode
    return json.loads(actinv.run(json.dumps(s)))
def heat(r): return np.array([st["heat_W_per_g"]["total"] for st in r["steps"][1:]])
# ---- (a) the FNS spec: burn-up negligible, auto must pick trace, and coupled must agree
a_auto, a_tr, a_co = solve(spec), solve(spec, "trace"), solve(spec, "coupled")
h_tr, h_co = heat(a_tr), heat(a_co); m = h_tr > 0
agree = float(np.max(np.abs(h_co[m] - h_tr[m]) / h_tr[m]))
# every nuclide carrying >= 1 % of the heat must agree between modes to within coupled's floor
STEP = 1
floor_co = a_co["steps"][STEP]["numerical_floor_atoms_per_g"]; floor_tr = a_tr["steps"][STEP]["numerical_floor_atoms_per_g"]
inv_tr = {n["nuclide"]: n["atoms_per_g"] for n in a_tr["steps"][STEP]["inventory"]}
inv_co = {n["nuclide"]: n["atoms_per_g"] for n in a_co["steps"][STEP]["inventory"]}
act = a_tr["steps"][STEP]["activity_Bq_per_g"]; tot_act = sum(act.values()) or 1.0
sig_nuc = [n for n, v in act.items() if v / tot_act >= 0.01]
worst_abs = max((abs(inv_co.get(n, 0.0) - inv_tr.get(n, 0.0)) for n in sig_nuc), default=0.0)
within_floor = bool(worst_abs <= floor_co)
# ---- (b) high fluence: scale the flux until burn-up crosses the threshold, auto must flip to coupled
hi = json.loads(json.dumps(spec))
hi["spectrum"]["total"] = spec["spectrum"]["total"] * 1e11        # ~1e21 n/cm2/s, a deliberately extreme test
hi["schedule"] = [{"dt": "1 y", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}]
b_auto, b_tr, b_co = solve(hi), solve(hi, "trace"), solve(hi, "coupled")
bh_tr, bh_co = heat(b_tr), heat(b_co); bm = bh_tr > 0
diff = float(np.max(np.abs(bh_co[bm] - bh_tr[bm]) / bh_tr[bm])) if bm.any() else float("nan")
burn = b_auto["ledger"]["max_burnup_fraction"]
res = {"low_burnup": {"burnup": a_auto["ledger"]["max_burnup_fraction"], "auto_mode": a_auto["mode"],
                      "trace_vs_coupled_max_rel_heat": agree,
                      "floor_coupled_atoms_per_g": floor_co, "floor_trace_atoms_per_g": floor_tr,
                      "floor_ratio_coupled_over_trace": floor_co / floor_tr if floor_tr > 0 else None,
                      "significant_nuclides": sig_nuc, "worst_abs_difference_atoms_per_g": worst_abs,
                      "difference_within_coupled_floor": within_floor,
                      "pass": bool(a_auto["mode"] == "trace" and within_floor)},
       "high_fluence": {"flux_total": hi["spectrum"]["total"], "burnup": burn, "auto_mode": b_auto["mode"],
                        "trace_vs_coupled_max_rel": diff,
                        "expected_first_order": burn,
                        "difference_is_first_order_in_burnup": bool(np.isfinite(diff) and 0.05 * burn <= diff <= 20 * burn),
                        "pass": bool(b_auto["mode"] == "coupled" and burn >= 1e-6)},
       "threshold": 1e-6}
res["pass"] = bool(res["low_burnup"]["pass"] and res["high_fluence"]["pass"])
json.dump(res, open(os.path.join(RES, "g7_mode_selection.json"), "w"), indent=1); print(json.dumps(res, indent=1))
