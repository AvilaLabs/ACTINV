#!/usr/bin/env python3
"""Source-only checks for the terminal P116 history adapter."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))

import check_p116
import check_p116_history as history
import check_p116_verdict


class P116HistoryTests(unittest.TestCase):
    def test_history_rederives_exact_ci_only_terminal_failure(self):
        report = history.verify()
        self.assertIs(report.get("pass"), True, report)
        self.assertEqual(report["verdict"], "P116-FAIL")
        self.assertEqual(report["checkpoint_commit"], history.COMMIT)
        self.assertEqual(report["failed_workflows"], ["controls", "fns-iron"])
        self.assertEqual(report["successful_workflow_count"], 4)
        self.assertEqual(report["ci_archive_file_count"], 13)
        self.assertTrue(report["all_100_rust_sources_match_git"])
        self.assertTrue(report["derived_verdict_matches_persisted"])

    def test_archive_verifier_rejects_manifest_population_or_hash_drift(self):
        found, okay = history._archive_files()
        self.assertTrue(okay)
        self.assertEqual(found, history.EXPECTED_ARCHIVE_FILES)
        changed = dict(history.EXPECTED_ARCHIVE_FILES)
        changed["metadata/p116-ci-terminal.json"] = "0" * 64
        with patch.object(history, "EXPECTED_ARCHIVE_FILES", changed):
            self.assertEqual(history._archive_files(), ({}, False))
        with patch.object(history, "DISCOVERY_SHA256", "0" * 64):
            self.assertEqual(history._archive_files(), ({}, False))

    def test_p116_source_seal_rejects_changed_control_digest(self):
        g0 = history._read("results/g0_p116_twin_waste.json")
        control_hashes = copy.deepcopy(g0["control_sha256"])
        first_path = next(iter(control_hashes))
        control_hashes[first_path] = "0" * 64
        g0["control_sha256"] = control_hashes
        count, okay = history._check_control_sources(g0)
        self.assertEqual(count, len(control_hashes))
        self.assertFalse(okay)

    def test_rust_sources_must_match_the_implementation_commit_git_objects(self):
        # P120: the sealed production map is checked against the implementation commit's Git objects; the working
        # tree is not consulted, so later source changes cannot fail this history check.
        blobs = {f"src/{index}.rs": f"fn f{index}() {{}}\n".encode() for index in range(100)}
        g3 = {"production_rust_sha256": {path: history._sha(data) for path, data in blobs.items()}}
        implementation = {"commit_sha": history.COMMIT}
        with patch.object(check_p116, "_rust_paths_at_commit", return_value=set(blobs)), \
             patch.object(check_p116, "_git_blob", side_effect=lambda commit, path: blobs[path]), \
             patch.object(check_p116, "_current_rust_source_hashes", side_effect=AssertionError("working tree read")), \
             patch.object(check_p116_verdict, "_source_commit_matches", return_value=True):
            self.assertTrue(history._current_rust_matches(g3, implementation))
            changed = dict(blobs)
            changed["src/0.rs"] = b"changed\n"
            with patch.object(check_p116, "_git_blob", side_effect=lambda commit, path: changed[path]):
                self.assertFalse(history._current_rust_matches(g3, implementation))


if __name__ == "__main__":
    unittest.main()
