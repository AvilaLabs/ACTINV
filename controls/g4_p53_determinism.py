#!/usr/bin/env python3
"""P53 G4 — determinism: the joint export is a pure transform over
(mesh bytes, spec, step) with no wall-clock fields, so two runs must be
byte-identical. Runs on the small P11-derived fixture for speed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import g2_p53_exactness as g2  # noqa: E402
import p53_artifacts as p53a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g4_p53_determinism.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    problems = []
    with tempfile.TemporaryDirectory(prefix="p53-g4-", dir="target") as d:
        work = Path(d)
        # reuse the G2 fixture builder (writes fixture + flux + spec,
        # runs mesh + one export)
        recs, mesh_path = g2.run_pair(work / "det", g2.COV_MATS,
                                      [[1.0, 1.0], [3.0, 0.5]])
        spec_path = work / "det" / "mesh_spec.json"
        a = work / "a.ndjson"
        b = work / "b.ndjson"
        for out in (a, b):
            r = subprocess.run(
                [str(BIN), "export-r2s-joint", str(mesh_path),
                 str(spec_path), "2", str(out)],
                capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                problems.append(f"export failed: {r.stderr[-800:]}")
        if not problems:
            checks = {"run_twice_identical":
                      sha256_file(a) == sha256_file(b)}
            problems.extend(k for k, v in checks.items() if not v)
        result = {"pass": not problems, "problems": problems}
        OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result))
        return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
