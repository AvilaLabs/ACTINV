#!/usr/bin/env python3
"""P65 G4 — screened run is byte-identical on repeat."""
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
OUT = ROOT / "results/g4_p65_determinism.json"

tmp = Path(tempfile.mkdtemp(prefix="p65_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
base["options"]["prune"] = "rate"
base["options"]["screen"] = {"bmin_atoms_per_g": 1e-4}
p = tmp / "s.json"
p.write_text(json.dumps(base) + "\n")

outs = []
for i in range(2):
    o = tmp / f"o{i}.json"
    r = subprocess.run([str(ACTINV), "run", str(p), str(o)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-300:]
    outs.append(o.read_text())

def strip_ms(t):
    import re
    return re.sub(r'"ms": [0-9.e+-]+', '"ms": 0', t)

ok = strip_ms(outs[0]) == strip_ms(outs[1])
OUT.write_text(json.dumps({"pass": ok}, indent=1) + "\n")
print(json.dumps({"pass": ok}))
sys.exit(0 if ok else 1)
