#!/usr/bin/env python3
"""P49 G3 — the executed demonstration campaign on the sealed release
binary under the bounded scope. Runs `actinv optimize` on the frozen
RA-steel optspec and writes results/p49_campaign.json with the measured
wall time, ledger digest and outcome summary.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p49_artifacts as p49a  # noqa: E402

ACTINV = ROOT / "target/release/actinv"
OPTSPEC = ROOT / "examples/optimize_ra_steel/opt.json"
OUTDIR = ROOT / "results/p49_campaign"
OUT = ROOT / "results/p49_campaign_report.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    missing = {n: r["path"] for n, r in p49a.verify().items()
               if not r["present"]}
    if missing:
        print(json.dumps({"gate": "G3", "pass": False,
                          "reason": f"missing artifacts: {missing}"}))
        return 1

    t0 = time.time()
    # --resume is a no-op on a fresh ledger and lets an interrupted
    # campaign continue from the last committed evaluation
    p = subprocess.run(
        [str(ACTINV), "optimize", str(OPTSPEC), str(OUTDIR), "--resume"],
        capture_output=True, text=True, cwd=ROOT)
    wall = time.time() - t0
    if p.returncode != 0:
        print(json.dumps({"gate": "G3", "pass": False,
                          "reason": f"optimize failed: {p.stderr[-400:]}"}))
        return 1

    result = json.loads((OUTDIR / "optimize_result.json").read_text())
    ledger_rows = [json.loads(l) for l in
                   (OUTDIR / "optimize_ledger.jsonl").read_text().splitlines()
                   if l.strip()]
    report = {
        "gate": "G3",
        "pass": True,
        "wall_s": wall,
        "eval_wall_s_total": sum(r.get("wall_s", 0.0)
                                 for r in ledger_rows),
        "n_evals": result["n_evals"],
        "infeasible": result["infeasible"],
        "best_feasible": result["best_feasible"],
        "winner_verification": result["winner_verification"],
        "ledger_sha256": sha256(OUTDIR / "optimize_ledger.jsonl"),
        "result_sha256": sha256(OUTDIR / "optimize_result.json"),
        "n_candidate_specs": len(list((OUTDIR / "candidates").glob("*.json"))),
        "ledger_rows": len(ledger_rows),
        "n_feasible": sum(1 for r in ledger_rows if r.get("feasible")),
        "n_infeasible": sum(1 for r in ledger_rows if not r.get("feasible")),
        "stderr_tail": p.stderr[-200:],
    }
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
