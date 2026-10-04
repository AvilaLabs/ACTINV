"""Source-only regressions for P112 adoption and verdict evidence."""
from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p112_verdict as verdict


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P112VerdictTests(unittest.TestCase):
    def _fixture(self):
        names = [f"adopted_{index:02}" for index in range(18)]
        successful = {
            name: {"name": name, "exit_code": 0, "log": f"target/{name}.log",
                   "log_sha256": _hash(name.encode())}
            for name in names
        }
        old_gates = {**successful,
                     "historical_p108_replay": {"exit_code": 2, "log_sha256": verdict.P111_LOG_SHA256},
                     "historical_p109_replay": {"exit_code": None},
                     "historical_p110_replay": {"exit_code": None}}
        rust = {f"crates/example/src/file{i}.rs": _hash(b"rust") for i in range(99)}
        handbook_paths = {"book.toml", "scripts/check_docs.py", "web/docs-smoke.mjs", "docs/guide/a.md"}
        handbook = {path: _hash(b"handbook:" + path.encode()) for path in handbook_paths}
        old = {"gates": old_gates, "production_rust_sha256": rust,
               "release_build": {"binary_sha256": _hash(b"release")}}
        gates = copy.deepcopy(successful)
        for name in verdict.NEW_GATES:
            gates[name] = {"name": name, "exit_code": 0, "log": f"target/{name}.log",
                           "log_sha256": _hash(name.encode())}
        quality = {"gates": gates, "production_rust_sha256": copy.deepcopy(rust),
                   "adopted_quality": {"checkpoint_commit": verdict.CHECKPOINT,
                       "quality_sha256": verdict.P111_G3_SHA256,
                       "gate_names": sorted(names), "public_handbook_sha256": handbook,
                       "release_binary_sha256": _hash(b"release")}}
        def fake_sha(path: Path):
            if path == verdict.OLD_QUALITY:
                return verdict.P111_G3_SHA256
            relative = path.relative_to(verdict.ROOT).as_posix()
            if relative in rust:
                return rust[relative]
            if relative in handbook:
                return handbook[relative]
            return None
        return old, quality, successful, rust, handbook_paths, handbook, fake_sha

    def _run_adoption(self, old, quality, fake_sha, *, blob_reader=None, handbook_paths=None):
        from check_p107_history import _safe_rust_path
        blobs = blob_reader or (lambda _commit, path: b"handbook:" + path.encode()
                                if path in (handbook_paths or set()) else b"rust")
        with patch.object(verdict, "read", return_value=old), \
             patch.object(verdict, "sha", side_effect=fake_sha), \
             patch.object(verdict, "_git_blob", side_effect=blobs), \
             patch.object(verdict, "_handbook_paths", return_value=handbook_paths or set()):
            return verdict._adoption(quality, None)

    def test_adopts_only_exact_successes_and_requires_fresh_successor_gates(self):
        old, quality, _successful, _rust, paths, _handbook, fake_sha = self._fixture()
        passed, detail = self._run_adoption(old, quality, fake_sha, handbook_paths=paths)
        self.assertTrue(passed, detail)
        self.assertEqual(detail["adopted_gate_count"], 18)
        self.assertEqual(detail["fresh_gate_count"], len(verdict.NEW_GATES))

        changed = copy.deepcopy(quality)
        changed["gates"]["p112_source_regressions"]["exit_code"] = 2
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

        changed = copy.deepcopy(quality)
        changed["gates"]["adopted_00"]["log_sha256"] = "0" * 64
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

    def test_failed_or_unrun_old_observation_cannot_be_adopted(self):
        old, quality, _successful, _rust, paths, _handbook, fake_sha = self._fixture()
        changed = copy.deepcopy(quality)
        failed = old["gates"]["historical_p108_replay"]
        changed["gates"]["historical_p108_replay"] = failed
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

        changed = copy.deepcopy(quality)
        changed["adopted_quality"]["gate_names"].append("historical_p108_replay")
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

    def test_adoption_hashes_bind_checkpoint_and_reject_unsafe_paths(self):
        old, quality, _successful, _rust, paths, _handbook, fake_sha = self._fixture()
        changed = copy.deepcopy(quality)
        changed["adopted_quality"]["checkpoint_commit"] = "0" * 40
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

        changed = copy.deepcopy(quality)
        changed["production_rust_sha256"]["../escape.rs"] = _hash(b"rust")
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

        changed = copy.deepcopy(quality)
        changed["adopted_quality"]["public_handbook_sha256"]["book.toml"] = "0" * 64
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

        changed = copy.deepcopy(quality)
        changed["adopted_quality"]["release_binary_sha256"] = "0" * 64
        self.assertFalse(self._run_adoption(old, changed, fake_sha, handbook_paths=paths)[0])

    def test_boolean_repair_round_is_not_integer_zero(self):
        with patch.object(verdict, "ROOT", Path("/path/that/has/no/amendment")):
            self.assertTrue(verdict._repair_policy({"repair_rounds": 0}))
            self.assertFalse(verdict._repair_policy({"repair_rounds": False}))

    def test_workspace_counts_reject_boolean_numeric_evidence(self):
        expected = {"passed": 474, "failed": 0, "ignored": 2}
        self.assertTrue(verdict._workspace_counts_match(expected, expected))
        self.assertFalse(verdict._workspace_counts_match(
            {"passed": 474, "failed": False, "ignored": 2}, expected))

    def test_ci_may_be_pending_after_valid_record_but_not_without_record(self):
        record = {"commit_sha": "a" * 40}
        self.assertEqual(verdict._ci_state(True, record, None, False), (True, False))
        runs = [{"workflowName": name, "headSha": "a" * 40,
                 "status": "completed", "conclusion": "success"}
                for name in verdict.REQUIRED_WORKFLOWS]
        self.assertEqual(verdict._ci_state(True, record, runs, True), (True, True))
        self.assertEqual(verdict._ci_state(False, None, runs, True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, [], True), (False, False))

    def test_path_parser_rejects_absolute_and_parent_paths(self):
        for name in ("/tmp/escape", "../escape", "docs/../escape", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                verdict._safe_rel(name)


if __name__ == "__main__":
    unittest.main()
