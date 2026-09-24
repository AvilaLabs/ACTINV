#!/usr/bin/env python3
"""P48 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P48_PROTOCOL.md",
    "sweep_module": "crates/actinv-gui/src/sweep.rs",
    "smoke_module": "crates/actinv-gui/src/smoke.rs",
    "app_module": "crates/actinv-gui/src/app.rs",
    "worker_module": "crates/actinv-gui/src/worker.rs",
    "model_module": "crates/actinv-gui/src/model.rs",
    "gui_manifest": "crates/actinv-gui/Cargo.toml",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "flagship_spec": "examples/fns_fe_5min.json",
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
