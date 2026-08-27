#!/usr/bin/env python3
"""P10-G7 independent high-precision control for the EAF MF=9 product fast path."""
from __future__ import annotations

import bisect
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EAF = Path(
    os.environ.get("ACTINV_EAF2010_DIR", "/home/connoravila/nuclear-data/eaf-2010/files")
)
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
RESULT = ROOT / "results/g7_p10_eaf_product.json"
AMENDMENT = ROOT / "protocols/ACTINV-P10_AMENDMENT_F.md"
AMENDMENT_SHA256 = "1746c478a3e31025c0a98446f8567daac67a192eea08b27c76a03503c4a42e49"
RELATIVE_TOLERANCE = Decimal("2e-12")
ABSOLUTE_TOLERANCE_B = Decimal("1e-14")
SCORE_FLOOR_B = Decimal("1e-12")
TIMEOUT_SECONDS = 5.0

HOTSPOTS = {
    "n_3037_30-ZN-68.dat": "aafaa5b1424883bc1100545d9d577f9aceab987da5f441df8bafc2d0f43666f5",
    "n_3043_30-ZN-70.dat": "68d296300a38cc73bd6b233a7ead07c6c13767ba62c2e4526ff7e574381759b5",
    "n_3131_31-GA-71.dat": "e08c9a949158c56c14f5baf5378c3746a1440306a38adc4803d29bf704682f66",
    "n_3225_32-GE-70.dat": "02041dacd224a97aab4aae26c32b8ceab501946490581f0ee09465a3ce8d40f5",
    "n_3419_34-SE-72.dat": "a0b17466726c96e537b4f53cd3f71e0cce87a01765d76a51ba09f4e52cd6b2e8",
    "n_3922_39-Y-88.dat": "e0e1c8851b6a3c94de4e309c8ef83c4f74d1c7c17ea272b702ea43c0e663c951",
}


