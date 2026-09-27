#!/usr/bin/env python3
"""P65 artifact registry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P65_PROTOCOL.md",
    "spec_rs": "crates/actinv-core/src/spec.rs",
    "run_rs": "crates/actinv-core/src/run.rs",
    "g1": "controls/g1_p65_mechanics.py",
    "g2": "controls/g2_p65_exactness.py",
    "g3": "controls/g3_p65_demo.py",
    "g4": "controls/g4_p65_determinism.py",
    "g5": "controls/check_g5_p65.py",
}


def verify() -> dict:
    out = {}
    for name, rel in ARTIFACTS.items():
        p = ROOT / rel
        out[name] = {
            "path": rel,
            "present": p.exists(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
            if p.exists() else None,
        }
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(verify(), indent=2))
