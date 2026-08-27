#!/usr/bin/env python3
"""Small deterministic files for P11 sensitivity and propagation controls."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def group_hash(boundaries: list[float]) -> str:
    digest = hashlib.sha256(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def _field(value) -> str:
    return f"{value:11d}" if isinstance(value, int) else f"{float(value):11.4E}"


def _record(values, mat, mf, mt, sequence) -> str:
    return "".join(_field(value) for value in values) + f"{mat:4d}{mf:2d}{mt:3d}{sequence:5d}"


def _payload(values, mat, mf, mt, sequence):
    output = []
    for start in range(0, len(values), 6):
        row = list(values[start : start + 6])
        row.extend([0.0] * (6 - len(row)))
        output.append(_record(row, mat, mf, mt, sequence))
        sequence += 1
    return output, sequence


def write_decay(path: Path) -> None:
    # ZA, AWR, stable, half life, light/EM/heavy mean energies.
    nuclides = [
        (26056, 55.454, True, 0.0, (0.0, 0.0, 0.0)),
        (25056, 55.460, False, 2.0, (1.0e6, 2.0e6, 0.5e6)),
        (25057, 56.450, False, 3.0, (0.7e6, 1.1e6, 0.2e6)),
    ]
    lines = []
    for material, (za, awr, stable, half_life, energies) in enumerate(nuclides, 100):
        sequence = 1
        lines.append(_record([float(za), awr, 0, 0, int(stable), 0], material, 8, 457, sequence))
        sequence += 1
        lines.append(_record([half_life, 0.0, 0, 0, 0 if stable else 6, 0], material, 8, 457, sequence))
        sequence += 1
        if not stable:
            light, electromagnetic, heavy = energies
            payload, sequence = _payload(
                [light, 0.0, electromagnetic, 0.0, heavy, 0.0], material, 8, 457, sequence
            )
            lines.extend(payload)
        lines.append(_record([0.0, 0.0, 0, 0, 0, 0], material, 8, 457, sequence))
        sequence += 1
        lines.append(_record([0.0] * 6, material, 8, 0, sequence))
    path.write_text("\n".join(lines) + "\n")


ROWS = np.asarray(
    [
        [0, 102, -1, -1, 0],
        [0, 102, 25056, 0, 3],
        [0, 103, -1, -1, 0],
        [0, 103, 25057, 0, 3],
    ],
    dtype=np.int64,
)
SIGMA = np.asarray([[0.2], [0.2], [0.1], [0.1]], dtype=np.float64)
BOUNDS = [1.0, 3.0]


def write_library(path: Path, sigma: np.ndarray = SIGMA) -> Path:
    np.savez(
        path,
        rows=ROWS,
        sig=np.asarray(sigma, dtype=np.float64),
        bounds=np.asarray(BOUNDS, dtype=np.float64),
    )
    index = path.with_name(path.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-library-index-1",
            "projectile": "neutron",
            "groups": "custom",
            "group_boundary_sha256": group_hash(BOUNDS),
            "temperature_K": 293.6,
            "sha256_npz": sha256(path),
            "targets": [
                {
                    "file": "p11-synthetic.endf",
                    "source_sha256": "1" * 64,
                    "mat": 2631,
                    "za": 26056,
                    "liso": 0,
                    "awr": 55.454,
                    "ledger": [],
                }
            ],
        },
    )
    return index


def write_covariance(path: Path, activation: Path) -> Path:
    # Absolute one-group covariance for MT102/103: positive definite with a nonzero cross term.
    np.savez(
        path,
        components=np.asarray(
            [
                [0, 102, 102, 0, 0, 0, 0, 0, 1],
                [0, 102, 103, 0, 0, 0, 0, 1, 1],
                [0, 103, 103, 0, 0, 0, 0, 2, 1],
            ],
            dtype=np.int64,
        ),
        grid_offsets=np.asarray([0, 2], dtype=np.int64),
        grid_values=np.asarray(BOUNDS, dtype=np.float64),
        values=np.asarray([4.0e-4, 1.5e-4, 9.0e-4], dtype=np.float64),
    )
    activation_index = activation.with_name(activation.stem + "_index.json")
    index = path.with_name(path.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-covariance-index-1",
            "projectile": "neutron",
            "activation_library": str(activation),
            "activation_library_sha256": sha256(activation),
            "activation_index": str(activation_index),
            "activation_index_sha256": sha256(activation_index),
            "group_boundary_sha256": group_hash(BOUNDS),
            "builder_fingerprint": "2" * 64,
            "source_manifest_sha256": "3" * 64,
            "targets": [
                {
                    "target": 0,
                    "file": "p11-synthetic.endf",
                    "source_sha256": "1" * 64,
                    "mat": 2631,
                    "za": 26056,
                    "liso": 0,
                    "mf33_sections": 2,
                    "components": 3,
                    "lb_counts": {"0": 3},
                }
            ],
            "files": 1,
            "files_with_mf33": 1,
            "mf33_sections": 2,
            "components": 3,
            "lb_counts": {"0": 3},
            "columns": "synthetic control",
            "sha256_npz": sha256(path),
        },
    )
    return index


def make_fixture(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    decay = work / "p11-decay.endf"
    library = work / "p11-activation.npz"
    covariance = work / "p11-covariance.npz"
    write_decay(decay)
    write_library(library)
    write_covariance(covariance, library)
    return {"decay": decay, "library": library, "covariance": covariance}


def specification(
    fixture: dict[str, Path],
    *,
    mode: str,
    cram_order: int,
    library: Path | None = None,
    uncertainty: bool = True,
) -> dict:
    selected_library = library or fixture["library"]
    value = {
        "spec": "actinv-spec-1",
        "title": f"P11 synthetic {mode} CRAM-{cram_order}",
        "library": {"path": str(selected_library), "sha256": sha256(selected_library)},
        "decay": {"primary": str(fixture["decay"])},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Fe56": 1.0}},
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": BOUNDS,
            "flux_per_group": [1.0],
            "total": 1.0e24,
            "descending": False,
        },
        "schedule": [
            {"dt": "0.7 s", "flux": 1.0},
            {"dt": "0.2 s", "flux": 0.0},
            {"dt": "0.4 s", "flux": 2.0},
            {"dt": "0.6 s", "flux": 0.0},
        ],
        "options": {
            "mode": mode,
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "cram_order": cram_order,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
    }
    if uncertainty:
        value["uncertainty"] = {
            "covariance": {
                "path": str(fixture["covariance"]),
                "sha256": sha256(fixture["covariance"]),
            },
            "responses": [
                "heat.total",
                "heat.alpha",
                "heat.beta",
                "heat.gamma",
                "activity:Mn56",
                "activity:Mn57",
            ],
            "confidence_level": 0.95,
            "require_complete": True,
        }
    return value
