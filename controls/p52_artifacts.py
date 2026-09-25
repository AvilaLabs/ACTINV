#!/usr/bin/env python3
"""P52 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P52_PROTOCOL.md",
    "core_r2s_module": "crates/actinv-core/src/r2s.rs",
    "core_mesh_module": "crates/actinv-core/src/mesh.rs",
    "core_run_module": "crates/actinv-core/src/run.rs",
    "cli_command_module": "crates/actinv-cli/src/command.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "mesh_flux_example": "examples/mesh_flux.ndjson",
    "g1_mechanics": "controls/g1_p52_mechanics.py",
    "g2_parity": "controls/g2_p52_parity.py",
    "openmc_parity_driver": "controls/p52_openmc_parity.py",
    "g3_demo": "controls/g3_p52_demo.py",
    "g4_determinism": "controls/g4_p52_determinism.py",
    "g5_checker": "controls/check_g5_p52.py",
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
