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
import p116_repair_control as repair
from test_p116_repair import P116RepairControlTests


class P116SealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Full historical/source verification is deliberately performed once;
        # policy and write/read tests below reuse this immutable report.
        # P120 Amendment B: verify the sealed production map and pinned Git objects.
        cls.g0_report = controls._g0_base(verify_current_sources=False)

    def test_g0_binds_frozen_population_p115_failure_and_current_checkpoint(self):
        report = self.g0_report
        self.assertTrue(report["pass"], report)
        self.assertEqual(report["schema"], "actinv-p116-twin-waste-g0-1")
        self.assertEqual(report["phase"], "P116")
        self.assertEqual(report["repair_rounds"], 1)
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
        self.assertFalse(report["inherited_rust_population_matches"])
        self.assertEqual(report["production_rust_source_count"], 100)
        self.assertTrue(report["production_rust_population_allowed"])
        old_rust = report["inherited_rust_sha256"]
        new_rust = report["production_rust_sha256"]
        self.assertEqual(set(new_rust), set(old_rust))
        self.assertEqual({path for path in old_rust if old_rust[path] != new_rust[path]},
                         {"crates/actinv-cli/src/twin_waste.rs"})
        evidence = report["p116_repair_evidence"]
        self.assertTrue(evidence["pass"])
        self.assertEqual(evidence["checkpoint"], repair.CHECKPOINT)
        self.assertEqual(len(evidence["original_source_sha256"]), 200)
        self.assertEqual(len(evidence["original_retained_sha256"]), 88)
        self.assertEqual(len(evidence["original_rust_sha256"]), 100)
        self.assertEqual(evidence["original_g0_sha256"], repair.ORIGINAL_G0_SHA256)
        self.assertEqual(evidence["original_g1_sha256"], repair.ORIGINAL_G1_SHA256)
        self.assertEqual(evidence["original_quality_gate_count"], 25)
        self.assertTrue(controls._repair_policy(report, evidence))
        self.assertEqual(set(report["control_sha256"]), set(controls.CONTROL_FILES))

    def test_registered_repair_round_is_strict_one_separate_from_p115_history(self):
        report = self.g0_report
        evidence = report["p116_repair_evidence"]
        self.assertTrue(controls._repair_policy(report, evidence))
        self.assertFalse(controls._repair_policy({**report, "repair_rounds": False}))
        self.assertFalse(controls._repair_policy({**report, "repair_rounds": 0}, evidence))
        self.assertFalse(controls._repair_policy({**report, "historical_p115_verified": False}, evidence))
        self.assertFalse(controls._repair_policy({**report, "repair_amendment_sha256": "0" * 64}, evidence))
        altered_evidence = copy.deepcopy(evidence)
        altered_evidence["original_g1_sha256"] = "0" * 64
        self.assertFalse(controls._repair_policy(report, altered_evidence))
        self.assertFalse(controls._repair_policy({**report, "p116_repair_evidence": altered_evidence}, evidence))

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
            with patch.object(controls, "G0", path), patch.object(
                    controls, "_g0_base", return_value=copy.deepcopy(self.g0_report)):
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
