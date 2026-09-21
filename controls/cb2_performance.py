#!/usr/bin/env python3
"""CB2: refresh of the CB1 CRAM-48 kernel benchmark on actinv 1.1.2.

Same operators, same sampling protocol as cb1_performance.kernel_case;
only the actinv module and result path differ. Run under the venv that
has both actinv 1.1.2 and openmc 0.15.3.
"""
import gc
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import cb1_numerical as numerical  # noqa: E402

RESULT = ROOT / "results/cb2_performance.json"
WARMUPS = 5
SAMPLES = 30
SIZES = (2, 32, 256, 1024, 2048, 4096)


def kernel_measurement(function, repetitions=20):
    samples, profile = [], []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(WARMUPS):
            function()
        for _ in range(SAMPLES):
            started = time.perf_counter_ns()
            output = None
            for _ in range(repetitions):
                output = function()
            elapsed = (time.perf_counter_ns() - started) * 1.0e-6 / repetitions
            samples.append(elapsed)
            t1 = time.perf_counter_ns()
            output = function()
            profile.append((time.perf_counter_ns() - t1) * 1.0e-6)
    finally:
        if gc_enabled:
            gc.enable()
    return ({
        "median_ms": statistics.median(samples),
        "mean_ms": statistics.mean(samples),
        "minimum_ms": min(samples),
        "p95_ms": sorted(samples)[int(len(samples) * 0.95) - 1],
        "sample_standard_deviation_ms": statistics.stdev(samples)
        if len(samples) > 1 else 0.0,
        "samples": len(samples),
        "warmups": WARMUPS,
        "profile_single_call_median_ms": statistics.median(profile),
    }, np.asarray(output))


def kernel_case(size: int) -> dict:
    operator = numerical.operator(size, 1.0)
    initial = np.zeros(size)
    initial[0] = 1.0e20
    if size > 2:
        initial[size // 2] = 2.5e17
    rows, columns = np.nonzero(operator)
    row_list, column_list = rows.tolist(), columns.tolist()
    values = operator[rows, columns].tolist()
    initial_list = initial.tolist()
    sparse = csr_matrix(operator)

    def actinv_call():
        return numerical.actinv.cram_step(
            size, row_list, column_list, values, initial_list, 31.0,
            numerical.coefficient["alpha0"],
            numerical.coefficient["theta_re"],
            numerical.coefficient["theta_im"],
            numerical.coefficient["alpha_re"],
            numerical.coefficient["alpha_im"])

    def openmc_call():
        return numerical.CRAM48(sparse, initial, 31.0)

    actinv_timing, actinv_output = kernel_measurement(actinv_call)
    openmc_timing, openmc_output = kernel_measurement(openmc_call)
    initial_norm = float(np.linalg.norm(initial, 1))
    difference = np.abs(actinv_output - openmc_output)
    material = np.abs(openmc_output) > 1.0e-14 * initial_norm
    return {
        "states": size,
        "nonzeros": int(np.count_nonzero(operator)),
        "operator_sha256": hashlib.sha256(
            operator.tobytes(order="C")).hexdigest(),
        "actinv_pyo3_cram48": actinv_timing,
        "openmc_python_cram48": openmc_timing,
        "openmc_over_actinv_median_time_ratio":
            openmc_timing["median_ms"] / actinv_timing["median_ms"],
        "output_comparison": {
            "maximum_relative_above_1e-14_initial_1norm": float(
                np.max(difference[material]
                       / np.abs(openmc_output[material])))
            if np.any(material) else 0.0,
            "within_cb1_numerical_tolerance": bool(np.all(
                (difference <= 5.0e-14 * initial_norm)
                | (difference <= 5.0e-12 * np.abs(openmc_output)))),
        },
    }


def main():
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                "RAYON_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    cases = [kernel_case(s) for s in SIZES]
    out = {
        "schema": "actinv-cb2-performance-1",
        "refresh_of": "results/cb1_performance.json",
        "implementations": {
            "actinv_version": getattr(numerical.actinv, "__version__", "?"),
            "actinv_python_module_sha256": hashlib.sha256(
                Path(numerical.MODULE).read_bytes()).hexdigest(),
            "openmc_version": numerical.openmc.__version__,
        },
        "identical_operator_cram48": cases,
        "note": "kernel-only refresh: same operators and sampling "
                "protocol as CB1; startup/example/ALARA legs superseded "
                "by P40 identical-data census or unchanged",
    }
    RESULT.write_text(json.dumps(out, indent=1))
    for c in cases:
        print(f"{c['states']:>5} states | actinv "
              f"{c['actinv_pyo3_cram48']['median_ms']:.4f} ms | openmc "
              f"{c['openmc_python_cram48']['median_ms']:.4f} ms | "
              f"ratio {c['openmc_over_actinv_median_time_ratio']:.2f}")


if __name__ == "__main__":
    main()
