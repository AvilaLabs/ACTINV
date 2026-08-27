#!/usr/bin/env python3
"""P10 G3 independent high-order unresolved-width quadrature and LSSF control."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess

import numpy as np
from scipy.special import gamma, roots_genlaguerre


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p10_unresolved_quadrature.json"
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
ORDER = 96
COMPARISON_FLOOR_B = 1e-12
K_WAVE = 2.196_771e-3
AWR = 56.0
SPIN = 0.5
SEQUENCE_SPIN = 0.5
AP = 0.5
ENERGY = 200.0

CASES = [
    {
        "case": "A",
        "spacing": 10.0,
        "competitive": 0.0,
        "neutron": 7e-5,
        "capture": 8.0,
        "fission": 0.0,
        "competitive_dof": 0,
        "neutron_dof": 1,
        "fission_dof": 0,
    },
    {
        "case": "B",
        "spacing": 10.0,
        "competitive": 0.0,
        "neutron": 5e-5,
        "capture": 1e6,
        "fission": 6e-4,
        "competitive_dof": 0,
        "neutron_dof": 2,
        "fission_dof": 2,
    },
    {
        "case": "C",
        "spacing": 12.0,
        "competitive": 4e-4,
        "neutron": 2.5e-5,
        "capture": 1e6,
        "fission": 5e-4,
        "competitive_dof": 2,
        "neutron_dof": 3,
        "fission_dof": 4,
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def limit_address_space() -> None:
    limit = 2 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def probe(case: dict, lssf: int) -> tuple[np.ndarray, dict[int, tuple[float, float]]]:
    arguments = [
        str(DUMP),
        "unresolved-probe",
        case["case"],
        f"{ENERGY:.17e}",
        f"{case['spacing']:.17e}",
        f"{case['competitive']:.17e}",
        f"{case['neutron']:.17e}",
        f"{case['capture']:.17e}",
        f"{case['fission']:.17e}",
        str(case["competitive_dof"]),
        str(case["neutron_dof"]),
        str(case["fission_dof"]),
        str(lssf),
    ]
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=limit_address_space,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"unresolved probe failed ({completed.returncode})\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    direct = None
    processed = {}
    for line in completed.stdout.splitlines():
        values = line.split()
        if values[:1] == ["U"]:
            if values[1] != case["case"] or int(values[2]) != lssf:
                raise ValueError(f"mismatched unresolved probe header: {line}")
            direct = np.asarray([float(value) for value in values[4:8]])
        elif values[:1] == ["P"]:
            processed[int(values[1])] = (float(values[2]), float(values[3]))
    if direct is None or set(processed) != {2, 18, 102}:
        raise ValueError(f"incomplete unresolved probe output:\n{completed.stdout}")
    return direct, processed


def width_nodes(mean: float, degrees: int) -> tuple[np.ndarray, np.ndarray]:
    if mean == 0.0 or degrees == 0:
        return np.asarray([mean]), np.asarray([1.0])
    shape = degrees / 2.0
    nodes, weights = roots_genlaguerre(ORDER, shape - 1.0)
    samples = mean * 2.0 * nodes / degrees
    probabilities = weights / gamma(shape)
    if abs(float(np.sum(probabilities)) - 1.0) > 2e-14:
        raise ValueError(f"high-order chi-square weights do not close for dof={degrees}")
    return samples, probabilities


def high_order(case: dict) -> np.ndarray:
    wave = K_WAVE * AWR / (AWR + 1.0) * math.sqrt(ENERGY)
    phase = wave * AP
    statistical = (2.0 * abs(SEQUENCE_SPIN) + 1.0) / (
        2.0 * (2.0 * abs(SPIN) + 1.0)
    )
    neutron_mean = (
        case["neutron"] * math.sqrt(ENERGY) * case["neutron_dof"]
    )
    neutrons, neutron_weights = width_nodes(neutron_mean, case["neutron_dof"])
    fissions, fission_weights = width_nodes(
        case["fission"], case["fission_dof"]
    )
    competitive, competitive_weights = width_nodes(
        case["competitive"], case["competitive_dof"]
    )
    fission_grid = fissions[:, None]
    competitive_grid = competitive[None, :]
    joint_weights = fission_weights[:, None] * competitive_weights[None, :]
    averages = np.zeros(4)
    for neutron, neutron_weight in zip(neutrons, neutron_weights):
        total = neutron + case["capture"] + fission_grid + competitive_grid
        weight = neutron_weight * joint_weights
        averages[0] += np.sum(weight * neutron * neutron / total)
        averages[1] += np.sum(weight * neutron * case["capture"] / total)
        averages[2] += np.sum(weight * neutron * fission_grid / total)
        averages[3] += np.sum(weight * neutron * competitive_grid / total)

    scale = (
        2.0
        * math.pi**2
        / wave**2
        * statistical
        / case["spacing"]
    )
    result = scale * averages
    sin_squared = math.sin(phase) ** 2
    result[0] += 4.0 * math.pi / wave**2 * sin_squared
    result[0] -= (
        4.0
        * math.pi**2
        / wave**2
        * statistical
        * neutron_mean
        * sin_squared
        / case["spacing"]
    )
    return result


def relative(actual: float, reference: float) -> float:
    return float(abs(actual - reference) / max(abs(actual), abs(reference), 1e-300))


def main() -> None:
    if not DUMP.exists():
        raise SystemExit(f"missing P10 G3 probe binary: {DUMP}")
    comparisons = {}
    maximum_relative = 0.0
    maximum_addition_relative = 0.0
    for case in CASES:
        actual, processed = probe(case, 0)
        reference = high_order(case)
        channels = {}
        for index, channel in enumerate(
            ("elastic", "capture", "fission", "competitive")
        ):
            error = relative(actual[index], reference[index])
            if max(abs(actual[index]), abs(reference[index])) >= COMPARISON_FLOOR_B:
                maximum_relative = max(maximum_relative, error)
            channels[channel] = {
                "rust_b": float(actual[index]),
                "reference_b": float(reference[index]),
                "relative": error,
            }

        addition = {}
        for mt, channel_index in ((2, 0), (18, 2), (102, 1)):
            background, value = processed[mt]
            expected = background + actual[channel_index]
            error = relative(value, expected)
            maximum_addition_relative = max(maximum_addition_relative, error)
            addition[str(mt)] = {
                "background_b": background,
                "processed_b": value,
                "expected_b": expected,
                "relative": error,
            }
        comparisons[case["case"]] = {
            "parameters": case,
            "channels": channels,
            "lssf0_addition": addition,
        }

    lssf1_direct, lssf1_processed = probe(CASES[-1], 1)
    lssf1_maximum_absolute = float(np.max(np.abs(lssf1_direct)))
    lssf1_background_maximum_absolute = max(
        abs(value - background) for background, value in lssf1_processed.values()
    )
    passed = (
        maximum_relative <= 1e-10
        and maximum_addition_relative <= 1e-12
        and lssf1_maximum_absolute <= 1e-12
        and lssf1_background_maximum_absolute <= 1e-12
    )
    output = {
        "schema": "actinv-p10-g3-unresolved-quadrature-1",
        "inputs": {
            "resonance_rs_sha256": sha256(
                ROOT / "crates" / "actinv-data" / "src" / "resonance.rs"
            ),
            "dump_rs_sha256": sha256(
                ROOT / "crates" / "actinv-data" / "src" / "bin" / "dump.rs"
            ),
        },
        "reference": {
            "method": "independent generalized Gauss-Laguerre chi-square quadrature",
            "order": ORDER,
            "scipy_generated_nodes": True,
            "njoy_hwang_constants_imported": False,
            "conditioning": "capture-dominated synthetic cases isolate normalized Hwang moments",
            "degrees_of_freedom_covered": [1, 2, 3, 4],
            "comparison_floor_b": COMPARISON_FLOOR_B,
        },
        "cases": comparisons,
        "maximum_quadrature_relative": maximum_relative,
        "maximum_lssf0_addition_relative": maximum_addition_relative,
        "lssf1": {
            "maximum_resonance_absolute_b": lssf1_maximum_absolute,
            "maximum_background_absolute_b": lssf1_background_maximum_absolute,
        },
        "pass": bool(passed),
    }
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if passed else 2)


if __name__ == "__main__":
    main()
