#!/usr/bin/env python3
"""P62 artifact registry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P62_PROTOCOL.md",
    "case": "controls/p62_case.py",
    "g1": "controls/g1_p62_mechanics.py",
    "g2": "controls/g2_p62_identity.py",
    "g5": "controls/check_g5_p62.py",
    "optimize_rs": "crates/actinv-cli/src/optimize.rs",
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