def load_independent_parser():
    path = ROOT / "controls/g1_p10_builder.py"
    specification = importlib.util.spec_from_file_location("p10_g1", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load the independent G1 parser")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.parse_evaluations


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def decimal(value: float) -> Decimal:
    return Decimal.from_float(float(value))


def interpolation_law(table: dict, segment: int) -> int:
    endpoint = segment + 2
    return next(law for nbt, law in table["interpolation"] if endpoint <= nbt)


def interval_terms(table: dict, low: float, high: float) -> list[tuple[Decimal, Decimal]]:
    middle = low + 0.5 * (high - low)
    x = table["x"]
    if middle < x[0] or middle > x[-1]:
        return []
    segment = min(max(bisect.bisect_right(x, middle) - 1, 0), len(x) - 2)
    x1, x2 = decimal(x[segment]), decimal(x[segment + 1])
    y1, y2 = decimal(table["y"][segment]), decimal(table["y"][segment + 1])
    energy = decimal(low)
    law = interpolation_law(table, segment)
    if law == 1:
        return [(y1, Decimal(0))]
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        value = y1 + slope * (energy - x1)
        scaled_slope = slope * energy
        return [(value - scaled_slope, Decimal(0)), (scaled_slope, Decimal(1))]
    if law == 5:
        power = (y2 / y1).ln() / (x2 / x1).ln()
        value = y1 * (power * (energy / x1).ln()).exp()
        return [(value, power)]
    raise ValueError(f"actual hotspot unexpectedly uses INT={law}")


def multiply_terms(
    left: list[tuple[Decimal, Decimal]], right: list[tuple[Decimal, Decimal]]
) -> list[tuple[Decimal, Decimal]]:
    combined: dict[Decimal, Decimal] = {}
    for left_coefficient, left_power in left:
        for right_coefficient, right_power in right:
            power = left_power + right_power
            combined[power] = combined.get(power, Decimal(0)) + (
                left_coefficient * right_coefficient
            )
    return [(coefficient, power) for power, coefficient in combined.items()]


def interval_integral(tables: list[dict], low: float, high: float) -> Decimal:
    terms = [(Decimal(1), Decimal(0))]
    for table in tables:
        factor = interval_terms(table, low, high)
        if not factor:
            return Decimal(0)
        terms = multiply_terms(terms, factor)
    log_ratio = (decimal(high) / decimal(low)).ln()
    values = []
    for coefficient, power in terms:
        if power == 0:
            factor = log_ratio
        else:
            factor = ((power * log_ratio).exp() - Decimal(1)) / power
        values.append(coefficient * factor)
    return sum(values, Decimal(0))


def collapse_product(tables: list[dict], bounds: np.ndarray) -> list[Decimal]:
    collapsed = []
    for low_value, high_value in zip(bounds[:-1], bounds[1:]):
        low, high = float(low_value), float(high_value)
        breaks = [low, high]
        for table in tables:
            begin = bisect.bisect_right(table["x"], low)
            end = bisect.bisect_left(table["x"], high)
            breaks.extend(table["x"][begin:end])
        breaks = sorted(set(breaks))
        integral = sum(
            (interval_integral(tables, left, right) for left, right in zip(breaks[:-1], breaks[1:])),
            Decimal(0),
        )
        collapsed.append(integral / (decimal(high) / decimal(low)).ln())
    return collapsed


def build_source(source: Path, output: Path, cache: Path) -> tuple[dict, float]:
    started = time.monotonic()
    completed = subprocess.run(
        [
            str(ACTINV),
            "build-library",
            str(source),
            str(output),
            "--format",
            "eaf",
            "--projectile",
            "neutron",
            "--groups",
            "fispact-709",
            "--temperature-K",
            "293.6",
            "--workers",
            "1",
            "--cache",
            str(cache),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT_SECONDS,
    )
    seconds = time.monotonic() - started
    if completed.returncode:
        raise RuntimeError(f"hotspot build failed: {source.name}: {completed.stderr[-1000:]}")
    index_path = output.with_name(f"{output.stem}_index.json")
    return json.loads(index_path.read_text()), seconds


def score(
    actual: np.ndarray, expected: list[Decimal], identity: tuple[int, int, int, int]
) -> dict[str, object]:
    maximum_relative = Decimal(0)
    maximum_fraction = Decimal(0)
    worst = None
    failures = []
    scored = 0
    for group, (actual_value, expected_value) in enumerate(zip(actual, expected)):
        actual_decimal = decimal(float(actual_value))
        magnitude = max(abs(actual_decimal), abs(expected_value))
        if magnitude < SCORE_FLOOR_B:
            continue
        scored += 1
        absolute = abs(actual_decimal - expected_value)
        relative = absolute / magnitude
        tolerance = max(ABSOLUTE_TOLERANCE_B, RELATIVE_TOLERANCE * magnitude)
        fraction = absolute / tolerance
        if relative > maximum_relative:
            maximum_relative = relative
        if fraction > maximum_fraction:
            maximum_fraction = fraction
            worst = {
                "identity": list(identity),
                "group": group,
                "actual_b": float(actual_value),
                "independent_b": float(expected_value),
                "relative": float(relative),
                "tolerance_fraction": float(fraction),
            }
        if fraction > 1 and len(failures) < 10:
            failures.append(
                {
                    "identity": list(identity),
                    "group": group,
                    "actual_b": float(actual_value),
                    "independent_b": float(expected_value),
                    "relative": float(relative),
                    "tolerance_fraction": float(fraction),
                }
            )
    return {
        "scored_groups": scored,
        "maximum_relative": float(maximum_relative),
        "maximum_tolerance_fraction": float(maximum_fraction),
        "worst": worst,
        "failure_examples": failures,
        "pass": not failures,
    }


def main() -> None:
    parse_evaluations = load_independent_parser()
    cases = {}
    with localcontext() as context, tempfile.TemporaryDirectory(
        prefix="actinv-p10-eaf-products-"
    ) as raw:
        context.prec = 80
        work = Path(raw)
        for name, expected_hash in HOTSPOTS.items():
            source = EAF / name
            if sha256(source) != expected_hash:
                raise ValueError(f"source hash mismatch: {name}")
            output = work / f"{source.stem}.npz"
            index, seconds = build_source(source, output, work / "cache")
            evaluation = parse_evaluations(source)[0]
            if set(evaluation["mf9"]) != {"102"}:
                raise ValueError(f"unexpected hotspot MF=9 inventory: {name}")
            with np.load(output) as library:
                rows = library["rows"]
                sigma = library["sig"]
                bounds = library["bounds"]
            comparisons = []
            for product in evaluation["mf9"]["102"]:
                identity = (102, int(product["zap"]), int(product["lfs"]), 9)
                matches = np.flatnonzero(
                    np.all(rows[:, 1:] == np.asarray(identity, dtype=np.int64), axis=1)
                )
                if len(matches) != 1:
                    raise ValueError(f"expected one Rust row {name}/{identity}")
                independent = collapse_product(
                    [evaluation["mf3"]["102"], product["table"]], bounds
                )
                comparisons.append(score(sigma[matches[0]], independent, identity))
            cases[name] = {
                "source_sha256": expected_hash,
                "builder_fingerprint": index["builder_fingerprint"],
                "rows": index["n_rows"],
                "product_rows": len(comparisons),
                "completed_below_five_seconds": seconds < TIMEOUT_SECONDS,
                "comparisons": comparisons,
                "pass": seconds < TIMEOUT_SECONDS
                and len(comparisons) == 2
                and all(comparison["pass"] for comparison in comparisons),
            }
    output = {
        "schema": "actinv-p10-g7-eaf-product-1",
        "gate": "P10-G7",
        "precision_decimal_digits": 80,
        "relative_tolerance": float(RELATIVE_TOLERANCE),
        "absolute_tolerance_b": float(ABSOLUTE_TOLERANCE_B),
        "former_timeout_seconds": TIMEOUT_SECONDS,
        "amendment_f_sha256": sha256(AMENDMENT),
        "cases": cases,
        "pass": sha256(AMENDMENT) == AMENDMENT_SHA256
        and len(cases) == len(HOTSPOTS)
        and all(case["pass"] for case in cases.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
