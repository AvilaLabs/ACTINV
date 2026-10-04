#!/usr/bin/env python3
"""Source-only P118 history seal and safe replay regressions."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p116
import check_p117
import check_p118


class P118ControlTests(unittest.TestCase):
    def test_registered_initial_failure_archive_is_exact_and_kept_failed(self):
        proof = check_p118._initial_failure_evidence()
        self.assertIs(proof.get("pass"), True, proof)
        self.assertEqual(proof["actual_child_exit_code"], 1)
        self.assertEqual(proof["actual_outer_exit_code"], 1)
        self.assertEqual(proof["failed_gate"], "recorder_regressions")
        self.assertEqual(proof["test_count"], 14)
        self.assertEqual(proof["test_failure_count"], 1)
        self.assertEqual(len(proof["preserved_files_sha256"]), 15)

    def test_p117_adopted_gate_verification_is_portable_without_target_logs(self):
        name = sorted(check_p118.P117_ADOPTED_GATES)[0]
        old_g3 = check_p117._read(check_p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
        entry = old_g3["fresh_gates"][name]
        original_safe_file = check_p118._safe_file
        def safe_without_target(rel):
            if rel == f"target/p117-{name}.log":
                raise AssertionError("portable replay consulted an ignored target log")
            return original_safe_file(rel)
        with patch.object(check_p118, "_safe_file", side_effect=safe_without_target):
            # Archived raw bytes remain authoritative; no live ignored target path is requested.
            self.assertTrue(check_p118._adopted_initial_entry_matches(name, entry, require_raw_target=False))

    def test_g0_binds_p118_phase_amendment_and_full_predecessor_reports(self):
        report = check_p118._g0_base()
        self.assertEqual(report["schema"], "actinv-p118-twin-waste-g0-1")
        self.assertEqual(report["phase"], "P118")
        self.assertEqual(report["protocol_sha256"], check_p118.PROTOCOL_SHA256)
        self.assertEqual(report["repair_rounds"], 1)
        self.assertEqual(report["amendment_sha256"], check_p118.AMENDMENT_SHA256)
        self.assertTrue(report["initial_failure_matches"])
        self.assertEqual(report["initial_failure"]["actual_child_exit_code"], 1)
        self.assertTrue(report["historical_p117_verified"])
        self.assertTrue(report["historical_p116_verified"])
        self.assertEqual(len(report["inherited_rust_sha256"]), 100)
        self.assertEqual(len(report["p117_history"]["terminal_source_sha256"]), 193)
        self.assertTrue(report["pass"], report)

    def test_source_map_rejects_missing_or_changed_p118_control(self):
        source_map = {"controls/check_p118.py": "a" * 64}
        rust = {f"crates/core/{index}.rs": "b" * 64 for index in range(100)}
        handbook = {"docs/guide/index.md": "c" * 64}
        report = {"schema": "actinv-p118-twin-waste-g0-1", "phase": "P118",
                  "protocol_sha256": check_p118.PROTOCOL_SHA256, "pass": True,
                  "control_sha256": source_map, "inherited_rust_sha256": rust,
                  "historical_p117_verified": True, "historical_p116_verified": True,
                  "repair_rounds": 1, "amendment_sha256": check_p118.AMENDMENT_SHA256,
                  "initial_failure_matches": True, "source_population_matches_p116_commit": True,
                  "public_handbook_sha256": handbook}
        sealed = copy.deepcopy(report)
        with patch.object(check_p118, "_read", return_value=sealed), \
             patch.object(check_p118, "_control_hashes", return_value=source_map), \
             patch.object(check_p116, "_current_rust_source_hashes", return_value=rust), \
             patch.object(check_p118, "_handbook_snapshot", return_value=(handbook, True)), \
             patch.object(check_p118, "_g0_base", return_value=report):
            self.assertTrue(check_p118._sealed_g0_present(report))
            with tempfile.TemporaryDirectory(prefix="p118-control-tamper-") as temp, \
                 patch.object(check_p118, "G1", Path(temp) / "g1.json"), \
                 patch.object(check_p118, "_control_hashes", return_value={}), \
                 patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
                code, failed = check_p118.g1(quiet=True)
            self.assertEqual(code, 1)
            self.assertFalse(failed["pass"])

            sealed.clear()
            sealed.update(copy.deepcopy(report))
            sealed["seed_authorities_match"] = False
            with patch.object(check_p118, "_read", return_value=sealed), \
                 patch.object(check_p118, "_g0_base", return_value=report), \
                 patch.object(check_p118, "_control_hashes", return_value=source_map), \
                 patch.object(check_p116, "_current_rust_source_hashes", return_value=rust), \
                 patch.object(check_p118, "_handbook_snapshot", return_value=(handbook, True)), \
                 tempfile.TemporaryDirectory(prefix="p118-authority-tamper-") as temp, \
                 patch.object(check_p118, "G1", Path(temp) / "g1.json"), \
                 patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
                code, failed = check_p118.g1(quiet=True)
            self.assertEqual(code, 1)
            self.assertFalse(failed["pass"])

    def test_g1_refuses_to_start_without_current_g0_seal(self):
        with tempfile.TemporaryDirectory(prefix="p118-g1-no-seal-") as temp:
            with patch.object(check_p118, "G1", Path(temp) / "g1.json"), \
                 patch.object(check_p118, "_g0_base", return_value={"pass": False}), \
                 patch.object(check_p118, "_sealed_g0_present", return_value=False), \
                 patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
                code, report = check_p118.g1(quiet=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["pass"])
        self.assertIn("G0 seal", report["failures"][0])

    def test_g2_refuses_campaign_when_g0_readonly_replay_fails(self):
        with tempfile.TemporaryDirectory(prefix="p118-g2-no-seal-") as temp:
            with patch.object(check_p118, "G2", Path(temp) / "g2.json"), \
                 patch.object(check_p118, "g0", return_value=(1, {})), \
                 patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
                code, report = check_p118.g2(quiet=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["pass"])
        self.assertFalse(report["g0_exact_replay_equal"])

    def test_full_readonly_replay_does_not_launch_without_g0(self):
        with patch.object(check_p118, "_g0_replay", return_value=(False, {})), \
             patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
            self.assertEqual(check_p118.main(["--no-write"]), 1)

    def test_g0_and_no_write_flags_are_independent(self):
        with patch.object(check_p118, "g0", return_value=(0, {})) as g0:
            self.assertEqual(check_p118.main(["--g0-only", "--no-write"]), 0)
        g0.assert_called_once_with(seal=False, no_write=True)


if __name__ == "__main__":
    unittest.main()
