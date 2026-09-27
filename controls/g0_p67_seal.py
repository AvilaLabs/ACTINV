#!/usr/bin/env python3
"""P67 seal — artifact presence + protocol hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/g0_p67_seals.json"

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P67_PROTOCOL.md",
    "optimize_rs": "crates/actinv-cli/src/optimize.rs",
    "g1": "controls/g1_p67_mechanics.py",
    "g3": "controls/g3_p67_demo.py",
    "g4": "controls/g4_p67_determinism.py",
    "g5": "controls/check_g5_p67.py",
}

seals = {}
for name, rel in ARTIFACTS.items():
    p = ROOT / rel
    seals[name] = {"path": rel, "present": p.exists(),
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
                   if p.exists() else None}
all_present = all(v["present"] for v in seals.values())
sha = hashlib.sha256(
    (ROOT / ARTIFACTS["protocol"]).read_bytes()).hexdigest()
OUT.write_text(json.dumps({
    "sealed": all_present,
    "protocol_sha256": sha,
    "seals": seals,
}, indent=2) + "\n")
print(json.dumps({"sealed": all_present, "protocol_sha256": sha}))
raise SystemExit(0 if all_present else 1)
