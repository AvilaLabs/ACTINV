"""Source-only regressions for the retained terminal P114 failure."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p114_history as history


class P114HistoryTests(unittest.TestCase):
    def test_terminal_failure_is_rederived_from_checkpoint_sources(self):
        report = history.verify()
        self.assertIs(report.get("pass"), True, report)
        self.assertEqual(report["checkpoint_commit"], "88e9251a6c7a61db2293ee9772457e4f51842253")
        self.assertEqual(report["verdict"], "P114-FAIL")
        self.assertEqual(report["initial_archive_files_verified"], 176)
        self.assertEqual(report["final_amended_archive_files_verified"], 196)
        self.assertEqual(report["rust_files_verified_against_checkpoint"], 100)
        self.assertEqual(report["executed_gate_receipts_verified"], 9)
        self.assertEqual(report["successful_gate_receipts_verified"], 8)
        self.assertIs(report["historical_source_verification"], False)

    def test_record_rejects_counter_bool_status_and_artifact_tampering(self):
        record = history._read_json(history.FAILURE_RECORD)
        self.assertIsInstance(record, dict)
        cases = (
            {**record, "failure_exit_code": True},
            {**record, "repair_rounds": True},
            {**record, "g0_sealed": 0},
            {**record, "g1_executed": True},
            {**record, "implementation_record": {"commit_sha": "f" * 40}},
            {**record, "ci_record": {"headSha": "f" * 40}},
            {**record, "artifact_sha256": {**record["artifact_sha256"], "../escape": "0" * 64}},
            {key: value for key, value in record.items() if key != "first_failure_log_path"},
        )
        for changed in cases:
            with self.subTest(fields=tuple(changed)), self.assertRaises(ValueError):
                history._validate_record(changed)

    def test_record_positive_shape_binds_all_artifacts(self):
        record = history._read_json(history.FAILURE_RECORD)
        self.assertIsInstance(record, dict)
        returned: list[str] = []

        def exact_file(path: str, digest: str) -> bytes:
            returned.append(path)
            return digest.encode("ascii")

        with patch.object(history, "_exact_checkpoint_file", side_effect=exact_file):
            artifacts = history._validate_record(copy.deepcopy(record))
        self.assertEqual(set(artifacts), set(history.EXPECTED_ARTIFACTS))
        self.assertCountEqual(returned, history.EXPECTED_ARTIFACTS)

    def test_initial_and_final_archive_reject_missing_members_and_changed_blobs(self):
        discovery = history._read_json(history.ROOT / history.INITIAL_DISCOVERY)
        quality = history._read_json(history.QUALITY)
        self.assertIsInstance(discovery, dict)
        self.assertIsInstance(quality, dict)
        initial = discovery["files_sha256"]
        final = quality["retained_after_amendment_sha256"]
        for prefix, mapping, count in ((history.INITIAL_PREFIX, initial, 176),
                                       (history.FINAL_PREFIX, final, 196)):
            missing = dict(mapping)
            missing.pop(next(iter(missing)))
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                history._validate_archive(prefix, missing, count)

        relative = next(iter(initial))
        original_blob = history._blob
        visited: list[str] = []

        def altered_blob(commit: str, path: str) -> bytes:
            visited.append(path)
            raw = original_blob(commit, path)
            return raw + b"tampered" if path == relative else raw

        with patch.object(history, "_blob", side_effect=altered_blob), self.assertRaises(ValueError):
            history._validate_archive(
                history.INITIAL_PREFIX, initial, 176, extra_members={history.INITIAL_DISCOVERY}
            )
        self.assertIn(relative, visited)

    def test_initial_discovery_rejects_wrong_observed_failure_counts(self):
        original = history._read_json(history.ROOT / history.INITIAL_DISCOVERY)
        self.assertIsInstance(original, dict)
        for field, value in (("observed_test_count", True), ("observed_error_count", 1),
                             ("initial_successful_gate_names", [])):
            changed = copy.deepcopy(original)
            changed[field] = value
            with patch.object(history, "_read_json", return_value=changed), \
                    self.assertRaises(ValueError):
                history._validate_initial_failure()

    def test_current_historical_control_source_must_match_checkpoint_and_archive(self):
        relative = "controls/frozen.py"
        checkpoint = b"frozen checkpoint source"
        digest = hashlib.sha256(checkpoint).hexdigest()
        archive = {history.FINAL_PREFIX + relative: digest}
        with tempfile.TemporaryDirectory(prefix="p114-history-source-") as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(checkpoint)
            with patch.object(history, "ROOT", root), \
                    patch.object(history, "_p114_control_files", return_value=(relative,)), \
                    patch.object(history, "_blob", return_value=checkpoint):
                self.assertEqual(history._validate_current_control_sources(archive), 1)
                path.write_bytes(b"changed working source")
                with self.assertRaises(ValueError):
                    history._validate_current_control_sources(archive)


if __name__ == "__main__":
    unittest.main()
