#!/usr/bin/env python3
"""P49 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P49_PROTOCOL.md",
    "optimize_module": "crates/actinv-cli/src/optimize.rs",
    "command_module": "crates/actinv-cli/src/command.rs",
    "cli_lib": "crates/actinv-cli/src/lib.rs",
    "cli_manifest": "crates/actinv-cli/Cargo.toml",
    "core_run_module": "crates/actinv-core/src/run.rs",
    "core_spec_module": "crates/actinv-core/src/spec.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "demo_base_spec": "examples/optimize_ra_steel/base_spec.json",
    "demo_optspec": "examples/optimize_ra_steel/opt.json",
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
