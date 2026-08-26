#!/usr/bin/env python3
"""ACTINV P4b verdict: C1 (non-inelastic vs pointwise collapse; inelastic loss = sum of isomer partials) and C2
(subset vs full within the physical threshold), plus no regression in P4's other gates. G2c is out of scope."""
import os, sys, json
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
conv = load("g2_tendl_dense1.json"); sv = load("g5_subset_vs_full.json"); p4 = load("verdict_p4.json")
v = {"controls": {}, "no_regression": {}}
if conv is None or sv is None or p4 is None: v["verdict"] = "UNSCORED"
else:
    cb, ci = conv["control_b"], conv["control_b_inelastic"]
    v["controls"]["C1_non_inelastic"] = {"pass": cb["pass"], "n": cb["n_reactions"], "max_rel": cb["max_rel"]}
    v["controls"]["C1_inelastic_internal"] = {"pass": ci["pass"], "n": ci["n_reactions"], "max_rel": ci["max_rel"]}
    v["controls"]["C2_subset_vs_full"] = {"pass": bool(sv["worst_rel"] <= 1e-4 and not sv["missing"]), "n": sv["n_compared"], "worst_rel": sv["worst_rel"], "threshold": 1e-4}
    for g in ("G1", "G2a", "G3", "G4"): v["no_regression"][g] = p4["gates"].get(g, "?").split(" ")[0]
    ok = all(c["pass"] for c in v["controls"].values()) and all(x == "PASS" for x in v["no_regression"].values())
    v["G2c_out_of_scope"] = p4["gates"].get("G2c", "?")[:80]
    v["verdict"] = "P4b-PASS" if ok else "P4b-FAIL"
json.dump(v, open(os.path.join(RES, "verdict_p4b.json"), "w"), indent=1); print(json.dumps(v, indent=1))
sys.exit(0 if v["verdict"] == "P4b-PASS" else 2)
