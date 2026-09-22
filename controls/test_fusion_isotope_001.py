#!/usr/bin/env python3
"""Independent dimensional identities and negative evidence controls; no subprocesses."""

import copy
import json
import math
import unittest

from check_fusion_isotope_001 import REFERENCE, calculate, compare, opening


class OpeningControlTests(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads(REFERENCE.read_text())

    def test_one_half_life_constant_source(self):
        # Unit source, one-second half-life and interval: N=1/(2 ln 2), A=1/2.
        p = self.reference["published"]
        p.update(source_area_m2=1e-4, emitted_neutrons_per_cm2_s=1,
                 th229_atoms_per_emitted_neutron=1, th229_half_life_years=1)
        self.reference["conventions"].update(seconds_per_year=1, bq_per_ci=1)
        result = calculate(self.reference)
        self.assertAlmostEqual(result["th229_activity_one_year_with_parent_decay_ci"], 0.5)
        self.assertAlmostEqual(result["th229_gross_activity_one_year_ci"], math.log(2))

    def test_year_cancels_from_gross_conversion(self):
        before = calculate(self.reference)
        self.reference["conventions"]["seconds_per_year"] *= 2
        after = calculate(self.reference)
        self.assertAlmostEqual(before["th229_gross_activity_one_year_ci"],
                               after["th229_gross_activity_one_year_ci"])

    def test_interception_not_counted_twice(self):
        before = calculate(self.reference)
        self.reference["published"]["target_facing_fraction"] = 1
        self.assertEqual(before, calculate(self.reference))

    def test_reject_corrupt_evidence(self):
        expected = opening(self.reference)
        mutations = [
            ("overall_status", "reproduced"), ("actinv_executed", True),
            ("actinv_executed", 0), ("protocol_sha256", "0" * 64),
            ("reference_sha256", "0" * 64), ("missing_for_reproduction", []),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(expected)
                changed[key] = value
                with self.assertRaises(ValueError):
                    compare(changed, expected)
        for value in [float("nan"), float("inf"), True, 1.8e18]:
            changed = copy.deepcopy(expected)
            changed["arithmetic"]["emitted_source_neutrons_per_s"] = value
            with self.assertRaises(ValueError):
                compare(changed, expected)

    def test_invalid_input(self):
        for value in [0, -1, float("nan"), float("inf"), True, "3e14"]:
            with self.subTest(value=value):
                changed = copy.deepcopy(self.reference)
                changed["published"]["emitted_neutrons_per_cm2_s"] = value
                with self.assertRaises(ValueError):
                    calculate(changed)


if __name__ == "__main__":
    unittest.main()
