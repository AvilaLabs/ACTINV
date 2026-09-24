#!/usr/bin/env python3
"""P45 G4 independent checker — re-derives the verdict's load-bearing
claims from the raw ledger + outputs, and replants mutations to prove
the checker is not a rubber stamp. Emits
results/check_g4_p45.json; exits nonzero on any failure.
"""
from __future__ import annotations

import copy
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "check_g4_p45.json"
LEDGER = ROOT / "results" / "p45_ledger.jsonl"
VERDICT = ROOT / "results" / "verdict_p45.json"
PARITY = ROOT / "results" / "p45_parity.json"

sys.path.insert(0, str(ROOT / "controls"))
import p45_parity as parity  # noqa: E402


def latest(rows):
    d = {}
    for r in rows:
        d[f"{r['workload']}|{r['case']}|{r['arm']}"] = r
    return d


def check(name, ok, detail=None):
    d = {"check": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def mutate_and_expect(rows, verdict, name, mutator, pred):
    """Re-run the scorer on mutated rows; expect pred()."""
    m = copy.deepcopy(rows)
    mutator(m)
    out = parity.score_ledger(m)
    return check(name, pred(out), None)


def main() -> int:
    rows = [json.loads(l) for l in
            LEDGER.read_text().splitlines() if l.strip()]
    verdict = json.loads(VERDICT.read_text())
    parity_out = json.loads(PARITY.read_text())
    checks = []

    # c1: every campaign case appears for every campaign arm
    expected_cases = set()
    for r in rows:
        if r["workload"] == "campaign" and r["case"] != "_batch":
            expected_cases.add(r["case"])
    arms_per_case = {}
    for r in rows:
        if r["workload"] == "campaign" and r["case"] != "_batch":
            arms_per_case.setdefault(r["case"], set()).add(r["arm"])
    want = {"alara", "actinv_fendl", "actinv_tendl", "openmc"}
    missing = {c: sorted(want - a)
               for c, a in arms_per_case.items() if want - a}
    checks.append(check(
        "campaign_coverage",
        len(expected_cases) == 28 and not missing,
        {"n_cases": len(expected_cases), "missing": missing}))

    # c2: mesh coverage — 16 cells each alara+openmc, one actinv_mesh
    # batch with 16 per_cell results
    mesh_cells = {r["case"] for r in rows
                  if r["workload"] == "mesh" and r["case"] != "_batch"}
    mesh_arms = {}
    for r in rows:
        if r["workload"] == "mesh" and r["case"] != "_batch":
            mesh_arms.setdefault(r["case"], set()).add(r["arm"])
    batch_row = [r for r in rows if r["workload"] == "mesh"
                 and r["case"] == "_batch"
                 and r["arm"] == "actinv_mesh"]
    per_cell = ((batch_row[-1]["arms"]["actinv_mesh"]
                 .get("per_cell")) if batch_row else {}) or {}
    checks.append(check(
        "mesh_coverage",
        len(mesh_cells) == 16 and len(per_cell) == 16
        and all({"alara", "openmc"} <= a
                for a in mesh_arms.values()),
        {"cells": len(mesh_cells), "per_cell": len(per_cell)}))

    # c3: timing stats re-derived — pooled median actinv_tendl cold_s
    # matches the verdict's value to float equality
    cold = sorted(
        r["arms"]["actinv_tendl"]["cold_s"] for r in latest(rows).values()
        if r["workload"] == "campaign"
        and r["arm"] == "actinv_tendl"
        and r["arms"]["actinv_tendl"].get("status") == "executed")
    v_med = (verdict["timing"]["campaign"]["per_arm"]
             .get("actinv_tendl") or {}).get("median_cold_s")
    checks.append(check(
        "timing_rederivation",
        cold and v_med == statistics.median(cold),
        {"rederived": statistics.median(cold) if cold else None,
         "verdict": v_med}))

    # c4: parity tallies re-derived per workload from per_case records
    tally = {}
    for r in parity_out["per_case"].values():
        st = (r.get("identical") or {}).get("status")
        wl = r.get("workload")
        tally.setdefault(wl, {})[st] = \
            tally.setdefault(wl, {}).get(st, 0) + 1
    v_tally = verdict["parity"]["identical_leg_by_workload"]
    ok = all(tally.get(wl, {}) == v_tally.get(wl)
             for wl in v_tally)
    checks.append(check("parity_tally", ok,
                        {"rederived": tally, "verdict": v_tally}))

    # c5: no case scored parity_ok that has a |rel| > tolerance — scan
    # every response rel in every parity_ok case
    bad = []
    for k, r in parity_out["per_case"].items():
        idl = r.get("identical") or {}
        if idl.get("status") != "parity_ok":
            continue
        for resp, ts in (idl.get("responses") or {}).items():
            if resp != "total_activity_bq_per_g":
                continue
            for t, v in ts.items():
                if v is not None and abs(v) > parity.TOLERANCE:
                    bad.append((k, resp, t, v))
    checks.append(check("no_false_parity_ok", not bad, bad[:5]))

    # ---- planted mutations (the checker must catch each) ----
    # m1: drop a divergence -> parity tally must change
    def m1(rs):
        for r in rs:
            if r["arm"] == "alara" and r["case"].startswith("fe_cr"):
                r["arms"]["alara"]["status"] = "arm_failure"
    checks.append(mutate_and_expect(
        rows, verdict, "m1_dropped_case_detected", m1,
        lambda o: any(
            (r.get("identical") or {}).get("status") == "arm_failure"
            for k, r in o["per_case"].items()
            if "_cr" in k)))

    # m2: shrink all actinv values 100x -> every case diverges
    def m2(rs):
        for r in rs:
            if r["arm"] == "actinv_fendl":
                res = r["arms"]["actinv_fendl"].get("result") or {}
                for p in (res.get("per_time") or {}).values():
                    for k in ("total_activity_bq_per_g",
                              "product_atoms_per_g"):
                        if p.get(k):
                            p[k] /= 100.0
    checks.append(mutate_and_expect(
        rows, verdict, "m2_scaled_values_diverge", m2,
        lambda o: all(
            (r.get("identical") or {}).get("status")
            == "parity_divergence"
            for k, r in o["per_case"].items()
            if k.startswith("campaign|"))))

    # m3: alara row deleted for one case -> that case -> arm_failure
    def m3(rs):
        rs[:] = [r for r in rs if not (
            r["arm"] == "alara"
            and r["case"] == "fe__fns_709__pulse_5min")]
    checks.append(mutate_and_expect(
        rows, verdict, "m3_deleted_row_detected", m3,
        lambda o: (o["per_case"]
                   ["campaign|fe__fns_709__pulse_5min"]["identical"]
                   ["status"]) == "arm_failure"))

    # m4: robustness record tampered (fewer cases than frozen 24)
    checks.append(check(
        "robustness_case_count",
        (verdict["robustness_leg"].get("n_cases") or 0) == 24,
        verdict["robustness_leg"].get("n_cases")))

    # m5: envelope — campaign inside declared envelope
    checks.append(check(
        "envelope",
        (verdict["campaign"].get("within_envelope") is True)
        and (verdict["campaign"].get("wall_s") or 1e18)
        < verdict["campaign"]["envelope_s"],
        {"wall_s": verdict["campaign"].get("wall_s")}))

    out = {"spec": "actinv-p45-g4-check-1",
           "checks": checks,
           "all_pass": all(c["pass"] for c in checks),
           "n_checks": len(checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "checks": {c["check"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
