"""Source-only regressions for the retained terminal P113 failure record."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p113
import check_p113_history as history


class P113HistoryTests(unittest.TestCase):
    def test_terminal_checkpoint_rederives_fail_and_keeps_initial_g0_distinct(self):
        report = history.verify()
        self.assertTrue(report.get("pass"), report)
        self.assertEqual(report["checkpoint_commit"], "81db5c03eeb876a88b6ddd7a2d2b8975308178e5")
        self.assertEqual(report["verdict"], "P113-FAIL")
        self.assertEqual(report["initial_g0_archive_files_verified"], 59)
        self.assertEqual(report["amended_source_archive_files_verified"], 109)
        self.assertEqual(report["historical_rust_files_materialized"], 100)
        self.assertTrue(report["amended_g0_g1_g2_not_run"])
        self.assertIs(report["historical_source_verification"], False)

    def test_invocation_record_requires_actual_127_and_no_child_launch(self):
        raw = history._exact_git_file(history.INVOCATION_PATH, history.INVOCATION_SHA256)
        parsed = history._validate_invocation(raw)
        self.assertEqual(parsed["observed_exit_code"], 127)
        self.assertFalse(parsed["cgroup_started"])
        self.assertFalse(parsed["cargo_started"])
        original = json.loads(raw)
        for field, value in (("observed_exit_code", True), ("cargo_started", True),
                             ("amended_g0_executed", True), ("g2_executed", True),
                             ("cargo_started", 0),
                             ("command", "timeout 1200s cargo test")):
            changed = copy.deepcopy(original)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                history._validate_invocation(json.dumps(changed).encode())

    def test_failure_record_refuses_counter_bool_unearned_qualification_and_path_tampering(self):
        record = history._read_json(history.FAILURE_RECORD)
        self.assertIsInstance(record, dict)
        for changed in (
            {**record, "failure_exit_code": True},
            {**record, "amended_g0_executed": 0},
            {**record, "implementation_record": {"commit_sha": "f" * 40}},
            {**record, "ci_record": {"headSha": "f" * 40}},
            {**record, "amended_g0_executed": True},
            {**record, "artifact_sha256": {**record["artifact_sha256"], "../escape": "0" * 64}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                history._validate_record(changed)

    def test_terminal_rust_map_rejects_incomplete_bool_and_unsafe_paths(self):
        quality = history._read_json(history.QUALITY)
        self.assertIsInstance(quality, dict)
        expected = quality["production_rust_sha256"]
        for changed in (
            {**quality, "production_rust_sha256": dict(list(expected.items())[:-1])},
            {**quality, "production_rust_sha256": {**expected, "../outside.rs": "0" * 64}},
            {**quality, "production_rust_sha256": {**expected, next(iter(expected)): True}},
        ):
            with self.subTest(), self.assertRaises(ValueError):
                history._source_map(changed)

    def test_initial_archive_rejects_missing_member_and_checkpoint_blob_drift(self):
        retained, matches = check_p113._repair_evidence()
        self.assertTrue(matches)
        self.assertEqual(len(retained), 59)
        missing = dict(retained)
        missing.pop(next(iter(missing)))
        with patch.object(check_p113, "_repair_evidence", return_value=(missing, True)), \
                self.assertRaises(ValueError):
            history._validate_initial_seal()

        original_blob = history._blob
        first_path = next(iter(retained))

        def changed_blob(commit: str, relative: str) -> bytes:
            raw = original_blob(commit, relative)
            return raw + b" altered" if relative == first_path else raw

        with patch.object(history, "_blob", side_effect=changed_blob), self.assertRaises(ValueError):
            history._validate_initial_seal()

    def test_historical_snapshot_rejects_changed_rust_blob_and_fabricated_qualification(self):
        raw = b"checkpoint Rust source"
        expected = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory(prefix="p113-rust-snapshot-") as temp:
            with patch.object(history, "TEMP_ROOT", Path(temp)), \
                    patch.object(history, "_blob", return_value=b"different source"), \
                    self.assertRaises(ValueError):
                history._materialize_rust({"crates/actinv-core/src/example.rs": expected})

        for relative in ("results/p113_implementation_commit.json", "results/p113_ci_runs.json"):
            with tempfile.TemporaryDirectory(prefix="p113-fabricated-record-") as temp:
                root = Path(temp)
                fake = root / relative
                fake.parent.mkdir(parents=True)
                fake.write_text("{}\n", encoding="utf-8")
                with self.subTest(relative=relative), patch.object(history, "ROOT", root), \
                        self.assertRaises(ValueError):
                    history._assert_no_unearned_records()


if __name__ == "__main__":
    unittest.main()
