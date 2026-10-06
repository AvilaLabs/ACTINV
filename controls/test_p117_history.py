#!/usr/bin/env python3
"""Source-only regressions for the immutable P117 history verifier."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p117_history as history
import check_p117_verdict


class P117HistoryTests(unittest.TestCase):
    def test_history_verifies_both_failure_episodes_and_full_terminal_archive(self):
        report = history.verify()
        self.assertIs(report.get("pass"), True, report)
        self.assertEqual(report["verdict"], "P117-FAIL")
        self.assertEqual(report["checkpoint_commit"], history.CHECKPOINT)
        self.assertTrue(report["derived_verdict_matches_persisted"])
        self.assertTrue(report["historical_p116_verified"])
        self.assertEqual(report["control_source_count"], 193)
        self.assertEqual(report["current_rust_source_count"], 100)
        self.assertEqual(len(report["terminal_source_sha256"]), 193)
        self.assertEqual(len(report["inherited_rust_sha256"]), 100)
        self.assertEqual(len(report["terminal_archive_files_sha256"]), 100)
        self.assertEqual(len(report["initial_archive_files_sha256"]), 71)
        self.assertEqual(report["terminal_gates"], history.TERMINAL_GATES)

    def test_historical_projection_is_limited_to_g0_and_source_and_restores(self):
        original_g0_ok = check_p117_verdict._g0_ok
        original_source = check_p117_verdict._source_commit_matches
        snapshot = {"pass": True, "identity": "sealed"}
        observed = {}

        def fake_derive():
            observed["g0_snapshot"] = check_p117_verdict._g0_ok(snapshot)
            observed["g0_other"] = check_p117_verdict._g0_ok({"pass": True})
            observed["source_snapshot"] = check_p117_verdict._source_commit_matches(snapshot, None)
            observed["source_other"] = check_p117_verdict._source_commit_matches({"pass": True}, None)
            observed["source_with_record"] = check_p117_verdict._source_commit_matches(snapshot, {})
            return {"verdict": "P117-FAIL"}

        with patch.object(check_p117_verdict, "derive", side_effect=fake_derive):
            self.assertEqual(history._derive_historical_failure(snapshot, {"verdict": "P117-FAIL"}),
                             {"verdict": "P117-FAIL"})
        self.assertEqual(observed, {
            "g0_snapshot": True, "g0_other": False, "source_snapshot": True,
            "source_other": False, "source_with_record": False,
        })
        self.assertIs(check_p117_verdict._g0_ok, original_g0_ok)
        self.assertIs(check_p117_verdict._source_commit_matches, original_source)

    def test_archive_population_rejects_mutated_hash_missing_and_extra_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "archive").mkdir()
            (root / "archive/a.txt").write_bytes(b"a")
            with patch.object(history, "ROOT", root):
                self.assertEqual(history._archive_map("archive", {"a.txt": history._sha(b"a")}),
                                 {"archive/a.txt": history._sha(b"a")})
                with self.assertRaises(ValueError):
                    history._archive_map("archive", {"a.txt": "0" * 64})
                # Resolving a missing archive member with strict=True raises
                # FileNotFoundError before the verifier can normalize it to
                # its own ValueError. Both are fail-closed outcomes.
                with self.assertRaises((OSError, ValueError)):
                    history._archive_map("archive", {"missing.txt": history._sha(b"a")})
                (root / "archive/extra.txt").write_bytes(b"extra")
                with self.assertRaises(ValueError):
                    history._archive_map("archive", {"a.txt": history._sha(b"a")})

    def test_archive_population_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "archive").mkdir()
            (root / "outside").write_bytes(b"a")
            (root / "archive/a.txt").symlink_to(root / "outside")
            with patch.object(history, "ROOT", root):
                with self.assertRaises(ValueError):
                    history._archive_map("archive", {"a.txt": history._sha(b"a")})

    def test_ci_transition_accepts_only_registered_p118_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            prefix = b"name: CI\njobs:\n  controls:\n    steps:\n"
            suffix = b"      - name: unchanged\n        run: true\n"
            previous = prefix + history.OLD_P117_CI_STEP + suffix
            legacy_expected = prefix + history.NEW_P117_CI_STEP + b"\n" + history.NEW_P118_CI_STEP + suffix
            history_expected = (prefix + history.NEW_P117_CI_STEP + b"\n"
                                + history.NEW_P118_HISTORY_CI_STEP + suffix)
            workflow.write_bytes(legacy_expected)
            with patch.object(history, "ROOT", root), \
                    patch.object(history.p116, "_git_blob", return_value=previous):
                self.assertTrue(history._ci_transition_matches())
                workflow.write_bytes(history_expected)
                self.assertTrue(history._ci_transition_matches())
                workflow.write_bytes(history_expected + b"# unrelated edit\n")
                self.assertFalse(history._ci_transition_matches())
                workflow.write_bytes(prefix + history.NEW_P117_CI_STEP + suffix)
                self.assertFalse(history._ci_transition_matches())
                workflow.write_bytes(prefix + history.OLD_P117_CI_STEP + suffix)
                self.assertFalse(history._ci_transition_matches())

    def test_genuine_initial_discovery_schema_has_no_file_count(self):
        raw = history._safe_file(f"{history.INITIAL}/discovery.json").read_bytes()
        discovery = history._unique(raw)
        expected = discovery["preserved_files_sha256"]
        self.assertEqual(discovery["schema"], "actinv-p117-initial-preservation-1")
        self.assertEqual(len(expected), 70)
        self.assertNotIn("file_count", discovery)
        history._validate_initial_discovery(discovery, expected)

        wrong_count = copy.deepcopy(discovery)
        del wrong_count["preserved_files_sha256"][next(iter(expected))]
        with self.assertRaises(ValueError):
            history._validate_initial_discovery(wrong_count, expected)

        wrong_hash = copy.deepcopy(discovery)
        first_path = next(iter(expected))
        wrong_hash["preserved_files_sha256"][first_path] = "0" * 64
        with self.assertRaises(ValueError):
            history._validate_initial_discovery(wrong_hash, expected)

    def test_initial_archive_manifest_mutation_is_rejected(self):
        _discovery, _terminal_map, _initial_map = history._terminal_archive()
        initial_files = history._unique(history._safe_file(
            f"{history.INITIAL}/discovery.json").read_bytes())["preserved_files_sha256"]
        changed = dict(initial_files)
        changed["results/g0_p117_twin_waste.json"] = "0" * 64
        with self.assertRaises(ValueError):
            history._verify_initial(changed)

    def test_duplicate_recorder_exit_mutation_is_rejected(self):
        discovery, _terminal_map, _initial_map = history._terminal_archive()
        changed = copy.deepcopy(discovery)
        changed["duplicate_invocation"]["observed_exit_code"] = 0
        with self.assertRaises(ValueError):
            history._verify_terminal_disposition(changed)

    def test_amended_success_receipt_exit_mutation_is_rejected(self):
        discovery, _terminal_map, _initial_map = history._terminal_archive()
        changed = copy.deepcopy(discovery)
        changed["amended_successful_gates"]["g0_replay"]["child_exit_code"] = 1
        with self.assertRaises(ValueError):
            history._verify_terminal_disposition(changed)

    def test_terminal_observed_exit_identity_mutation_is_rejected(self):
        discovery, _terminal_map, _initial_map = history._terminal_archive()
        changed = copy.deepcopy(discovery)
        changed["terminal_verdict_observation"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            history._verify_terminal_disposition(changed)

    def test_terminal_source_digest_mutation_is_rejected_against_git(self):
        discovery, _terminal_map, _initial_map = history._terminal_archive()
        changed = copy.deepcopy(discovery)
        first_path = next(iter(changed["amended_g0_control_sha256"]))
        changed["amended_g0_control_sha256"][first_path] = "0" * 64
        with self.assertRaises(ValueError):
            history._verify_terminal_sources(changed)

    def test_terminal_discovery_pin_is_fail_closed(self):
        with patch.object(history, "TERMINAL_DISCOVERY_SHA256", "0" * 64):
            report = history.verify()
        self.assertIs(report.get("pass"), False)
        self.assertFalse(report.get("derived_verdict_matches_persisted"))

    def test_cgroup_resource_mutations_are_rejected(self):
        discovery, _terminal_map, _initial_map = history._terminal_archive()
        receipt = history._unique(history._safe_file(
            f"{history.TERMINAL}/results/quality/p117/g0_replay.json").read_bytes())
        resources = copy.deepcopy(receipt["resources"])
        self.assertTrue(history._resource_ok(resources))
        resources["cgroup_limits"]["memory.swap.max"] = "1"
        self.assertFalse(history._resource_ok(resources))
        self.assertEqual(discovery["checkpoint_commit"], history.CHECKPOINT)

if __name__ == "__main__":
    unittest.main()
