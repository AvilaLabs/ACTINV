#!/usr/bin/env python3
"""P11-G4: direct and sampled 2x2 propagation plus production edge-case guards."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from p11_fixtures import make_fixture, specification, write_json


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g4_p11_propagation.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PROBE = Path(
    os.environ.get(
        "ACTINV_UNCERTAINTY_PROBE", ROOT / "target" / "release" / "uncertainty_probe"
    )
)
SAMPLES = 2**26
CHUNK = 2**18


def command(arguments, *, ok=True, timeout=180):
    result = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if ok != (result.returncode == 0):
        raise RuntimeError(
            f"unexpected command result {result.returncode}: {' '.join(map(str, arguments))}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result


def probe(sensitivity, covariance, *, ok=True):
    result = command(
        [
            PROBE,
            ",".join(str(value) for value in sensitivity),
            ",".join(str(value) for value in np.asarray(covariance).ravel()),
        ],
        ok=ok,
    )
    return json.loads(result.stdout) if ok else {"returncode": result.returncode, "stderr": result.stderr.strip()}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-p11-g4-") as directory:
        work = Path(directory)
        fixture = make_fixture(work / "fixture")
        spec = specification(fixture, mode="trace", cram_order=48)
        spec_path, result_path = work / "spec.json", work / "result.json"
        write_json(spec_path, spec)
        command([ACTINV, "run", spec_path, result_path])
        result = json.loads(result_path.read_text())
        response = result["steps"][-1]["uncertainty"]["responses"]["heat.total"]
        records = [
            value
            for value in response["sensitivities"]
            if value["parameter"]["library_row"] in (1, 3)
        ]
        records.sort(key=lambda value: value["parameter"]["library_row"])
        sensitivity = np.asarray([value["value"] for value in records], dtype=np.float64)
        covariance = np.asarray([[4.0e-4, 1.5e-4], [1.5e-4, 9.0e-4]])
        direct = float(sensitivity @ covariance @ sensitivity)
        rust = probe(sensitivity, covariance)
        reported = response["mf33_standard_uncertainty"] ** 2

        rng = np.random.Generator(np.random.PCG64(0xA11CE))
        cholesky = np.linalg.cholesky(covariance)
        remaining_pairs = SAMPLES // 2
        sum_square = 0.0
        while remaining_pairs:
            count = min(CHUNK, remaining_pairs)
            normal = rng.standard_normal((count, 2))
            linearized = (normal @ cholesky.T) @ sensitivity
            # The antithetic partner is -linearized and has the same square.
            sum_square += 2.0 * float(linearized @ linearized)
            remaining_pairs -= count
        sampled = sum_square / SAMPLES

        diagonal = np.diag(np.diag(covariance))
        without_cross = float(sensitivity @ diagonal @ sensitivity)
        predicted_cross_change = float(2.0 * sensitivity[0] * sensitivity[1] * covariance[0, 1])
        observed_cross_change = direct - without_cross

        perfect = np.asarray([[4.0, 6.0], [6.0, 9.0]])
        perfect_expected = float(sensitivity @ perfect @ sensitivity)
        edge_cases = {
            "zero": probe(sensitivity, np.zeros((2, 2))),
            "perfect_correlation": probe(sensitivity, perfect),
            "roundoff_negative": probe([1.0, 1.0], [1.0, -1.0, -1.0, 1.0 - 1.0e-16]),
            "materially_negative": probe([1.0, 1.0], [1.0, -2.0, -2.0, 1.0], ok=False),
            "nonfinite": probe([1.0, float("nan")], np.eye(2), ok=False),
            "dimension_mismatch": probe([1.0, 2.0], [1.0, 0.0, 1.0], ok=False),
        }
        direct_relative = abs(reported - direct) / max(abs(direct), 1.0e-300)
        rust_relative = abs(rust["variance"] - direct) / max(abs(direct), 1.0e-300)
        sample_relative = abs(sampled - direct) / max(abs(direct), 1.0e-300)
        cross_relative = abs(observed_cross_change - predicted_cross_change) / max(
            abs(predicted_cross_change), 1.0e-300
        )
        edge_pass = bool(
            edge_cases["zero"]["variance"] == 0.0
            and abs(edge_cases["perfect_correlation"]["variance"] - perfect_expected)
            <= 1.0e-12 * max(abs(perfect_expected), 1.0)
            and edge_cases["roundoff_negative"]["variance"] == 0.0
            and edge_cases["roundoff_negative"]["negative_roundoff_removed"] > 0.0
            and all(
                edge_cases[name]["returncode"] != 0
                for name in ("materially_negative", "nonfinite", "dimension_mismatch")
            )
        )
        output = {
            "samples": SAMPLES,
            "rng": "NumPy PCG64 seed 0xA11CE, chunked antithetic normals",
            "sensitivity": sensitivity.tolist(),
            "covariance_barn2": covariance.tolist(),
            "direct_variance": direct,
            "rust_variance": rust["variance"],
            "reported_variance": reported,
            "sampled_variance": sampled,
            "rust_relative": rust_relative,
            "reported_relative": direct_relative,
            "sample_relative": sample_relative,
            "without_cross_variance": without_cross,
            "predicted_cross_change": predicted_cross_change,
            "observed_cross_change": observed_cross_change,
            "cross_change_relative": cross_relative,
            "edge_cases": edge_cases,
            "edge_cases_pass": edge_pass,
        }
        output["pass"] = bool(
            rust_relative <= 1.0e-12
            and direct_relative <= 1.0e-12
            and sample_relative <= 1.0e-3
            and cross_relative <= 1.0e-12
            and edge_pass
        )
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
