#!/usr/bin/env python3
"""ACTINV P2 verdict deriver: G1 library controls, G2 CLI controls, G3 harness controls, G4 instrument gate
(every experiment ran, gaps ledgered, every C/E reproduced from stored inventories to 1e-12), G5 conditional.
Also computes the diagnostic trigger (median max|ln C/E| ACTINV vs FISPACT)."""
import os, sys, json, glob, math, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
v = {"gates": {}}
g1 = load("g1_library.json")
v["gates"]["G1"] = "UNSCORED" if g1 is None else ("PASS" if g1["pass"] else "FAIL") + f" ({g1.get('note','')})"
g2 = load("g2_cli.json")
if g2 is None: v["gates"]["G2"] = "UNSCORED"
else:
    ok = all(c["pass"] for c in g2["controls"].values()); v["gates"]["G2"] = ("PASS" if ok else "FAIL (" + ",".join(k for k, c in g2["controls"].items() if not c["pass"]) + ")") + f"; pruned {g2['timing']['prune1']['ms_total']:.3f} ms vs unpruned {g2['timing']['prune0']['ms_total']:.1f} ms"
g3 = load("g3_harness.json")
v["gates"]["G3"] = "UNSCORED" if g3 is None else ("PASS" if g3["pass"] else "FAIL")
# ---- G4 instrument gate
files = sorted(glob.glob(os.path.join(RES, "fns", "*.json")))
if not files: v["gates"]["G4"] = "UNSCORED"
else:
    from harness import decayheat as dh
    n_err = 0; worst = 0.0; n_ok = 0; ln_a = []; ln_f = []; disp = {"AGREE_MEAS": 0, "AGREE_REF": 0, "DISAGREE": 0}
    for f in files:
        r = json.load(open(f))
        if r.get("error"): n_err += 1; continue
        if not r.get("CE_actinv"): n_nodata = globals().get("n_nodata", 0) + 1; globals()["n_nodata"] = n_nodata; disp["NO_DATA"] = disp.get("NO_DATA", 0) + 1; continue
        # reproduce C/E from stored inventories
        E = np.array(r["measured"]["heat_uW_g"]); k = len(E); rep = []
        steps = r["measured"].get("steps", list(range(k)))   # Amendment C: matched schedule steps
        for inv in [r["inventories"][kk] for kk in steps]:
            d = {(x["Z"] * 1000 + x["A"], x["LISO"]): x["atoms_per_g"] for x in inv["nuclides"]}
            tot, per, miss = dh.heat_W_per_g(d); rep.append(tot * 1e6 + r.get("bulk_background_heat_uW_g", 0.0))
        ce = np.array(rep) / E; stored = np.array(r["CE_actinv"]); worst = max(worst, float(np.max(np.abs(ce - stored) / stored))); n_ok += 1
        ln_a.append(r["summary"]["actinv"]["max_abs_lnCE"])
        if "fispact" in r["summary"]: ln_f.append(r["summary"]["fispact"]["max_abs_lnCE"])
        dd = r.get("disposition", {})
        if dd.get("AGREE_MEAS"): disp["AGREE_MEAS"] += 1
        elif dd.get("AGREE_REF"): disp["AGREE_REF"] += 1
        else: disp["DISAGREE"] += 1
    med_a = float(np.median(ln_a)) if ln_a else None; med_f = float(np.median(ln_f)) if ln_f else None
    suspect = (med_a is not None and med_f is not None and med_a - med_f > math.log(2))
    gate = n_err == 0 and worst <= 1e-12
    v["gates"]["G4"] = ("PASS" if gate else "FAIL") + f" (ran {n_ok}, errors {n_err}, C/E reproduction worst {worst:.1e}); median max|lnCE| ACTINV {med_a} vs FISPACT {med_f}; " + ("INSTRUMENT-SUSPECT" if suspect else "no trigger") + f"; dispositions {disp}"
    v["g4"] = {"n_ok": n_ok, "n_err": n_err, "worst_reproduction": worst, "median_maxlnCE_actinv": med_a, "median_maxlnCE_fispact": med_f, "instrument_suspect": suspect, "dispositions": disp}
g5 = load("g5_pyo3.json"); v["gates"]["G5"] = "DEFERRED" if g5 is None else ("PASS" if g5.get("pass") else ("DEFERRED" if g5.get("deferred") else "FAIL"))
gs = v["gates"]
if any(gs[k] == "UNSCORED" for k in ("G1", "G2", "G3", "G4")): verdict = "UNSCORED"
elif any(gs[k].startswith("FAIL") for k in ("G1", "G2", "G3", "G4")) or gs["G5"] == "FAIL": verdict = "P2-FAIL"
else: verdict = "P2-CONDITIONAL" if os.path.exists(os.path.join(RES, "..", "protocols", "ACTINV-P2_AMENDMENT_B.md")) else "P2-PASS"
v["verdict"] = verdict; json.dump(v, open(os.path.join(RES, "verdict_p2.json"), "w"), indent=1); print(json.dumps(v, indent=1))
sys.exit(0 if verdict.startswith("P2-PASS") or verdict.startswith("P2-COND") else (2 if verdict == "P2-FAIL" else 3))
