#!/usr/bin/env python3
"""P50 G3 demonstration — run the P49 RA-steel winner spec with
`uncertainty.voi` against the full corpus and ledger the product answer:
the ranked nuclear-data parameters carrying each declared response band.

Emits results/p50_voi_result.json (the sealed run record) and
results/g3_p50_demonstration.json (the gate record).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p50_artifacts as p50a  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
SPEC = ROOT / "examples/optimize_ra_steel/opt_v2_winner.json"
RESULT = ROOT / "results/p50_voi_result.json"
OUT = ROOT / "results" / "g3_p50_demonstration.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    seal = json.load(open(ROOT / "results/g0_p50_seals.json"))
    artifacts = p50a.verify()
    drift = {n: r["path"] for n, r in artifacts.items()
             if r["sha256"] != seal["artifacts"][n]["sha256"]}
    if drift:
        print(json.dumps({"pass": False, "seal_drift": drift}))
        return 1

    start = time.monotonic()
    proc = subprocess.run(
        [str(ACTINV), "run", str(SPEC), str(RESULT)],
        capture_output=True, text=True, cwd=ROOT)
    wall = time.monotonic() - start
    if proc.returncode != 0:
        OUT.write_text(json.dumps({"schema": "actinv-p50-g3-1", "pass": False,
                                   "stderr_tail": proc.stderr[-2000:]},
                                  indent=2) + "\n")
        return 1

    result = json.load(open(RESULT))
    tables = {}
    total_entries = 0
    for index, step in enumerate(result["steps"]):
        for name, response in step["uncertainty"]["responses"].items():
            voi = response.get("voi")
            if voi is None:
                continue
            top = voi["top"][:5]
            key = f"step{index}/{name}"
            tables[key] = [{
                "channel": e["channel"],
                "nuclide": e["parameter"].get("target_nuclide")
                           or e["parameter"].get("nuclide")
                           or e["parameter"].get("parent_nuclide"),
                "MT": e["parameter"].get("MT"),
                "share_fraction": e["share_fraction"],
                "variance_share": e["variance_share"],
            } for e in top]
            total_entries += len(voi["top"])

    record = {
        "schema": "actinv-p50-g3-1",
        "spec": str(SPEC.relative_to(ROOT)),
        "spec_sha256": sha256(SPEC),
        "result": str(RESULT.relative_to(ROOT)),
        "result_sha256": sha256(RESULT),
        "wall_s": wall,
        "voi_tables": len(tables),
        "total_ranked_entries": total_entries,
        "tables_head": tables,
        "pass": total_entries > 0,
    }
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "wall_s": round(wall, 1),
                      "tables": len(tables), "entries": total_entries}))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
