#!/usr/bin/env python3
"""P56 G4 — two identical optimize runs produce identical ledgers and
certification modulo wall_s/fingerprint_ms."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p56_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g4_p56_determinism.json"


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()
                if k not in ("wall_s", "fingerprint_ms")}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


tmp = Path(tempfile.mkdtemp(prefix="p56_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
op = p56_case.optspec(fx, tmp, 1.5e-37)
a = p56_case.optimize(ACTINV, op, tmp / "a")
b = p56_case.optimize(ACTINV, op, tmp / "b")
ok_led = [scrub(r) for r in a["rows"]] == [scrub(r) for r in b["rows"]]
ok_res = scrub(a["result"]) == scrub(b["result"])
out = {"pass": ok_led and ok_res, "ledger_equal": ok_led,
       "result_equal": ok_res}
OUT.write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out))
sys.exit(0 if out["pass"] else 1)
