#!/usr/bin/env python3
"""P8-G6: measured streaming scaling, extrapolation, quality gates and pre-P8 regression."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ci_result import matches_baseline
from g5_p8_mesh_identity import SCALES, canonical_flux, shared_problem
from p8_fixtures import BIN, command, ensure_ci_library, sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
MEASURED_SIZES = [8, 16, 32, 64]
EXTRAPOLATED_SIZES = [1_000, 10_000, 100_000, 1_000_000]


def timed_mesh(spec: Path, output: Path) -> tuple[dict, int]:
    result = command(["/usr/bin/time", "-v", BIN, "mesh", spec, output])
    summary = json.loads(result.stdout)
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", result.stderr)
    if not match:
        raise AssertionError("GNU time did not report maximum resident set size")
    return summary, int(match.group(1))


def quality_command(arguments: list[str]) -> dict:
    cargo = os.environ.get("CARGO") or shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    environment = os.environ.copy()
    environment["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        [cargo, *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {"command": ["cargo", *arguments], "returncode": result.returncode, "tail": result.stdout[-2000:]}


def main() -> None:
    work = Path(os.environ.get("ACTINV_P8_WORK", tempfile.mkdtemp(prefix="actinv-p8-g6-"))) / "g6"
    work.mkdir(parents=True, exist_ok=True)
    library, decay = ensure_ci_library(work)
    example = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    base = list(reversed(example["spectrum"]["flux_per_group"]))
    base_sum = math.fsum(base)
    normalized = [value * example["spectrum"]["total"] / base_sum for value in base]
    scale_cycle = SCALES[3:7]
    measured = []
    for cells in MEASURED_SIZES:
        spectra = [
            [value * scale_cycle[index % len(scale_cycle)] for value in normalized]
            for index in range(cells)
        ]
        canonical = work / f"scaling-{cells}.flux.ndjson"
        canonical_flux(canonical, library, spectra)
        mesh_spec = {
            "spec": "actinv-mesh-spec-1",
            **shared_problem(library, decay),
            "title": f"P8 measured scaling {cells} cells",
            "flux": {"path": str(canonical), "sha256": sha256(canonical)},
            "chunk_cells": 4,
            "threads": min(4, os.cpu_count() or 1),
        }
        spec_path, result_path = work / f"scaling-{cells}.json", work / f"scaling-{cells}.result.ndjson"
        write_json(spec_path, mesh_spec)
        summary, peak_rss_kib = timed_mesh(spec_path, result_path)
        measured.append(
            {
                "kind": "measured",
                "cells": cells,
                "chunks": math.ceil(cells / 4),
                "wall_time_s": summary["wall_time_s"],
                "peak_rss_bytes": peak_rss_kib * 1024,
                "output_bytes": result_path.stat().st_size,
                "cells_per_s": summary["cells_per_s"],
            }
        )

    counts = np.asarray([row["cells"] for row in measured], dtype=float)
    wall_coefficients = np.polyfit(counts, [row["wall_time_s"] for row in measured], 1)
    output_coefficients = np.polyfit(counts, [row["output_bytes"] for row in measured], 1)
    bounded_rss = max(row["peak_rss_bytes"] for row in measured)
    extrapolated = []
    for cells in EXTRAPOLATED_SIZES:
        wall = max(0.0, float(np.polyval(wall_coefficients, cells)))
        output_bytes = max(0, round(float(np.polyval(output_coefficients, cells))))
        extrapolated.append(
            {
                "kind": "extrapolated (not executed)",
                "cells": cells,
                "chunks": math.ceil(cells / 4),
                "wall_time_s": wall,
                "peak_rss_bytes": bounded_rss,
                "output_bytes": output_bytes,
                "cells_per_s": cells / max(wall, 1e-300),
            }
        )

    tests = quality_command(["test", "--workspace"])
    clippy = quality_command(["clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"])
    current_ci = json.loads((RESULTS / "ci_end_to_end.json").read_text())
    baseline_ci = json.loads((ROOT / "controls" / "ci_expected.json").read_text())["result_baseline"]
    ci_regression_exact = matches_baseline(current_ci, baseline_ci)
    prior_verdicts = {
        phase: json.loads((RESULTS / f"verdict_{phase.lower()}.json").read_text())["verdict"]
        for phase in ("P5", "P6", "P7")
    }
    rss_spread = max(row["peak_rss_bytes"] for row in measured) - min(
        row["peak_rss_bytes"] for row in measured
    )
    output = {
        "configuration": {
            "chunk_cells": 4,
            "threads": min(4, os.cpu_count() or 1),
            "activation_groups": 709,
            "schedule_steps": 2,
            "measured_sizes": MEASURED_SIZES,
        },
        "rows": measured + extrapolated,
        "fit": {
            "wall_time_s": {"slope_per_cell": wall_coefficients[0], "intercept": wall_coefficients[1]},
            "output_bytes": {"slope_per_cell": output_coefficients[0], "intercept": output_coefficients[1]},
            "peak_rss": "bounded at the maximum measured RSS because input and result buffers contain one fixed-size chunk",
            "assumptions": [
                "identical 10-target CI library, two-step output detail, four workers and four-cell chunks",
                "linear per-cell solve and serialization cost fitted only from the four measured rows",
                "no extrapolated row was executed; filesystem and allocator behavior are assumed unchanged",
            ],
        },
        "bounded_memory": {
            "measured_peak_rss_spread_bytes": rss_spread,
            "largest_measured_chunks": measured[-1]["chunks"],
            "criterion_spread_bytes": 32 * 1024 * 1024,
        },
        "quality": {"workspace_tests": tests, "strict_clippy": clippy},
        "pre_p8_regression": {
            "current_ci_pass": current_ci["pass"],
            "deterministic_fields_equal_pre_p8_baseline": ci_regression_exact,
            "max_abs_deviation_cli_W_per_g": current_ci["max_abs_deviation_cli_W_per_g"],
            "cli_equals_python": current_ci["cli_equals_python"],
        },
        "retained_verdicts": prior_verdicts,
    }
    output["pass"] = bool(
        len(measured) == 4
        and measured[-1]["chunks"] > 1
        and rss_spread <= 32 * 1024 * 1024
        and all(right["output_bytes"] > left["output_bytes"] for left, right in zip(measured, measured[1:]))
        and extrapolated[-1]["cells"] == 1_000_000
        and tests["returncode"] == 0
        and clippy["returncode"] == 0
        and current_ci["pass"]
        and ci_regression_exact
        and prior_verdicts == {"P5": "P5-PASS", "P6": "P6-CONDITIONAL", "P7": "P7-CONDITIONAL"}
    )
    RESULTS.mkdir(exist_ok=True)
    write_json(RESULTS / "g6_p8_scaling_regression.json", output)
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
