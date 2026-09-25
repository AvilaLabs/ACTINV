#!/usr/bin/env python3
"""P52 G3 — executed multi-cell banded demonstration: 8 cells, distinct
FNS-perturbed spectra (deterministic modulation, P45 recipe), pure Fe,
MF=33 banded, cell_result_fields pruned to the fields the interchange
consumes; then `actinv export-r2s` emits actinv-r2s-source-1 for the final
cooling step. Wall time ledgered with hardware.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p52_artifacts as p52a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g3_p52_demo.json"
R2S = ROOT / "results/p52_r2s_source.ndjson"
MESH_OUT = ROOT / "results/p52_mesh.ndjson"
N_CELLS = 8
EMIT_STEP = 4  # final cooling step of 1 irr + 3 cool
MAX_UNBANDED_SHARE = 0.05


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def build_flux(work: Path) -> Path:
    """8 cells, cell i: fns*(1+0.5*sin(2π·(i+1)·g/709 + (i+1)*0.7)) rescaled
    to the fns total (P45 modulation recipe, deterministic)."""
    fns = json.loads((ROOT / "examples/fns_fe_5min.json")
                     .read_text())["spectrum"]["flux_per_group"]
    total = sum(fns)
    bounds_desc = json.loads(
        (ROOT / "crates/actinv-data/data/fispact_709_groups.json")
        .read_text())["boundaries_eV"]
    bounds_asc = list(reversed(bounds_desc))
    path = work / "p52_flux.ndjson"
    with path.open("w") as fh:
        fh.write(json.dumps({
            "record": "header", "schema": "actinv-flux-1",
            "source": {"format": "actinv-spec-1",
                       "path": "examples/fns_fe_5min.json",
                       "sha256": sha256_file(
                           ROOT / "examples/fns_fe_5min.json"),
                       "metadata": {"recipe": "fns*(1+0.5*sin(2*pi*(i+1)*g/"
                                              "709+(i+1)*0.7)) per cell"}},
            "energy_boundaries_eV": bounds_asc,
            "flux_units": "n cm^-2 s^-1", "cell_count": N_CELLS}) + "\n")
        flux_sum = 0.0
        for i in range(N_CELLS):
            shape = [fns[g] * (1.0 + 0.5 * math.sin(
                2 * math.pi * (i + 1) * g / len(fns) + (i + 1) * 0.7))
                for g in range(len(fns))]
            shape = [v * total / sum(shape) for v in shape]
            flux_sum += sum(shape)
            # cells spaced 1 cm apart along x so bounds are real
            fh.write(json.dumps({
                "record": "cell", "ordinal": i, "id": f"cell-{i}",
                "index": [i, 0, 0],
                "bounds_cm": [[i, i + 1], [0, 1], [0, 1]],
                "volume_cm3": 1.0,
                "flux_per_group": list(reversed(shape)),
                "flux_total": sum(shape)}) + "\n")
        fh.write(json.dumps({"record": "footer", "cell_count": N_CELLS,
                             "flux_sum_over_cells": flux_sum,
                             "volume_integrated_flux": flux_sum}) + "\n")
    return path


def build_spec(flux_path: Path) -> dict:
    lib = ROOT / "actinv-data/v1.1.0/activation/" \
        "tendl-2025-patched-neutron-709g.npz"
    cov = ROOT / "actinv-data/v1.1.0/uncertainty/" \
        "tendl-2025-neutron-709g.cov.npz"
    return {
        "spec": "actinv-mesh-spec-1",
        "title": "P52 demo: 8-cell Fe banded mesh",
        "projectile": "neutron",
        "library": {"path": str(lib), "sha256": sha256_file(lib)},
        "decay": {"primary": "catalog:endfb-viii-0-decay",
                  "fallback": "catalog:jeff-3-3-decay"},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {"FE": 100.0}},
        "schedule": ([{"dt": "300 s", "flux": 1.0}]
                     + [{"dt": "86400 s", "flux": 0.0},
                        {"dt": "2592000 s", "flux": 0.0},
                        {"dt": "31536000 s", "flux": 0.0}]),
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6},
        "uncertainty": {
            "covariance": {"path": str(cov), "sha256": sha256_file(cov)},
            "responses": ["activity:*"],
            "confidence_level": 0.95,
            "require_complete": False},
        "flux": {"path": str(flux_path),
                 "sha256": sha256_file(flux_path)},
        "threads": 2,
        "cell_result_fields": ["steps", "mode", "pruned_states",
                               "total_states", "entry_point"],
    }


def main() -> int:
    problems = []
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="p52-g3-", dir="target") as d:
        work = Path(d)
        flux_path = build_flux(work)
        spec = build_spec(flux_path)
        spec_path = work / "p52_mesh_spec.json"
        spec_path.write_text(json.dumps(spec, indent=1))

        tm = time.monotonic()
        r = subprocess.run([str(BIN), "mesh", str(spec_path),
                            str(MESH_OUT)],
                           capture_output=True, text=True, timeout=3000)
        mesh_wall = time.monotonic() - tm
        if r.returncode != 0:
            problems.append(f"actinv mesh failed: {r.stderr[-1500:]}")
            result = {"pass": False, "problems": problems}
            OUT.write_text(json.dumps(result, indent=2))
            return 1

        te = time.monotonic()
        r = subprocess.run([str(BIN), "export-r2s", str(MESH_OUT),
                            str(EMIT_STEP), str(R2S)],
                           capture_output=True, text=True, timeout=600)
        export_wall = time.monotonic() - te
        if r.returncode != 0:
            problems.append(f"export-r2s failed: {r.stderr[-1500:]}")

        # audit the emitted document
        cell_records = []
        footer = None
        if not problems:
            for line in R2S.read_text().splitlines():
                rec = json.loads(line)
                if rec["record"] == "cell":
                    cell_records.append(rec)
                elif rec["record"] == "footer":
                    footer = rec
            if len(cell_records) != N_CELLS:
                problems.append(
                    f"r2s doc has {len(cell_records)} cells, want {N_CELLS}")
            unbanded_max = max(
                (c["coverage"]["unbanded_photon_share"]
                 for c in cell_records), default=1.0)
            if unbanded_max > MAX_UNBANDED_SHARE:
                problems.append(
                    f"max unbanded_photon_share {unbanded_max:.3f} "
                    f"> {MAX_UNBANDED_SHARE}")
            for c in cell_records:
                if c["sigma_photons_s_independent"] is not None \
                        and c["sigma_photons_s_independent"] <= 0:
                    problems.append(
                        f"cell {c['id']}: non-positive sigma_independent")
            if footer is None or footer["cell_count"] != N_CELLS:
                problems.append("r2s footer missing or wrong cell_count")

        result = {
            "pass": not problems,
            "cells": N_CELLS,
            "mesh_wall_s": mesh_wall,
            "export_wall_s": export_wall,
            "total_wall_s": time.monotonic() - t0,
            "mesh_out_sha256": sha256_file(MESH_OUT),
            "r2s_sha256": sha256_file(R2S) if R2S.exists() else None,
            "mesh_bytes": MESH_OUT.stat().st_size,
            "hardware": {"cpu": platform.processor() or platform.machine(),
                         "cell_emit_step": EMIT_STEP},
            "problems": problems,
        }
        OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"pass": result["pass"],
                          "mesh_wall_s": mesh_wall,
                          "problems": problems[:6]}))
        return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
