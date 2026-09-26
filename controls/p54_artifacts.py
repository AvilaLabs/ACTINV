#!/usr/bin/env python3
"""P54 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P54_PROTOCOL.md",
    "core_clearance_module": "crates/actinv-core/src/clearance.rs",
    "core_lib_module": "crates/actinv-core/src/lib.rs",
    "cli_command_module": "crates/actinv-cli/src/command.rs",
    "cli_lib_module": "crates/actinv-cli/src/lib.rs",
    "limits_table": "data/clearance_iaea_2004.json",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "g1_mechanics": "controls/g1_p54_mechanics.py",
    "g2_exactness": "controls/g2_p54_exactness.py",
    "g3_demo": "controls/g3_p54_demo.py",
    "g4_determinism": "controls/g4_p54_determinism.py",
    "g5_checker": "controls/check_g5_p54.py",
    "p54_fixture": "controls/p54_fixture.py",
    "p53_fixture": "controls/p53_fixture.py",
    "p11_fixtures": "controls/p11_fixtures.py",
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
