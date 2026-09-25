#!/usr/bin/env python3
"""P51 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P51_PROTOCOL.md",
    "core_run_module": "crates/actinv-core/src/run.rs",
    "cli_worker_module": "crates/actinv-cli/src/worker.rs",
    "cli_command_module": "crates/actinv-cli/src/command.rs",
    "cli_lib_module": "crates/actinv-cli/src/lib.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "fixtures": "controls/p11_fixtures.py",
    "corpus_probe_spec": "examples/p51_battery/corpus_probe.json",
    "g1_mechanics": "controls/g1_p51_mechanics.py",
    "g2_identity": "controls/g2_p51_identity.py",
    "g3_amortization": "controls/g3_p51_amortization.py",
    "g4_lifecycle": "controls/g4_p51_lifecycle.py",
    "g5_checker": "controls/check_g5_p51.py",
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
