#!/usr/bin/env python3
"""P60 G4 — determinism: two identical design runs produce byte-identical
output (wall-clock `ms` excluded); emitted design blocks are bitwise equal
run-to-run.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g4_p60_determinism.json"

checks = []
tmp = Path(tempfile.mkdtemp(prefix="p60_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
a = p60_case.run(ACTINV, p60_case.spec(fx), tmp, "a")
b = p60_case.run(ACTINV, p60_case.spec(fx), tmp, "b")
a.pop("ms", None)
b.pop("ms", None)
checks.append({"name": "full document identical",
               "pass": json.dumps(a, sort_keys=True)
               == json.dumps(b, sort_keys=True)})
da = [s["uncertainty"]["responses"][r].get("design")
      for s in a["steps"] for r in s["uncertainty"]["responses"]]
db = [s["uncertainty"]["responses"][r].get("design")
      for s in b["steps"] for r in s["uncertainty"]["responses"]]
checks.append({"name": "design blocks identical",
               "pass": json.dumps(da, sort_keys=True)
               == json.dumps(db, sort_keys=True)})

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks)}))
sys.exit(1 if failed else 0)
