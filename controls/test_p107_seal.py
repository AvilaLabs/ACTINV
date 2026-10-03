"""Focused P107 regression for stable JSON seal identity and mutation rejection."""
import unittest
import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import check_p107


class SealRoundtripTests(unittest.TestCase):
    def test_json_roundtrip_normalizes_nested_tuple_values_and_detects_mutation(self):
        report = {
            "case_count": 146,
            "expected_labels": {"example": [("A", "C", ["A", "B", "C"], False, None)]},
        }
        self.assertTrue(check_p107.seal_roundtrip_regression(report))

    def test_json_compatible_conversion_is_stable(self):
        original = {"case": [("A", ["B", None])], "pass": True}
        stable = check_p107._json_compatible(original)
        self.assertEqual(stable, {"case": [["A", ["B", None]]], "pass": True})
        self.assertEqual(check_p107._json_compatible(stable), stable)

    def test_g0_write_then_replay_rejects_changed_seal(self):
        report = {"schema": "actinv-p107-bounds-g0-seal-1", "phase": "P107",
                  "case_count": 146, "pass": True,
                  "expected_labels": {"example": [("A", "C", ["A", "B", "C"], False, None)]}}
        fixture_bytes = b"frozen synthetic fixture\n"
        with tempfile.TemporaryDirectory(prefix="p107-g0-write-read-") as temp:
            root = Path(temp)
            seal = root / "g0.json"
            fixture = root / "cases.json"
            fixture.write_bytes(fixture_bytes)
            with patch.object(check_p107, "_g0_base_report", return_value=(report, fixture_bytes)), \
                 patch.object(check_p107, "SEAL", seal), patch.object(check_p107, "P106_CASES", fixture), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_p107.g0(no_write=False), 0)
                self.assertEqual(check_p107.g0(no_write=True), 0)
                altered = json.loads(seal.read_text(encoding="utf-8"))
                altered["case_count"] += 1
                seal.write_text(json.dumps(altered), encoding="utf-8")
                self.assertEqual(check_p107.g0(no_write=True), 1)


if __name__ == "__main__":
    unittest.main()
