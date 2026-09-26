#!/usr/bin/env python3
"""P61 artifact registry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P61_PROTOCOL.md",
    "case": "controls/p61_case.py",
    "g1": "controls/g1_p61_mechanics.py",
    "g2": "controls/g2_p61_exactness.py",
    "g3": "controls/g3_p61_demo.py",
    "g4": "controls/g4_p61_determinism.py",
    "g5": "controls/check_g5_p61.py",
    "run_rs": "crates/actinv-core/src/run.rs",
    "spec_rs": "crates/actinv-core/src/spec.rs",
    "fixture": "controls/p58_fixture.py",
    "p60_case": "controls/p60_case.py",
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
