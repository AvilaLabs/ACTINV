#!/usr/bin/env python3
"""Locate floating-point error in the P10 EAF product-collapse fast path."""
from __future__ import annotations

import argparse
import bisect
from decimal import Decimal, localcontext
import importlib.util
import json
import math
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/home/connoravila/nuclear-data/eaf-2010/files/n_3037_30-ZN-68.dat")
DUMP = ROOT / "target/release/dump"


def load_control():
    path = ROOT / "controls/g7_p10_eaf_product.py"
    specification = importlib.util.spec_from_file_location("p10_g7_product", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load G7 product control")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def interpolation_law(table: dict, segment: int) -> int:
    endpoint = segment + 2
    return next(law for nbt, law in table["interpolation"] if endpoint <= nbt)


def interval_terms(table: dict, low: float, high: float) -> list[tuple[float, float]]:
    middle = low + 0.5 * (high - low)
    x = table["x"]
    if middle < x[0] or middle > x[-1]:
        return [(0.0, 0.0)]
    segment = min(max(bisect.bisect_right(x, middle) - 1, 0), len(x) - 2)
    x1, x2 = x[segment], x[segment + 1]
    y1, y2 = table["y"][segment], table["y"][segment + 1]
    law = interpolation_law(table, segment)
    if law == 1:
        return [(y1, 0.0)]
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        value = y1 + slope * (low - x1)
        scaled_slope = slope * low
        return [(value - scaled_slope, 0.0), (scaled_slope, 1.0)]
    if law == 5:
        power = math.log1p((y2 - y1) / y1) / math.log1p((x2 - x1) / x1)
        value = y1 * math.exp(power * math.log1p((low - x1) / x1))
        return [(value, power)]
    raise ValueError(f"unexpected INT={law}")


def expm1_over_x(value: float) -> float:
    if abs(value) < 1e-8:
        return 1.0 + value * (0.5 + value * (1.0 / 6.0 + value / 24.0))
    return math.expm1(value) / value


def neumaier(values: list[float]) -> float:
    total = 0.0
    correction = 0.0
    for value in values:
        next_value = total + value
        if abs(total) >= abs(value):
            correction += (total - next_value) + value
        else:
            correction += (value - next_value) + total
        total = next_value
    return total + correction


def interval_float(tables: list[dict], low: float, high: float) -> float:
    terms = [(1.0, 0.0)]
    for table in tables:
        terms = [
            (left_coefficient * right_coefficient, left_power + right_power)
            for left_coefficient, left_power in terms
            for right_coefficient, right_power in interval_terms(table, low, high)
        ]
    log_ratio = math.log1p((high - low) / low)
    return neumaier(
        [
            coefficient * log_ratio * expm1_over_x(power * log_ratio)
            for coefficient, power in terms
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", type=int, default=482)
    arguments = parser.parse_args()
    control = load_control()
    rust = json.loads(
        subprocess.check_output([DUMP, "activation-json", SOURCE], text=True)
    )[0]
    tables = [rust["mf3"]["102"], rust["mf9"]["102"][0]["table"]]
    boundaries = json.loads(
        (ROOT / "crates/actinv-data/data/fispact_709_groups.json").read_text()
    )["boundaries_eV"]
    if boundaries[0] > boundaries[-1]:
        boundaries.reverse()
    low, high = boundaries[arguments.group : arguments.group + 2]
    breaks = [low, high]
    for table in tables:
        breaks.extend(value for value in table["x"] if low < value < high)
    breaks = sorted(set(breaks))

    with localcontext() as context:
        context.prec = 80
        rows = []
        for left, right in zip(breaks[:-1], breaks[1:]):
            expected = control.interval_integral(tables, left, right)
            actual = interval_float(tables, left, right)
            error = Decimal.from_float(actual) - expected
            rows.append((abs(error), error, left, right, actual, expected))
        float_total = neumaier([row[4] for row in rows])
        decimal_total = sum((row[5] for row in rows), Decimal(0))
        normalization = (Decimal.from_float(high) / Decimal.from_float(low)).ln()
        report = {
            "group": arguments.group,
            "bounds_eV": [low, high],
            "intervals": len(rows),
            "float_b": float_total / math.log(high / low),
            "decimal_b": float(decimal_total / normalization),
            "relative": float(
                abs(Decimal.from_float(float_total) - decimal_total) / abs(decimal_total)
            ),
            "largest_interval_errors": [
                {
                    "bounds_eV": [left, right],
                    "float_integral": actual,
                    "decimal_integral": float(expected),
                    "error": float(error),
                }
                for _, error, left, right, actual, expected in sorted(rows, reverse=True)[:12]
            ],
        }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
