#!/usr/bin/env python3
"""P94 G1 runtime contract: a synthetic one-step gamma activation through the CLI, PyO3 and
prepared mesh paths.

The case is P10-G6's charged-projectile case run with `projectile: "gamma"`. The synthetic
library carries Fe-56 -> Mn-56 at 4 b. Mn-56 decays back to Fe-56, a closed two-state system
with an analytic solution. The mesh route reads a one-cell canonical flux carrying the photon
particle label that `import-flux openmc` writes for a photon tally. All three results must be
identical apart from timing and entry-point labels. The parent loss and product feed must match
the analytic solution to 2e-12, as in P10-G6. P10-G6's own control and results are not touched;
its helpers are imported read-only.

    ACTINV_PYTHON_LIBRARY=python/target/release/libactinv.so python3 controls/p94_g1_gamma_runtime.py

Writes target/p94/g1_gamma_runtime.json and exits nonzero on any failure.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_WORK = ROOT / "target" / os.environ.get("ACTINV_GAMMA_PROTOCOL", "P94").lower()
OUT = _WORK / "g1_gamma_runtime.json"
os.environ["ACTINV_P10_WORK"] = str(_WORK / "g1_runtime")
sys.path.insert(0, str(ROOT / "controls"))

import g6_p10_projectile_runtime as p10  # noqa: E402
from p9_fixtures import base_spec, make_fixture, sha256, write_json  # noqa: E402


def write_photon_flux(path: Path, source: Path, total: float) -> None:
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "synthetic",
                "path": str(source),
                "sha256": sha256(source),
                "metadata": {"particle": "photon"},
            },
            "energy_boundaries_eV": [1.0, 3.0],
            "flux_units": "particles cm^-2 s^-1",
            "cell_count": 1,
        },
        {"record": "cell", "ordinal": 0, "id": "cell-0", "flux_per_group": [total], "flux_total": total},
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": total},
    ]
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records))


def load_extension():
    requested = os.environ.get("ACTINV_PYTHON_LIBRARY", str(ROOT / "python/target/release/libactinv.so"))
    module_spec = importlib.util.spec_from_file_location("actinv", requested)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load Python extension {requested}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module, requested


def main() -> int:
    work = p10.WORK
    work.mkdir(parents=True, exist_ok=True)
    initial_atoms, flux, duration = 2.5e20, 1.0e20, 2.0
    projectile = "gamma"

    fixture = make_fixture(work / projectile)
    index = p10.charged_index(json.loads(fixture["index"].read_text()), projectile)
    write_json(fixture["index"], index)
    spec = base_spec(
        fixture,
        composition={"FE56": initial_atoms},
        basis="atoms_per_g",
        schedule=[{"dt": f"{duration} s", "flux": 1.0}],
        mode="coupled",
        total_flux=flux,
    )
    spec["title"] = "P94 G1 synthetic gamma"
    spec["projectile"] = projectile
    spec["options"]["temperature_K"] = 0.0
    spec["fission_yields"] = {"files": [], "energy": "spectrum_average"}

    actinv, extension = load_extension()
    cli, _ = p10.run_cli(f"{projectile}-cli", spec)
    python_result = json.loads(actinv.run(json.dumps(spec)))

    source = work / "synthetic-photon-transport-source.txt"
    source.write_text("P94 deterministic photon transport source\n")
    canonical_flux = work / "photon-flux.ndjson"
    write_photon_flux(canonical_flux, source, flux)
    mesh_spec = {
        "spec": "actinv-mesh-spec-1",
        "title": spec["title"],
        "projectile": projectile,
        "library": spec["library"],
        "decay": spec["decay"],
        "material": spec["material"],
        "flux": {"path": str(canonical_flux), "sha256": sha256(canonical_flux)},
        "schedule": spec["schedule"],
        "options": spec["options"],
        "fission_yields": spec["fission_yields"],
        "chunk_cells": 1,
        "threads": 1,
    }
    mesh_spec_path = work / f"{projectile}-mesh.json"
    mesh_result_path = work / f"{projectile}-mesh.ndjson"
    write_json(mesh_spec_path, mesh_spec)
    p10.command([p10.BIN, "mesh", mesh_spec_path, mesh_result_path])
    records = [json.loads(line) for line in mesh_result_path.read_text().splitlines()]
    mesh_header, mesh_result = records[0], records[1]["result"]

    identical = (p10.normalized(cli) == p10.normalized(python_result)
                 and p10.normalized(cli) == p10.normalized(mesh_result))
    certificates = {label: p10.checked_certificate(result, projectile, f"{projectile}-{label}")
                    for label, result in (("cli", cli), ("python", python_result),
                                          ("prepared_mesh", mesh_result))}
    mesh_certificate = p10.checked_mesh_certificate(mesh_header, projectile, canonical_flux, source)

    step = cli["steps"][0]
    fluence_ok = "fluence_n_cm2" not in step and step.get("fluence_particles_cm2") == flux * duration
    inventory = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
    rate = 4.0e-24 * flux
    decay = math.log(2.0) / 100.0
    expected_product = initial_atoms * rate / (rate + decay) * (1.0 - math.exp(-(rate + decay) * duration))
    expected_parent = initial_atoms - expected_product
    analytic = max(abs(inventory["Fe56"] - expected_parent) / expected_parent,
                   abs(inventory["Mn56"] - expected_product) / expected_product)

    out = {
        "projectile": projectile,
        "binary_sha256": sha256(p10.BIN),
        "python_extension": extension,
        "python_extension_sha256": sha256(Path(extension)),
        "entry_points_identical": identical,
        "certificates": certificates,
        "mesh_certificate": mesh_certificate,
        "fluence_particles_cm2_ok": fluence_ok,
        "analytic_relative_error": analytic,
        "analytic_tolerance": 2.0e-12,
    }
    out["pass"] = identical and fluence_ok and analytic <= 2.0e-12
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in ("entry_points_identical", "fluence_particles_cm2_ok",
                                          "analytic_relative_error", "pass")}, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
