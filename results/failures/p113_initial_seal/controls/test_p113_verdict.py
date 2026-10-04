"""Source-only P113 verdict predicate regressions; no gate or solver execution."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p113_verdict as verdict


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P113VerdictTests(unittest.TestCase):
    def test_ci_absent_is_pending_but_malformed_present_evidence_fails(self):
        record = {"commit_sha": "a" * 40}
        self.assertEqual(verdict._ci_state(True, record, None, False), (True, False))
        green = [{"workflowName": name, "headSha": "a" * 40,
                  "status": "completed", "conclusion": "success", "databaseId": index + 1,
                  "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{index + 1}"}
                 for index, name in enumerate(sorted(verdict.REQUIRED_WORKFLOWS))]
        self.assertEqual(verdict._ci_state(True, record, green, True), (True, True))
        self.assertEqual(verdict._ci_state(False, None, green, True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, [], True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, [*green, None], True), (False, False))
        duplicate = [*green[:-1], {**green[0], "databaseId": 101,
                                    "url": "https://github.com/AvilaLabs/ACTINV/actions/runs/101"}]
        self.assertEqual(verdict._ci_state(True, record, duplicate, True), (False, False))
        bad_id = [*green[:-1], {**green[-1], "databaseId": True}]
        self.assertEqual(verdict._ci_state(True, record, bad_id, True), (False, False))

    def test_ci_requires_every_workflow_on_the_exact_commit(self):
        commit = "b" * 40
        record = {"commit_sha": commit}
        runs = [{"workflowName": name, "headSha": commit, "status": "completed",
                 "conclusion": "success", "databaseId": index + 1,
                 "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{index + 1}"}
                for index, name in enumerate(sorted(verdict.REQUIRED_WORKFLOWS))]
        self.assertEqual(verdict._ci_state(True, record, runs, True), (True, True))
        runs[0] = {**runs[0], "headSha": "c" * 40}
        self.assertEqual(verdict._ci_state(True, record, runs, True), (False, False))
        runs[0] = {**runs[0], "headSha": commit, "conclusion": "failure"}
        self.assertEqual(verdict._ci_state(True, record, runs, True), (False, False))

    def test_quality_gates_require_actual_integer_zero_and_bound_logs(self):
        gates = {name: {"name": name, "exit_code": 0, "log": f"logs/{name}.txt",
                        "log_sha256": _sha(name.encode())}
                 for name in verdict.REQUIRED_GATES}
        self.assertTrue(verdict._strict_gate_map({"gates": gates}))
        broken = {**gates, "workspace_tests": {**gates["workspace_tests"], "exit_code": False}}
        self.assertFalse(verdict._strict_gate_map({"gates": broken}))
        broken = dict(gates)
        del broken["historical_p112_replay"]
        self.assertFalse(verdict._strict_gate_map({"gates": broken}))
        broken = {**gates, "historical_p111_replay": {**gates["historical_p111_replay"],
                                                         "log_sha256": "not-a-digest"}}
        self.assertFalse(verdict._strict_gate_map({"gates": broken}))

    def test_repair_policy_rejects_boolean_round_and_requires_registered_amendment(self):
        with tempfile.TemporaryDirectory(prefix="p113-repair-policy-") as temp:
            root = Path(temp)
            registry = root / "protocols/protocol_hash.txt"
            registry.parent.mkdir()
            registry.write_text("", encoding="utf-8")
            with patch.object(verdict, "ROOT", root):
                self.assertTrue(verdict._repair_policy({"repair_rounds": 0}))
                self.assertFalse(verdict._repair_policy({"repair_rounds": False}))
                amendment = root / "protocols/ACTINV-P113_AMENDMENT_A.md"
                amendment.write_text("registered repair", encoding="utf-8")
                amendment_sha = _sha(b"registered repair")
                evidence = root / "results/repair.log"
                evidence.parent.mkdir(parents=True)
                evidence.write_text("retained failed gate", encoding="utf-8")
                evidence_sha = _sha(b"retained failed gate")
                record = {"repair_rounds": 1, "repair_amendment_sha256": amendment_sha,
                          "repair_evidence_sha256": {"results/repair.log": evidence_sha}}
                self.assertFalse(verdict._repair_policy(record))
                registry.write_text(
                    f"{amendment_sha}  protocols/ACTINV-P113_AMENDMENT_A.md\n", encoding="utf-8")
                self.assertTrue(verdict._repair_policy(record))
                self.assertFalse(verdict._repair_policy({**record, "repair_rounds": True}))

    def test_g1_requires_complete_structural_population_and_rejects_bool_counts(self):
        request_ids = [f"request-{i}" for i in range(24)]
        case_targets = [2] * 8 + [1] * 16
        g0 = {"request_count": 24, "component_target_count": 32,
              "request_ids": request_ids, "case_targets": case_targets}
        evidence = [{"id": f"request-{i}", "input_sha256": _sha(f"in{i}".encode()),
                     "output_sha256": _sha(f"out{i}".encode()),
                     "ordinary_waste_sha256": _sha(f"waste{i}".encode()),
                     "component_target_count": case_targets[i]} for i in range(24)]
        g1 = {"schema": "actinv-p113-twin-waste-g1-1", "phase": "P113", "pass": True,
              "protocol_sha256": verdict.PROTOCOL_SHA256, "request_count": 24,
              "component_target_count": 32, "independent_comparison_count": 32,
              "failures": [], "repeat_byte_identical": True, "request_evidence": evidence,
              "mutations_rejected": {f"mutation-{i}": True for i in range(30)},
              "refusal_controls": {"pass": True,
                  "checks": {f"refusal-{i}": True for i in range(30)}}}
        self.assertTrue(verdict._g1_ok(g1, g0))
        bad = {**g1, "request_count": True}
        self.assertFalse(verdict._g1_ok(bad, g0))
        bad = {**g1, "component_target_count": True, "independent_comparison_count": True}
        self.assertFalse(verdict._g1_ok(bad, {"request_count": 24, "component_target_count": True,
                                              "request_ids": request_ids, "case_targets": case_targets}))
        bad = {**g1, "independent_comparison_count": 31}
        self.assertFalse(verdict._g1_ok(bad, g0))
        bad = {**g1, "request_evidence": [*evidence[:-1], evidence[0]]}
        self.assertFalse(verdict._g1_ok(bad, g0))
        bad_evidence = [*evidence]
        bad_evidence[0] = {key: value for key, value in bad_evidence[0].items()
                           if key != "ordinary_waste_sha256"}
        self.assertFalse(verdict._g1_ok({**g1, "request_evidence": bad_evidence}, g0))

    def test_source_hashes_follow_recorded_git_tree_after_worktree_changes(self):
        historical = b"source at implementation commit"
        expected = _sha(historical)
        g3 = {"production_rust_sha256": {"crates/actinv-core/src/twin.rs": expected}}
        record = {"commit_sha": "d" * 40}
        with patch.object(verdict, "_rust_source_paths", return_value={"crates/actinv-core/src/twin.rs"}), \
             patch.object(verdict, "_git_blob", return_value=historical), \
             patch.object(verdict, "sha", return_value=_sha(b"later worktree edit")):
            self.assertTrue(verdict._source_commit_matches(g3, record))
        with patch.object(verdict, "_rust_source_paths", return_value={"crates/actinv-core/src/twin.rs"}), \
             patch.object(verdict, "_git_blob", return_value=b"tampered historical source"):
            self.assertFalse(verdict._source_commit_matches(g3, record))
        with patch.object(verdict, "_rust_source_paths", return_value={
                "crates/actinv-core/src/twin.rs", "crates/actinv-cli/src/twin.rs"}), \
             patch.object(verdict, "_git_blob", return_value=historical):
            self.assertFalse(verdict._source_commit_matches(g3, record))

    def test_implementation_record_binds_result_artifacts_to_commit(self):
        commit = "e" * 40
        contents = {path: f"artifact:{index}".encode()
                    for index, path in enumerate(verdict.ARTIFACTS.values())}
        record = {"schema": "actinv-p113-implementation-1", "commit_sha": commit}
        with patch.object(verdict, "sha", side_effect=lambda path: _sha(contents[path])), \
             patch.object(verdict, "_rust_source_paths", return_value=set()), \
             patch.object(verdict, "_git_blob", side_effect=lambda _commit, path: contents[
                 verdict.ROOT / path]):
            for index, path in enumerate(verdict.ARTIFACTS.values()):
                record[f"g{index}_sha256"] = _sha(contents[path])
            self.assertTrue(verdict._implementation_record_ok(record))
            record["g1_sha256"] = "0" * 64
            self.assertFalse(verdict._implementation_record_ok(record))

    def test_repository_paths_reject_traversal_and_symlink_escape(self):
        for value in ("/tmp/escape", "../escape", "docs/../escape", "", r"docs\escape"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verdict._safe_rel(value)
        with tempfile.TemporaryDirectory(prefix="p113-path-guard-") as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            outside = Path(temp) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(outside)
            with patch.object(verdict, "ROOT", root):
                self.assertFalse(verdict._bound_files({"link.txt": _sha(b"outside")}, set()))


if __name__ == "__main__":
    unittest.main()
