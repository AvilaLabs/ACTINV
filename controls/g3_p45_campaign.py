#!/usr/bin/env python3
"""P45 G3 sealed campaign — executes the frozen workload under the
envelope, then the frozen robustness leg, then the parity scorer.

Dev-ledger rows from feasibility are archived aside first: the sealed
campaign is a fresh measurement under sealed code.
Emits results/g3_p45_campaign.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g3_p45_campaign.json"
LEDGER = ROOT / "results" / "p45_ledger.jsonl"
ENVELOPE_S = 240 * 60


def sh(cmd, timeout):
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout)
    return {"wall_s": time.monotonic() - t0,
            "returncode": r.returncode,
            "stdout_tail": r.stdout[-500:],
            "stderr_tail": r.stderr[-500:]}


def main() -> int:
    t_all = time.monotonic()
    if LEDGER.exists():
        LEDGER.rename(ROOT / "results" / "p45_ledger_dev.jsonl")

    steps = {}
    # 1. full campaign+mesh, all arms, repeat=3, resumable
    steps["campaign"] = sh(
        [sys.executable, str(ROOT / "controls" / "p45_campaign.py"),
         "--workload", "all",
         "--arms", "alara,actinv_fendl,actinv_tendl,openmc,actinv_mesh",
         "--repeat", "3", "--resume"],
        timeout=18000)
    # 2. frozen robustness leg
    steps["robustness"] = sh(
        [sys.executable, str(ROOT / "controls" / "p45_robustness.py")],
        timeout=7200)
    # 3. parity scoring
    steps["parity"] = sh(
        [sys.executable, str(ROOT / "controls" / "p45_parity.py")],
        timeout=1800)

    rows = []
    if LEDGER.exists():
        rows = [json.loads(l) for l in
                LEDGER.read_text().splitlines() if l.strip()]
    n_executed = sum(
        1 for r in rows
        for a in r["arms"].values()
        if a.get("status") == "executed" or a.get("returncode") == 0)
    n_failed = sum(
        1 for r in rows
        for a in r["arms"].values()
        if a.get("status") == "arm_failure"
        or (a.get("returncode") not in (0, None)
            and a.get("status") != "executed"))

    out = {"spec": "actinv-p45-g3-campaign-1",
           "wall_s": time.monotonic() - t_all,
           "envelope_s": ENVELOPE_S,
           "within_envelope": time.monotonic() - t_all < ENVELOPE_S,
           "steps": steps,
           "ledger_rows": len(rows),
           "n_executed": n_executed, "n_failed": n_failed,
           "all_steps_ok": all(
               s["returncode"] == 0 for s in steps.values())}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"within_envelope": out["within_envelope"],
                      "wall_min": round(out["wall_s"] / 60, 1),
                      "steps_rc": {k: s["returncode"]
                                   for k, s in steps.items()},
                      "ledger_rows": len(rows),
                      "executed": n_executed,
                      "failed": n_failed}))
    return 0 if out["all_steps_ok"] and out["within_envelope"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
