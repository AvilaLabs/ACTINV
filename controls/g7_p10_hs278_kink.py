#!/usr/bin/env python3
"""P10-G7 independent SIGMA1 control for the Hs-278 thermal-threshold repair."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from g1_p10_builder import parse_evaluations  # noqa: E402

SOURCE = Path(
    os.environ.get(
        "ACTINV_P10_HS278",
        "/home/connoravila/nuclear-data/tendl-2025/files/n-working/n-Hs278.tendl",
    )
)
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target/release/dump"))
RESULT = ROOT / "results/g7_p10_hs278_kink.json"
SOURCE_SHA256 = "0563c68e7db9705394b64739546d3ef624442a5b1c3370343127722519c7e1bb"
AMENDMENT = ROOT / "protocols/ACTINV-P10_AMENDMENT_H.md"
AMENDMENT_SHA256 = "7c2c121ec2007696e824c1aa3ff3b948bf52f79746313b9bc2f6b5661704519a"
ADDRESS_SPACE_BYTES = 1 * 1024**3
TEMPERATURE_K = 293.6
THRESHOLD_EV = 2.0e5
LINEARIZATION_TOLERANCE = 2e-4
SCALE_FLOOR_B = 1e-6
KB_EV_PER_K = 8.617333262e-5
SIGMA1_WINDOW = 8.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def limited(command: list[object], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["prlimit", f"--as={ADDRESS_SPACE_BYTES}", "--", *(str(item) for item in command)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def independent_sigma1(
    energy: np.ndarray,
    sigma: np.ndarray,
    temperature_k: float,
    awr: float,
    output: np.ndarray,
) -> np.ndarray:
    """Directly quadrature the positive SIGMA1 Gaussian around this high-energy threshold."""
    kt = KB_EV_PER_K * temperature_k / awr
    x = np.sqrt(energy / kt)
    values = []
    for output_energy in output:
        y = math.sqrt(float(output_energy) / kt)
        window_low = y - SIGMA1_WINDOW
        window_high = y + SIGMA1_WINDOW
        first = max(0, int(np.searchsorted(x, window_low, side="right")) - 1)
        last = min(len(x) - 1, int(np.searchsorted(x, window_high, side="left")))
        integral = 0.0
        for index in range(first, last + 1):
            if index + 1 >= len(x):
                break
            low = max(float(x[index]), window_low)
            high = min(float(x[index + 1]), window_high)
            energy_width = float(energy[index + 1] - energy[index])
            if high <= low or energy_width <= 0.0:
                continue
            slope = float(sigma[index + 1] - sigma[index]) / energy_width
            base_energy = float(energy[index])
            base_sigma = float(sigma[index])

            def integrand(value: float) -> float:
                local_energy = kt * value * value
                local_sigma = base_sigma + slope * (local_energy - base_energy)
                return local_sigma * value * value * math.exp(-((value - y) ** 2))

            integral += quad(
                integrand,
                low,
                high,
                epsabs=1e-13,
                epsrel=1e-12,
                limit=100,
            )[0]
        # Here y > 1,400, so the negative-image kernel exp(-(x+y)^2), endpoint tails and the truncated Gaussian
        # window are each far below binary64 resolution. The retained integral is the independent SIGMA1 value.
        values.append(integral / (y * y * math.sqrt(math.pi)))
    return np.asarray(values, dtype=np.float64)


def main() -> None:
    evaluation = parse_evaluations(SOURCE)[0]
    table = evaluation["mf3"]["18"]
    awr = float(evaluation["metadata"]["awr"])
    width = math.sqrt(4.0 * KB_EV_PER_K * TEMPERATURE_K * THRESHOLD_EV / awr)
    # The production seed uses width/8. Sampling at width/16 checks every seed point and every intervening midpoint.
    energies = np.asarray(
        [THRESHOLD_EV + width * index / 16.0 for index in range(-128, 129)],
        dtype=np.float64,
    )

    probe = limited([DUMP, "processed-xs", SOURCE, 18, TEMPERATURE_K, *energies])
    certificate = None
    actual = []
    for line in probe.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["C"]:
            certificate = {
                "zero_k_points": int(fields[1]),
                "output_points": int(fields[2]),
                "zero_k_refinement_passes": int(fields[3]),
                "output_refinement_passes": int(fields[4]),
            }
        elif fields[:1] == ["X"]:
            actual.append(float(fields[2]))
    actual_array = np.asarray(actual, dtype=np.float64)
    independent = independent_sigma1(
        np.asarray(table["x"], dtype=np.float64),
        np.asarray(table["y"], dtype=np.float64),
        TEMPERATURE_K,
        awr,
        energies,
    )
    if len(actual_array) == len(energies):
        scale = np.maximum.reduce(
            [np.abs(actual_array), np.abs(independent), np.full_like(independent, SCALE_FLOOR_B)]
        )
        fractions = np.abs(actual_array - independent) / (LINEARIZATION_TOLERANCE * scale)
        worst_index = int(np.argmax(fractions))
        maximum_fraction = float(fractions[worst_index])
        worst = {
            "energy_eV": float(energies[worst_index]),
            "rust_b": float(actual_array[worst_index]),
            "independent_b": float(independent[worst_index]),
            "tolerance_fraction": maximum_fraction,
        }
    else:
        maximum_fraction = math.inf
        worst = None

    with tempfile.TemporaryDirectory(prefix="actinv-p10-hs278-") as raw:
        work = Path(raw)
        library = work / "hs278.npz"
        build = limited(
            [
                ACTINV,
                "build-library",
                SOURCE,
                library,
                "--format",
                "tendl",
                "--projectile",
                "neutron",
                "--groups",
                "fispact-709",
                "--temperature-K",
                TEMPERATURE_K,
                "--workers",
                1,
                "--cache",
                work / "cache",
            ]
        )
        index_path = work / "hs278_index.json"
        index = json.loads(index_path.read_text()) if index_path.is_file() else None
        isolated = {
            "returncode": build.returncode,
            "stderr_tail": build.stderr[-1000:],
            "targets": len(index["targets"]) if index else None,
            "rows": index["n_rows"] if index else None,
            "builder_fingerprint": index["builder_fingerprint"] if index else None,
            "pass": build.returncode == 0 and index is not None and len(index["targets"]) == 1,
        }

    comparison = {
        "samples": len(energies),
        "spacing_doppler_widths": 1.0 / 16.0,
        "span_doppler_widths": [-8.0, 8.0],
        "relative_tolerance": LINEARIZATION_TOLERANCE,
        "scale_floor_b": SCALE_FLOOR_B,
        "maximum_tolerance_fraction": maximum_fraction,
        "worst": worst,
        "pass": probe.returncode == 0
        and certificate is not None
        and len(actual_array) == len(energies)
        and maximum_fraction <= 1.0,
    }
    result = {
        "schema": "actinv-p10-g7-hs278-kink-1",
        "gate": "P10-G7",
        "source_file": SOURCE.name,
        "source_sha256": sha256(SOURCE),
        "expected_source_sha256": SOURCE_SHA256,
        "amendment_h_sha256": sha256(AMENDMENT),
        "temperature_K": TEMPERATURE_K,
        "threshold_eV": THRESHOLD_EV,
        "doppler_width_eV": width,
        "certificate": certificate,
        "independent_sigma1": comparison,
        "isolated_build": isolated,
    }
    result["pass"] = (
        result["source_sha256"] == SOURCE_SHA256
        and result["amendment_h_sha256"] == AMENDMENT_SHA256
        and comparison["pass"]
        and isolated["pass"]
    )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
