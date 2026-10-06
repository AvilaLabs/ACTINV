#!/usr/bin/env python3
"""Regressions for verification of the immutable P118 terminal failure."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p117_history as p117_history
import check_p118_history as history


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P118HistoryTests(unittest.TestCase):
    def test_genuine_archived_failure_is_verified_as_failure(self):
        report = history.verify()
        self.assertIs(report.get("pass"), True, report)
        self.assertEqual(report["verdict"], "P118-FAIL")
        self.assertEqual(report["checkpoint_commit"], history.CHECKPOINT)
        self.assertEqual(report["terminal_archive_file_count"], 42)
        self.assertEqual(report["initial_archive_file_count"], 16)
        self.assertEqual(report["predecessor_archive_file_count"], 100)
        self.assertEqual(report["terminal_source_file_count"], 319)
        self.assertEqual(report["rust_source_file_count"], 100)
        self.assertEqual(report["public_handbook_file_count"], 27)
        self.assertEqual(report["history_failure"]["tests"], 12)
        self.assertEqual(report["history_failure"]["failures"], 1)
        self.assertEqual(report["history_failure"]["errors"], 6)
        self.assertEqual(report["terminal_verdict"]["actual_exit_code"], 1)

    def test_all_four_fresh_gate_records_match_the_genuine_archive(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        verified = history._verify_fresh_gates(discovery, archive)
        self.assertEqual(set(verified), set(history.FRESH_GATES))
        self.assertEqual(verified[history.HISTORY_GATE]["exit_code"], 1)

    def test_archive_map_rejects_bad_hash_missing_and_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            (archive / "one.txt").write_bytes(b"one")
            expected = {"one.txt": _sha(b"one")}
            with patch.object(p117_history, "ROOT", root):
                self.assertEqual(p117_history._archive_map("archive", expected),
                                 {"archive/one.txt": _sha(b"one")})
                with self.assertRaises(ValueError):
                    p117_history._archive_map("archive", {"one.txt": "0" * 64})
                with self.assertRaises((OSError, ValueError)):
                    p117_history._archive_map("archive", {"missing.txt": _sha(b"one")})
                (archive / "extra.txt").write_bytes(b"extra")
                with self.assertRaises(ValueError):
                    p117_history._archive_map("archive", expected)

    def test_terminal_archive_map_mutation_is_rejected(self):
        discovery = history._json(history._read(history.DISCOVERY))
        changed = copy.deepcopy(discovery)
        path = next(iter(changed["preserved_files_sha256"]))
        changed["preserved_files_sha256"][path] = "0" * 64
        with self.assertRaises(ValueError):
            history._verify_terminal_archive(changed)
        changed = copy.deepcopy(discovery)
        del changed["preserved_files_sha256"][path]
        with self.assertRaises(ValueError):
            history._verify_terminal_archive(changed)

    def test_terminal_discovery_bytes_are_pinned(self):
        original_read = history._read
        raw = original_read(history.DISCOVERY) + b"\n"

        def read(path):
            return raw if path == history.DISCOVERY else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            report = history.verify()
        self.assertIs(report.get("pass"), False)
        self.assertIn("discovery digest", report.get("error", ""))

    def test_source_hash_mutation_is_rejected_against_checkpoint_git(self):
        discovery = history._json(history._read(history.DISCOVERY))
        changed = copy.deepcopy(discovery)
        path = next(iter(changed["terminal_source_sha256"]))
        changed["terminal_source_sha256"][path] = "0" * 64
        with self.assertRaises(ValueError):
            history._verify_source_maps(changed)

    def test_history_exit_mutation_is_rejected(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        changed_discovery = copy.deepcopy(discovery)
        changed_discovery["fresh_gate_observations"][history.HISTORY_GATE]["child_exit_code"] = 0
        with self.assertRaises(ValueError):
            history._verify_history_episode(changed_discovery, archive)

    def test_outer_exit_mutation_is_rejected_even_with_updated_archive_hash(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        original_read = history._read
        rel = f"{history.ARCHIVE}/{history.HISTORY_OBS}"
        observation = json.loads(original_read(rel))
        observation["exit_code"] = 0
        replacement = json.dumps(observation, sort_keys=True).encode()
        archive[rel] = _sha(replacement)

        def read(path):
            return replacement if path == rel else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            with self.assertRaises(ValueError):
                history._verify_history_episode(discovery, archive)

    def test_receipt_exit_mutation_is_rejected_even_with_updated_archive_hash(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        original_read = history._read
        rel = f"{history.ARCHIVE}/{history.HISTORY_RECEIPT}"
        receipt = json.loads(original_read(rel))
        receipt["child_exit_code"] = 0
        replacement = json.dumps(receipt, sort_keys=True).encode()
        archive[rel] = _sha(replacement)

        def read(path):
            return replacement if path == rel else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            with self.assertRaises(ValueError):
                history._verify_history_episode(discovery, archive)

    def test_resource_limit_mutation_is_rejected(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        original_read = history._read
        rel = f"{history.ARCHIVE}/{history.HISTORY_RECEIPT}"
        receipt = json.loads(original_read(rel))
        receipt["resources"]["cgroup_limits"]["memory.max"] = "unbounded"
        replacement = json.dumps(receipt, sort_keys=True).encode()
        archive[rel] = _sha(replacement)
        changed_discovery = copy.deepcopy(discovery)
        changed_discovery["fresh_gate_observations"][history.HISTORY_GATE] = receipt

        def read(path):
            return replacement if path in {rel, history.HISTORY_RECEIPT} else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            with self.assertRaises(ValueError):
                history._verify_fresh_gates(changed_discovery, archive)

    def test_raw_and_durable_history_log_must_match(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        original_read = history._read
        rel = f"{history.ARCHIVE}/{history.HISTORY_RAW}"
        replacement = original_read(rel) + b"mutated\n"

        def read(path):
            return replacement if path == rel else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            with self.assertRaises(ValueError):
                history._verify_fresh_gates(discovery, archive)

    def test_terminal_verdict_artifact_mutation_is_rejected(self):
        discovery = history._json(history._read(history.DISCOVERY))
        archive = history._verify_terminal_archive(discovery)
        original_read = history._read
        rel = f"{history.ARCHIVE}/results/p118_verdict.json"
        replacement = original_read(rel) + b"\n"

        def read(path):
            return replacement if path == rel else original_read(path)

        with patch.object(history, "_read", side_effect=read):
            with self.assertRaises(ValueError):
                history._verify_terminal_verdict(discovery, archive)

    def test_unrun_artifact_must_remain_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(history, "ROOT", root):
                history._verify_absent_artifacts()
                artifact = root / history.ABSENT[0]
                artifact.parent.mkdir(parents=True)
                artifact.write_text("unexpected")
                with self.assertRaises(ValueError):
                    history._verify_absent_artifacts()

    def test_workflow_must_be_exact_registered_replacement(self):
        original_read = history._read

        def read(path):
            raw = original_read(path)
            return raw + b"# unrelated change\n" if path == ".github/workflows/ci.yml" else raw

        with patch.object(history, "_read", side_effect=read):
            self.assertFalse(history._workflow_transition_matches())


if __name__ == "__main__":
    unittest.main()
