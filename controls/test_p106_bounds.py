"""Small deterministic generator-shape regressions; the coordinator runs CI tests."""
from __future__ import annotations

import unittest

from p106_bounds_control import generate_cases


class P106BoundsPopulationTests(unittest.TestCase):
    def test_population_has_unique_ids_and_protocol_case_count(self) -> None:
        cases = generate_cases()
        self.assertEqual(len(cases), 146)
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(ids[-20:]) == {
            "empty_complete", "empty_incomplete", "required_h3", "single_t1_boundary_box",
            "mixture_t1_strict_box", "zero_crossing_contributor", "sr90_cs137_example",
            "t1_t2_separate", "t2_three_columns", "metal_nb94", "alpha_tru",
            "cm242_cross_table", "dedicated_co60", "missing_active_properties",
            "inactive_and_malformed_properties", "long_lived_unlisted", "external_h3_cross",
            "external_h3_merge", "target_coverage_change", "target_bounds_change",
        })

    def test_unknown_property_vector_keeps_empty_properties_and_upper_unknown(self) -> None:
        case = next(c for c in generate_cases() if c["id"] == "missing_active_properties")
        self.assertEqual(case["component"]["nuclide_properties"], {})
        self.assertEqual(case["component"]["targets"][0]["activity_bounds_bq"]["C14"],
                         {"lower_bq": 0.0, "upper_bq": 29600.0})


if __name__ == "__main__":
    unittest.main()
