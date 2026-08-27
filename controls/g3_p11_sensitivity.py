#!/usr/bin/env python3
"""P11-G3: CRAM-48 dense references and end-to-end analytic sensitivities."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.linalg import expm

from p11_fixtures import SIGMA, make_fixture, specification, write_json, write_library


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p11_sensitivity.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
CRAM_PROBE = Path(
    os.environ.get("ACTINV_CRAM_PROBE", ROOT / "target" / "release" / "cram_probe")
)
COEFFICIENTS = ROOT / "data" / "cram_coefficients.json"
RESPONSES = [
    "heat.total",
    "heat.alpha",
    "heat.beta",
    "heat.gamma",
    "activity:Mn56",
    "activity:Mn57",
]


def command(arguments, timeout=180):
    result = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"command failed: {' '.join(map(str, arguments))}\n{result.stderr}")
    return result


def run_spec(work: Path, name: str, value: dict) -> dict:
    spec_path, result_path = work / f"{name}.json", work / f"{name}.result.json"
    write_json(spec_path, value)
    command([ACTINV, "run", spec_path, result_path])
    return json.loads(result_path.read_text())


def response(step: dict, selector: str) -> float:
    if selector == "heat.total":
        return step["heat_W_per_g"]["total"]
    if selector == "heat.alpha":
        return step["heat_W_per_g"]["alpha"]
    if selector == "heat.beta":
        return step["heat_W_per_g"]["beta"]
    if selector == "heat.gamma":
        return step["heat_W_per_g"]["gamma"]
    return step["activity_Bq_per_g"].get(selector.split(":", 1)[1], 0.0)


def cram_dense_controls(work: Path) -> dict:
    coefficients = json.loads(COEFFICIENTS.read_text())
    networks = {
        "two_state_source": (
            np.asarray([[-0.2, 0.0], [0.2, -0.05]]),
            np.asarray([1.0, 0.0]),
            0.7,
        ),
        "three_state_chain": (
            np.asarray([[-1.3, 0.0, 0.0], [1.3, -0.17, 0.0], [0.0, 0.17, -0.01]]),
            np.asarray([2.0, 0.0, 0.0]),
            3.2,
        ),
        "stiff_branch": (
            np.asarray([[-20.0, 0.0, 0.0], [12.0, -0.3, 0.0], [8.0, 0.3, -0.001]]),
            np.asarray([1.0, 0.0, 0.0]),
            1.0,
        ),
    }
    output = {}
    for order, key in ((16, "Cram16Solver"), (48, "Cram48Solver")):
        coefficient = coefficients[key]
        cases = {}
        for name, (matrix, initial, duration) in networks.items():
            entries = [
                (row, column, matrix[row, column])
                for row in range(len(matrix))
                for column in range(len(matrix))
                if matrix[row, column] != 0.0
            ]
            input_path, output_path = work / f"cram-{order}-{name}.in", work / f"cram-{order}-{name}.out"
            lines = [f"{len(matrix)} {len(entries)}"]
            lines.extend(f"{row} {column} {value:.17e}" for row, column, value in entries)
            lines.extend(
                [
                    f"{duration:.17e}",
                    " ".join(f"{value:.17e}" for value in initial),
                    f"{coefficient['alpha0']:.17e}",
                    str(len(coefficient["theta_re"])),
                ]
            )
            lines.extend(
                f"{tr:.17e} {ti:.17e} {ar:.17e} {ai:.17e}"
                for tr, ti, ar, ai in zip(
                    coefficient["theta_re"],
                    coefficient["theta_im"],
                    coefficient["alpha_re"],
                    coefficient["alpha_im"],
                )
            )
            input_path.write_text("\n".join(lines) + "\n")
            command([CRAM_PROBE, input_path, output_path, "1"])
            observed = np.loadtxt(output_path, comments="#")
            expected = expm(matrix * duration) @ initial
            absolute = np.abs(observed - expected)
            resolvable = np.abs(expected) >= max(coefficient["alpha0"] * np.max(initial), 1.0e-30)
            relative = np.divide(absolute, np.abs(expected), out=np.zeros_like(absolute), where=resolvable)
            cases[name] = {
                "maximum_absolute": float(np.max(absolute)),
                "maximum_resolvable_relative": float(np.max(relative)),
                "pass": bool(np.all((relative <= 1.0e-12) | (absolute <= 1.0e-14))),
            }
        output[f"CRAM-{order}"] = {
            "cases": cases,
            "pass": all(case["pass"] for case in cases.values()),
        }
    return {"orders": output, "pass": all(item["pass"] for item in output.values())}


def normalized(value: dict) -> dict:
    value = json.loads(json.dumps(value))
    value.pop("ms", None)
    return value


def sensitivity_controls(work: Path, fixture: dict[str, Path]) -> dict:
    cases = {}
    maximum_relative = maximum_absolute = 0.0
    for mode in ("trace", "coupled"):
        for order in (16, 48):
            case_name = f"{mode}-cram{order}"
            nominal = run_spec(work, case_name, specification(fixture, mode=mode, cram_order=order))
            parameter_rows = sorted(
                {
                    record["parameter"]["library_row"]
                    for record in nominal["steps"][0]["uncertainty"]["responses"]["heat.total"]["sensitivities"]
                }
            )
            perturbations = {}
            step_count = len(nominal["steps"])
            for row in parameter_rows:
                h = 1.0e-4
                samples = {}
                for multiplier in (-2, -1, 1, 2):
                    sigma = SIGMA.copy()
                    sigma[row, 0] += multiplier * h
                    library = work / f"{case_name}-row{row}-{multiplier:+d}.npz"
                    write_library(library, sigma)
                    value = specification(
                        fixture,
                        mode=mode,
                        cram_order=order,
                        library=library,
                        uncertainty=False,
                    )
                    samples[multiplier] = run_spec(
                        work, f"{case_name}-row{row}-{multiplier:+d}", value
                    )
                perturbations[row] = (h, samples)
            comparisons = []
            for step_index in range(step_count):
                for selector in RESPONSES:
                    records = nominal["steps"][step_index]["uncertainty"]["responses"][selector]["sensitivities"]
                    analytic = {
                        record["parameter"]["library_row"]: record["value"] for record in records
                    }
                    for row in parameter_rows:
                        h, samples = perturbations[row]
                        sampled_responses = {
                            multiplier: response(samples[multiplier]["steps"][step_index], selector)
                            for multiplier in (-2, -1, 1, 2)
                        }
                        raw_finite = (
                            sampled_responses[-2]
                            - 8.0 * sampled_responses[-1]
                            + 8.0 * sampled_responses[1]
                            - sampled_responses[2]
                        ) / (12.0 * h)
                        structural_zero = (selector, row) in {
                            ("activity:Mn56", 3),
                            ("activity:Mn57", 1),
                        }
                        sample_span = max(sampled_responses.values()) - min(sampled_responses.values())
                        roundoff_bound = (
                            128.0
                            * np.finfo(np.float64).eps
                            * max(abs(value) for value in sampled_responses.values())
                        )
                        conditioned = structural_zero and sample_span <= roundoff_bound
                        finite = 0.0 if conditioned else raw_finite
                        absolute = abs(analytic[row] - finite)
                        relative = absolute / max(abs(analytic[row]), abs(finite), 1.0e-300)
                        maximum_absolute, maximum_relative = max(maximum_absolute, absolute), max(maximum_relative, relative)
                        comparisons.append(
                            {
                                "step": step_index + 1,
                                "response": selector,
                                "library_row": row,
                                "analytic": analytic[row],
                                "five_point": finite,
                                "raw_five_point": raw_finite,
                                "structural_zero": structural_zero,
                                "roundoff_conditioned": conditioned,
                                "sample_span": sample_span,
                                "roundoff_bound": roundoff_bound,
                                "absolute": absolute,
                                "relative": relative,
                                "pass": relative <= 1.0e-4 or absolute <= 1.0e-18,
                            }
                        )
            cases[case_name] = {
                "mode": nominal["mode"],
                "pruned_states": nominal["pruned_states"],
                "parameter_rows": parameter_rows,
                "comparisons": len(comparisons),
                "maximum_relative": max((item["relative"] for item in comparisons), default=0.0),
                "maximum_absolute": max((item["absolute"] for item in comparisons), default=0.0),
                "conditioned_structural_zeros": int(
                    sum(item["roundoff_conditioned"] for item in comparisons)
                ),
                "maximum_raw_structural_zero": max(
                    (
                        abs(item["raw_five_point"])
                        for item in comparisons
                        if item["structural_zero"]
                    ),
                    default=0.0,
                ),
                "failures": [item for item in comparisons if not item["pass"]][:20],
                "pass": all(item["pass"] for item in comparisons),
            }

    default_spec = specification(fixture, mode="trace", cram_order=16, uncertainty=False)
    default_spec["options"].pop("cram_order")
    explicit_spec = specification(fixture, mode="trace", cram_order=16, uncertainty=False)
    legacy_default = run_spec(work, "legacy-default", default_spec)
    legacy_explicit = run_spec(work, "legacy-explicit", explicit_spec)
    legacy_identity = normalized(legacy_default) == normalized(legacy_explicit)
    return {
        "cases": cases,
        "maximum_relative": maximum_relative,
        "maximum_absolute": maximum_absolute,
        "legacy_default_equals_explicit_cram16": legacy_identity,
        "pass": all(case["pass"] for case in cases.values()) and legacy_identity,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-p11-g3-") as directory:
        work = Path(directory)
        fixture = make_fixture(work / "fixture")
        dense = cram_dense_controls(work)
        sensitivity = sensitivity_controls(work, fixture)
        output = {
            "dense_exponentials": dense,
            "sensitivities": sensitivity,
            "openmc_0_15_3_coefficients": "checked losslessly by controls/g0_cram_coefficients.py",
            "pass": dense["pass"] and sensitivity["pass"],
        }
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
