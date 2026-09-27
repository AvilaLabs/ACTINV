#!/usr/bin/env python3
"""P63 G4 — certificate generation is byte-identical on repeat."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/g4_p63_determinism.json"

tmp = Path(tempfile.mkdtemp(prefix="p63_g4_", dir=ROOT / "target"))
outs = []
for i in range(2):
    p = tmp / f"c{i}.json"
    subprocess.run([sys.executable, str(ROOT / "controls/p63_calibrate.py"),
                    str(ROOT / "results/p44_sealed_coverage.json"),
                    "--out", str(p)], check=True, capture_output=True)
    outs.append(p.read_bytes())
ok = outs[0] == outs[1]
OUT.write_text(json.dumps({"pass": ok, "byte_identical": ok},
                          indent=1) + "\n")
print(json.dumps({"pass": ok}))
sys.exit(0 if ok else 1)
