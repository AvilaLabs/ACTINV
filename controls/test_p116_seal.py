"""Source-only P116 seal, current-round, and inherited-history regressions."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p116 as controls


class P116SealTests(unittest.TestCase):
    def test_g0_binds_frozen_population_p115_failure_and_current_checkpoint(self):
        report = controls._g0_base()
        self.assertTrue(report["pass"], report)
        self.assertEqual(report["schema"], "actinv-p116-twin-waste-g0-1")
        self.assertEqual(report["phase"], "P116")
        self.assertEqual(report["repair_rounds"], 0)
        self.assertEqual(report["request_count"], 35)
        self.assertEqual(report["component_target_count"], 138)
        self.assertTrue(report["historical_p113_verified"])
        self.assertTrue(report["historical_p114_verified"])
        self.assertTrue(report["historical_p115_verified"])
        self.assertTrue(report["p115_failure_record_matches"])
        self.assertEqual(report["p115_failure_verdict_sha256"], controls.P115_VERDICT_SHA256)
        self.assertEqual(report["p115_failure_g3_sha256"], controls.P115_FINAL_G3_SHA256)
        self.assertTrue(report["p115_unexecuted_artifacts_absent"])
        self.assertEqual(report["inherited_rust_checkpoint"], controls.P116_CHECKPOINT)
        self.assertEqual(report["inherited_rust_source_count"], 100)
        self.assertTrue(report["inherited_rust_population_matches"])
        self.assertTrue(controls._repair_policy(report))
        self.assertEqual(set(report["control_sha256"]), set(controls.CONTROL_FILES))

    def test_p116_opening_round_is_strict_zero_separate_from_p115_history(self):
        report = controls._g0_base()
        self.assertTrue(controls._repair_policy(report))
        self.assertFalse(controls._repair_policy({**report, "repair_rounds": False}))
        self.assertFalse(controls._repair_policy({**report, "repair_rounds": 1}))
        self.assertFalse(controls._repair_policy({**report, "historical_p115_verified": False}))
        with tempfile.TemporaryDirectory(prefix="p116-current-round-") as temp:
            root = Path(temp)
            (root / "protocols").mkdir()
            registry = root / "protocols/protocol_hash.txt"
            registry.write_text("", encoding="utf-8")
            with patch.object(controls, "ROOT", root):
                self.assertTrue(controls._current_round_is_zero())
                amendment = root / "protocols/ACTINV-P116_AMENDMENT_A.md"
                amendment.write_text("unregistered amendment", encoding="utf-8")
                self.assertFalse(controls._current_round_is_zero())
                amendment.unlink()
                (root / "results/failures/p116_initial").mkdir(parents=True)
                self.assertFalse(controls._current_round_is_zero())

    def test_control_hash_seal_rejects_missing_traversal_and_symlink_entries(self):
        with tempfile.TemporaryDirectory(prefix="p116-control-seal-") as temp:
            root = Path(temp) / "repo"
            (root / "controls").mkdir(parents=True)
            (root / "data").mkdir()
            paths = ("controls/check.py", "controls/oracle.py", "data/pack.json")
            hashes = {}
            for relative in paths:
                (root / relative).write_bytes(relative.encode())
                hashes[relative] = controls._sha(root / relative)
            with patch.object(controls, "ROOT", root), patch.object(controls, "CONTROL_FILES", paths):
                self.assertTrue(controls._safe_control_hashes(hashes))
                self.assertFalse(controls._safe_control_hashes({
                    key: value for key, value in hashes.items() if key != paths[-1]}))
                self.assertFalse(controls._safe_control_hashes({**hashes, "../outside": "0" * 64}))
                outside = Path(temp) / "outside.py"
                outside.write_bytes(b"outside")
                (root / "controls/escape.py").symlink_to(outside)
                with patch.object(controls, "CONTROL_FILES", (*paths, "controls/escape.py")):
                    self.assertFalse(controls._safe_control_hashes({
                        **hashes, "controls/escape.py": controls._sha(outside)}))

    def test_g0_seal_write_read_replay_and_prior_history_mutation(self):
        with tempfile.TemporaryDirectory(prefix="p116-g0-") as temp:
            path = Path(temp) / "g0.json"
            with patch.object(controls, "G0", path):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(controls.g0(seal=True), 0)
                    persisted = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(controls.g0(no_write=True), 0)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), persisted)
                    changed = copy.deepcopy(persisted)
                    changed["historical_p115_verified"] = False
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)
                    changed = copy.deepcopy(persisted)
                    changed["p115_failure_verdict_sha256"] = "0" * 64
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)


if __name__ == "__main__":
    unittest.main()
