#!/usr/bin/env python3
"""P58 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P58_PROTOCOL.md",
    "core_run": "crates/actinv-core/src/run.rs",
    "core_uncertainty": "crates/actinv-core/src/uncertainty.rs",
    "core_spec": "crates/actinv-core/src/spec.rs",
    "core_study": "crates/actinv-core/src/study.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "fixture": "controls/p58_fixture.py",
    "g1_mechanics": "controls/g1_p58_mechanics.py",
    "g2_exactness": "controls/g2_p58_exactness.py",
    "g3_demo": "controls/g3_p58_demo.py",
    "g4_determinism": "controls/g4_p58_determinism.py",
    "g5_checker": "controls/check_g5_p58.py",
    "p58_case": "controls/p58_case.py",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def verify() -> dict:
    out = {}
    for name, rel in ARTIFACTS.items():
        p = ROOT / rel
        out[name] = {
            "path": rel,
            "present": p.exists(),
            "sha256": sha256(p) if p.exists() else None,
        }
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(verify(), indent=2, sort_keys=True))
