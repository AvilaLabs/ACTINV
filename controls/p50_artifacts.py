#!/usr/bin/env python3
"""P50 frozen artifact registry — every file the sealed verification
depends on, hashed at G0 and re-verified at each gate. Any drift
invalidates sealed evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARTIFACTS = {
    "protocol": "protocols/ACTINV-P50_PROTOCOL.md",
    "core_uncertainty_module": "crates/actinv-core/src/uncertainty.rs",
    "core_run_module": "crates/actinv-core/src/run.rs",
    "core_spec_module": "crates/actinv-core/src/spec.rs",
    "workspace_manifest": "Cargo.toml",
    "cargo_lock": "Cargo.lock",
    "fixtures": "controls/p11_fixtures.py",
    "python_covariance_reference": "controls/p11_covariance.py",
    "demo_spec": "examples/optimize_ra_steel/opt_v2_winner.json",
    "g1_mechanics": "controls/g1_p50_mechanics.py",
    "g2_controls": "controls/g2_p50_controls.py",
    "g3_demonstration": "controls/g3_p50_demonstration.py",
    "g4_checker": "controls/check_g4_p50.py",
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
