#!/usr/bin/env python3
"""P46 G3 sealed scoring — all 5 corpora x 132 experiments through
sealed code inside the envelope, then scoring + report.

Production is resume-aware (out.json cache), so a re-run only fills
gaps. Emits results/g3_p46_campaign.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/g3_p46_campaign.json"
LEDGER = ROOT / "results/p46_run_ledger.jsonl"
TABLES = ROOT / "results/p46_eval_tables.json"
ENVELOPE_S = 90 * 60


def main() -> int:
    t0 = time.monotonic()
    steps = {}

    rc = subprocess.run(
        [sys.executable, str(ROOT / "controls/p46_corpora.py"),
         "--partition", "all", "--out", str(LEDGER)],
        capture_output=True, text=True)
    steps["run"] = {"rc": rc.returncode,
                    "tail": (rc.stdout or "")[-300:] +
                            (rc.stderr or "")[-300:]}

    rc = subprocess.run(
        [sys.executable, str(ROOT / "controls/p46_score.py"),
         "--ledger", str(LEDGER), "--out", str(TABLES)],
        capture_output=True, text=True)
    steps["score"] = {"rc": rc.returncode,
                      "tail": (rc.stdout or "")[-300:] +
                              (rc.stderr or "")[-300:]}

    rc = subprocess.run(
        [sys.executable, str(ROOT / "controls/p46_report.py")],
        capture_output=True, text=True)
    steps["report"] = {"rc": rc.returncode,
                       "tail": (rc.stdout or "")[-300:] +
                               (rc.stderr or "")[-300:]}

    wall = time.monotonic() - t0
    rows = [json.loads(l) for l in
            LEDGER.read_text().splitlines() if l.strip()]
    latest = {}
    for r in rows:
        latest[(r["corpus"], r["material"], r["experiment"])] = r
    n_failed = sum(1 for r in latest.values()
                   if r.get("status") == "failed")
    out = {"spec": "actinv-p46-g3-1",
           "wall_s": wall,
           "within_envelope": wall < ENVELOPE_S,
           "envelope_s": ENVELOPE_S,
           "steps": steps,
           "records": len(latest),
           "executed": sum(1 for r in latest.values()
                           if r.get("status") == "executed"),
           "failed": n_failed,
           "all_steps_ok": all(s["rc"] == 0 for s in steps.values())}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"wall_min": round(wall / 60, 1),
                      "within_envelope": out["within_envelope"],
                      "records": len(latest),
                      "executed": out["executed"],
                      "failed": n_failed,
                      "steps_rc": {k: s["rc"]
                                   for k, s in steps.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
