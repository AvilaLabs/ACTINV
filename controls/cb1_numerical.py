#!/usr/bin/env python3
"""CB1-G1: identical-operator ACTINV/OpenMC/dense CRAM-48 comparisons."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from scipy.sparse import csr_matrix


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "cb1_numerical.json"
MODULE = Path(
    os.environ.get("ACTINV_PYTHON_MODULE", ROOT / "python/target/release/libactinv.so")
).resolve()
COEFFICIENTS = ROOT / "data/cram_coefficients.json"
RELATIVE_TOLERANCE = 5.0e-12
ABSOLUTE_NORM_TOLERANCE = 5.0e-14
RESOLVABLE_FRACTION = 1.0e-24


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_actinv():
    if not MODULE.is_file():
        raise FileNotFoundError(f"build the current Python module first: {MODULE}")
    spec = importlib.util.spec_from_file_location("actinv", MODULE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ACTINV extension from {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["actinv"] = module
    spec.loader.exec_module(module)
    return module


actinv = load_actinv()
from openmc.deplete.cram import CRAM48  # noqa: E402
import openmc  # noqa: E402


coefficient_document = json.loads(COEFFICIENTS.read_text(encoding="utf-8"))
coefficient = coefficient_document["Cram48Solver"]


def triplets(operator: np.ndarray) -> tuple[list[int], list[int], list[float]]:
    rows, cols = np.nonzero(operator)
    return rows.tolist(), cols.tolist(), operator[rows, cols].tolist()


def actinv_step(operator: np.ndarray, initial: np.ndarray, duration: float) -> np.ndarray:
    rows, cols, values = triplets(operator)
    return np.asarray(
        actinv.cram_step(
            len(initial),
            rows,
            cols,
            values,
            initial.tolist(),
            duration,
            coefficient["alpha0"],
            coefficient["theta_re"],
            coefficient["theta_im"],
            coefficient["alpha_re"],
            coefficient["alpha_im"],
        )
    )


def operator(size: int, multiplier: float) -> np.ndarray:
    value = np.zeros((size, size), dtype=float)
    # A closed deterministic decay chain. Rates span two orders of magnitude without
    # making the dense reference ill-conditioned at the frozen schedule lengths.
    for index in range(size - 1):
        rate = 2.5e-4 * (1.0 + (index % 11) / 4.0)
        value[index, index] -= rate
        value[index + 1, index] += rate
    # A flux-dependent skip path makes on/off operators noncommuting while preserving atoms.
    for index in range(size - 2):
        rate = multiplier * 7.0e-5 * (1.0 + (index % 7) / 5.0)
        value[index, index] -= rate
        value[index + 2, index] += rate
    return value


def compare(calculated: np.ndarray, reference: np.ndarray, initial_norm: float) -> dict[str, object]:
    absolute = np.abs(calculated - reference)
    absolute_tolerance = ABSOLUTE_NORM_TOLERANCE * initial_norm
    resolvable = np.abs(reference) >= max(RESOLVABLE_FRACTION * initial_norm, 1.0e-30)
    relative = absolute[resolvable] / np.abs(reference[resolvable])
    relative_crossover = absolute_tolerance / RELATIVE_TOLERANCE
    relative_dominant = np.abs(reference) > relative_crossover
    relative_dominant_values = absolute[relative_dominant] / np.abs(reference[relative_dominant])
    component_pass = (absolute <= absolute_tolerance) | (
        absolute <= RELATIVE_TOLERANCE * np.abs(reference)
    )
    return {
        "maximum_absolute_over_initial_norm": float(np.max(absolute) / initial_norm),
        "maximum_resolvable_relative": float(np.max(relative)) if relative.size else 0.0,
        "maximum_relative_above_tolerance_crossover": float(np.max(relative_dominant_values))
        if relative_dominant_values.size
        else 0.0,
        "tolerance_crossover_fraction_of_initial_norm": relative_crossover / initial_norm,
        "resolvable_components": int(np.count_nonzero(resolvable)),
        "all_components_within_relative_or_absolute": bool(np.all(component_pass)),
    }


def run_case(size: int) -> dict[str, object]:
    initial = np.zeros(size)
    initial[0] = 1.0e20
    if size > 2:
        initial[size // 2] = 2.5e17
    initial_norm = float(np.linalg.norm(initial, 1))
    schedule = [(31.0, 1.0), (17.0, 0.0), (23.0, 0.35), (11.0, 0.0), (7.0, 1.8)]
    states = {"actinv": initial.copy(), "openmc": initial.copy(), "dense": initial.copy()}
    boundaries = []
    for duration, multiplier in schedule:
        matrix = operator(size, multiplier)
        states["actinv"] = actinv_step(matrix, states["actinv"], duration)
        states["openmc"] = CRAM48(csr_matrix(matrix), states["openmc"], duration)
        states["dense"] = expm(matrix * duration) @ states["dense"]
        actinv_dense = compare(states["actinv"], states["dense"], initial_norm)
        actinv_openmc = compare(states["actinv"], states["openmc"], initial_norm)
        openmc_dense = compare(states["openmc"], states["dense"], initial_norm)
        boundaries.append(
            {
                "duration_s": duration,
                "flux_multiplier": multiplier,
                "actinv_vs_dense": actinv_dense,
                "actinv_vs_openmc": actinv_openmc,
                "openmc_vs_dense": openmc_dense,
                "actinv_total_atoms": float(np.sum(states["actinv"])),
                "dense_total_atoms": float(np.sum(states["dense"])),
            }
        )

    split_initial = initial.copy()
    split = actinv_step(operator(size, 0.7), split_initial, 13.0)
    split = actinv_step(operator(size, 0.7), split, 19.0)
    merged = actinv_step(operator(size, 0.7), split_initial, 32.0)
    split_merged = compare(split, merged, initial_norm)
    finite = all(np.all(np.isfinite(state)) for state in states.values())
    nonnegative = all(np.min(state) >= -ABSOLUTE_NORM_TOLERANCE * initial_norm for state in states.values())
    conservation = max(
        abs(float(np.sum(state)) - initial_norm) / initial_norm for state in states.values()
    )
    comparisons = [
        boundary[key]
        for boundary in boundaries
        for key in ("actinv_vs_dense", "actinv_vs_openmc", "openmc_vs_dense")
    ]
    passed = bool(
        all(row["all_components_within_relative_or_absolute"] for row in comparisons)
        and split_merged["all_components_within_relative_or_absolute"]
        and finite
        and nonnegative
        and conservation <= ABSOLUTE_NORM_TOLERANCE
    )
    return {
        "states": size,
        "nonzeros_on": int(np.count_nonzero(operator(size, 1.0))),
        "schedule": [{"duration_s": dt, "flux_multiplier": multiplier} for dt, multiplier in schedule],
        "boundaries": boundaries,
        "split_merged": split_merged,
        "finite": finite,
        "nonnegative_within_absolute_tolerance": nonnegative,
        "maximum_conservation_relative": conservation,
        "pass": passed,
    }


def main() -> None:
    cases = [run_case(size) for size in (2, 8, 32)]
    all_comparisons = [
        boundary[key]
        for case in cases
        for boundary in case["boundaries"]
        for key in ("actinv_vs_dense", "actinv_vs_openmc", "openmc_vs_dense")
    ]
    output = {
        "schema": "actinv-cb1-numerical-1",
        "access": {
            "ACTINV": "executed",
            "OpenMC": "executed",
            "SciPy dense expm": "executed",
            "FISPACT-II": "not-available",
            "SCALE/ORIGEN": "not-available",
            "ALARA": "not-applicable",
        },
        "implementations": {
            "actinv_version": getattr(actinv, "__version__", "unavailable"),
            "actinv_module_sha256": sha256(MODULE),
            "openmc_version": openmc.__version__,
            "openmc_solver": "openmc.deplete.cram.CRAM48",
            "dense_solver": "scipy.linalg.expm",
            "coefficient_document_sha256": sha256(COEFFICIENTS),
        },
        "tolerances": {
            "relative": RELATIVE_TOLERANCE,
            "absolute_fraction_of_initial_1norm": ABSOLUTE_NORM_TOLERANCE,
            "resolvable_fraction_of_initial_1norm": RESOLVABLE_FRACTION,
        },
        "cases": cases,
        "worst": {
            "absolute_over_initial_norm": max(
                row["maximum_absolute_over_initial_norm"] for row in all_comparisons
            ),
        "resolvable_relative": max(
                row["maximum_resolvable_relative"] for row in all_comparisons
            ),
            "relative_above_tolerance_crossover": max(
                row["maximum_relative_above_tolerance_crossover"] for row in all_comparisons
            ),
            "split_merged_absolute_over_initial_norm": max(
                case["split_merged"]["maximum_absolute_over_initial_norm"] for case in cases
            ),
        },
        "pass": all(case["pass"] for case in cases),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
