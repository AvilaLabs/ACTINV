#!/usr/bin/env python3
"""P55 G4 determinism — identical inputs produce byte-identical output
records. The qualified-inverse emit carries no wall-clock fields, so the
comparison is byte-exact over the whole document.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p55_case as p55  # noqa: E402

RESULT = ROOT / "results/g4_p55_determinism.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))


def main() -> int:
    checks: dict[str, bool] = {}
    work_root = ROOT / "target/preflight-tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="p55-g4-") as td:
        work = Path(td)
        fxmap = fx.make_fixture(work)
        sha = fx.fx.sha256
        fwd = p55.forward(ACTINV, p55.run_spec(fxmap, (1.5, 0.6), sha),
                          work, "truth")
        meas = p55.measurements_from(fwd)
        spec = p55.run_spec(fxmap, sha=sha)
        sp = work / "spec.json"; sp.write_text(json.dumps(spec))

        outputs = []
        for i in range(2):
            rc, log, out = p55.qualified(ACTINV, sp, meas, work, f"det{i}")
            checks[f"run{i}_ok"] = rc == 0
            outputs.append(out.read_text() if out.exists() else "")
        checks["byte_identical"] = outputs[0] == outputs[1] != ""

    evidence = {"schema": "actinv-p55-g4-determinism-1",
                "checks": checks, "pass": all(checks.values())}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
