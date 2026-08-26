#!/usr/bin/env python3
"""P8-G3: independent checks of the supported MCNP meshtal and mctal subsets."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from p8_fixtures import (
    BIN,
    BOUNDARIES_EV,
    RELERR,
    SOURCE_RATE,
    command,
    import_arguments,
    make_mctal,
    make_meshtal,
    read_ndjson,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def independent_meshtal(path: Path):
    lines = path.read_text().splitlines()
    energy_line = next(line for line in lines if "Energy bin boundaries:" in line)
    raw_bounds = [float(value) * 1e6 for value in energy_line.split(":", 1)[1].split()]
    raw_bounds[0] = 0.1
    header = next(i for i, line in enumerate(lines) if "Result" in line and "Rel Error" in line)
    rows = [line.split() for line in lines[header + 1 :] if line.strip()]
    values = [[0.0] * 4 for _ in range(4)]
    errors = [[0.0] * 4 for _ in range(4)]
    totals = [0.0] * 4
    for tokens in rows:
        if tokens[0] == "Total":
            i = round(float(tokens[1]) - 0.5)
            j = round(float(tokens[2]) - 0.5)
            totals[i + 2 * j] = float(tokens[4]) * SOURCE_RATE
            continue
        energy_eV = float(tokens[0]) * 1e6
        group = min(range(4), key=lambda index: abs(raw_bounds[index + 1] - energy_eV))
        if not math.isclose(raw_bounds[group + 1], energy_eV, rel_tol=1e-12):
            raise AssertionError(f"meshtal row energy {energy_eV} does not match a boundary")
        i = round(float(tokens[1]) - 0.5)
        j = round(float(tokens[2]) - 0.5)
        cell = i + 2 * j
        values[cell][group] = float(tokens[4]) * SOURCE_RATE
        errors[cell][group] = float(tokens[5])
    return raw_bounds, values, errors, totals


def independent_mctal(path: Path):
    lines = path.read_text().splitlines()
    cells_line = lines[lines.index("f 4") + 1]
    cell_ids = cells_line.split()
    energy_index = lines.index("et 5")
    bounds = [0.1] + [float(value) * 1e6 for value in lines[energy_index + 1].split()]
    vals_index = lines.index("vals")
    raw = []
    for line in lines[vals_index + 1 :]:
        if line.startswith("tfc"):
            break
        raw.extend(float(value) for value in line.split())
    values, errors, totals = [], [], []
    for cell in range(4):
        pairs = raw[cell * 10 : (cell + 1) * 10]
        values.append([pairs[group * 2] * SOURCE_RATE for group in range(4)])
        errors.append([pairs[group * 2 + 1] for group in range(4)])
        totals.append(pairs[8] * SOURCE_RATE)
    return cell_ids, bounds, values, errors, totals


def max_relative(left, right) -> float:
    differences = []
    for left_row, right_row in zip(left, right):
        for a, b in zip(left_row, right_row):
            differences.append(abs(a - b) / max(abs(a), abs(b), 1e-300))
    return max(differences, default=0.0)


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g3-"))) / "g3"
    work.mkdir(parents=True, exist_ok=True)
    meshtal, mctal = work / "meshtal.txt", work / "mctal.txt"
    make_meshtal(meshtal)
    make_mctal(mctal)
    meshtal_out, mctal_out = work / "meshtal.ndjson", work / "mctal.ndjson"
    command(import_arguments("meshtal", meshtal, meshtal_out))
    command(import_arguments("mctal", mctal, mctal_out))
    meshtal_header, meshtal_cells, meshtal_footer = read_ndjson(meshtal_out)
    mctal_header, mctal_cells, mctal_footer = read_ndjson(mctal_out)
    meshtal_ref = independent_meshtal(meshtal)
    mctal_ref = independent_mctal(mctal)
    meshtal_flux = [cell["flux_per_group"] for cell in meshtal_cells]
    mctal_flux = [cell["flux_per_group"] for cell in mctal_cells]
    meshtal_errors = [cell["relative_error"] for cell in meshtal_cells]
    mctal_errors = [cell["relative_error"] for cell in mctal_cells]

    planted = {}
    bad_meshtal = work / "bad_total.meshtal"
    make_meshtal(bad_meshtal, bad_total=True)
    planted["inconsistent_total"] = command(
        import_arguments("meshtal", bad_meshtal, work / "bad_total.ndjson"), ok=False
    )
    multiplier = work / "multiplier.meshtal"
    make_meshtal(multiplier, multiplier=True)
    planted["dose_multiplier"] = command(
        import_arguments("meshtal", multiplier, work / "multiplier.ndjson"), ok=False
    )
    wrong_type = work / "wrong_type.mctal"
    make_mctal(wrong_type, tally_id=5)
    planted["wrong_tally_type"] = command(
        [
            BIN,
            "import-flux",
            "mctal",
            wrong_type,
            work / "wrong_type.ndjson",
            "--tally",
            "5",
            "--source-rate",
            str(SOURCE_RATE),
            "--energy-floor-eV",
            "0.1",
        ],
        ok=False,
    )
    extra = work / "extra_dimension.mctal"
    make_mctal(extra, extra_dimension=True)
    planted["extra_dimension"] = command(
        import_arguments("mctal", extra, work / "extra_dimension.ndjson"), ok=False
    )
    truncated = work / "truncated.mctal"
    make_mctal(truncated, truncate=True)
    planted["truncation"] = command(
        import_arguments("mctal", truncated, work / "truncated.ndjson"), ok=False
    )
    messages = {name: (value.stdout + value.stderr).strip() for name, value in planted.items()}
    named = {
        "inconsistent_total": "Total" in messages["inconsistent_total"],
        "dose_multiplier": "response, multiplier" in messages["dose_multiplier"],
        "wrong_tally_type": "F4:N" in messages["wrong_tally_type"],
        "extra_dimension": "u dimension" in messages["extra_dimension"],
        "truncation": "truncated" in messages["truncation"],
    }

    expected_indices = [[1, 1, 1], [2, 1, 1], [1, 2, 1], [2, 2, 1]]
    expected_bounds = [
        [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
        [[1.0, 2.0], [0.0, 1.0], [0.0, 1.0]],
        [[0.0, 1.0], [1.0, 2.0], [0.0, 1.0]],
        [[1.0, 2.0], [1.0, 2.0], [0.0, 1.0]],
    ]
    output = {
        "meshtal": {
            "boundaries_exact": meshtal_header["energy_boundaries_eV"] == meshtal_ref[0] == BOUNDARIES_EV,
            "indices_exact": [cell["index"] for cell in meshtal_cells] == expected_indices,
            "bounds_exact": [cell["bounds_cm"] for cell in meshtal_cells] == expected_bounds,
            "max_flux_relative": max_relative(meshtal_flux, meshtal_ref[1]),
            "max_error_relative": max_relative(meshtal_errors, meshtal_ref[2]),
            "max_total_relative": max(
                abs(cell["flux_total"] - total) / total
                for cell, total in zip(meshtal_cells, meshtal_ref[3])
            ),
            "source_total_check": meshtal_footer["source_total_checks_max_relative_error"],
        },
        "mctal": {
            "ids_exact": [cell["id"] for cell in mctal_cells] == mctal_ref[0],
            "boundaries_exact": mctal_header["energy_boundaries_eV"] == mctal_ref[1] == BOUNDARIES_EV,
            "max_flux_relative": max_relative(mctal_flux, mctal_ref[2]),
            "max_error_relative": max_relative(mctal_errors, mctal_ref[3]),
            "max_total_relative": max(
                abs(cell["flux_total"] - total) / total for cell, total in zip(mctal_cells, mctal_ref[4])
            ),
            "source_total_check": mctal_footer["source_total_checks_max_relative_error"],
        },
        "cross_format_flux_exact": meshtal_flux == mctal_flux,
        "cross_format_error_exact": meshtal_errors == mctal_errors,
        "planted_failures": messages,
        "planted_premises_named": named,
    }
    output["pass"] = bool(
        all(
            value
            for key, value in output["meshtal"].items()
            if key.endswith("_exact")
        )
        and output["meshtal"]["max_flux_relative"] <= 1e-12
        and output["meshtal"]["max_error_relative"] <= 1e-12
        and output["meshtal"]["max_total_relative"] <= 1e-12
        and output["mctal"]["ids_exact"]
        and output["mctal"]["boundaries_exact"]
        and output["mctal"]["max_flux_relative"] <= 1e-12
        and output["mctal"]["max_error_relative"] <= 1e-12
        and output["mctal"]["max_total_relative"] <= 1e-12
        and output["cross_format_flux_exact"]
        and output["cross_format_error_exact"]
        and all(named.values())
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g3_p8_mcnp.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
