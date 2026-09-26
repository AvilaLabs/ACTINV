#!/usr/bin/env python3
"""P53 fixture — 2-group synthetic activation library + covariance sidecar
+ decay file WITH a gamma spectrum on Mn57 (the P11 decay fixture carries
only MT=457 energy totals; the photon source needs discrete styp=0 lines).

Layout mirrors controls/p11_fixtures.py; covariance components are LB=0
absolute on the full 2-group grid.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import p11_fixtures as fx

BOUNDS = [1.0, 2.0, 3.0]
ROWS = fx.ROWS
# (mt102 base, mt102, mt103 base, mt103) — group-dependent sigmas so
# differently-shaped spectra collapse to different weights.
SIGMA = np.asarray(
    [[0.20, 0.30],
     [0.20, 0.30],
     [0.10, 0.02],
     [0.10, 0.02]],
    dtype=np.float64)

COV_MATS = {
    (102, 102): np.asarray([[4.0e-4, 1.0e-4], [1.0e-4, 3.0e-4]]),
    (102, 103): np.asarray([[1.5e-4, 0.5e-4], [0.8e-4, 1.0e-4]]),
    (103, 103): np.asarray([[9.0e-4, 2.0e-4], [2.0e-4, 5.0e-4]]),
}
DEFECT_COV = dict(COV_MATS)
DEFECT_COV[(103, 103)] = np.asarray([[-9.0e-4, 2.0e-4],
                                     [2.0e-4, -5.0e-4]])


def write_decay(path: Path) -> None:
    """Fe56 stable; Mn56 (t½=2s, no photon lines); Mn57 (t½=3s) with a
    two-line gamma spectrum at 0.8 MeV and 1.4 MeV."""
    lines = []
    nuclides = [
        (26056, 55.454, True, 0.0, (0.0, 0.0, 0.0), []),
        (25056, 55.460, False, 2.0, (1.0e6, 2.0e6, 0.5e6), []),
        (25057, 56.450, False, 3.0, (0.7e6, 1.1e6, 0.2e6),
         [(0.8e6, 1.0), (1.4e6, 0.5)]),
    ]
    for material, (za, awr, stable, t_half, energies, gammas) in \
            enumerate(nuclides, 100):
        seq = 1
        lines.append(fx._record(
            [float(za), awr, 0, 0, int(stable), 1 if gammas else 0],
            material, 8, 457, seq))
        seq += 1
        lines.append(fx._record(
            [t_half, 0.0, 0, 0, 0 if stable else 6, 0],
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
            # spectrum LIST: head [c1, styp, lcon, lcov, npl, ner],
            # values [fd, dfd, erav, derav, fc, dfc]
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


def write_library(path: Path) -> Path:
    np.savez(path, rows=ROWS, sig=SIGMA,
             bounds=np.asarray(BOUNDS, dtype=np.float64))
    index = path.with_name(path.stem + "_index.json")
    fx.write_json(index, {
        "schema": "actinv-library-index-1",
        "projectile": "neutron",
        "groups": "custom",
        "group_boundary_sha256": fx.group_hash(BOUNDS),
        "temperature_K": 293.6,
        "sha256_npz": fx.sha256(path),
        "targets": [{
            "file": "p53-synthetic.endf", "source_sha256": "1" * 64,
            "mat": 2631, "za": 26056, "liso": 0, "awr": 55.454,
            "ledger": []}],
    })
    return index


def write_covariance(path: Path, activation: Path, mats=None) -> Path:
    mats = COV_MATS if mats is None else mats
    grid_values = np.asarray(BOUNDS, dtype=np.float64)
    components, values, offset = [], [], 0
    for (mt, mt1), m in mats.items():
        flat = np.asarray(m, dtype=np.float64).reshape(-1)
        components.append([0, mt, mt1, 0, 0, 0, 0, offset, len(flat)])
        values.extend(flat.tolist())
        offset += len(flat)
    np.savez(
        path,
        components=np.asarray(components, dtype=np.int64),
        grid_offsets=np.asarray([0, len(grid_values)], dtype=np.int64),
        grid_values=grid_values,
        values=np.asarray(values, dtype=np.float64),
    )
    activation_index = activation.with_name(activation.stem + "_index.json")
    fx.write_json(path.with_name(path.stem + "_index.json"), {
        "schema": "actinv-covariance-index-1",
        "projectile": "neutron",
        "activation_library": str(activation),
        "activation_library_sha256": fx.sha256(activation),
        "activation_index": str(activation_index),
        "activation_index_sha256": fx.sha256(activation_index),
        "group_boundary_sha256": fx.group_hash(BOUNDS),
        "builder_fingerprint": "2" * 64,
        "source_manifest_sha256": "3" * 64,
        "targets": [{
            "target": 0, "file": "p53-synthetic.endf",
            "source_sha256": "1" * 64, "mat": 2631, "za": 26056,
            "liso": 0, "mf33_sections": 2, "components": 3,
            "lb_counts": {"0": 3}}],
        "files": 1, "files_with_mf33": 1, "mf33_sections": 2,
        "components": 3, "lb_counts": {"0": 3},
        "columns": "synthetic control",
        "sha256_npz": fx.sha256(path),
    })
    return path


def make_fixture(work: Path, mats=None) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    decay = work / "p53-decay.endf"
    library = work / "p53-activation.npz"
    covariance = work / "p53-covariance.npz"
    write_decay(decay)
    write_library(library)
    write_covariance(covariance, library, mats)
    return {"decay": decay, "library": library, "covariance": covariance}


def mesh_spec(fxmap: dict, flux_path: Path,
              uncertainty: bool = True) -> dict:
    spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "P53 synthetic two-cell banded mesh",
        "projectile": "neutron",
        "library": {"path": str(fxmap["library"]),
                    "sha256": fx.sha256(fxmap["library"])},
        "decay": {"primary": str(fxmap["decay"])},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {"Fe56": 1.0}},
        "schedule": [{"dt": "0.7 s", "flux": 1.0},
                     {"dt": "0.2 s", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "none",
                    "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "cram_order": 16,
                    "outputs": ["inventory", "activity", "heat",
                                "photons", "ledger", "certificate"]},
        "flux": {"path": str(flux_path),
                 "sha256": fx.sha256(flux_path)},
        "threads": 1,
    }
    if uncertainty:
        spec["uncertainty"] = {
            "covariance": {"path": str(fxmap["covariance"]),
                           "sha256": fx.sha256(fxmap["covariance"])},
            "responses": ["activity:Mn56", "activity:Mn57"],
            "confidence_level": 0.95,
            "require_complete": True}
    return spec


def write_flux(path: Path, cells: list[list[float]]) -> None:
    flux_sum = sum(sum(c) for c in cells)
    with path.open("w") as fh:
        fh.write(json.dumps({
            "record": "header", "schema": "actinv-flux-1",
            "source": {"format": "p53-fixture", "path": "synthetic",
                       "sha256": "0" * 64, "metadata": {}},
            "energy_boundaries_eV": BOUNDS,
            "flux_units": "n cm^-2 s^-1",
            "cell_count": len(cells)}) + "\n")
        for i, cell in enumerate(cells):
            fh.write(json.dumps({
                "record": "cell", "ordinal": i, "id": f"cell-{i}",
                "bounds_cm": [[i, i + 1], [0, 1], [0, 1]],
                "volume_cm3": 1.0,
                "flux_per_group": cell,
                "flux_total": sum(cell)}) + "\n")
        fh.write(json.dumps({"record": "footer",
                             "cell_count": len(cells),
                             "flux_sum_over_cells": flux_sum,
                             "volume_integrated_flux": flux_sum}) + "\n")
