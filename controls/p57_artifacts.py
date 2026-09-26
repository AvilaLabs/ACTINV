#!/usr/bin/env python3
"""P57 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P57_PROTOCOL.md",
    "core_source_adapter": "crates/actinv-core/src/source_adapter.rs",
    "cli_command_module": "crates/actinv-cli/src/command.rs",
    "interchange_doc": "docs/INTERCHANGE_TRANSPORT.md",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "g1_mechanics": "controls/g1_p57_mechanics.py",
    "g2_exactness": "controls/g2_p57_exactness.py",
    "g3_demo": "controls/g3_p57_demo.py",
    "g4_determinism": "controls/g4_p57_determinism.py",
    "g5_checker": "controls/check_g5_p57.py",
    "p57_case": "controls/p57_case.py",
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
