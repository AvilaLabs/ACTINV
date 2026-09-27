#!/usr/bin/env python3
"""P67 G4 — pareto'd optimize run is byte-identical on repeat."""
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
OUT = ROOT / "results/g4_p67_determinism.json"

tmp = Path(tempfile.mkdtemp(prefix="p67_g4_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
s = p60_case.spec(fx)
(tmp / "s.json").write_text(json.dumps(s))
op = tmp / "opt.json"
op.write_text(json.dumps({
    "schema": "actinv-optimize-1", "base_spec": "s.json",
    "optimizer": {"algorithm": "lhs_coordinate", "seed": 11,
                  "init_points": 5, "refine_points": 0},
    "objective": {"direction": "min", "response": "heat.total",
                  "time_s": 1.9, "edge": "nominal"},
    "objectives": [
        {"response": "heat.total", "time_s": 1.9, "edge": "nominal",
         "direction": "min"},
        {"response": "activity:Mn57", "time_s": 1.9, "edge": "nominal",
         "direction": "max"}],
    "constraints": [],
    "design_axes": [{"kind": "composition_fraction", "element": "MN",
                     "bounds": [0.0, 1.0]}]}) + "\n")

import re
outs = []
for i in range(2):
    r = subprocess.run([str(ACTINV), "optimize", str(op), str(tmp / f"o{i}")],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-300:]
    t = (tmp / f"o{i}/optimize_result.json").read_text()
    outs.append(re.sub(r'"wall_s": [0-9.e+-]+', '"wall_s": 0', t))
ok = outs[0] == outs[1]
OUT.write_text(json.dumps({"pass": ok}, indent=1) + "\n")
print(json.dumps({"pass": ok}))
sys.exit(0 if ok else 1)
