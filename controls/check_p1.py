#!/usr/bin/env python3
"""ACTINV P1 verdict deriver (protocol §2). Exit 0 PASS/CONDITIONAL, 2 FAIL, 3 UNSCORED."""
import json, os, sys
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
v = {"gates": {}}
g1 = load("g1_collapse.json")
if g1 is None: v["gates"]["G1"] = "UNSCORED"
else:
    tests = [t for t in g1["tests"] if "max_rel_diff" in t]; worst = max((t["max_rel_diff"] for t in tests), default=None)
    missing = [t for t in g1["tests"] if "max_rel_diff" not in t]
    v["gates"]["G1"] = ("PASS" if worst is not None and worst <= 1e-6 and not missing else "FAIL") + f" (worst rel {worst}, {len(tests)} compared, {len(missing)} without control)"
g2 = load("g2_solver.json")
if g2 is None: v["gates"]["G2"] = "UNSCORED"
else:
    c = g2["controls"]; ok = all(c[k]["pass"] for k in c); ms = g2["timing"].get("rust_ms_per_cram16_step")
    timing = "PASS" if ms is not None and ms <= 10 else ("MARGINAL" if ms is not None and ms <= 100 else "FAIL")
    v["gates"]["G2"] = ("controls PASS" if ok else "controls FAIL (" + ",".join(k for k in c if not c[k]["pass"]) + ")") + f", timing {timing} ({ms} ms)"; v["g2_ok"] = ok; v["g2_timing"] = timing
g3 = load("g3_ledger.json"); v["gates"]["G3"] = "UNSCORED" if g3 is None else ("PASS" if g3["pass"] else "FAIL")
v["gates"]["G4"] = "recorded (Cargo workspace builds; local git)"
gs = v["gates"]
if any(gs[k] == "UNSCORED" for k in ("G1", "G2", "G3")): verdict = "UNSCORED"
elif gs["G1"].startswith("FAIL") or gs["G3"] == "FAIL" or not v.get("g2_ok") or v.get("g2_timing") == "FAIL": verdict = "P1-FAIL"
elif v.get("g2_timing") == "MARGINAL": verdict = "P1-CONDITIONAL"
else: verdict = "P1-PASS"
v["verdict"] = verdict; json.dump(v, open(os.path.join(RES, "verdict.json"), "w"), indent=1); print(json.dumps(v, indent=1))
sys.exit(0 if verdict.startswith("P1-PASS") or verdict.startswith("P1-COND") else (2 if verdict == "P1-FAIL" else 3))
