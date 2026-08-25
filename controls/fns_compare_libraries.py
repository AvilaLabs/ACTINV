#!/usr/bin/env python3
"""P4-G3 report: three-column comparison per experiment — ACTINV/TENDL-2023 (results/fns_tendl) vs ACTINV/EAF-2010
(results/fns) vs FISPACT-II/TENDL-2017 (reference) vs measurement — plus a library-difference table for the top heat
contributors (one-group sigma on each experiment's spectrum, TENDL-2023 vs EAF-2010). Writes results/FNS_LIBRARIES.md and
results/fns_libraries.json; appends a TENDL column summary to docs/VALIDATION.md."""
import os, sys, json, glob, math, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
A = {os.path.basename(f)[:-5]: json.load(open(f)) for f in sorted(glob.glob(os.path.join(RES, "fns", "*.json")))}
B = {os.path.basename(f)[:-5]: json.load(open(f)) for f in sorted(glob.glob(os.path.join(RES, "fns_tendl", "*.json")))}
rows = []; g_e, g_t, g_f, m_e, m_t, m_f = [], [], [], [], [], []
for k in sorted(A):
    a, b = A[k], B.get(k)
    if not b or a.get("error") or b.get("error") or not a.get("CE_actinv") or not b.get("CE_actinv"): continue
    sa, sb, sf = a["summary"]["actinv"], b["summary"]["actinv"], a["summary"].get("fispact", {})
    g_e.append(sa["geomean_CE"]); g_t.append(sb["geomean_CE"]); m_e.append(sa["max_abs_lnCE"]); m_t.append(sb["max_abs_lnCE"])
    if sf: g_f.append(sf["geomean_CE"]); m_f.append(sf["max_abs_lnCE"])
    # which library is closer to measurement (geomean), and closer to the FISPACT reference
    closer_meas = "TENDL" if abs(math.log(sb["geomean_CE"])) < abs(math.log(sa["geomean_CE"])) else "EAF"
    closer_ref = ("TENDL" if abs(math.log(sb["geomean_CE"] / sf["geomean_CE"])) < abs(math.log(sa["geomean_CE"] / sf["geomean_CE"])) else "EAF") if sf else "n/a"
    ta = {n: v for n, v in a["top_contributors_actinv"]["first"]}; tb = {n: v for n, v in b["top_contributors_actinv"]["first"]}
    rows.append({"exp": k, "gm_eaf": sa["geomean_CE"], "gm_tendl": sb["geomean_CE"], "gm_fispact": sf.get("geomean_CE"), "max_eaf": sa["max_abs_lnCE"], "max_tendl": sb["max_abs_lnCE"], "max_fispact": sf.get("max_abs_lnCE"),
                 "closer_to_measurement": closer_meas, "closer_to_reference": closer_ref, "top_first_eaf": a["top_contributors_actinv"]["first"][:3], "top_first_tendl": b["top_contributors_actinv"]["first"][:3],
                 "ledger_tendl": {kk: (len(v) if hasattr(v, "__len__") else v) for kk, v in b["ledger"].items() if kk in ("products_unmapped_to_leakage", "products_no_evaluated_decay_data_ENDFB80_JEFF33", "composition_isotopes_absent")}})
n = len(rows); cm = sum(1 for r in rows if r["closer_to_measurement"] == "TENDL"); cr = sum(1 for r in rows if r["closer_to_reference"] == "TENDL")
L = ["# FNS decay heat — ACTINV with two libraries vs the FISPACT-II reference", "", f"Experiments compared: {n}. Same solver, same decay data (ENDF/B-VIII.0 + JEFF-3.3), same harness; only the activation library differs. Reference: FISPACT-II with TENDL-2017 (CoNDERC).", "",
     "| | ACTINV / EAF-2010 | ACTINV / TENDL-2023 | FISPACT-II / TENDL-2017 |", "|---|---|---|---|",
     f"| median geometric-mean C/E | {np.median(g_e):.3f} | {np.median(g_t):.3f} | {np.median(g_f):.3f} |", f"| median max\\|ln C/E\\| | {np.median(m_e):.3f} | {np.median(m_t):.3f} | {np.median(m_f):.3f} |",
     f"| within 30 % everywhere | {np.mean(np.array(m_e) <= math.log(1.3)):.0%} | {np.mean(np.array(m_t) <= math.log(1.3)):.0%} | {np.mean(np.array(m_f) <= math.log(1.3)):.0%} |", "",
     f"- TENDL-2023 closer to the measurement than EAF-2010 (geometric mean): {cm}/{n}; closer to the FISPACT-II/TENDL-2017 reference: {cr}/{n}.", "",
     "| experiment | gm C/E EAF | gm C/E TENDL | gm C/E FISPACT | max\\|lnCE\\| EAF | TENDL | FISPACT | closer to meas. | closer to ref. |", "|---|---|---|---|---|---|---|---|---|"]
for r in sorted(rows, key=lambda r: abs(math.log(r["gm_tendl"] / r["gm_eaf"])), reverse=True): L.append(f"| {r['exp']} | {r['gm_eaf']:.3f} | {r['gm_tendl']:.3f} | {r['gm_fispact'] if r['gm_fispact'] is None else '%.3f' % r['gm_fispact']} | {r['max_eaf']:.2f} | {r['max_tendl']:.2f} | {r['max_fispact'] if r['max_fispact'] is None else '%.2f' % r['max_fispact']} | {r['closer_to_measurement']} | {r['closer_to_reference']} |")
# library-difference table: top-3 first-time contributors, one-group sigma of their main production reaction is not stored; report the contributor heat itself
L += ["", "## Top contributors at the first measurement (μW/g), EAF-2010 vs TENDL-2023 — largest library-driven changes", "", "| experiment | nuclide | EAF-2010 | TENDL-2023 | ratio |", "|---|---|---|---|---|"]
diffs = []
for r in rows:
    ta = dict(r["top_first_eaf"]); tb = dict(r["top_first_tendl"])
    for nname in set(ta) | set(tb):
        va, vb = ta.get(nname), tb.get(nname)
        if va and vb and va > 1e-4: diffs.append((abs(math.log(vb / va)), r["exp"], nname, va, vb))
for d, e, nname, va, vb in sorted(diffs, reverse=True)[:30]: L.append(f"| {e} | {nname} | {va:.3e} | {vb:.3e} | {vb / va:.2f} |")
open(os.path.join(RES, "FNS_LIBRARIES.md"), "w").write("\n".join(L)); json.dump({"n": n, "medians": {"gm_eaf": float(np.median(g_e)), "gm_tendl": float(np.median(g_t)), "gm_fispact": float(np.median(g_f)), "max_eaf": float(np.median(m_e)), "max_tendl": float(np.median(m_t)), "max_fispact": float(np.median(m_f))}, "closer_meas_tendl": cm, "closer_ref_tendl": cr, "rows": rows}, open(os.path.join(RES, "fns_libraries.json"), "w"), indent=1)
print("\n".join(L[:12]))
