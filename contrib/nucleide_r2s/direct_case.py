#!/usr/bin/env python3
"""Build the literal direct ACTINV control for the fixed Nucleide example.

This baseline is specified independently of the Nucleide fixtures and adapter
output: pure FE56 at 15.748 g, descending 709-group flux with group 88 set to
5e11 n/cm²/s (total 1e12), then 300 s irradiation and 3600 s cooling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


GROUPS = 709


def _sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def make_spec(
    library: Path,
    decay_primary: Path,
    decay_fallback: Path | None = None,
) -> dict:
    """Return the literal ``actinv-spec-1`` control for this fixed example."""
    library = library.resolve()
    decay = {"primary": str(decay_primary.resolve())}
    if decay_fallback is not None:
        decay["fallback"] = str(decay_fallback.resolve())

    flux = [0.0] * GROUPS
    flux[88] = 5.0e11
    return {
        "spec": "actinv-spec-1",
        "title": "Nucleide one-zone Fe56 direct translation control",
        "projectile": "neutron",
        "library": {"path": str(library), "sha256": _sha256_file(library)},
        "decay": decay,
        "material": {
            "mass_g": 15.748,
            "basis": "wt_percent",
            "composition": {"FE56": 100.0},
        },
        "spectrum": {
            "structure": "fispact-709",
            "flux_per_group": flux,
            "total": 1.0e12,
            "descending": True,
            "relative_error": [0.0] * GROUPS,
        },
        "schedule": [{"dt": "300 s", "flux": 1.0}, {"dt": "3600 s", "flux": 0.0}],
        "options": {
            "mode": "auto",
            "prune": "rate",
            "bmin_atoms_per_g": 1e-12,
            "temperature_K": 293.6,
            "outputs": ["photons"],
        },
        "photon": {"group_structure": "fispact-24"},
        "uncertainty": {
            "channels": ["flux"],
            "responses": ["activity:Mn56"],
            "require_complete": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--decay-primary", required=True)
    parser.add_argument("--decay-fallback")
    parser.add_argument("--out", required=True, help="new direct ACTINV problem JSON path")
    args = parser.parse_args()
    try:
        path = Path(args.out)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        spec = make_spec(
            Path(args.library),
            Path(args.decay_primary),
            Path(args.decay_fallback) if args.decay_fallback else None,
        )
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        print(f"wrote independent direct ACTINV spec: {path}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"direct case: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
