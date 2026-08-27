#!/usr/bin/env python3
"""P12-G1: strict radiological tables, independent response math and entry-point identity."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

from p11_fixtures import BOUNDS, make_fixture, sha256, specification, write_json


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p12_radiological.json"
TABLE = ROOT / "controls" / "fixtures" / "p12_radiological_table.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PREPARED = Path(
    os.environ.get("ACTINV_PREPARED_PROBE", ROOT / "target" / "release" / "prepared_probe")
)
PYTHON_LIBRARY = Path(
    os.environ.get("ACTINV_PYTHON_LIBRARY", ROOT / "python" / "target" / "release" / "libactinv.so")
)
RELATIVE_LIMIT = 2.0e-15
ABSOLUTE_LIMIT = 1.0e-30


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
    write_json(descriptor, {"fixture": "P12 G1 exact-grid one-cell flux"})
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "p12-g1-control",
                "path": str(descriptor),
                "sha256": sha256(descriptor),
            },
            "energy_boundaries_eV": BOUNDS,
            "flux_units": "n cm^-2 s^-1",
            "cell_count": 1,
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "p12-g1-cell",
            "flux_per_group": [1.0e24],
            "flux_total": 1.0e24,
        },
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": 1.0e24},
    ]
    path.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))


def make_spec(fixture: dict[str, Path], table: Path = TABLE) -> dict:
    value = specification(fixture, mode="trace", cram_order=48, uncertainty=False)
    value["title"] = "P12 G1 radiological response fixture"
    value["schedule"].insert(0, {"dt": "0 s", "flux": 0.0})
    value["options"]["outputs"].append("radiological")
    value["radiological"] = {
        "table": {"path": str(table), "sha256": sha256(table)},
        "responses": [],
        "require_complete": False,
    }
    return value


def independent_response(response: dict, activity: dict[str, float]) -> dict:
    positive = {name: value for name, value in activity.items() if value > 0.0}
    covered_names = sorted(set(positive).intersection(response["coefficients"]))
    missing_names = sorted(set(positive).difference(response["coefficients"]))
    covered = math.fsum(positive[name] for name in covered_names)
    missing = math.fsum(positive[name] for name in missing_names)
    total = math.fsum(positive.values())
    if response["kind"] in ("clearance_index", "waste_index"):
        value = math.fsum(
            1000.0 * positive[name] / response["coefficients"][name]
            for name in covered_names
        )
        unit = "dimensionless"
    else:
        value = math.fsum(
            positive[name] * response["coefficients"][name] for name in covered_names
        )
        unit = "Sv/g_material_intake"
    return {
        "id": response["id"],
        "value": value,
        "unit": unit,
        "covered_activity_Bq_per_g": covered,
        "missing_activity_Bq_per_g": missing,
        "activity_coverage_fraction": covered / total if total > 0.0 else 1.0,
        "contributing_nuclide_count": len(covered_names),
        "missing_active_nuclides": missing_names,
    }


def close(left: float, right: float) -> tuple[bool, float, float]:
    absolute = abs(left - right)
    relative = absolute / max(abs(left), abs(right), float.fromhex("0x1p-1022"))
    return absolute <= ABSOLUTE_LIMIT or relative <= RELATIVE_LIMIT, relative, absolute


def compare_responses(result: dict, table: dict) -> dict:
    response_by_id = {response["id"]: response for response in table["responses"]}
    maximum_relative = 0.0
    maximum_absolute = 0.0
    exact_metadata = True
    comparisons = 0
    all_close = True
    for step in result["steps"]:
        actual_responses = step["radiological"]["responses"]
        if [response["id"] for response in actual_responses] != list(response_by_id):
            exact_metadata = False
        for actual in actual_responses:
            expected = independent_response(
                response_by_id[actual["id"]], step["activity_Bq_per_g"]
            )
            for field in (
                "unit",
                "contributing_nuclide_count",
                "missing_active_nuclides",
            ):
                exact_metadata &= actual[field] == expected[field]
            for field in (
                "value",
                "covered_activity_Bq_per_g",
                "missing_activity_Bq_per_g",
                "activity_coverage_fraction",
            ):
                acceptable, relative, absolute = close(actual[field], expected[field])
                all_close &= acceptable
                maximum_relative = max(maximum_relative, relative)
                maximum_absolute = max(maximum_absolute, absolute)
                comparisons += 1
    return {
        "comparisons": comparisons,
        "maximum_relative": maximum_relative,
        "maximum_absolute": maximum_absolute,
        "metadata_exact": exact_metadata,
        "pass": all_close and exact_metadata,
    }


def run_failure(work: Path, name: str, value: dict) -> dict:
    spec_path = work / f"plant-{name}.json"
    output_path = work / f"plant-{name}.result.json"
    write_json(spec_path, value)
    result = command([ACTINV, "run", spec_path, output_path], ok=False)
    return {
        "returncode": result.returncode,
        "output_published": output_path.exists(),
        "stderr_tail": result.stderr.strip()
        .replace(str(work), "<WORK>")
        .replace(str(ROOT), "<ROOT>")[-500:],
        "pass": result.returncode != 0 and not output_path.exists(),
    }


def table_plant(work: Path, fixture: dict[str, Path], name: str, text: str) -> dict:
    table_path = work / f"table-{name}.json"
    table_path.write_text(text)
    value = make_spec(fixture, table_path)
    return run_failure(work, name, value)


def main() -> None:
    source_table = json.loads(TABLE.read_text())
    with tempfile.TemporaryDirectory(prefix="actinv-p12-g1-") as directory:
        work = Path(directory)
        fixture = make_fixture(work / "fixture")
        spec = make_spec(fixture)
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
            "radiological": spec["radiological"],
            "chunk_cells": 1,
            "threads": 1,
        }
        mesh_spec_path = work / "mesh.json"
        mesh_path = work / "mesh.ndjson"
        write_json(mesh_spec_path, mesh_spec)
        command([ACTINV, "mesh", mesh_spec_path, mesh_path])
        mesh_records = [json.loads(line) for line in mesh_path.read_text().splitlines()]
        mesh = mesh_records[1]["result"]

        entry_points = {
            "cli_vs_python": normalized(cli) == normalized(python),
            "cli_vs_prepared": normalized(cli) == normalized(prepared),
            "cli_vs_mesh": normalized(cli) == normalized(mesh),
            "labels": [
                cli["entry_point"],
                python["entry_point"],
                prepared["entry_point"],
                mesh["entry_point"],
            ],
        }
        independent = compare_responses(cli, source_table)

        certificate = cli["certificate"]
        radiological_certificate = certificate["radiological"]
        input_certificate = certificate["inputs"]["radiological_table"]
        expected_metadata = [
            (
                response["id"],
                response["kind"],
                response["basis"],
                len(response["coefficients"]),
            )
            for response in source_table["responses"]
        ]
        actual_metadata = [
            (
                response["id"],
                response["kind"],
                response["basis"],
                response["coefficient_count"],
            )
            for response in radiological_certificate["responses"]
        ]
        certificate_checks = {
            "declared_hash": input_certificate["sha256_declared"] == sha256(TABLE),
            "computed_hash": input_certificate["sha256"] == sha256(TABLE),
            "path": input_certificate["path"] == str(TABLE),
            "source": radiological_certificate["source"] == source_table["source"],
            "metadata": actual_metadata == expected_metadata,
            "scenario_semantics": "no intake mass" in radiological_certificate["scenario_semantics"],
            "missing_semantics": "never treated as a zero" in radiological_certificate[
                "missing_coefficient_semantics"
            ],
        }

        coverage_ledger = cli["ledger"]["radiological"]["coverage_per_step"]
        coverage_matches = True
        for ledger_step, result_step in zip(coverage_ledger, cli["steps"], strict=True):
            coverage_matches &= ledger_step["step"] == result_step["step"]
            for ledger_response, result_response in zip(
                ledger_step["responses"], result_step["radiological"]["responses"], strict=True
            ):
                coverage_matches &= all(
                    ledger_response[field] == result_response[field]
                    for field in (
                        "id",
                        "covered_activity_Bq_per_g",
                        "missing_activity_Bq_per_g",
                        "activity_coverage_fraction",
                        "contributing_nuclide_count",
                        "missing_active_nuclides",
                    )
                )
        coverage_checks = {
            "ledger_matches_result": coverage_matches,
            "zero_activity_coverage_one": all(
                response["activity_coverage_fraction"] == 1.0
                and response["value"] == 0.0
                and not response["missing_active_nuclides"]
                for response in cli["steps"][0]["radiological"]["responses"]
            ),
            "mixed_missing_reported": all(
                {
                    response["id"]: response["missing_active_nuclides"]
                    for response in step["radiological"]["responses"]
                }["clearance-fixture"]
                == ["Mn57"]
                for step in cli["steps"][1:]
            ),
            "full_coverage_present": all(
                {
                    response["id"]: response["activity_coverage_fraction"]
                    for response in step["radiological"]["responses"]
                }["waste-fixture"]
                == 1.0
                for step in cli["steps"]
            ),
        }

        plants = {}
        value = json.loads(json.dumps(spec))
        value["radiological"]["table"]["sha256"] = "0" * 64
        plants["wrong_hash"] = run_failure(work, "wrong-hash", value)

        value = json.loads(json.dumps(spec))
        value["radiological"]["responses"] = ["not-a-response"]
        plants["unknown_selector"] = run_failure(work, "unknown-selector", value)

        value = json.loads(json.dumps(spec))
        value["radiological"]["responses"] = ["waste-fixture", "waste-fixture"]
        plants["duplicate_selector"] = run_failure(work, "duplicate-selector", value)

        value = json.loads(json.dumps(spec))
        value["radiological"]["require_complete"] = True
        plants["require_complete"] = run_failure(work, "require-complete", value)

        table_mutations = {}
        value = json.loads(json.dumps(source_table))
        value["surprise"] = True
        table_mutations["unknown_field"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["responses"][0]["kind"] = "external_dose"
        table_mutations["unknown_kind"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["responses"][1]["id"] = value["responses"][0]["id"]
        table_mutations["duplicate_id"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["source"]["citation"] = ""
        table_mutations["empty_metadata"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["responses"][0]["coefficients"]["Mn56"] = 0.0
        table_mutations["nonpositive_coefficient"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["responses"][0]["coefficients"] = {"MN-56": 4.0e6}
        table_mutations["malformed_nuclide"] = json.dumps(value)
        value = json.loads(json.dumps(source_table))
        value["responses"][0]["coefficients"] = {}
        table_mutations["empty_coefficients"] = json.dumps(value)
        table_mutations["duplicate_canonical_nuclide"] = TABLE.read_text().replace(
            '"Mn56": 4000000.0', '"Mn56": 4000000.0, "Mn56": 5000000.0', 1
        )
        table_mutations["nonfinite_coefficient"] = TABLE.read_text().replace(
            '"Mn56": 4000000.0', '"Mn56": 1e999', 1
        )
        for name, text in table_mutations.items():
            plants[name] = table_plant(work, fixture, name, text)

        output = {
            "gate": "P12-G1",
            "protocol_sha256": hashlib.sha256(
                (ROOT / "protocols" / "ACTINV-P12_PROTOCOL.md").read_bytes()
            ).hexdigest(),
            "table_sha256": sha256(TABLE),
            "relative_limit": RELATIVE_LIMIT,
            "absolute_limit": ABSOLUTE_LIMIT,
            "steps": len(cli["steps"]),
            "responses_per_step": len(source_table["responses"]),
            "entry_points": entry_points,
            "independent_dense_response": independent,
            "certificate": certificate_checks,
            "coverage": coverage_checks,
            "plants": plants,
        }
        output["pass"] = bool(
            all(entry_points[name] for name in ("cli_vs_python", "cli_vs_prepared", "cli_vs_mesh"))
            and entry_points["labels"] == ["cli", "python", "prepared", "mesh"]
            and independent["pass"]
            and all(certificate_checks.values())
            and all(coverage_checks.values())
            and all(plant["pass"] for plant in plants.values())
        )
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
