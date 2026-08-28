#!/usr/bin/env python3
"""CB1-G4: fresh bounded, repeated ACTINV mesh throughput measurements."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from g5_p8_mesh_identity import SCALES, canonical_flux, shared_problem  # noqa: E402
from p8_fixtures import ensure_ci_library, sha256, write_json  # noqa: E402


RESULT = ROOT / "results/cb1_mesh_performance.json"
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv")).resolve()
WARMUPS = 5
SAMPLES = 30
SIZES = (8, 64, 256)
CHUNK_CELLS = 16


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def statistics(values: list[float]) -> dict[str, object]:
    array = np.asarray(values, dtype=float)
    return {
        "samples": len(values),
        "minimum": float(np.min(array)),
        "median": float(np.median(array)),
        "p95": float(np.quantile(array, 0.95)),
        "mean": float(np.mean(array)),
        "sample_standard_deviation": float(np.std(array, ddof=1)),
        "raw": values,
    }


def execute(specification: Path, output: Path) -> tuple[float, dict[str, object]]:
    started = time.perf_counter_ns()
    run = subprocess.run(
        [str(BIN), "mesh", str(specification), str(output)],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    wall_s = (time.perf_counter_ns() - started) * 1.0e-9
    if run.returncode != 0:
        raise RuntimeError(f"mesh process failed: {(run.stdout + run.stderr)[-2000:]}")
    try:
        summary = json.loads(run.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"mesh process emitted invalid summary: {run.stdout[-1000:]}") from error
    return wall_s, summary


def peak_rss(specification: Path, output: Path) -> int:
    run = subprocess.run(
        ["/usr/bin/time", "-v", str(BIN), "mesh", str(specification), str(output)],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if run.returncode != 0:
        raise RuntimeError(f"mesh peak-RSS process failed: {(run.stdout + run.stderr)[-2000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", run.stderr)
    if match is None:
        raise RuntimeError("GNU time did not expose mesh peak RSS")
    return int(match.group(1)) * 1024


def benchmark(
    work: Path,
    library: Path,
    decay: Path,
    normalized_flux: list[float],
    cells: int,
    threads: int,
) -> dict[str, object]:
    scales = SCALES[3:7]
    spectra = [
        [value * scales[index % len(scales)] for value in normalized_flux]
        for index in range(cells)
    ]
    canonical = work / f"flux-{cells}.ndjson"
    if not canonical.is_file():
        canonical_flux(canonical, library, spectra)
    specification = {
        "spec": "actinv-mesh-spec-1",
        **shared_problem(library, decay),
        "title": f"CB1 mesh {cells} cells {threads} threads",
        "flux": {"path": str(canonical), "sha256": sha256(canonical)},
        "chunk_cells": CHUNK_CELLS,
        "threads": threads,
    }
    spec_path = work / f"mesh-{cells}-{threads}t.json"
    output_path = work / f"mesh-{cells}-{threads}t.result.ndjson"
    write_json(spec_path, specification)
    for _ in range(WARMUPS):
        execute(spec_path, output_path)
    process_wall = []
    reported_wall = []
    reported_throughput = []
    summaries = []
    for _ in range(SAMPLES):
        wall_s, summary = execute(spec_path, output_path)
        process_wall.append(wall_s)
        reported_wall.append(float(summary["wall_time_s"]))
        reported_throughput.append(float(summary["cells_per_s"]))
        summaries.append(summary)
    rss = peak_rss(spec_path, output_path)
    summary_cells = {value["cells"] for value in summaries}
    if summary_cells != {cells}:
        raise RuntimeError(f"mesh summaries reported unexpected cell counts: {summary_cells}")
    return {
        "cells": cells,
        "threads": threads,
        "chunk_cells": CHUNK_CELLS,
        "chunks": math.ceil(cells / CHUNK_CELLS),
        "warmups": WARMUPS,
        "process_wall_s": statistics(process_wall),
        "reported_wall_s": statistics(reported_wall),
        "reported_cells_per_s": statistics(reported_throughput),
        "peak_rss_bytes": rss,
        "peak_rss_measurement_processes": 1,
        "canonical_flux_bytes": canonical.stat().st_size,
        "output_bytes": output_path.stat().st_size,
        "all_processes_completed": len(summaries) == SAMPLES,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cb1m-") as directory:
        work = Path(directory)
        library, decay = ensure_ci_library(work)
        expected = {
            "library": "6096380705ca3155e4c7a2d826bbfe3797bd0ceba8494878b2bbb25655a9810d",
            "library_index": "d341703416ae15ab05d617f5ca5615f16180f4b86135d755ef4914be9d1283e1",
            "decay": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
        }
        identities = {
            "library": file_sha256(library),
            "library_index": file_sha256(library.with_name(library.stem + "_index.json")),
            "decay": file_sha256(decay),
            "binary": file_sha256(BIN),
        }
        if any(identities[name] != value for name, value in expected.items()):
            raise RuntimeError(f"CB1 mesh input identity mismatch: {identities}")
        example = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
        ascending = list(reversed(example["spectrum"]["flux_per_group"]))
        normalized_flux = [
            value * example["spectrum"]["total"] / math.fsum(ascending) for value in ascending
        ]
        rows = [benchmark(work, library, decay, normalized_flux, cells, 4) for cells in SIZES]
        one_thread = benchmark(work, library, decay, normalized_flux, SIZES[-1], 1)

    counts = np.asarray([row["cells"] for row in rows], dtype=float)
    medians = np.asarray([row["reported_wall_s"]["median"] for row in rows], dtype=float)
    coefficients = np.polyfit(counts, medians, 1)
    million_wall = max(0.0, float(np.polyval(coefficients, 1_000_000)))
    million_output = max(
        0,
        round(
            float(
                np.polyval(
                    np.polyfit(counts, [row["output_bytes"] for row in rows], 1),
                    1_000_000,
                )
            )
        ),
    )
    four_thread_256 = rows[-1]["reported_wall_s"]["median"]
    one_thread_256 = one_thread["reported_wall_s"]["median"]
    checks = {
        "all_repetitions_complete": all(row["all_processes_completed"] for row in [*rows, one_thread]),
        "sizes_increase": [row["cells"] for row in rows] == list(SIZES),
        "output_size_increases": all(
            right["output_bytes"] > left["output_bytes"] for left, right in zip(rows, rows[1:])
        ),
        "identities_match": all(identities[name] == value for name, value in expected.items()),
    }
    observations = {
        "four_threads_faster_at_256_cells": four_thread_256 < one_thread_256,
    }
    output = {
        "schema": "actinv-cb1-mesh-performance-1",
        "access": {"ACTINV": "executed", "other products": "not-applicable"},
        "configuration": {
            "activation_groups": 709,
            "schedule_steps": 2,
            "measured_sizes": list(SIZES),
            "threads": [1, 4],
            "chunk_cells": CHUNK_CELLS,
            "warmups": WARMUPS,
            "measured_processes": SAMPLES,
            "file_cache": "warm after five complete warm-up processes; OS caches were not dropped",
        },
        "identities": identities,
        "four_thread_scaling": rows,
        "one_thread_256": one_thread,
        "four_thread_speedup_at_256_cells": one_thread_256 / four_thread_256,
        "observations": observations,
        "million_cell_linear_extrapolation_not_executed": {
            "wall_time_s": million_wall,
            "cells_per_s": 1_000_000 / million_wall,
            "output_bytes": million_output,
            "peak_rss_bytes_assumed_bounded_at_measured_max": max(
                row["peak_rss_bytes"] for row in [*rows, one_thread]
            ),
            "fit_wall_slope_per_cell": float(coefficients[0]),
            "fit_wall_intercept_s": float(coefficients[1]),
            "warning": "not executed; linear solve/serialization, filesystem, and allocator behavior are assumptions",
        },
        "limitations": [
            "mesh has no same-workload competitor timing in CB1",
            "the small public CI library is used so repeated timing is distributable; the full data-load cost is measured separately",
            "million-cell values are extrapolated and must not be quoted as an executed benchmark",
        ],
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "four_thread": [
                    {
                        "cells": row["cells"],
                        "median_wall_s": row["reported_wall_s"]["median"],
                        "median_cells_per_s": row["reported_cells_per_s"]["median"],
                        "peak_rss_bytes": row["peak_rss_bytes"],
                    }
                    for row in rows
                ],
                "four_thread_speedup_at_256_cells": output[
                    "four_thread_speedup_at_256_cells"
                ],
                "million_cell_extrapolation": output[
                    "million_cell_linear_extrapolation_not_executed"
                ],
                "checks": checks,
                "pass": output["pass"],
            },
            indent=1,
        )
    )
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
