#!/usr/bin/env python3
"""CB1-G4: same-operator kernel and warm-cache standalone performance measurements."""
from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import cb1_numerical as numerical  # noqa: E402
import g5_p9_alara as alara_fixture  # noqa: E402


RESULT = ROOT / "results/cb1_performance.json"
ACTINV_BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv")).resolve()
DATA = Path.home() / "nuclear-data"
LIBRARY = Path(
    os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz")
).resolve()
DECAY_PRIMARY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    )
).resolve()
DECAY_FALLBACK = Path(
    os.environ.get("ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
).resolve()
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
WARMUPS = 5
SAMPLES = 30


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sample_statistics(samples_ms: list[float]) -> dict[str, object]:
    values = np.asarray(samples_ms, dtype=float)
    return {
        "samples": len(samples_ms),
        "minimum_ms": float(np.min(values)),
        "median_ms": float(np.median(values)),
        "p95_ms": float(np.quantile(values, 0.95)),
        "mean_ms": float(np.mean(values)),
        "sample_standard_deviation_ms": float(np.std(values, ddof=1)),
        "raw_ms": samples_ms,
    }


def kernel_measurement(function) -> tuple[dict[str, object], np.ndarray]:
    # Profile before choosing a batch size, as required by the frozen protocol.
    profile = []
    output = None
    for _ in range(5):
        started = time.perf_counter_ns()
        output = function()
        profile.append((time.perf_counter_ns() - started) * 1.0e-9)
    estimate = max(statistics.median(profile), 1.0e-7)
    repetitions = min(5000, max(1, math.ceil(0.025 / estimate)))

    for _ in range(WARMUPS):
        for _ in range(repetitions):
            output = function()
    samples = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(SAMPLES):
            started = time.perf_counter_ns()
            for _ in range(repetitions):
                output = function()
            elapsed = (time.perf_counter_ns() - started) * 1.0e-6 / repetitions
            samples.append(elapsed)
    finally:
        if gc_enabled:
            gc.enable()
    result = sample_statistics(samples)
    result.update(
        {
            "warmup_batches": WARMUPS,
            "measured_batches": SAMPLES,
            "calls_per_batch": repetitions,
            "profile_single_call_median_ms": statistics.median(profile) * 1.0e3,
            "target_batch_duration_ms": 25.0,
        }
    )
    return result, np.asarray(output)


def kernel_case(size: int) -> dict[str, object]:
    operator = numerical.operator(size, 1.0)
    initial = np.zeros(size)
    initial[0] = 1.0e20
    if size > 2:
        initial[size // 2] = 2.5e17
    rows, columns = np.nonzero(operator)
    row_list = rows.tolist()
    column_list = columns.tolist()
    values = operator[rows, columns].tolist()
    initial_list = initial.tolist()
    sparse = csr_matrix(operator)

    def actinv_call():
        return numerical.actinv.cram_step(
            size,
            row_list,
            column_list,
            values,
            initial_list,
            31.0,
            numerical.coefficient["alpha0"],
            numerical.coefficient["theta_re"],
            numerical.coefficient["theta_im"],
            numerical.coefficient["alpha_re"],
            numerical.coefficient["alpha_im"],
        )

    def openmc_call():
        return numerical.CRAM48(sparse, initial, 31.0)

    actinv_timing, actinv_output = kernel_measurement(actinv_call)
    openmc_timing, openmc_output = kernel_measurement(openmc_call)
    initial_norm = float(np.linalg.norm(initial, 1))
    difference = np.abs(actinv_output - openmc_output)
    material = np.abs(openmc_output) > 1.0e-14 * initial_norm
    output_comparison = {
        "maximum_absolute_over_initial_1norm": float(np.max(difference) / initial_norm),
        "maximum_relative_above_1e-14_initial_1norm": float(
            np.max(difference[material] / np.abs(openmc_output[material]))
        )
        if np.any(material)
        else 0.0,
        "within_cb1_numerical_tolerance": bool(
            np.all(
                (difference <= 5.0e-14 * initial_norm)
                | (difference <= 5.0e-12 * np.abs(openmc_output))
            )
        ),
    }
    return {
        "states": size,
        "nonzeros": int(np.count_nonzero(operator)),
        "duration_s": 31.0,
        "operator_sha256": hashlib.sha256(operator.tobytes(order="C")).hexdigest(),
        "initial_sha256": hashlib.sha256(initial.tobytes(order="C")).hexdigest(),
        "actinv_pyo3_cram48": actinv_timing,
        "openmc_python_cram48": openmc_timing,
        "openmc_over_actinv_median_time_ratio": openmc_timing["median_ms"]
        / actinv_timing["median_ms"],
        "output_comparison": output_comparison,
    }


def run_process(arguments: list[str | Path], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )


def process_measurement(
    arguments: list[str | Path],
    *,
    cwd: Path,
    success_pattern: str | None = None,
) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
    last = None
    for _ in range(WARMUPS):
        last = run_process(arguments, cwd=cwd)
        if last.returncode != 0:
            raise RuntimeError(f"warm-up failed: {(last.stdout + last.stderr)[-1000:]}")
    samples = []
    for _ in range(SAMPLES):
        started = time.perf_counter_ns()
        last = run_process(arguments, cwd=cwd)
        samples.append((time.perf_counter_ns() - started) * 1.0e-6)
        if last.returncode != 0:
            raise RuntimeError(f"timed process failed: {(last.stdout + last.stderr)[-1000:]}")
        if success_pattern and re.search(success_pattern, last.stdout + last.stderr) is None:
            raise RuntimeError(f"timed process omitted expected marker {success_pattern!r}")
    timing = sample_statistics(samples)
    timing.update(
        {
            "warmups": WARMUPS,
            "measured_processes": SAMPLES,
            "file_cache": "warm after five complete warm-up processes; OS caches were not dropped",
        }
    )
    return timing, last


def peak_rss(arguments: list[str | Path], *, cwd: Path) -> dict[str, object]:
    result = run_process(["/usr/bin/time", "-v", *arguments], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"peak-RSS process failed: {(result.stdout + result.stderr)[-1000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", result.stderr)
    if match is None:
        raise RuntimeError("GNU time did not expose maximum resident set size")
    return {
        "measurement_processes": 1,
        "peak_rss_bytes": int(match.group(1)) * 1024,
        "method": "/usr/bin/time -v after the timed repetitions",
    }


def public_example(work: Path) -> tuple[Path, Path]:
    specification = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
    specification["library"] = {"path": str(LIBRARY), "sha256": sha256(LIBRARY)}
    specification["decay"] = {
        "primary": str(DECAY_PRIMARY),
        "fallback": str(DECAY_FALLBACK),
    }
    spec_path = work / "public-example.json"
    result_path = work / "public-example.result.json"
    spec_path.write_text(json.dumps(specification, sort_keys=True) + "\n", encoding="utf-8")
    return spec_path, result_path


def same_data_processes(work: Path) -> dict[str, object]:
    work.mkdir(parents=True, exist_ok=True)
    paths = alara_fixture.checked_inputs()
    subset, cross_sections = alara_fixture.extract_fe56_np(paths["activation"])
    library, _ = alara_fixture.write_actinv_library(work, cross_sections)
    flux_path = work / "fe56-flux"
    flux = alara_fixture.write_flux(flux_path)
    _, alara_metadata = alara_fixture.custom_alara(work, paths, subset, flux_path)
    spec_path = work / "same-data.json"
    result_path = work / "same-data.result.json"
    alara_fixture.write_json(spec_path, alara_fixture.actinv_spec(paths, library, flux))
    alara_input = Path(alara_metadata["run_input"]).name
    alara_arguments = [alara_fixture.ALARA_BIN, alara_input]
    actinv_arguments = [ACTINV_BINARY, "run", spec_path, result_path]
    alara_timing, alara_last = process_measurement(
        alara_arguments, cwd=work, success_pattern=r"\*\*\* Number Density"
    )
    actinv_timing, _ = process_measurement(actinv_arguments, cwd=work)
    actinv_result = json.loads(result_path.read_text(encoding="utf-8"))
    return {
        "comparison_status": "standalone measurements, not a direct speed ratio",
        "reason": (
            "inputs and schedule are identical, but ALARA prints requested number density from its preconverted "
            "binary library while ACTINV verifies inputs, parses decay data, and emits inventory/activity/heat/ledger/certificate"
        ),
        "states_actinv": actinv_result["total_states"],
        "schedule_segments": len(actinv_result["steps"]),
        "alara_2_9_2_number_density": {
            **alara_timing,
            **peak_rss(alara_arguments, cwd=work),
            "last_output_has_number_density": "*** Number Density" in alara_last.stdout,
        },
        "actinv_1_0_0_full_diagnostics": {
            **actinv_timing,
            **peak_rss(actinv_arguments, cwd=work),
            "result_mode": actinv_result["mode"],
            "result_steps": len(actinv_result["steps"]),
        },
    }


def main() -> None:
    thread_environment = {name: os.environ.get(name, "unset") for name in THREAD_VARIABLES}
    if any(value != "1" for value in thread_environment.values()):
        raise RuntimeError(f"CB1 scalar timing requires every thread variable to equal 1: {thread_environment}")
    load_start = os.getloadavg() if hasattr(os, "getloadavg") else None
    kernel = [kernel_case(size) for size in (2, 32, 256, 1024)]
    # ALARA's legacy lexer has a short pathname token ceiling, so keep this external
    # scratch prefix deliberately compact. Scientific filenames and inputs are unchanged.
    with tempfile.TemporaryDirectory(prefix="cb1p-") as directory:
        work = Path(directory)
        startup_arguments = [ACTINV_BINARY, "--version"]
        startup, startup_last = process_measurement(
            startup_arguments, cwd=work, success_pattern=r"^actinv 1\.0\.0$"
        )
        startup.update(peak_rss(startup_arguments, cwd=work))

        spec_path, result_path = public_example(work)
        example_arguments = [ACTINV_BINARY, "run", spec_path, result_path]
        example, _ = process_measurement(example_arguments, cwd=work)
        example.update(peak_rss(example_arguments, cwd=work))
        example_result = json.loads(result_path.read_text(encoding="utf-8"))
        example.update(
            {
                "result_steps": len(example_result["steps"]),
                "result_mode": example_result["mode"],
                "states_total": example_result["total_states"],
                "states_pruned": example_result["pruned_states"],
                "input_bytes_hashed_and_parsed": LIBRARY.stat().st_size
                + DECAY_PRIMARY.stat().st_size
                + DECAY_FALLBACK.stat().st_size,
            }
        )
        identical_data = same_data_processes(work / "a")

    checks = {
        "thread_environment_is_one": all(value == "1" for value in thread_environment.values()),
        "all_kernel_outputs_match": all(
            row["output_comparison"]["within_cb1_numerical_tolerance"] for row in kernel
        ),
        "all_kernel_samples_complete": all(
            row[implementation]["samples"] == SAMPLES
            for row in kernel
            for implementation in ("actinv_pyo3_cram48", "openmc_python_cram48")
        ),
        "startup_version": startup_last.stdout.strip() == "actinv 1.0.0",
        "public_example_complete": example["result_steps"] == 21,
        "same_data_runs_complete": bool(
            identical_data["alara_2_9_2_number_density"]["last_output_has_number_density"]
            and identical_data["actinv_1_0_0_full_diagnostics"]["result_steps"] == 19
        ),
    }
    output = {
        "schema": "actinv-cb1-performance-1",
        "access": {
            "ACTINV": "executed",
            "OpenMC": "executed for identical-operator kernels",
            "ALARA": "executed for the identical-data standalone case",
            "FISPACT-II": "not-available",
            "SCALE/ORIGEN": "not-available",
        },
        "environment": {
            "thread_variables": thread_environment,
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "load_average_start": load_start,
            "load_average_end": os.getloadavg() if hasattr(os, "getloadavg") else None,
            "clock": "time.perf_counter_ns",
        },
        "implementations": {
            "actinv_version": getattr(numerical.actinv, "__version__", "unavailable"),
            "actinv_python_module_sha256": sha256(numerical.MODULE),
            "actinv_binary_sha256": sha256(ACTINV_BINARY),
            "openmc_version": numerical.openmc.__version__,
            "alara_version": "2.9.2",
            "alara_source_commit": "faa5b330460fe865e38fc788f1b792ea33d13d1b",
        },
        "identical_operator_cram48": kernel,
        "actinv_process_startup": startup,
        "actinv_public_example_warm_cache": example,
        "identical_data_standalone": identical_data,
        "limitations": [
            "kernel rows time the public Python-call boundaries, including language data conversion",
            "warm-cache process rows do not claim cold-storage performance",
            "ALARA and ACTINV standalone rows have identical physics inputs but different requested outputs and data-loading work",
            "no executable-level FISPACT-II or SCALE/ORIGEN timing was available",
        ],
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    concise = {
        "kernel_medians_ms": [
            {
                "states": row["states"],
                "actinv": row["actinv_pyo3_cram48"]["median_ms"],
                "openmc": row["openmc_python_cram48"]["median_ms"],
                "ratio": row["openmc_over_actinv_median_time_ratio"],
            }
            for row in kernel
        ],
        "startup_median_ms": startup["median_ms"],
        "public_example_median_ms": example["median_ms"],
        "same_data_standalone_median_ms": {
            "alara": identical_data["alara_2_9_2_number_density"]["median_ms"],
            "actinv": identical_data["actinv_1_0_0_full_diagnostics"]["median_ms"],
        },
        "checks": checks,
        "pass": output["pass"],
    }
    print(json.dumps(concise, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
