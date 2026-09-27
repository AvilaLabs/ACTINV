#!/usr/bin/env python3
"""P64 G4 — decide is byte-identical on repeat (fixture level)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g4_p64_determinism.json"

tmp = Path(tempfile.mkdtemp(prefix="p64_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
spec_path = tmp / "run.json"
spec_path.write_text(json.dumps(base, sort_keys=True) + "\n")

dspec = {
    "schema": "actinv-decide-1", "run_spec": str(spec_path),
    "decision": {"measurement_top": 3, "constraints": [
        {"name": "h", "response": "heat.total", "time_s": 0.9,
         "edge": "normal_upper", "sense": "le", "limit": 1e-30}]},
}
dp = tmp / "decide.json"
dp.write_text(json.dumps(dspec) + "\n")

outs = []
for i in range(2):
    p = tmp / f"d{i}.json"
    r = subprocess.run([str(ACTINV), "decide", str(dp), str(p)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-300:]
    outs.append(p.read_bytes())
ok = outs[0] == outs[1]
OUT.write_text(json.dumps({"pass": ok, "byte_identical": ok},
                          indent=1) + "\n")
print(json.dumps({"pass": ok}))
sys.exit(0 if ok else 1)
