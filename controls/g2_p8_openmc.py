#!/usr/bin/env python3
"""P8-G2: OpenMC statepoint-18 mesh-flux import against independent h5py reads."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path

import h5py
import numpy as np

from p8_fixtures import (
    BIN,
    BOUNDARIES_EV,
    PHYSICAL,
    RELERR,
    SOURCE_RATE,
    command,
    import_arguments,
    make_openmc,
    read_ndjson,
    sha256,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def independent(path: Path) -> tuple[list[list[float]], list[list[float]], list[float]]:
    with h5py.File(path, "r") as handle:
        tally = handle["tallies/tally 7"]
        filter_ids = tally["filters"][:].tolist()
        filter_types = []
        for filter_id in filter_ids:
            raw = handle[f"tallies/filters/filter {filter_id}/type"][()]
            filter_types.append(raw.decode() if isinstance(raw, bytes) else str(raw))
        mesh = handle["tallies/meshes/mesh 5"]
        kind_raw = mesh["type"][()]
        kind = kind_raw.decode() if isinstance(kind_raw, bytes) else str(kind_raw)
        if kind == "regular":
            lower, upper = mesh["lower_left"][:], mesh["upper_right"][:]
            dimensions = mesh["dimension"][:]
            grids = [np.linspace(lower[i], upper[i], dimensions[i] + 1) for i in range(3)]
        else:
            grids = [mesh[f"{axis}_grid"][:] for axis in "xyz"]
        volumes = []
        for k in range(len(grids[2]) - 1):
            for j in range(len(grids[1]) - 1):
                for i in range(len(grids[0]) - 1):
                    volumes.append(
                        (grids[0][i + 1] - grids[0][i])
                        * (grids[1][j + 1] - grids[1][j])
                        * (grids[2][k + 1] - grids[2][k])
                    )
        n = float(tally["n_realizations"][()])
        raw_results = tally["results"][:, 0, :]
        physical = [[0.0] * 4 for _ in range(4)]
        errors = [[0.0] * 4 for _ in range(4)]
        for row, (summed, sum_sq) in enumerate(raw_results):
            if filter_types == ["mesh", "energy"]:
                cell, group = divmod(row, 4)
            else:
                group, cell = divmod(row, 4)
            mean = summed / n
            physical[cell][group] = mean * SOURCE_RATE / volumes[cell]
            standard_deviation = math.sqrt(max(0.0, (sum_sq / n - mean * mean) / (n - 1.0)))
            errors[cell][group] = standard_deviation / mean
        return physical, errors, volumes


def max_relative(got, expected) -> float:
    differences = []
    for got_row, expected_row in zip(got, expected):
        for left, right in zip(got_row, expected_row):
            differences.append(abs(left - right) / max(abs(left), abs(right), 1e-300))
    return max(differences, default=0.0)


def measured_import(source: Path, output: Path) -> tuple[int, str]:
    result = command(["/usr/bin/time", "-v", *import_arguments("openmc", source, output)])
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", result.stderr)
    if not match:
        raise AssertionError(f"could not read peak RSS from time output: {result.stderr}")
    return int(match.group(1)), result.stderr


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g2-"))) / "g2"
    work.mkdir(parents=True, exist_ok=True)
    cases = [
        ("mesh_first_regular", ("mesh", "energy"), False),
        ("energy_first_rectilinear", ("energy", "mesh"), True),
    ]
    comparisons = {}
    for name, order, rectilinear in cases:
        source, canonical = work / f"{name}.h5", work / f"{name}.ndjson"
        make_openmc(source, order=order, rectilinear=rectilinear)
        command(import_arguments("openmc", source, canonical))
        header, cells, footer = read_ndjson(canonical)
        expected_flux, expected_errors, expected_volumes = independent(source)
        got_flux = [cell["flux_per_group"] for cell in cells]
        got_errors = [cell["relative_error"] for cell in cells]
        indices = [cell["index"] for cell in cells]
        expected_indices = [[1, 1, 1], [2, 1, 1], [1, 2, 1], [2, 2, 1]]
        comparisons[name] = {
            "source_sha256_matches": header["source"]["sha256"] == sha256(source),
            "boundaries_exact": header["energy_boundaries_eV"] == BOUNDARIES_EV,
            "indices_exact": indices == expected_indices,
            "max_flux_relative": max_relative(got_flux, expected_flux),
            "max_error_relative": max_relative(got_errors, expected_errors),
            "volume_integrated_relative": abs(
                footer["volume_integrated_flux"]
                - math.fsum(math.fsum(row) * volume for row, volume in zip(expected_flux, expected_volumes))
            )
            / footer["volume_integrated_flux"],
            "all_cell_values": got_flux,
        }

    plants = [
        ("score", {"score": "heating"}, "scores"),
        ("nuclide", {"nuclide": "U235"}, "nuclides"),
        ("filter", {"extra_filter": True}, "exactly MeshFilter and EnergyFilter"),
        ("mesh_type", {"mesh_type": "cylindrical"}, "mesh type"),
        ("version", {"version": (17, 0)}, "format version"),
    ]
    failures = {}
    for name, options, expected_text in plants:
        source = work / f"wrong_{name}.h5"
        make_openmc(source, **options)
        result = command(import_arguments("openmc", source, work / f"wrong_{name}.ndjson"), ok=False)
        message = (result.stdout + result.stderr).strip()
        failures[name] = {"message": message, "named_premise": expected_text in message}
    source = work / "missing_rate.h5"
    make_openmc(source)
    missing_rate = command(
        [BIN, "import-flux", "openmc", source, work / "missing_rate.ndjson", "--tally", "7", "--energy-floor-eV", "0.1"],
        ok=False,
    )
    missing_message = (missing_rate.stdout + missing_rate.stderr).strip()
    failures["source_rate"] = {"message": missing_message, "named_premise": "--source-rate" in missing_message}

    small = work / "rss_small.h5"
    padded = work / "rss_padded.h5"
    make_openmc(small)
    make_openmc(padded, padding_mb=48)
    small_rss, _ = measured_import(small, work / "rss_small.ndjson")
    padded_rss, _ = measured_import(padded, work / "rss_padded.ndjson")
    padding_bytes = padded.stat().st_size - small.stat().st_size
    rss_delta_bytes = max(0, padded_rss - small_rss) * 1024

    output = {
        "h5py_version": h5py.__version__,
        "comparisons": comparisons,
        "failures": failures,
        "bounded_window": {
            "small_peak_rss_kib": small_rss,
            "padded_peak_rss_kib": padded_rss,
            "padded_file_extra_bytes": padding_bytes,
            "peak_rss_extra_bytes": rss_delta_bytes,
        },
    }
    output["pass"] = bool(
        h5py.__version__ == "3.16.0"
        and all(
            case["source_sha256_matches"]
            and case["boundaries_exact"]
            and case["indices_exact"]
            and case["max_flux_relative"] <= 1e-12
            and case["max_error_relative"] <= 1e-12
            and case["volume_integrated_relative"] <= 1e-12
            for case in comparisons.values()
        )
        and all(value["named_premise"] for value in failures.values())
        and padding_bytes >= 47 * 1024 * 1024
        and rss_delta_bytes < padding_bytes // 2
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g2_p8_openmc.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
