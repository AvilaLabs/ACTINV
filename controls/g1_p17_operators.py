#!/usr/bin/env python3
"""P17 G1: expanded identical-operator ACTINV/OpenMC/dense controls."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.linalg import expm
from scipy.sparse import csr_matrix


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p17_operators.json"
MODULE = Path(
    os.environ.get("ACTINV_PYTHON_MODULE", ROOT / "python/target/release/libactinv.so")
).resolve()
COEFFICIENTS = ROOT / "data/cram_coefficients.json"
RELATIVE_TOLERANCE = 5.0e-12
ABSOLUTE_NORM_TOLERANCE = 5.0e-14
RESOLVABLE_FRACTION = 1.0e-24
SCHEDULE = [(31.0, 1.0), (17.0, 0.0), (23.0, 0.35), (11.0, 0.0), (7.0, 1.8)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_actinv() -> Any:
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


COEFFICIENT_DOCUMENT = json.loads(COEFFICIENTS.read_text(encoding="utf-8"))
COEFFICIENT = COEFFICIENT_DOCUMENT["Cram48Solver"]


def add_transition(matrix: np.ndarray, source: int, product: int, rate: float) -> None:
    matrix[source, source] -= rate
    matrix[product, source] += rate


def network(name: str, multiplier: float) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if name == "capture_decay_2":
        matrix = np.zeros((2, 2), dtype=np.float64)
        add_transition(matrix, 1, 0, 1.2e-4)
        add_transition(matrix, 0, 1, multiplier * 8.0e-5)
        return matrix, np.ones(2), ["capture-chain", "decay-chain"]

    if name == "branch_isomer_8":
        matrix = np.zeros((8, 8), dtype=np.float64)
        # Ground/isomer decay and a second radioactive branch.
        for source, product, rate in (
            (2, 1, 1.7e-4),
            (1, 3, 8.0e-5),
            (4, 7, 3.0e-5),
            (5, 6, 1.1e-4),
            (6, 7, 4.0e-5),
        ):
            add_transition(matrix, source, product, rate)
        # Two independent reactions leave target 0. The first has a 60/40
        # ground/isomer product split; the second makes a competing product.
        branch_rate = multiplier * 9.0e-5
        matrix[0, 0] -= branch_rate
        matrix[1, 0] += 0.60 * branch_rate
        matrix[2, 0] += 0.40 * branch_rate
        add_transition(matrix, 0, 5, multiplier * 2.5e-5)
        add_transition(matrix, 1, 4, multiplier * 3.5e-5)
        add_transition(matrix, 3, 4, multiplier * 1.5e-5)
        return matrix, np.ones(8), [
            "capture-chain",
            "competing-reactions",
            "decay-chain",
            "metastable-branch",
        ]

    if name == "binary_fission_32":
        matrix = np.zeros((32, 32), dtype=np.float64)
        weights = np.asarray([236.0] + [95.0] * 15 + [141.0] * 16)
        # One parent becomes one light and one heavy fragment. Atom count grows,
        # while the independently checked mass-number measure remains closed.
        fission_rate = multiplier * 1.8e-5
        matrix[0, 0] -= fission_rate
        matrix[1, 0] += fission_rate
        matrix[16, 0] += fission_rate
        for start, stop in ((1, 16), (16, 32)):
            for source in range(start, stop - 1):
                rate = 2.5e-5 * (1.0 + ((source - start) % 7) / 5.0)
                add_transition(matrix, source, source + 1, rate)
            for source in range(start, stop - 2, 3):
                rate = multiplier * 1.2e-5 * (1.0 + ((source - start) % 5) / 4.0)
                add_transition(matrix, source, source + 2, rate)
        return matrix, weights, ["decay-chain", "binary-fission-yield"]

    raise ValueError(f"unknown P17 network {name}")


def initial_vector(name: str) -> np.ndarray:
    size = int(name.rsplit("_", 1)[1])
    initial = np.zeros(size, dtype=np.float64)
    initial[0] = 1.0e20
    if name == "branch_isomer_8":
        initial[3] = 2.5e17
    return initial


def triplets(operator: np.ndarray) -> tuple[list[int], list[int], list[float]]:
    rows, columns = np.nonzero(operator)
    return rows.tolist(), columns.tolist(), operator[rows, columns].tolist()


def actinv_step(operator: np.ndarray, initial: np.ndarray, duration: float) -> np.ndarray:
    rows, columns, values = triplets(operator)
    return np.asarray(
        actinv.cram_step(
            len(initial),
            rows,
            columns,
            values,
            initial.tolist(),
            duration,
            COEFFICIENT["alpha0"],
            COEFFICIENT["theta_re"],
            COEFFICIENT["theta_im"],
            COEFFICIENT["alpha_re"],
            COEFFICIENT["alpha_im"],
        )
    )


def compare(calculated: np.ndarray, reference: np.ndarray, initial_norm: float) -> dict[str, object]:
    absolute = np.abs(calculated - reference)
    absolute_tolerance = ABSOLUTE_NORM_TOLERANCE * initial_norm
    resolvable = np.abs(reference) >= max(RESOLVABLE_FRACTION * initial_norm, 1.0e-30)
    relative = absolute[resolvable] / np.abs(reference[resolvable])
    relative_crossover = absolute_tolerance / RELATIVE_TOLERANCE
    relative_dominant = np.abs(reference) > relative_crossover
    dominant_relative = absolute[relative_dominant] / np.abs(reference[relative_dominant])
    component_pass = (absolute <= absolute_tolerance) | (
        absolute <= RELATIVE_TOLERANCE * np.abs(reference)
    )
    return {
        "maximum_absolute_over_initial_norm": float(np.max(absolute) / initial_norm),
        "maximum_resolvable_relative": float(np.max(relative)) if relative.size else 0.0,
        "maximum_relative_above_tolerance_crossover": float(np.max(dominant_relative))
        if dominant_relative.size
        else 0.0,
        "resolvable_components": int(np.count_nonzero(resolvable)),
        "all_components_within_relative_or_absolute": bool(np.all(component_pass)),
    }


def run_case(name: str) -> dict[str, object]:
    initial = initial_vector(name)
    initial_norm = float(np.linalg.norm(initial, 1))
    initial_operator, weights, features = network(name, 0.0)
    del initial_operator
    conserved_initial = float(weights @ initial)
    states = {"actinv": initial.copy(), "openmc": initial.copy(), "dense": initial.copy()}
    boundaries = []
    operator_hashes = []
    for duration, multiplier in SCHEDULE:
        matrix, actual_weights, actual_features = network(name, multiplier)
        if not np.array_equal(weights, actual_weights) or features != actual_features:
            raise AssertionError("network metadata changed with flux multiplier")
        operator_hashes.append(sha256_bytes(matrix.astype("<f8", copy=False).tobytes()))
        states["actinv"] = actinv_step(matrix, states["actinv"], duration)
        states["openmc"] = CRAM48(csr_matrix(matrix), states["openmc"], duration)
        states["dense"] = expm(matrix * duration) @ states["dense"]
        comparisons = {
            "actinv_vs_dense": compare(states["actinv"], states["dense"], initial_norm),
            "actinv_vs_openmc": compare(states["actinv"], states["openmc"], initial_norm),
            "openmc_vs_dense": compare(states["openmc"], states["dense"], initial_norm),
        }
        boundaries.append(
            {
                "duration_s": duration,
                "flux_multiplier": multiplier,
                "comparisons": comparisons,
                "conserved_measure": {
                    method: float(weights @ state) for method, state in states.items()
                },
            }
        )

    split_matrix, _, _ = network(name, 0.7)
    split = actinv_step(split_matrix, initial, 13.0)
    split = actinv_step(split_matrix, split, 19.0)
    merged = actinv_step(split_matrix, initial, 32.0)
    split_merged = compare(split, merged, initial_norm)

    all_comparisons = [
        comparison
        for boundary in boundaries
        for comparison in boundary["comparisons"].values()
    ]
    finite = all(np.all(np.isfinite(state)) for state in states.values())
    nonnegative = all(
        float(np.min(state)) >= -ABSOLUTE_NORM_TOLERANCE * initial_norm
        for state in states.values()
    )
    conservation = max(
        abs(float(weights @ state) - conserved_initial) / conserved_initial
        for state in states.values()
    )
    passed = bool(
        all(row["all_components_within_relative_or_absolute"] for row in all_comparisons)
        and split_merged["all_components_within_relative_or_absolute"]
        and finite
        and nonnegative
        and conservation <= ABSOLUTE_NORM_TOLERANCE
    )
    return {
        "name": name,
        "states": len(initial),
        "features": features,
        "schedule": [
            {"duration_s": duration, "flux_multiplier": multiplier}
            for duration, multiplier in SCHEDULE
        ],
        "operator_sha256_by_boundary": operator_hashes,
        "boundaries": boundaries,
        "split_merged": split_merged,
        "finite": finite,
        "nonnegative_within_absolute_tolerance": nonnegative,
        "conservation_measure": "mass_number" if name == "binary_fission_32" else "atoms",
        "maximum_conservation_relative": conservation,
        "pass": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    cases = [
        run_case("capture_decay_2"),
        run_case("branch_isomer_8"),
        run_case("binary_fission_32"),
    ]
    comparisons = [
        comparison
        for case in cases
        for boundary in case["boundaries"]
        for comparison in boundary["comparisons"].values()
    ]
    output = {
        "schema": "actinv-p17-operators-1",
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
                row["maximum_absolute_over_initial_norm"] for row in comparisons
            ),
            "resolvable_relative": max(
                row["maximum_resolvable_relative"] for row in comparisons
            ),
            "relative_above_tolerance_crossover": max(
                row["maximum_relative_above_tolerance_crossover"] for row in comparisons
            ),
            "conservation_relative": max(
                case["maximum_conservation_relative"] for case in cases
            ),
            "split_merged_absolute_over_initial_norm": max(
                case["split_merged"]["maximum_absolute_over_initial_norm"]
                for case in cases
            ),
        },
        "pass": all(case["pass"] for case in cases),
    }
    encoded = json.dumps(output, indent=1, sort_keys=True) + "\n"
    if not arguments.no_write:
        RESULT.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
