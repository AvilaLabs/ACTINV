#!/usr/bin/env python3
"""P63 artifact registry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P63_PROTOCOL.md",
    "calibrate": "controls/p63_calibrate.py",
    "g1": "controls/g1_p63_mechanics.py",
    "g2": "controls/g2_p63_exactness.py",
    "g3": "controls/g3_p63_demo.py",
    "g4": "controls/g4_p63_determinism.py",
    "g5": "controls/check_g5_p63.py",
    "uncertainty_rs": "crates/actinv-core/src/uncertainty.rs",
    "spec_rs": "crates/actinv-core/src/spec.rs",
    "run_rs": "crates/actinv-core/src/run.rs",
    "sealed_corpus": "results/p44_sealed_coverage.json",
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
