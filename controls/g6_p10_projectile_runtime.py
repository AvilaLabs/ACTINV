#!/usr/bin/env python3
"""P10-G6: charged-projectile identity across ordinary, Python and prepared mesh runs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

from p9_fixtures import base_spec, command, make_fixture, sha256, write_json  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
WORK = Path(os.environ.get("ACTINV_P10_WORK", "/tmp/actinv-p10")) / "g6"


def boundary_hash(boundaries: list[float]) -> str:
    digest = hashlib.sha256(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def normalized(result: dict) -> dict:
    value = copy.deepcopy(result)
    value.pop("ms", None)
    value["entry_point"] = "normalized"
    value["certificate"]["entry_point"] = "normalized"
    return value


def run_cli(name: str, spec: dict) -> tuple[dict, Path]:
    spec_path = WORK / f"{name}.json"
    result_path = WORK / f"{name}.result.json"
    write_json(spec_path, spec)
    command([BIN, "run", spec_path, result_path])
    return json.loads(result_path.read_text()), spec_path


def expect_failure(name: str, spec: dict, needle: str, action: str = "run") -> None:
    path = WORK / f"reject-{name}.json"
    write_json(path, spec)
    result = command([BIN, action, path], ok=False)
    message = result.stdout + result.stderr
    if needle not in message:
        raise AssertionError(f"{name}: expected {needle!r}, got {message!r}")


def write_flux(path: Path, source: Path, total: float) -> None:
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "synthetic",
                "path": str(source),
                "sha256": sha256(source),
            },
            "energy_boundaries_eV": [1.0, 3.0],
            "flux_units": "particles cm^-2 s^-1",
            "cell_count": 1,
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "cell-0",
            "flux_per_group": [total],
            "flux_total": total,
        },
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": total},
    ]
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records))


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    fixture = make_fixture(WORK)
    initial_atoms = 2.5e20
    flux = 1.0e20
    duration = 2.0

    neutron_spec = base_spec(
        fixture,
        composition={"FE56": initial_atoms},
        basis="atoms_per_g",
        schedule=[{"dt": f"{duration} s", "flux": 1.0}],
        mode="coupled",
        total_flux=flux,
    )
    neutron_spec["fission_yields"] = {"files": [], "energy": "spectrum_average"}
    neutron, _ = run_cli("legacy-neutron", neutron_spec)
    if "projectile" in neutron or "projectile" in neutron["ledger"] or "projectile" in neutron["certificate"]:
        raise AssertionError("legacy neutron result gained a serialized projectile field")
    if "fluence_n_cm2" not in neutron["steps"][0] or "fluence_particles_cm2" in neutron["steps"][0]:
        raise AssertionError("legacy neutron fluence schema changed")

    source = WORK / "synthetic-transport-source.txt"
    source.write_text("P10 deterministic charged-particle transport source\n")
    canonical_flux = WORK / "charged-flux.ndjson"
    write_flux(canonical_flux, source, flux)

    try:
        import actinv
    except ImportError as error:
        raise RuntimeError("P10-G6 requires the built PyO3 actinv module") from error

    index_path = fixture["index"]
    base_index = json.loads(index_path.read_text())
    analytic_errors: dict[str, float] = {}
    entry_point_identity: dict[str, bool] = {}

    for projectile in ("proton", "deuteron", "alpha"):
        index = copy.deepcopy(base_index)
        index.update(
            {
                "projectile": projectile,
                "temperature_K": 0.0,
                "groups": "synthetic-1",
                "group_boundary_sha256": boundary_hash([1.0, 3.0]),
            }
        )
        write_json(index_path, index)

        spec = copy.deepcopy(neutron_spec)
        spec["title"] = f"P10 G6 synthetic {projectile}"
        spec["projectile"] = projectile
        spec["options"]["temperature_K"] = 0.0
        cli, _ = run_cli(f"{projectile}-cli", spec)
        python_result = json.loads(actinv.run(json.dumps(spec)))

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
        mesh_spec_path = WORK / f"{projectile}-mesh.json"
        mesh_result_path = WORK / f"{projectile}-mesh.ndjson"
        write_json(mesh_spec_path, mesh_spec)
        command([BIN, "mesh", mesh_spec_path, mesh_result_path])
        mesh_records = [json.loads(line) for line in mesh_result_path.read_text().splitlines()]
        mesh_header, mesh_cell = mesh_records[0], mesh_records[1]
        mesh_result = mesh_cell["result"]

        if normalized(cli) != normalized(python_result) or normalized(cli) != normalized(mesh_result):
            raise AssertionError(f"{projectile}: CLI, PyO3 and prepared mesh results differ")
        entry_point_identity[projectile] = True

        if cli.get("projectile") != projectile:
            raise AssertionError(f"{projectile}: result projectile missing")
        if cli["ledger"].get("projectile") != projectile:
            raise AssertionError(f"{projectile}: ledger projectile missing")
        if cli["certificate"].get("projectile") != projectile:
            raise AssertionError(f"{projectile}: certificate projectile missing")
        step = cli["steps"][0]
        if "fluence_n_cm2" in step or step.get("fluence_particles_cm2") != flux * duration:
            raise AssertionError(f"{projectile}: charged fluence schema/value is wrong")
        if mesh_header.get("projectile") != projectile or mesh_header["flux_units"] != "particles cm^-2 s^-1":
            raise AssertionError(f"{projectile}: mesh projectile header is wrong")
        if mesh_header["certificate"].get("projectile") != projectile:
            raise AssertionError(f"{projectile}: mesh certificate projectile missing")
        if mesh_header["certificate"]["upstream_source"]["sha256"] != sha256(source):
            raise AssertionError(f"{projectile}: upstream source hash changed")

        inventory = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
        rate = 4.0e-24 * flux
        decay = math.log(2.0) / 100.0
        # Synthetic Mn-56 beta decay returns to Fe-56, making a closed two-state system.
        expected_product = (
            initial_atoms
            * rate
            / (rate + decay)
            * (1.0 - math.exp(-(rate + decay) * duration))
        )
        expected_parent = initial_atoms - expected_product
        relative = max(
            abs(inventory["Fe56"] - expected_parent) / expected_parent,
            abs(inventory["Mn56"] - expected_product) / expected_product,
        )
        analytic_errors[projectile] = relative
        if relative > 2.0e-12:
            raise AssertionError(f"{projectile}: analytic inventory error {relative:.3e}")

    charged = copy.deepcopy(spec)
    charged["projectile"] = "proton"
    write_json(
        index_path,
        {
            **base_index,
            "projectile": "proton",
            "temperature_K": 0.0,
            "groups": "synthetic-1",
            "group_boundary_sha256": boundary_hash([1.0, 3.0]),
        },
    )
    mismatch = copy.deepcopy(charged)
    mismatch["projectile"] = "deuteron"
    expect_failure("projectile-mismatch", mismatch, "does not match activation-library projectile")
    hot = copy.deepcopy(charged)
    hot["options"]["temperature_K"] = 293.6
    expect_failure("charged-temperature", hot, "temperature_K: 0", "validate")
    fission = copy.deepcopy(charged)
    fission["fission_yields"] = {
        "files": [{"path": "unused.endf", "sha256": "0" * 64}],
        "energy": "spectrum_average",
    }
    expect_failure("charged-fission", fission, "not supported for proton", "validate")
    unknown = copy.deepcopy(charged)
    unknown["projectile"] = "muon"
    expect_failure("unknown-projectile", unknown, "unknown variant", "validate")

    report = {
        "gate": "P10-G6",
        "projectiles": list(analytic_errors),
        "max_analytic_relative_error": max(analytic_errors.values()),
        "entry_point_identity": entry_point_identity,
        "legacy_neutron_schema_unchanged": True,
        "source_sha256": sha256(source),
        "canonical_flux_sha256": sha256(canonical_flux),
        "pass": True,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
