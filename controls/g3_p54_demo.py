#!/usr/bin/env python3
"""P54 G3 demonstration — clearance over the sealed P53 corpus mesh
(8 cells, real TENDL bands) at every schedule step: certified clearance is
a cooling-time statement, so the demo reports the classification at each
step plus the dominant nuclides driving it. Zero new solve compute —
arithmetic over the emitted banded result only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p54_artifacts as p54a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN",
                          ROOT / "target/release/actinv"))
MESH = ROOT / "results/p53_mesh.ndjson"
OUT = ROOT / "results/g3_p54_demo.json"
NDJSON = ROOT / "results/p54_clearance.ndjson"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN)] + args, capture_output=True,
                          text=True, timeout=900)


def load_ndjson(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip()]


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    seal = p54a.verify()
    checks["artifacts_present"] = all(
        r["present"] for r in seal.values())

    steps = [1, 2, 3, 4]
    per_step = {}
    # The emitted clearance doc for the LAST step is persisted as the
    # phase artifact; earlier steps are re-derived by the checker.
    for step in steps:
        out = NDJSON if step == steps[-1] else Path(
            NDJSON.parent / f"p54_clearance_s{step}.ndjson")
        r = run(["clearance", str(MESH), str(step), str(out)])
        if r.returncode != 0:
            problems.append(f"step {step}: {r.stderr[-600:]}")
            checks[f"s{step}_ran"] = False
            continue
        checks[f"s{step}_ran"] = True
        recs = load_ndjson(out)
        cells = [x for x in recs if x["record"] == "clearance"]
        footer = recs[-1]
        classes = [c["classification"] for c in cells]
        per_step[step] = {
            "sum_ratios": [c["sum_ratio"] for c in cells],
            "classifications": classes,
            "p_intervals": [c["p_clear_interval"] for c in cells],
            "dominant": [c["dominant_ratio"]["nuclide"]
                         if c["dominant_ratio"] else None
                         for c in cells],
            "unregulated_share": [
                c["coverage"]["unregulated_activity_share"]
                for c in cells],
            "banded_share": [
                c["coverage"]["banded_ratio_share"] for c in cells],
            "footer": {k: footer[k] for k in footer
                       if k.startswith("cells_")},
        }
    checks["all_cells_evaluated"] = all(
        v["footer"]["cells_evaluated"] == 8
        for v in per_step.values())
    # Physical sanity: cooling reduces the worst cell's sum-of-ratios over
    # the full schedule (daughter feed-in can move individual steps, so the
    # assertion is the endpoints, not strict monotonicity).
    if len(per_step) == len(steps):
        checks["cooling_net_decrease"] = (
            max(per_step[steps[-1]]["sum_ratios"])
            < max(per_step[steps[0]]["sum_ratios"]))
    # Keep only the last-step artifact on disk.
    for step in steps[:-1]:
        p = NDJSON.parent / f"p54_clearance_s{step}.ndjson"
        if p.exists():
            p.unlink()

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "per_step": per_step, "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": result["pass"],
                      "problems": problems,
                      "classes_at_each_step": {
                          s: v["classifications"][:3]
                          for s, v in per_step.items()}}))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
