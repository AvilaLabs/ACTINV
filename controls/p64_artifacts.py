#!/usr/bin/env python3
"""P64 artifact registry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P64_PROTOCOL.md",
    "decide_rs": "crates/actinv-cli/src/decide.rs",
    "optimize_rs": "crates/actinv-cli/src/optimize.rs",
    "command_rs": "crates/actinv-cli/src/command.rs",
    "lib_rs": "crates/actinv-cli/src/lib.rs",
    "g1": "controls/g1_p64_mechanics.py",
    "g2": "controls/g2_p64_exactness.py",
    "g3": "controls/g3_p64_demo.py",
    "g4": "controls/g4_p64_determinism.py",
    "g5": "controls/check_g5_p64.py",
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
