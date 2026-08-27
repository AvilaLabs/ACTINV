#!/usr/bin/env python3
"""P11-G5: uncertainty identity through CLI/PyO3/prepared/mesh and fail-closed plants."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from p11_fixtures import make_fixture, sha256, specification, write_json


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g5_p11_entry_points.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PREPARED = Path(
    os.environ.get("ACTINV_PREPARED_PROBE", ROOT / "target" / "release" / "prepared_probe")
)
PYTHON_LIBRARY = Path(
    os.environ.get("ACTINV_PYTHON_LIBRARY", ROOT / "python" / "target" / "release" / "libactinv.so")
)


def command(arguments, *, ok=True, timeout=180):
    result = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if ok != (result.returncode == 0):
        raise RuntimeError(
            f"unexpected command result {result.returncode}: {' '.join(map(str, arguments))}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result


def normalized(value: dict) -> dict:
    value = json.loads(json.dumps(value))
    value.pop("ms", None)
    value.pop("entry_point", None)
    value.get("certificate", {}).pop("entry_point", None)
    return value


def canonical_flux(path: Path) -> None:
    descriptor = path.with_suffix(".source.json")
    write_json(descriptor, {"fixture": "P11 exact-grid one-cell flux"})
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {"format": "p11-control", "path": str(descriptor), "sha256": sha256(descriptor)},
            "energy_boundaries_eV": [1.0, 3.0],
            "flux_units": "n cm^-2 s^-1",
            "cell_count": 1,
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "p11-cell",
            "flux_per_group": [1.0e24],
            "flux_total": 1.0e24,
        },
        {
            "record": "footer",
            "cell_count": 1,
            "flux_sum_over_cells": 1.0e24,
        },
    ]
    path.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))


def copy_covariance(source: Path, destination: Path, edit) -> None:
    shutil.copy2(source, destination)
    source_index = source.with_name(source.stem + "_index.json")
    destination_index = destination.with_name(destination.stem + "_index.json")
    value = json.loads(source_index.read_text())
    edit(value)
    write_json(destination_index, value)


def run_failure(work: Path, name: str, value: dict) -> dict:
    spec_path, output_path = work / f"plant-{name}.json", work / f"plant-{name}.result.json"
    write_json(spec_path, value)
    result = command([ACTINV, "run", spec_path, output_path], ok=False)
    return {
        "returncode": result.returncode,
        "output_published": output_path.exists(),
        "stderr_tail": result.stderr.strip().replace(str(work), "<WORK>")[-500:],
        "pass": result.returncode != 0 and not output_path.exists(),
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-p11-g5-") as directory:
        work = Path(directory)
        fixture = make_fixture(work / "fixture")
        spec = specification(fixture, mode="trace", cram_order=48)
        spec_path = work / "ordinary.json"
        write_json(spec_path, spec)

        cli_path = work / "cli.json"
        command([ACTINV, "run", spec_path, cli_path])
        cli = json.loads(cli_path.read_text())

        module_spec = importlib.util.spec_from_file_location("actinv", PYTHON_LIBRARY)
        if module_spec is None or module_spec.loader is None:
            raise RuntimeError(f"cannot load Python extension {PYTHON_LIBRARY}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        python = json.loads(module.run(spec_path.read_text()))

        prepared_path = work / "prepared.json"
        command([PREPARED, spec_path, prepared_path])
        prepared = json.loads(prepared_path.read_text())

        flux = work / "flux.ndjson"
        canonical_flux(flux)
        mesh_spec = {
            "spec": "actinv-mesh-spec-1",
            "title": spec["title"],
            "library": spec["library"],
            "decay": spec["decay"],
            "material": spec["material"],
            "flux": {"path": str(flux), "sha256": sha256(flux)},
            "schedule": spec["schedule"],
            "options": spec["options"],
            "uncertainty": spec["uncertainty"],
            "chunk_cells": 1,
            "threads": 1,
        }
        mesh_spec_path, mesh_path = work / "mesh.json", work / "mesh.ndjson"
        write_json(mesh_spec_path, mesh_spec)
        command([ACTINV, "mesh", mesh_spec_path, mesh_path])
        mesh_records = [json.loads(line) for line in mesh_path.read_text().splitlines()]
        mesh = mesh_records[1]["result"]

        entry_points = {
            "cli_vs_python": normalized(cli) == normalized(python),
            "cli_vs_prepared": normalized(cli) == normalized(prepared),
            "cli_vs_mesh": normalized(cli) == normalized(mesh),
            "labels": [cli["entry_point"], python["entry_point"], prepared["entry_point"], mesh["entry_point"]],
        }

        covariance_certificate = cli["certificate"]["inputs"]["covariance"]
        hashes = {
            "library": sha256(fixture["library"]) == cli["certificate"]["inputs"]["library"]["sha256"],
            "library_index": sha256(fixture["library"].with_name(fixture["library"].stem + "_index.json"))
            == cli["certificate"]["inputs"]["library_index"]["sha256"],
            "covariance": sha256(fixture["covariance"]) == covariance_certificate["sha256"],
            "covariance_index": sha256(fixture["covariance"].with_name(fixture["covariance"].stem + "_index.json"))
            == covariance_certificate["index"]["sha256"],
            "decay": sha256(fixture["decay"]) == cli["certificate"]["inputs"]["decay_primary"]["sha256"],
        }

        plants = {}
        value = json.loads(json.dumps(spec))
        value["uncertainty"]["covariance"]["sha256"] = "0" * 64
        plants["wrong_covariance_hash"] = run_failure(work, "wrong-hash", value)

        for name, edit in {
            "projectile_mismatch": lambda index: index.__setitem__("projectile", "proton"),
            "group_mismatch": lambda index: index.__setitem__("group_boundary_sha256", "0" * 64),
            "target_mismatch": lambda index: index["targets"][0].__setitem__("za", 26057),
            "source_mismatch": lambda index: index["targets"][0].__setitem__("source_sha256", "0" * 64),
        }.items():
            covariance = work / f"{name}.npz"
            copy_covariance(fixture["covariance"], covariance, edit)
            value = json.loads(json.dumps(spec))
            value["uncertainty"]["covariance"] = {"path": str(covariance), "sha256": sha256(covariance)}
            plants[name] = run_failure(work, name, value)

        for name, edit in {
            "duplicate_selector": lambda value: value["uncertainty"].__setitem__("responses", ["heat.total", "heat.total"]),
            "bad_confidence": lambda value: value["uncertainty"].__setitem__("confidence_level", 1.0),
            "unknown_nuclide": lambda value: value["uncertainty"].__setitem__("responses", ["activity:NoSuch999"]),
            "unknown_response": lambda value: value["uncertainty"].__setitem__("responses", ["dose.total"]),
        }.items():
            value = json.loads(json.dumps(spec))
            edit(value)
            plants[name] = run_failure(work, name, value)

        uncovered = work / "uncovered.npz"
        with np.load(fixture["covariance"], allow_pickle=False) as archive:
            np.savez(
                uncovered,
                components=np.asarray(archive["components"][:2]),
                grid_offsets=np.asarray(archive["grid_offsets"]),
                grid_values=np.asarray(archive["grid_values"]),
                values=np.asarray(archive["values"][:2]),
            )
        original_index = json.loads(
            fixture["covariance"].with_name(fixture["covariance"].stem + "_index.json").read_text()
        )
        original_index["sha256_npz"] = sha256(uncovered)
        original_index["components"] = 2
        original_index["mf33_sections"] = 1
        original_index["lb_counts"] = {"0": 2}
        original_index["targets"][0]["components"] = 2
        original_index["targets"][0]["mf33_sections"] = 1
        original_index["targets"][0]["lb_counts"] = {"0": 2}
        write_json(uncovered.with_name(uncovered.stem + "_index.json"), original_index)
        value = json.loads(json.dumps(spec))
        value["uncertainty"]["covariance"] = {"path": str(uncovered), "sha256": sha256(uncovered)}
        plants["require_complete_uncovered"] = run_failure(work, "uncovered", value)

        responses = [
            response
            for step in cli["steps"]
            for response in step["uncertainty"]["responses"].values()
        ]
        reports_complete = bool(
            responses
            and all(
                all(
                    key in response
                    for key in (
                        "nominal",
                        "mf33_standard_uncertainty",
                        "normal_interval",
                        "cram_order_bound",
                        "conservative_interval",
                        "coverage",
                        "sensitivities",
                    )
                )
                for response in responses
            )
        )
        output = {
            "entry_points": entry_points,
            "hashes_rederived": hashes,
            "response_records": len(responses),
            "reports_complete": reports_complete,
            "plants": plants,
        }
        output["pass"] = bool(
            all(entry_points[name] for name in ("cli_vs_python", "cli_vs_prepared", "cli_vs_mesh"))
            and entry_points["labels"] == ["cli", "python", "prepared", "mesh"]
            and all(hashes.values())
            and reports_complete
            and all(item["pass"] for item in plants.values())
        )
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
