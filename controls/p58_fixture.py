#!/usr/bin/env python3
"""P58 fixture — 2-group synthetic activation library + covariance sidecar
+ decay file extended with an isomer channel on top of the P53 layout.

Adds to the P53 fixture:
- a fifth library row: MT=103 -> ZAP 25057 LFS=1, producing (25057,1)
  "Mn57m1" (the chain resolves (zap,lfs) -> (za,liso) directly);
- a decay record for Mn57m1 (t1/2 = 1.5 s, dThalf = 0.05 s so the
  decay_constants channel covers it) carrying one gamma line;
- the covariance components are keyed (mt_a, mt_b), so the isomer row
  shares the (103,103) block with the ground MT=103 row — both are
  covered, and the partition is exercised on real numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import p11_fixtures as fx
from p53_fixture import COV_MATS, write_covariance, BOUNDS

ROWS = np.asarray(
    [
        [0, 102, -1, -1, 0],
        [0, 102, 25056, 0, 3],
        [0, 103, -1, -1, 0],
        [0, 103, 25057, 0, 3],
        [0, 103, 25057, 1, 3],
    ],
    dtype=np.int64,
)

SIGMA = np.asarray(
    [[0.20, 0.30],
     [0.20, 0.30],
     [0.10, 0.02],
     [0.10, 0.02],
     [0.04, 0.06]],
    dtype=np.float64)


def write_library(path: Path) -> Path:
    np.savez(
        path,
        rows=ROWS,
        sig=np.asarray(SIGMA, dtype=np.float64),
        bounds=np.asarray(BOUNDS, dtype=np.float64),
    )
    index = path.with_name(path.stem + "_index.json")
    fx.write_json(
        index,
        {
            "schema": "actinv-library-index-1",
            "projectile": "neutron",
            "groups": "custom",
            "group_boundary_sha256": fx.group_hash(BOUNDS),
            "temperature_K": 293.6,
            "sha256_npz": fx.sha256(path),
            "targets": [
                {
                    "file": "p58-synthetic.endf",
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


def write_decay(path: Path) -> None:
    """P53 decay set plus Mn57m1: liso encoded in head record L2."""
    lines = []
    # (za, awr, stable, t_half, dthalf, energies, gammas, liso)
    nuclides = [
        (26056, 55.454, True, 0.0, 0.0, (0.0, 0.0, 0.0), [], 0),
        (25056, 55.460, False, 2.0, 0.0, (1.0e6, 2.0e6, 0.5e6), [], 0),
        (25057, 56.450, False, 3.0, 0.0, (0.7e6, 1.1e6, 0.2e6),
         [(0.8e6, 1.0), (1.4e6, 0.5)], 0),
        (25057, 56.450, False, 1.5, 0.05, (0.4e6, 0.6e6, 0.1e6),
         [(2.1e6, 1.0)], 1),
    ]
    for material, (za, awr, stable, t_half, dthalf, energies,
                   gammas, liso) in enumerate(nuclides, 100):
        seq = 1
        lines.append(fx._record(
            [float(za), awr, 0, liso, int(stable), 1 if gammas else 0],
            material, 8, 457, seq))
        seq += 1
        lines.append(fx._record(
            [t_half, dthalf, 0, 0, 0 if stable else 6, 0],
            material, 8, 457, seq))
        seq += 1
        if not stable:
            light, em, heavy = energies
            payload, seq = fx._payload(
                [light, 0.0, em, 0.0, heavy, 0.0], material, 8, 457, seq)
            lines.extend(payload)
        lines.append(fx._record([0.0, 0.0, 0, 0, 0, 0],
                                material, 8, 457, seq))
        seq += 1
        if gammas:
            lines.append(fx._record(
                [0.0, 0.0, 0, 0, 6, len(gammas)], material, 8, 457, seq))
            seq += 1
            payload, seq = fx._payload(
                [1.0, 0.0, sum(e * i for e, i in gammas), 0.0, 0.0, 0.0],
                material, 8, 457, seq)
            lines.extend(payload)
            for energy, intensity in gammas:
                lines.append(fx._record(
                    [energy, 0.0, 0, 0, 12, 0], material, 8, 457, seq))
                seq += 1
                payload, seq = fx._payload(
                    [0.0, 0.0, intensity, 0.0] + [0.0] * 8,
                    material, 8, 457, seq)
                lines.extend(payload)
        lines.append(fx._record([0.0, 0.0, 0, 0, 0, 0],
                                material, 8, 0, seq))
    path.write_text("\n".join(lines) + "\n")


def build(tmp: Path) -> dict:
    lib = tmp / "act58.npz"
    write_library(lib)
    decay = tmp / "dec58.txt"
    write_decay(decay)
    cov = tmp / "cov58.npz"
    write_covariance(cov, lib)
    # the shared writer stamps p53's file name; retarget it to this library
    ci = cov.with_name(cov.stem + "_index.json")
    index = json.loads(ci.read_text())
    index["targets"][0]["file"] = "p58-synthetic.endf"
    fx.write_json(ci, index)
    return {"library": lib, "decay": decay, "covariance": cov}


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        m = build(Path(t))
        print({k: str(v) for k, v in m.items()})
