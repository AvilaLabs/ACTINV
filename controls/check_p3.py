#!/usr/bin/env python3
"""ACTINV P3 verdict deriver: G1 (decay fallback parser control + FNS (b) count), G2 (resonance/Doppler controls), G3 (pruning),
G4 (certificate re-derivation: every C/E recomputed from stored inventories, every hash re-matched), G5 recorded."""
import os, sys, json, glob, hashlib, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
v = {"gates": {}}
g1 = load("g1_decay_fallback.json"); g3 = load("g3_pruning.json")
v["gates"]["G1"] = "UNSCORED" if g1 is None else (("PASS" if g1["pass"] else "FAIL") + f" (parser {g1['n_mismatch']}/200 mismatches; {g1['merge']['n_added_from_fallback']} nuclides from JEFF-3.3; FNS residual products without evaluated decay data per experiment: {g3['g1b_products_no_decay_record_per_experiment'] if g3 else 'n/a'})")
g2 = load("g2_resonance.json")
v["gates"]["G2"] = "UNSCORED" if g2 is None else (("PASS" if g2["pass"] else "FAIL") + f" (a worst {g2['control_a'].get('worst')}; b {g2['control_b'].get('pass')}; c1 {g2.get('control_c1', {}).get('pass')}; c2 {g2.get('control_c2', {}).get('pass')})")
v["gates"]["G3"] = "UNSCORED" if g3 is None else (("PASS" if g3["pass"] else "FAIL") + f" (worst rel {g3['worst_rel']:.2e}; bound fraction {g3.get('worst_dropped_bound_fraction_of_heat')}; states {g3['median_states']}; ms {g3['median_ms']})")
# ---- G4: certificate re-derivation
cert = load("fns_certificate.json")
if cert is None: v["gates"]["G4"] = "UNSCORED"
else:
    from harness import decayheat as dh
    def sha(p): h = hashlib.sha256(); h.update(open(p, "rb").read()); return h.hexdigest()
    bad_inputs = [k for k, d in cert["inputs"].items() if d["sha256"] is not None and (not os.path.exists(d["path"]) or sha(d["path"]) != d["sha256"])]
    worst = 0.0; bad_exp = []
    for k, d in cert["experiments"].items():
        f = os.path.join(RES, "fns", k + ".json"); r = json.load(open(f))
        if sha(f) != d["record_sha256"]: bad_exp.append(k + ":record"); continue
        if hashlib.sha256(json.dumps(r.get("inventories", []), sort_keys=True).encode()).hexdigest() != d["inventories_sha256"]: bad_exp.append(k + ":inventories")
        if not r.get("CE_actinv"): continue
        E = np.array(r["measured"]["heat_uW_g"]); steps = r["measured"]["steps"]; rep = []
        for inv in [r["inventories"][kk] for kk in steps]:
            dd = {(x["Z"] * 1000 + x["A"], x["LISO"]): x["atoms_per_g"] for x in inv["nuclides"]}; tot, per, miss = dh.heat_W_per_g(dd); rep.append(tot * 1e6 + r.get("bulk_background_heat_uW_g", 0.0))
        ce = np.array(rep) / E; st = np.array(r["CE_actinv"]); worst = max(worst, float(np.max(np.abs(ce - st) / st)))
    v["gates"]["G4"] = ("PASS" if not bad_inputs and not bad_exp and worst <= 1e-12 else "FAIL") + f" (inputs mismatched {bad_inputs}; experiments mismatched {bad_exp[:5]}; C/E re-derivation worst {worst:.1e}; {len(cert['experiments'])} experiments)"
v["gates"]["G5"] = "recorded" if all(os.path.exists(os.path.join(ROOT, p)) for p in ("README.md", "docs/METHOD.md", "docs/DATA.md", "docs/HARNESS.md", "docs/LEDGER.md", "CONTRIBUTING.md", "docs/VALIDATION.md")) else "incomplete"
gs = v["gates"]; amended = [os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "protocols", "ACTINV-P3_AMENDMENT_*.md"))]
if any(gs[k] == "UNSCORED" for k in ("G1", "G2", "G3", "G4")): verdict = "UNSCORED"
elif any(gs[k].startswith("FAIL") for k in ("G1", "G2", "G3", "G4")): verdict = "P3-FAIL"
else: verdict = "P3-CONDITIONAL" if amended else "P3-PASS"
v["verdict"] = verdict; v["amendments"] = amended; json.dump(v, open(os.path.join(RES, "verdict_p3.json"), "w"), indent=1); print(json.dumps(v, indent=1))
sys.exit(0 if verdict.startswith("P3-PASS") or verdict.startswith("P3-COND") else (2 if verdict == "P3-FAIL" else 3))
