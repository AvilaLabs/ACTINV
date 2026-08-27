#!/usr/bin/env python3
"""Regression tests for tolerance-aware end-to-end CI result validation."""

import copy
import unittest

from ci_result import baseline_mismatches, matches_baseline


BASELINE = {
    "library_targets": 10,
    "expected_first_cooling_W_per_g": 1.1497333991518499e-7,
    "cli_first_cooling_W_per_g": 1.1497333991518499e-7,
    "max_abs_deviation_cli_W_per_g": 0.0,
    "max_abs_deviation_python_W_per_g": 0.0,
    "criterion_abs_W_per_g": 1.0e-17,
    "cli_equals_python": True,
    "mode": "trace",
    "pruned_states": 36,
    "pass": True,
}


class CiResultTests(unittest.TestCase):
    def test_roundoff_within_recorded_criterion_passes(self):
        result = copy.deepcopy(BASELINE)
        result["max_abs_deviation_cli_W_per_g"] = 5.293955920339377e-23
        result["max_abs_deviation_python_W_per_g"] = 5.293955920339377e-23
        self.assertTrue(matches_baseline(result, BASELINE))

    def test_numerical_error_over_recorded_criterion_fails(self):
        result = copy.deepcopy(BASELINE)
        result["max_abs_deviation_cli_W_per_g"] = 1.01e-17
        self.assertEqual(baseline_mismatches(result, BASELINE), ["max_abs_deviation_cli_W_per_g"])

    def test_categorical_regression_fails(self):
        result = copy.deepcopy(BASELINE)
        result["pruned_states"] += 1
        self.assertEqual(baseline_mismatches(result, BASELINE), ["pruned_states"])

    def test_non_finite_value_fails(self):
        result = copy.deepcopy(BASELINE)
        result["cli_first_cooling_W_per_g"] = float("nan")
        self.assertEqual(baseline_mismatches(result, BASELINE), ["cli_first_cooling_W_per_g"])


if __name__ == "__main__":
    unittest.main()
