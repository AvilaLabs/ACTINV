"""Pure source regressions for the independent P113 population and oracle."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

import p113_legacy_control as legacy
import p113_twin_control as oracle


class P113OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = oracle.frozen_fixture()
        cls.requests = cls.fixture["requests"]
        cls.by_id = {case["id"]: case for case in cls.requests}

    def test_frozen_population_exercises_required_outcomes_and_counts(self):
        counts = oracle.fixture_counts(self.fixture)
        self.assertTrue(counts["pass"], counts["errors"])
        self.assertGreaterEqual(counts["request_count"], 24)
        self.assertGreaterEqual(counts["component_target_count"], 32)
        self.assertEqual(len(counts["request_ids"]), len(set(counts["request_ids"])))
        self.assertEqual(set(counts["class_outcomes"]),
                         {"A", "B", "C", "above_class_c", "unknown"})

    def test_literal_single_row_class_boundaries_and_no_limit_behavior(self):
        below = oracle.expected_component(self.by_id["boundary_below"], "component-a", 1)
        equal = oracle.expected_component(self.by_id["boundary_equal"], "component-a", 1)
        above = oracle.expected_component(self.by_id["boundary_above"], "component-a", 1)
        self.assertEqual(below["classification"]["class"], "A")
        self.assertEqual(equal["classification"]["class"], "A")
        self.assertEqual(above["classification"]["class"], "C")

        co60 = oracle.expected_component(self.by_id["class_b"], "component-a", 1)
        self.assertEqual(co60["classification"]["class"], "B")
        co_rows = co60["classification"]["row_details"]
        self.assertTrue(any(row["nuclide"] == "Co60" and row["limit"] is None
                            and row["fraction"] is None for row in co_rows))
        # Co-60 has a finite Table 2 A column; its B/C cells have no
        # numeric limits. The unrelated Table 1 constraints remain zero.
        co_constraints = co60["classification"]["constraints"]
        table2 = {item["column"]: item for item in co_constraints if item["table"] == 2}
        self.assertGreater(table2[1]["normalized_sum"], 1.0)
        self.assertFalse(table2[1]["passes"])
        for column in (2, 3):
            self.assertEqual(table2[column]["normalized_sum"], 0.0)
            self.assertTrue(table2[column]["passes"])
        table1 = [item for item in co_constraints if item["table"] == 1]
        self.assertTrue(all(item["normalized_sum"] == 0.0 for item in table1))

    def test_independent_inventory_uses_declared_unequal_cell_masses(self):
        case = self.by_id["unequal_cell_split_equivalent"]
        result = oracle.expected_component(case, "component-a", 1)
        # Source mesh values are C-14 400/0 Bq/g; declared masses are 0.25/0.75 g.
        self.assertEqual(result["activation_activity_bq"], {"C14": 100.0})
        self.assertEqual(result["inventory_activity_bq"], {"C14": 100.0})
        self.assertEqual(result["classification"]["calculated_only_class"], "A")

    def test_alpha_group_aggregate_contains_all_eight_declared_alpha_emitters(self):
        case = self.by_id["alpha_group"]
        names = {"Pu238", "Pu239", "Pu240", "Pu242", "Am241", "Am243", "Cm243", "Cm244"}
        properties = case["spec"]["waste"]["nuclide_properties"]
        self.assertEqual(set(properties), names)
        self.assertTrue(all(properties[name]["alpha_emitting"] is True for name in names))
        result = oracle.expected_component(case, "component-a", 1)
        evaluation = result["classification"]
        alpha_rows = [row for row in evaluation["row_details"]
                      if row["table"] == 1 and row["row_id"] == "alpha_transuranic_gt5y"]
        self.assertEqual({row["nuclide"] for row in alpha_rows}, names)
        self.assertEqual(len(alpha_rows), 8)
        alpha_constraints = [item for item in evaluation["constraints"]
                             if item["table"] == 1]
        self.assertTrue(alpha_constraints)
        self.assertTrue(all(item["contributor_count"] == 8
                            and item["contributors"] == sorted(names)
                            for item in alpha_constraints))

    def test_split_unsplit_and_density_volume_encodings_preserve_classification(self):
        uniform = self.by_id["uniform_split_reference"]
        split = self.by_id["unequal_cell_split_equivalent"]
        uniform_component = oracle.expected_component(uniform, "component-a", 1)
        split_component = oracle.expected_component(split, "component-a", 1)
        self.assertEqual(uniform_component["inventory_activity_bq"],
                         split_component["inventory_activity_bq"])
        self.assertEqual(uniform_component["classification"], split_component["classification"])

        density = self.by_id["density_geometry"]
        volume = self.by_id["volume_geometry"]
        for component in ("component-a", "component-b"):
            left = oracle.expected_component(density, component, 1)
            right = oracle.expected_component(volume, component, 1)
            self.assertEqual(left["classification"], right["classification"])
            self.assertEqual(left["inventory_activity_bq"], right["inventory_activity_bq"])

    def test_external_h3_is_reported_once_separately_from_activation(self):
        case = self.by_id["external_h3_once"]
        result = oracle.expected_component(case, "component-a", 1)
        activation = result["activation_activity_bq"]
        total = result["inventory_activity_bq"]
        external = result["external_tritium_activity_bq"]
        self.assertEqual(activation["H3"], 3_000_000.0)
        self.assertEqual(external, 2_000_000.0)
        self.assertEqual(total["H3"], activation["H3"] + external)
        self.assertIn("calculated_only_class", result["classification"])
        self.assertIn("combined_calculated_only_class", result["classification"])
        document = oracle.expected_waste_document(case)
        target = document["components"][0]["targets"][0]
        self.assertEqual(target["calculated_only_class"], "A")
        self.assertEqual(target["evaluation"]["calculated_only_class"], "B")
        self.assertEqual(target["class"], "B")
        self.assertEqual(target["inventory_activity_bq"]["H3"], 5_000_000.0)

        # Here H-3 is external-only and intentionally lacks properties. The
        # activation-only subset remains Class A, while the total is unknown.
        external_only = copy.deepcopy(self.by_id["class_a"])
        for component in external_only["spec"]["waste"]["components"].values():
            component["external_tritium"] = {
                "status": "declared", "source": "synthetic external-only",
                "excludes_activation": True, "activity_bq": {"1": 1.0e9, "2": 1.0e9}}
        result = oracle.expected_component(external_only, "component-a", 1)
        classification = result["classification"]
        self.assertEqual(classification["calculated_only_class"], "A")
        self.assertEqual(classification["class"], "unknown")
        self.assertIn("H3", classification["unknown_nuclides"])
        target = oracle.expected_waste_document(external_only)["components"][0]["targets"][0]
        self.assertEqual(target["calculated_only_class"], "A")
        self.assertEqual(target["evaluation"]["class"], "unknown")
        self.assertEqual(target["evaluation"]["calculated_only_class"], "A")
        self.assertEqual(target["class"], "unknown")

    def test_missing_positive_metadata_stays_unknown_without_known_subset_rows(self):
        case = self.by_id["missing_metadata"]
        result = oracle.expected_component(case, "component-a", 1)
        self.assertEqual(result["classification"]["unknown_nuclides"], ["C14", "Xe135"])
        target = oracle.expected_waste_document(case)["components"][0]["targets"][0]
        evaluation = target["evaluation"]
        self.assertEqual(target["class"], "unknown")
        self.assertEqual(target["coverage"], "incomplete")
        self.assertEqual(evaluation["unknown_nuclides"], ["C14", "Xe135"])
        self.assertEqual(evaluation["row_fractions"], [])
        self.assertTrue(evaluation["constraints"])
        self.assertTrue(all(item["source_sum_fraction"] == 0.0
                            and item["normalized_sum"] == 0.0
                            for item in evaluation["constraints"]))

    def test_full_expected_report_contains_all_independent_rows_and_constraints(self):
        case = self.by_id["two_table_mixture"]
        report = case["expected"]["waste_classification"]
        self.assertEqual(report["schema"], "actinv-waste-result-1")
        self.assertEqual(len(report["components"]), 2)
        for component in report["components"]:
            self.assertEqual(len(component["targets"]), 2)
            for target in component["targets"]:
                evaluation = target["evaluation"]
                constraints = evaluation["constraints"]
                rows = evaluation["row_fractions"]
                self.assertTrue(constraints)
                self.assertTrue(rows)
                self.assertTrue(any(row["table"] == 1 for row in rows))
                self.assertTrue(any(row["table"] == 2 for row in rows))
                self.assertTrue(all("contributors" in item and "normalized_margin" in item
                                    for item in constraints))
                self.assertTrue(all("fraction" in row and "concentration" in row
                                    for row in rows))

    def test_legacy_oracle_pins_full_band_population_and_existing_result_shape(self):
        case = self.by_id["class_a"]
        expected = legacy.expected_legacy(case)
        self.assertIsNotNone(expected)
        self.assertEqual(expected["schema"], "actinv-twin-1")
        self.assertEqual(expected["cells"], 4)
        self.assertEqual(expected["facility"]["cleared"], 4)
        self.assertEqual(expected["facility"]["restricted"], 0)
        self.assertEqual([row["verdict"] for row in expected["per_cell"]],
                         ["cleared"] * 4)
        self.assertEqual(sum(len(row["entries"]) for row in expected["per_cell"]), 8)
        for row in expected["per_cell"]:
            self.assertEqual(len(row["entries"]), 2)
            for entry in row["entries"]:
                self.assertEqual(entry["band"], [0.8, 1.2])
                self.assertEqual(entry["margin"], 0.4)
                self.assertTrue(entry["clears"])
        self.assertEqual(expected["facility"]["binding_cells"], {
            "heat@0": {"cell": "cell-0", "margin": 0.4, "band": [0.8, 1.2], "limit": 2.0},
            "heat@86400": {"cell": "cell-0", "margin": 0.4,
                            "band": [0.8, 1.2], "limit": 2.0},
        })
        self.assertEqual(expected["components"], {
            "component-a": {"cells": ["cell-0", "cell-1"], "verdict": "cleared",
                             "missing_cells": []},
            "component-b": {"cells": ["cell-2", "cell-3"], "verdict": "cleared",
                             "missing_cells": []},
        })
        self.assertEqual(set(expected), {"schema", "mesh_output", "cells", "limits",
            "per_cell", "components", "dose_points", "facility", "note"})
        self.assertEqual(expected["limits"], [
            {"name": "heat", "response": "heat.total", "limit": 2.0, "sense": "le"}])

    def test_legacy_validator_accepts_only_exact_prior_payload_and_mesh_path(self):
        case = self.by_id["class_a"]
        expected = legacy.expected_legacy(case)
        case["expected"]["legacy_twin_result"] = expected
        case_dir = Path("/synthetic/request")
        output = copy.deepcopy(expected)
        output["mesh_output"] = str(case_dir / case["spec"]["mesh_output"])
        output["waste_classification"] = {"new": "field"}
        output["waste_facility_coverage"] = {"new": "field"}
        compare = lambda actual, wanted, path: oracle_compare(actual, wanted, path)
        self.assertEqual(legacy.validate_legacy(output, case, case_dir, compare), [])

        wrong_path = copy.deepcopy(output)
        wrong_path["mesh_output"] = str(case_dir / "different.ndjson")
        self.assertTrue(legacy.validate_legacy(wrong_path, case, case_dir, compare))
        mutations = []
        band = copy.deepcopy(output)
        band["per_cell"][0]["entries"][0]["band"] = [0.7, 1.2]
        mutations.append(band)
        margin = copy.deepcopy(output)
        margin["per_cell"][0]["worst_margin"] = 0.3
        mutations.append(margin)
        membership = copy.deepcopy(output)
        membership["components"]["component-a"]["cells"] = ["cell-0"]
        mutations.append(membership)
        unexpected = copy.deepcopy(output)
        unexpected["unexpected_legacy_field"] = True
        mutations.append(unexpected)
        for changed in mutations:
            with self.subTest(changed=changed):
                self.assertTrue(legacy.validate_legacy(changed, case, case_dir, compare))

    def test_fixture_population_validator_fails_closed_on_malformed_shapes(self):
        for invalid in (None, [], {"schema": "actinv-waste-twin-cases-1", "requests": []},
                        {"schema": "actinv-p113-twin-waste-cases-1", "requests": [None]}):
            with self.subTest(invalid=type(invalid).__name__):
                result = oracle.fixture_counts(invalid)
                self.assertFalse(result["pass"])
        duplicate_ids = copy.deepcopy(self.fixture)
        duplicate_ids["requests"][1]["id"] = duplicate_ids["requests"][0]["id"]
        self.assertFalse(oracle.fixture_counts(duplicate_ids)["pass"])
        malformed_count = copy.deepcopy(self.fixture)
        malformed_count["requests"][0]["expected"]["request_component_target_count"] = "2"
        self.assertFalse(oracle.fixture_counts(malformed_count)["pass"])
        boolean_count = copy.deepcopy(self.fixture)
        boolean_count["requests"][0]["expected"]["request_component_target_count"] = True
        self.assertFalse(oracle.fixture_counts(boolean_count)["pass"])


def oracle_compare(actual, expected, path):
    import check_p113
    return check_p113._compare(actual, expected, path)


if __name__ == "__main__":
    unittest.main()
