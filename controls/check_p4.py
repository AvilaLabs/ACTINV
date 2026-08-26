#!/usr/bin/env python3
"""ACTINV P4 verdict: G1 build (zero silent skips), G2 (a) twins, (b) non-resonant consistency, (c) grid convergence, G3 FNS
reproduction (fns_tendl records), G4 certificate re-derivation."""
import os, sys, json, glob, hashlib, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results"); LIB = os.path.expanduser("~/nuclear-data/tendl-2023")
def load(p): return json.load(open(p)) if os.path.exists(p) else None
v = {"gates": {}}
LIBNAME = os.environ.get("ACTINV_LIBRARY", os.path.join(LIB, "actinv_tendl2023_709g.npz"))
idx = load(LIBNAME.replace(".npz", "_index.json"))
if idx is None: v["gates"]["G1"] = "UNSCORED"
else:
    n = len(idx["targets"]); nerr = idx["n_errors"]; led = sum(1 for t in idx["targets"] if t.get("ledger")); urr = sum(1 for t in idx["targets"] if any("INCOMPLETE-URR" in x for x in t.get("ledger", []))); unsup = sum(1 for t in idx["targets"] if any("unsupported" in x for x in t.get("ledger", [])))
    v["gates"]["G1"] = ("PASS" if nerr == 0 and n >= int(os.environ.get("ACTINV_MIN_TARGETS", "2847")) else "FAIL") + f" (targets {n}, errors {nerr}, targets with ledger entries {led}, INCOMPLETE-URR {urr}, unsupported formalism/range {unsup}, rows {idx['n_rows']}, build {idx['build_seconds']/3600:.1f} h)"
tw = load(os.path.join(RES, "g2_fendl_twins.json")); dn = sorted(glob.glob(os.path.join(RES, "g2_tendl_dense*.json")))
conv = load(dn[-1]) if dn else None
v["gates"]["G2a"] = "UNSCORED" if tw is None else (("PASS" if tw["pass"] else "FAIL") + f" (twins {tw['n_twins']}, compared {tw['n_compared']}, worst one-group {tw['worst_one_group']}, worst per-group {tw['worst_per_group']})")
v["gates"]["G2b"] = "UNSCORED" if conv is None else (("PASS" if conv["control_b"]["pass"] else "FAIL") + f" ({conv['control_b']['n_reactions']} reactions, max {conv['control_b']['max_rel']:.1e})")
if conv is None: v["gates"]["G2c"] = "UNSCORED"
else:
    cc = conv["control_c"]; frac = 1 - len(cc["rows_over"]) / max(1, cc["n_capture_rows"]); flagged = {r["target"] for r in cc["rows_over"]}
    present = {t["file"] for t in (idx or {"targets": []})["targets"]}   # a flagged target absent from the library needs no flag
    in_index = all(any(t["file"] == f and t.get("convergence_flag") for t in idx["targets"]) for f in flagged if f in present)
    ok = frac >= 0.95 and cc["max_rel"] <= 2e-2 and in_index   # P4 Amendment A
    v["gates"]["G2c"] = ("PASS" if ok else "FAIL") + f" (Amendment A: {frac:.1%} of {cc['n_capture_rows']} rows ≤ 1e-3, max {cc['max_rel']:.1e}, flagged targets {sorted(flagged)} in index: {in_index})"
files = sorted(glob.glob(os.path.join(RES, "fns_tendl", "*.json")))
if not files: v["gates"]["G3"] = "UNSCORED"
else:
    from harness import decayheat as dh
    worst = 0.0; nerr = 0; n_ok = 0; gm = []
    for f in files:
        r = json.load(open(f))
        if r.get("error"): nerr += 1; continue
        if not r.get("CE_actinv"): continue
        E = np.array(r["measured"]["heat_uW_g"]); rep = []
        for inv in [r["inventories"][k] for k in r["measured"]["steps"]]:
            d = {(x["Z"] * 1000 + x["A"], x["LISO"]): x["atoms_per_g"] for x in inv["nuclides"]}; tot, per, miss = dh.heat_W_per_g(d); rep.append(tot * 1e6 + r.get("bulk_background_heat_uW_g", 0.0))
        ce = np.array(rep) / E; st = np.array(r["CE_actinv"]); worst = max(worst, float(np.max(np.abs(ce - st) / st))); n_ok += 1; gm.append(r["summary"]["actinv"]["geomean_CE"])
    v["gates"]["G3"] = ("PASS" if nerr == 0 and worst <= 1e-12 and n_ok == 132 else "FAIL") + f" (ran {n_ok}, errors {nerr}, reproduction worst {worst:.1e}, median gm C/E {np.median(gm):.3f})"
cert = load(os.path.join(RES, os.environ.get("ACTINV_CERT", "fns_tendl_certificate.json")))
if cert is None: v["gates"]["G4"] = "UNSCORED"
else:
    def sha(p): h = hashlib.sha256(); h.update(open(p, "rb").read()); return h.hexdigest()
    bad = [k for k, d in cert["inputs"].items() if d["sha256"] and (not os.path.exists(d["path"]) or sha(d["path"]) != d["sha256"])]
    v["gates"]["G4"] = ("PASS" if not bad else "FAIL") + f" (inputs {len(cert['inputs'])}, mismatched {bad[:3]})"
gs = v["gates"]; amended = [os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "protocols", "ACTINV-P4_AMENDMENT_*.md"))]
if any(gs[k] == "UNSCORED" for k in gs): verdict = "UNSCORED"
elif any(gs[k].startswith("FAIL") for k in gs): verdict = "P4-FAIL"
else: verdict = "P4-CONDITIONAL" if amended else "P4-PASS"
v["verdict"] = verdict; v["amendments"] = amended; json.dump(v, open(os.path.join(RES, "verdict_p4.json"), "w"), indent=1); print(json.dumps(v, indent=1))
sys.exit(0 if verdict.startswith("P4-PASS") or verdict.startswith("P4-COND") else (2 if verdict == "P4-FAIL" else 3))
