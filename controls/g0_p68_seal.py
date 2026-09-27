#!/usr/bin/env python3
"""P68 seal — artifact presence + protocol hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/g0_p68_seals.json"

ARTIFACTS = {
    "sweep_rs": "crates/actinv-gui/src/sweep.rs",
    "app_rs": "crates/actinv-gui/src/app.rs",
    "smoke_rs": "crates/actinv-gui/src/smoke.rs",
    "g1": "controls/g1_p68_mechanics.py",
    "g0": "controls/g0_p68_seal.py",
}

seals = {}
for name, rel in ARTIFACTS.items():
    p = ROOT / rel
    seals[name] = {"path": rel, "present": p.exists(),
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
                   if p.exists() else None}
all_present = all(v["present"] for v in seals.values())
sha = hashlib.sha256((ROOT / "docs/ROADMAP.md").read_bytes()).hexdigest()
OUT.write_text(json.dumps({
    "sealed": all_present,
    "roadmap_sha256": sha,
    "seals": seals,
}, indent=2) + "\n")
print(json.dumps({"sealed": all_present, "roadmap_sha256": sha}))
raise SystemExit(0 if all_present else 1)
