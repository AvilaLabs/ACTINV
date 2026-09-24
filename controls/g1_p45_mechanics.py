#!/usr/bin/env python3
"""P45 G1 mechanics gate: representative end-to-end feasibility under
the sealed code — every arm, both workloads — before the campaign is
trusted to produce numbers.

Runs the sealed harness on a small subset (2 campaign cases, 2 mesh
cells, repeat=3 so rows carry warm statistics) plus a reduced
robustness smoke (1 case, 2 samples), then verifies each arm's ledger
outcome. Emits results/g1_p45_mechanics.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g1_p45_mechanics.json"
LEDGER = ROOT / "results" / "p45_ledger.jsonl"

sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402
import p45_campaign as camp  # noqa: E402
import p45_robustness  # noqa: E402

G1_CASES = "fe__fns_709__pulse_5min,fe_co1000wppm__fns_709__60s"
G1_CELLS = "cell_00,cell_01"


def run_harness(workload: str, arms: str, cases: str) -> dict:
    t0 = time.monotonic()
    r = subprocess.run(
        [sys.executable, str(ROOT / "controls" / "p45_campaign.py"),
         "--workload", workload, "--arms", arms,
         "--repeat", "3", "--cases", cases, "--resume"],
        capture_output=True, text=True, timeout=3600)
    return {"wall_s": time.monotonic() - t0,
            "returncode": r.returncode,
            "stdout_tail": r.stdout[-300:],
            "stderr_tail": r.stderr[-300:]}


def robustness_smoke() -> dict:
    """1 material, 1 schedule, 2 samples — qualifies the study driver
    end-to-end without running the frozen 24×16 production leg."""
    pop = json.loads(camp.POPULATION.read_text())
    study = p45_robustness.build_study(pop)
    study["cases"]["materials"] = study["cases"]["materials"][:1]
    study["cases"]["schedules"] = study["cases"]["schedules"][:1]
    study["robustness"]["samples"] = 2
    wdir = Path.home() / "nuclear-data" / "p45-work" / "g1_rob"
    wdir.mkdir(parents=True, exist_ok=True)
    spath = wdir / "study.json"
    spath.write_text(json.dumps(study))
    outdir = wdir / "study_out"
    t0 = time.monotonic()
    r = leg.timed_run(
        [str(leg.ACTINV), "study", "run", str(spath),
         str(outdir)], wdir, timeout=1800)
    rec = {"wall_s": time.monotonic() - t0,
           "returncode": r["returncode"]}
    if r["returncode"] == 0:
        o = json.loads((outdir / "study_record.json").read_text())
        cs = o.get("cases") or []
        rec["n_cases"] = len(cs)
        rec["samples_done"] = []
        rec["failed"] = 0
        for c in cs:
            rb = c.get("robustness") or {}
            rec["samples_done"].append(rb.get("samples"))
            rec["failed"] += rb.get("n_failed_samples") or 0
    else:
        rec["failure"] = (r.get("stderr_tail") or "")[-300:]
    return rec


def ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines()
            if l.strip()]


def main() -> int:
    t0 = time.monotonic()
    checks = []
    rows0 = ledger_rows()

    # campaign subset — all four arms
    h1 = run_harness("campaign",
                     "alara,actinv_fendl,actinv_tendl,openmc",
                     G1_CASES)
    checks.append({"probe": "campaign_harness",
                   "pass": h1["returncode"] == 0, **h1})

    # mesh subset — three mesh arms
    h2 = run_harness("mesh", "actinv_mesh,alara,openmc", G1_CELLS)
    checks.append({"probe": "mesh_harness",
                   "pass": h2["returncode"] == 0, **h2})

    # robustness smoke
    rb = robustness_smoke()
    checks.append({"probe": "robustness_smoke",
                   "pass": rb["returncode"] == 0
                   and rb.get("failed", 1) == 0, **rb})

    # ledger verification: latest row per (workload, case, arm) covering
    # the G1 subset — resume may legitimately produce zero new rows
    g1_cases = set(G1_CASES.split(",")) | set(G1_CELLS.split(","))
    latest = {}
    for r in ledger_rows():
        if r["case"] in g1_cases or r["case"] == "_batch":
            latest[f"{r['workload']}|{r['case']}|{r['arm']}"] = r
    by_arm = {}
    for k, r in latest.items():
        a = r["arms"][r["arm"]]
        st = a.get("status") or (
            "executed" if a.get("returncode") == 0
            else "arm_failure")
        by_arm.setdefault(r["arm"], []).append(st)
    for arm in ("alara", "actinv_fendl", "actinv_tendl", "openmc",
                "actinv_mesh"):
        sts = by_arm.get(arm, [])
        checks.append({
            "probe": f"arm_{arm}",
            "pass": bool(sts) and all(s == "executed" for s in sts),
            "statuses": sts})

    # parity smoke on the identical-data leg (fe only)
    from p45_parity import score_ledger
    pc = score_ledger(ledger_rows())
    fe = pc["per_case"].get("campaign|fe__fns_709__pulse_5min", {})
    checks.append({
        "probe": "parity_scorer_runs",
        "pass": bool(fe.get("identical")),
        "identical_status": (fe.get("identical") or {}).get("status")})

    out = {"spec": "actinv-p45-g1-mechanics-1",
           "wall_s": time.monotonic() - t0,
           "checks": checks,
           "all_pass": all(c["pass"] for c in checks),
           "ledger_rows_checked": len(latest)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "probes": {c["probe"]: c["pass"] for c in checks},
                      "wall_s": round(out["wall_s"], 1)}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
