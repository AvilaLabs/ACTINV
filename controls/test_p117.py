#!/usr/bin/env python3
"""Source-only P117 successor identity and immutable replay regressions."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))

import check_p116
import check_p117


class P117ControlsTests(unittest.TestCase):
    def test_g0_binds_exact_predecessor_and_manifest_authorities(self):
        g0 = check_p117._g0_base()
        self.assertEqual(g0["schema"], "actinv-p117-twin-waste-g0-1")
        self.assertEqual(g0["phase"], "P117")
        self.assertTrue(g0["protocol_registered"])
        self.assertTrue(g0["seed_authorities_match"])
        self.assertTrue(g0["historical_p116_verified"])
        self.assertTrue(g0["predecessor_p116_fail_preserved"])
        self.assertEqual(g0["prior_p116"]["verdict"], "P116-FAIL")
        self.assertEqual(g0["repair_rounds"], 1)
        self.assertEqual(g0["repair_amendment_sha256"], check_p117.AMENDMENT_SHA256)
        self.assertTrue(g0["repair_amendment_registered"])
        self.assertTrue(g0["repair_evidence_matches"])
        self.assertEqual(len(g0["repair_evidence_sha256"]), 70)
        self.assertEqual(g0["initial_failure"]["verdict"], "P117-FAIL")
        self.assertEqual(len(g0["initial_failure"]["adopted_initial_gates"]),
                         len(check_p117.ADOPTED_INITIAL_GATES))
        self.assertEqual(g0["seed_file_count"], 13)
        self.assertEqual(g0["seed_total_bytes"], 166789318)
        self.assertTrue(g0["pass"], g0)

    def test_registered_repair_rejects_archived_and_checkpoint_source_tampering(self):
        real_archive_file = check_p117._archive_file
        with tempfile.TemporaryDirectory(prefix="p117-repair-tamper-") as temp:
            corrupted = Path(temp) / "corrupted.json"
            corrupted.write_bytes(b"{}")

            def changed_archive(relative):
                if relative == "results/g1_p117_twin_waste.json":
                    return corrupted
                return real_archive_file(relative)

            with patch.object(check_p117, "_archive_file", side_effect=changed_archive):
                self.assertFalse(check_p117._repair_evidence()["pass"])

        original_git_blob = check_p116._git_blob

        def changed_checkpoint_blob(commit, path):
            if path == "controls/check_p117.py":
                return b"changed checkpoint source"
            return original_git_blob(commit, path)

        with patch.object(check_p116, "_git_blob", side_effect=changed_checkpoint_blob):
            self.assertFalse(check_p117._repair_evidence()["pass"])

    def test_current_source_seal_is_exact_and_path_complete(self):
        old = check_p117._read(check_p117.P116_G0)
        okay, hashes, rust = check_p117._source_population_ok(old)
        self.assertTrue(okay)
        self.assertEqual(set(hashes), set(check_p117.CONTROL_FILES))
        self.assertEqual(len(rust), 100)
        bad_old = copy.deepcopy(old)
        first = next(iter(bad_old["control_sha256"]))
        bad_old["control_sha256"][first] = "0" * 64
        self.assertFalse(check_p117._source_population_ok(bad_old)[0])

    def test_native_launch_seal_also_binds_current_handbook(self):
        controls = {"controls/example.py": "a" * 64}
        rust = {"crates/core/src/lib.rs": "b" * 64}
        handbook = {"docs/guide/intro.md": "c" * 64}
        sealed = {"pass": True, "phase": "P117", "protocol_sha256": check_p117.PROTOCOL_SHA256,
                  "control_sha256": controls, "inherited_rust_sha256": rust,
                  "public_handbook_sha256": handbook,
                  "source_population_matches_p116_commit": True}
        with patch.object(check_p117, "_read", return_value=sealed), \
             patch.object(check_p117, "_control_hashes", return_value=controls), \
             patch.object(check_p116, "_current_rust_source_hashes", return_value=rust), \
             patch.object(check_p117, "_handbook_snapshot", return_value=(handbook, True)):
            self.assertTrue(check_p117._sealed_g0_present())
            with patch.object(check_p117, "_handbook_snapshot", return_value=({}, False)):
                self.assertFalse(check_p117._sealed_g0_present())

    def test_successor_report_preserves_entire_p116_science_object(self):
        old = check_p117._read(check_p117.P116_G1)
        report = check_p117._g1_report(old, True)
        self.assertEqual(report["p116_report_preserved"], old)
        self.assertEqual(report["fresh_p117_report"], old)
        self.assertEqual(report["request_count"], 35)
        self.assertEqual(report["component_target_count"], 138)
        self.assertEqual(report["independent_comparison_count"], 138)
        self.assertEqual(len(report["mutations_rejected"]), 43)
        self.assertEqual(len(report["refusal_controls"]["checks"]), 50)
        self.assertTrue(report["repeat_byte_identical"])
        mutated = copy.deepcopy(report)
        mutated["fresh_p117_report"]["repeat_byte_identical"] = False
        self.assertNotEqual(check_p117._canonical(mutated), check_p117._canonical(report))

    def test_p117_uses_disjoint_replay_output_names(self):
        self.assertNotEqual(check_p117.G1, check_p117.P116_G1)
        self.assertEqual(check_p117._campaign.__annotations__["output_name"], "str")
        # The wrapper passes only its named output directory to the unchanged
        # bounded P116 runner; inputs and expected science remain immutable.
        self.assertIn("output_name", check_p116._run_g1.__annotations__)
        self.assertEqual(check_p117.P116_G1_SHA256,
                         check_p117._sha(check_p117.P116_G1))

    def test_g1_does_not_launch_the_native_campaign_without_g0(self):
        with tempfile.TemporaryDirectory(prefix="p117-g1-no-seal-") as temp:
            destination = Path(temp) / "g1.json"
            with patch.object(check_p117, "G1", destination), \
                 patch.object(check_p117, "_sealed_g0_present", return_value=False), \
                 patch.object(check_p117, "_campaign", side_effect=AssertionError("native launch")):
                code, report = check_p117.g1()
            self.assertEqual(code, 1)
            self.assertFalse(report["pass"])
            self.assertIn("G0 seal", report["failures"][0])

    def test_g2_does_not_launch_the_native_campaign_when_g0_replay_fails(self):
        with tempfile.TemporaryDirectory(prefix="p117-g2-no-seal-") as temp:
            destination = Path(temp) / "g2.json"
            with patch.object(check_p117, "G2", destination), \
                 patch.object(check_p117, "g0", return_value=(1, {})), \
                 patch.object(check_p117, "_campaign", side_effect=AssertionError("native launch")):
                code, report = check_p117.g2()
            self.assertEqual(code, 1)
            self.assertFalse(report["pass"])
            self.assertFalse(report["g0_exact_replay_equal"])

    def test_full_read_only_replay_does_not_launch_without_valid_g0(self):
        with tempfile.TemporaryDirectory(prefix="p117-full-no-seal-") as temp:
            with patch.object(check_p117, "G2", Path(temp) / "g2.json"), \
                 patch.object(check_p117, "_g0_replay_ok", return_value=False), \
                 patch.object(check_p117, "_campaign", side_effect=AssertionError("native launch")):
                self.assertEqual(check_p117.main(["--no-write"]), 1)

    def test_g0_replay_flags_are_not_mutually_exclusive(self):
        with patch.object(check_p117, "g0", return_value=(0, {})) as g0:
            self.assertEqual(check_p117.main(["--g0-only", "--no-write"]), 0)
        g0.assert_called_once_with(seal=False, no_write=True)


if __name__ == "__main__":
    unittest.main()
