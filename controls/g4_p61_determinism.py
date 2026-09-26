#!/usr/bin/env python3
"""P61 G4 — determinism: two identical audit runs produce byte-identical
output (wall-clock `ms` excluded); and an outputs list without "audit"
stays byte-identical to the pre-feature schema (no completeness key).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p61_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g4_p61_determinism.json"

checks = []
tmp = Path(tempfile.mkdtemp(prefix="p61_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
a = p61_case.run(ACTINV, p61_case.spec(fx), tmp, "a")
b = p61_case.run(ACTINV, p61_case.spec(fx), tmp, "b")
a.pop("ms", None)
b.pop("ms", None)
checks.append({"name": "audit run byte-identical",
               "pass": json.dumps(a, sort_keys=True)
               == json.dumps(b, sort_keys=True)})
checks.append({"name": "completeness emitted",
               "pass": a["ledger"].get("completeness") is not None})
c = p61_case.run(ACTINV, p61_case.spec(fx, audit=False), tmp, "c")
checks.append({"name": "audit absent -> no completeness key",
               "pass": "completeness" not in c["ledger"]})

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks)}))
sys.exit(1 if failed else 0)
