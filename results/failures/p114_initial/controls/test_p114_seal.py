"""Source-only P114 G0 seal and historical checkpoint regressions."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p114 as controls


class P114SealTests(unittest.TestCase):
    def test_g0_binds_exact_population_and_preserves_p113_failure_history(self):
        report = controls._g0_base()
        self.assertTrue(report["pass"], report)
        self.assertEqual(report["repair_rounds"], 0)
        self.assertEqual(report["request_count"], 35)
        self.assertEqual(report["component_target_count"], 138)
        self.assertEqual(report["p113_inherited_g0_sha256"], controls.INITIAL_G0_SHA256)
        self.assertEqual(report["p113_failure_verdict_sha256"],
                         "ae000348b4e6c58eb217d1ce9c5e9946fcf7b440562835855f724f6a1650e461")
        self.assertTrue(report["p113_failure_verdict_is_terminal_fail"])
        self.assertTrue(report["historical_p113_verified"])
        self.assertTrue(report["p113_initial_archive_matches"])
        self.assertEqual(report["inherited_rust_checkpoint"], controls.P113_CHECKPOINT)
        self.assertEqual(report["inherited_rust_source_count"], 100)
        self.assertTrue(report["inherited_rust_population_matches"])
        self.assertEqual(set(report["control_sha256"]), set(controls.CONTROL_FILES))

    def test_control_hash_seal_rejects_missing_traversal_and_symlink_entries(self):
        with tempfile.TemporaryDirectory(prefix="p114-control-seal-") as temp:
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

    def test_g0_seal_write_read_replay_and_failure_history_tampering(self):
        with tempfile.TemporaryDirectory(prefix="p114-g0-") as temp:
            path = Path(temp) / "g0.json"
            with patch.object(controls, "G0", path):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(controls.g0(seal=True), 0)
                    persisted = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(controls.g0(no_write=True), 0)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), persisted)
                    changed = copy.deepcopy(persisted)
                    changed["historical_p113_verified"] = False
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)
                    changed = copy.deepcopy(persisted)
                    changed["p113_failure_verdict_sha256"] = "0" * 64
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)


if __name__ == "__main__":
    unittest.main()
