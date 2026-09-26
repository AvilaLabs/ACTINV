#!/usr/bin/env python3
"""P54 G4 determinism — two identical `actinv clearance` invocations on the
sealed corpus mesh must produce byte-identical output modulo the declared
nondeterministic footer field (wall_time_s).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p54_artifacts as p54a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN",
                          ROOT / "target/release/actinv"))
MESH = ROOT / "results/p53_mesh.ndjson"
OUT = ROOT / "results/g4_p54_determinism.json"


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    seal = p54a.verify()
    checks["artifacts_present"] = all(
        r["present"] for r in seal.values())

    with tempfile.TemporaryDirectory(prefix="p54-g4-", dir="target") as d:
        work = Path(d)
        outs = []
        for i in range(2):
            out = work / f"cl_{i}.ndjson"
            r = subprocess.run(
                [str(BIN), "clearance", str(MESH), "4", str(out)],
                capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                problems.append(f"run {i}: {r.stderr[-400:]}")
                checks[f"run_{i}"] = False
            else:
                checks[f"run_{i}"] = True
                outs.append(out)

        if len(outs) == 2:
            a = [json.loads(l) for l in outs[0].read_text().splitlines()
                 if l.strip()]
            b = [json.loads(l) for l in outs[1].read_text().splitlines()
                 if l.strip()]
            checks["record_count"] = len(a) == len(b)
            diffs = []
            for i, (ra, rb) in enumerate(zip(a, b)):
                if ra["record"] == "footer":
                    ra.pop("wall_time_s", None)
                    rb.pop("wall_time_s", None)
                if ra != rb:
                    diffs.append(i)
            checks["byte_identical_modulo_wallclock"] = not diffs
            if diffs:
                problems.append(f"differing records: {diffs}")

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
