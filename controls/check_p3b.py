#!/usr/bin/env python3
"""ACTINV P3b verdict: G2 controls (a),(b),(c1),(c2) from results/g2_resonance_p3b.json."""
import os, sys, json
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"); g = json.load(open(os.path.join(RES, "g2_resonance_p3b.json")))
gates = {"a": g["control_a"]["pass"], "b": g["control_b"]["pass"], "c1": g["control_c1"]["pass"], "c2": g["control_c2"]["pass"]}
v = {"gates": gates, "a_worst": g["control_a"]["worst"], "verdict": "P3b-PASS" if all(gates.values()) else "P3b-FAIL"}
json.dump(v, open(os.path.join(RES, "verdict_p3b.json"), "w"), indent=1); print(json.dumps(v)); sys.exit(0 if all(gates.values()) else 2)
