#!/usr/bin/env python3
"""P53 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P53_PROTOCOL.md",
    "core_r2s_module": "crates/actinv-core/src/r2s.rs",
    "core_covariance_module": "crates/actinv-data/src/covariance.rs",
    "core_mesh_module": "crates/actinv-core/src/mesh.rs",
    "core_run_module": "crates/actinv-core/src/run.rs",
    "cli_command_module": "crates/actinv-cli/src/command.rs",
    "cli_lib_module": "crates/actinv-cli/src/lib.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "g1_mechanics": "controls/g1_p53_mechanics.py",
    "g2_exactness": "controls/g2_p53_exactness.py",
    "g3_demo": "controls/g3_p53_demo.py",
    "g4_determinism": "controls/g4_p53_determinism.py",
    "g5_checker": "controls/check_g5_p53.py",
    "p53_fixture": "controls/p53_fixture.py",
    "p11_fixtures": "controls/p11_fixtures.py",
    "p11_covariance": "controls/p11_covariance.py",
    "p52_demo": "controls/g3_p52_demo.py",
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
