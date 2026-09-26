#!/usr/bin/env python3
"""P58 G4 — determinism: two identical isomer runs produce byte-identical
output (wall-clock `ms` excluded); the fixture's emitted isomer blocks
are bitwise equal run-to-run.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_case  # noqa: E402
import p58_fixture  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g4_p58_determinism.json"

checks = []
tmp = Path(tempfile.mkdtemp(prefix="p58_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
a = p58_case.run(ACTINV, p58_case.spec(fx), tmp, "a")
b = p58_case.run(ACTINV, p58_case.spec(fx), tmp, "b")
a.pop("ms", None)
b.pop("ms", None)
checks.append({"name": "full document identical",
               "pass": json.dumps(a, sort_keys=True)
               == json.dumps(b, sort_keys=True)})
ia = [s["uncertainty"]["responses"] for s in a["steps"]]
ib = [s["uncertainty"]["responses"] for s in b["steps"]]
checks.append({"name": "isomer blocks identical",
               "pass": json.dumps(ia, sort_keys=True)
               == json.dumps(ib, sort_keys=True)})
pa = a.get("isomer_pathway_shares")
pb = b.get("isomer_pathway_shares")
checks.append({"name": "pathway shares identical",
               "pass": json.dumps(pa, sort_keys=True)
               == json.dumps(pb, sort_keys=True)})

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks)}))
sys.exit(1 if failed else 0)
