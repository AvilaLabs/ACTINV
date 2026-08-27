"""Shared validation for the deterministic end-to-end CI result."""

from __future__ import annotations

import math


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def baseline_mismatches(result: dict, baseline: dict) -> list[str]:
    """Return meaningful CI baseline mismatches.

    Numerical results are controlled by the recorded absolute criterion.  Their
    diagnostic values must not also be required to equal a particular floating-
    point representation bit-for-bit.
    """

    mismatches = []
    for key in ("library_targets", "criterion_abs_W_per_g", "cli_equals_python", "mode", "pruned_states"):
        if result.get(key) != baseline.get(key):
            mismatches.append(key)

    limit = _finite_number(baseline.get("criterion_abs_W_per_g"))
    if limit is None or limit < 0.0:
        return mismatches + ["criterion_abs_W_per_g"]

    for key in ("expected_first_cooling_W_per_g", "cli_first_cooling_W_per_g"):
        actual = _finite_number(result.get(key))
        expected = _finite_number(baseline.get(key))
        if actual is None or expected is None or abs(actual - expected) > limit:
            mismatches.append(key)

    for key in ("max_abs_deviation_cli_W_per_g", "max_abs_deviation_python_W_per_g"):
        deviation = _finite_number(result.get(key))
        if deviation is None or not 0.0 <= deviation <= limit:
            mismatches.append(key)

    return mismatches


def matches_baseline(result: dict, baseline: dict) -> bool:
    return not baseline_mismatches(result, baseline)
