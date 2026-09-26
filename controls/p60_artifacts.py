#!/usr/bin/env python3
"""P60 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P60_PROTOCOL.md",
    "core_run": "crates/actinv-core/src/run.rs",
    "core_uncertainty": "crates/actinv-core/src/uncertainty.rs",
    "core_spec": "crates/actinv-core/src/spec.rs",
    "core_study": "crates/actinv-core/src/study.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "g1_mechanics": "controls/g1_p60_mechanics.py",
    "g2_exactness": "controls/g2_p60_exactness.py",
    "g3_demo": "controls/g3_p60_demo.py",
    "g4_determinism": "controls/g4_p60_determinism.py",
    "g5_checker": "controls/check_g5_p60.py",
    "p60_case": "controls/p60_case.py",
}


def verify() -> dict[str, dict]:
    report = {}
    for name, rel in ARTIFACTS.items():
        path = ROOT / rel
        report[name] = {
            "path": rel,
            "present": path.is_file(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() else None,
        }
    return report


if __name__ == "__main__":
    import json
    print(json.dumps(verify(), indent=2, sort_keys=True))
