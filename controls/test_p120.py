"""Fail-closed regressions for the P120 independent control."""
import copy
import math
import unittest

from check_p120 import analytic_steps, check_analytic, close_vectors, frozen_case
from p120_evaluated_smoke import compare_sparse_steps


class IndependentControl(unittest.TestCase):
    def document(self):
        return {"steps": [{"t_s": item["t_s"],
                           "inventory": [{"nuclide": name, "atoms_per_g": atoms}
                                         for name, atoms in item["atoms"].items()],
                           "activity_Bq_per_g": item["activity"],
                           "heat_W_per_g": item["heat"]} for item in analytic_steps()]}

    def test_cooling_preserves_parent_and_halves_products_by_half_life(self):
        steps, case = analytic_steps(), frozen_case()
        self.assertEqual(steps[0]["atoms"]["Fe56"], steps[1]["atoms"]["Fe56"])
        for name, half_life in case["half_life_s"].items():
            self.assertAlmostEqual(steps[1]["atoms"][name] / steps[0]["atoms"][name],
                                   math.exp(-math.log(2) * .2 / half_life), places=14)

    def test_zero_heat_does_not_pass_small_absolute_inventory_tolerance(self):
        document = self.document()
        self.assertEqual(check_analytic(document), [])
        document["steps"][0]["heat_W_per_g"]["total"] = 0.0
        self.assertTrue(check_analytic(document))

    def test_missing_inventory_and_nonfinite_values_fail(self):
        document = self.document()
        document["steps"][0]["inventory"] = []
        self.assertTrue(check_analytic(document))
        for actual in (math.nan, math.inf, -math.inf, None):
            self.assertTrue(close_vectors(actual, 1.0))

    def test_parity_requires_complete_shape(self):
        expected = self.document()
        actual = copy.deepcopy(expected)
        actual["steps"][0]["extra"] = 1
        self.assertTrue(close_vectors(actual, expected))
        self.assertTrue(close_vectors({"steps": []}, expected))

    def test_sparse_omission_of_large_inventory_or_activity_fails(self):
        actual = [{"t_s": 300.0, "flux": 1.0, "heat_W_per_g": {"total": 0.0},
                   "inventory": [{"nuclide": "Fe56", "Z": 26, "A": 56,
                                  "LISO": 0, "atoms_per_g": 1.0}],
                   "activity_Bq_per_g": {"Mn56": 1.0}}]
        expected = copy.deepcopy(actual)
        expected[0]["inventory"] = []
        self.assertFalse(compare_sparse_steps(actual, expected)["pass"])
        expected = copy.deepcopy(actual)
        expected[0]["activity_Bq_per_g"] = {}
        self.assertFalse(compare_sparse_steps(actual, expected)["pass"])

    def test_sparse_omission_of_tiny_values_passes_and_is_reported(self):
        actual = [{"t_s": 300.0, "flux": 1.0, "heat_W_per_g": {"total": 0.0},
                   "inventory": [{"nuclide": "Fe56", "Z": 26, "A": 56,
                                  "LISO": 0, "atoms_per_g": 1.0e-52}],
                   "activity_Bq_per_g": {"Mn56": 1.0e-69}}]
        expected = [{"t_s": 300.0, "flux": 1.0, "heat_W_per_g": {"total": 0.0},
                     "inventory": [], "activity_Bq_per_g": {}}]
        comparison = compare_sparse_steps(actual, expected)
        self.assertTrue(comparison["pass"], comparison["differences"])
        self.assertEqual(comparison["unmatched_inventory"], [{
            "step": 0, "nuclide": "Fe56", "actual_atoms_per_g": 1.0e-52,
            "expected_atoms_per_g": 0.0,
        }])
        self.assertEqual(comparison["unmatched_activity"], [{
            "step": 0, "nuclide": "Mn56", "actual_Bq_per_g": 1.0e-69,
            "expected_Bq_per_g": 0.0,
        }])

    def test_sparse_inventory_duplicates_and_shared_metadata_must_match(self):
        step = {"t_s": 1.0, "flux": 0.0, "heat_W_per_g": {"total": 0.0},
                "inventory": [{"nuclide": "Fe56", "Z": 26, "A": 56,
                               "LISO": 0, "atoms_per_g": 1.0}],
                "activity_Bq_per_g": {}}
        duplicate = copy.deepcopy(step)
        duplicate["inventory"].append(copy.deepcopy(duplicate["inventory"][0]))
        self.assertFalse(compare_sparse_steps([duplicate], [step])["pass"])
        metadata_mismatch = copy.deepcopy(step)
        metadata_mismatch["inventory"][0]["A"] = 57
        self.assertFalse(compare_sparse_steps([metadata_mismatch], [step])["pass"])

    def test_sparse_comparison_requires_time_flux_and_heat(self):
        step = {"t_s": 1.0, "flux": 0.0, "heat_W_per_g": {"total": 0.0},
                "inventory": [], "activity_Bq_per_g": {}}
        for field in ("t_s", "flux", "heat_W_per_g"):
            incomplete = {key: value for key, value in step.items() if key != field}
            self.assertFalse(compare_sparse_steps([incomplete], [incomplete])["pass"])


if __name__ == "__main__":
    unittest.main()
