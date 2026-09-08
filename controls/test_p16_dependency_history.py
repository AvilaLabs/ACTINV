"""Regression for the frozen P16 dependency comparison endpoints."""
import copy
import json
import unittest
from unittest.mock import patch

import check_p16
import g1_p16_quantities


class HistoricalDependencies(unittest.TestCase):
    def test_historical_inventory_matches_stored_evidence(self):
        actual = g1_p16_quantities.manifest_identity()
        stored = json.loads(g1_p16_quantities.RESULT.read_text())["dependency_manifests"]
        self.assertEqual(actual, stored)
        self.assertTrue(check_p16.dependency_contract(actual))
        self.assertNotIn("crates/actinv-gui/Cargo.toml", [r["path"] for r in actual["files"]])

    def test_altered_or_missing_evidence_fails(self):
        actual = g1_p16_quantities.manifest_identity()
        for field in ("opening_sha256", "current_sha256"):
            changed = copy.deepcopy(actual)
            changed["files"][0][field] = "0" * 64
            self.assertFalse(check_p16.dependency_contract(changed))
        actual["files"].pop()
        self.assertFalse(check_p16.dependency_contract(actual))

    def test_missing_checkpoint_fails_closed(self):
        actual = g1_p16_quantities.manifest_identity()
        with patch.object(check_p16, "opening_file", return_value=None):
            self.assertFalse(check_p16.dependency_contract(actual))
        with patch.object(g1_p16_quantities, "SOURCE_EVIDENCE_COMMIT", "0" * 40):
            with self.assertRaises(RuntimeError):
                g1_p16_quantities.manifest_identity()


if __name__ == "__main__":
    unittest.main()
