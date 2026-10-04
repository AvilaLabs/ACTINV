"""Source-only P115 regressions over the unchanged P113 fixture/oracle."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

import check_p115 as controls
import p113_twin_control as oracle
import test_p113_oracle as inherited_oracle


class P115InheritedOracleTests(inherited_oracle.P113OracleTests):
    """Re-run the full frozen 12-case P113 arithmetic/legacy regression suite."""

    pass


class P115OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = oracle.frozen_fixture()
        cls.cases = cls.fixture["requests"]
        cls.by_id = {case["id"]: case for case in cls.cases}

    def test_inherited_fixture_is_byte_pinned_and_has_exact_population(self):
        counts = oracle.fixture_counts(self.fixture)
        self.assertTrue(counts["pass"], counts.get("errors"))
        self.assertEqual(counts["request_count"], 35)
        self.assertEqual(counts["component_target_count"], 138)
        self.assertEqual(len(counts["request_ids"]), 35)
        self.assertEqual(len(set(counts["request_ids"])), 35)
        self.assertEqual(controls._sha(controls.FIXTURE), controls.FIXTURE_SHA256)
        sealed = controls._fixture_check()
        self.assertTrue(sealed["pass"], sealed)
        self.assertEqual(sealed["request_count"], 35)
        self.assertEqual(sealed["component_target_count"], 138)

    def test_inherited_nominal_boundaries_groups_and_unknowns_remain_explicit(self):
        expected_classes = {
            "boundary_below": "A", "boundary_equal": "A", "boundary_above": "C",
            "class_b": "B", "class_c": "C", "above_class_c": "above_class_c",
            "missing_metadata": "unknown", "required_h3": "unknown",
        }
        for request_id, wanted in expected_classes.items():
            with self.subTest(request=request_id):
                case = self.by_id[request_id]
                first = case["spec"]["waste"]["targets"][0]
                result = oracle.expected_component(case, "component-a", first)
                self.assertEqual(result["classification"]["class"], wanted)
        alpha = oracle.expected_component(self.by_id["alpha_group"], "component-a", 1)
        members = {"Pu238", "Pu239", "Pu240", "Pu242", "Am241", "Am243", "Cm243", "Cm244"}
        grouped = [row for row in alpha["classification"]["row_details"]
                   if row["table"] == 1 and row["row_id"] == "alpha_transuranic_gt5y"]
        self.assertEqual({row["nuclide"] for row in grouped}, members)
        self.assertTrue(all(row["fraction"] is not None for row in grouped))
        unknown = oracle.expected_component(self.by_id["missing_metadata"], "component-a", 1)
        self.assertEqual(unknown["classification"]["unknown_nuclides"], ["C14", "Xe135"])

    def test_external_tritium_is_applied_once_and_known_subset_is_separate(self):
        case = self.by_id["external_h3_once"]
        component = oracle.expected_component(case, "component-a", 1)
        doc = oracle.expected_waste_document(case)
        target = doc["components"][0]["targets"][0]
        self.assertEqual(target["external_tritium_activity_bq"], 2_000_000.0)
        self.assertEqual(component["activation_activity_bq"]["H3"], 3_000_000.0)
        self.assertEqual(target["inventory_activity_bq"]["H3"], 5_000_000.0)
        self.assertEqual(target["calculated_only_class"], "A")
        self.assertEqual(target["evaluation"]["calculated_only_class"], "B")
        self.assertEqual(target["class"], "B")

        external_only = copy.deepcopy(self.by_id["class_a"])
        for component in external_only["spec"]["waste"]["components"].values():
            component["external_tritium"] = {
                "status": "declared", "source": "synthetic external-only",
                "excludes_activation": True, "activity_bq": {"1": 1e9, "2": 1e9}}
        target = oracle.expected_waste_document(external_only)["components"][0]["targets"][0]
        self.assertEqual(target["calculated_only_class"], "A")
        self.assertEqual(target["evaluation"]["class"], "unknown")
        self.assertEqual(target["evaluation"]["calculated_only_class"], "A")
        self.assertEqual(target["class"], "unknown")

    def test_complete_output_digest_normalizes_only_verified_mesh_path(self):
        case = self.by_id["class_a"]
        output = copy.deepcopy(case["expected"]["legacy_twin_result"])
        relative = case["spec"]["mesh_output"]
        root_a = Path("/tmp/p115-output-a")
        root_b = Path("/tmp/p115-output-b")
        output["mesh_output"] = str(root_a / relative)
        digest_a = controls.normalized_output_sha256(output, root_a / relative, relative)
        other_root_output = copy.deepcopy(output)
        other_root_output["mesh_output"] = str(root_b / relative)
        digest_b = controls.normalized_output_sha256(other_root_output, root_b / relative, relative)
        self.assertIsNotNone(digest_a)
        self.assertEqual(digest_a, digest_b)
        changed_science = copy.deepcopy(output)
        changed_science["per_cell"][0]["entries"][0]["band"][0] -= 0.01
        self.assertNotEqual(digest_a, controls.normalized_output_sha256(
            changed_science, root_a / relative, relative))
        self.assertIsNone(controls.normalized_output_sha256(
            output, root_b / relative, relative))


if __name__ == "__main__":
    unittest.main()
