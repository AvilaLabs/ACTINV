"""Source-only P115 disposition, workflow, and durable-receipt regressions."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p115_verdict as verdict
import check_p115 as controls


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P115VerdictTests(unittest.TestCase):
    def test_exact_frozen_quality_gate_set(self):
        self.assertEqual(len(verdict.REQUIRED_GATES), 30)
        required = {
            "rust_fmt", "workspace_check", "workspace_clippy", "workspace_tests", "release_build",
            "p115_oracle_regressions", "p115_seal_regressions", "p115_verdict_regressions",
            "p113_history_regressions", "p114_history_regressions", "gate_recorder_regressions",
            "p105_child_lifecycle_regressions", "historical_p107_replay", "historical_p108_replay",
            "historical_p109_replay", "historical_p110_replay", "historical_p111_replay",
            "historical_p112_replay", "historical_p113_replay", "historical_p114_replay",
            "p105_scientific_replay", "p107_scientific_replay", "p108_scientific_replay",
            "p110_scientific_replay", "p111_scientific_replay", "p112_scientific_replay",
            "full_p115_read_only_replay", "handbook_build", "handbook_links", "handbook_chromium",
        }
        self.assertEqual(verdict.REQUIRED_GATES, required)

    def test_ci_requires_six_workflows_on_implementation_commit(self):
        commit = "a" * 40
        record = {"commit_sha": commit}
        runs = [{"workflowName": name, "headSha": commit, "status": "completed",
                 "conclusion": "success", "databaseId": index + 1,
                 "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{index + 1}"}
                for index, name in enumerate(sorted(verdict.REQUIRED_WORKFLOWS))]
        self.assertEqual(verdict._ci_state(True, record, runs, True), (True, True))
        changed = [*runs]
        changed[0] = {**changed[0], "headSha": "b" * 40}
        self.assertEqual(verdict._ci_state(True, record, changed, True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, runs[:-1], True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, None, False), (True, False))

    def test_receipt_binds_exit_argv_resources_and_committed_log_bytes(self):
        resources = {"platform": "linux", "cgroup_path": "/user.slice/test.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1",
                             "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/home",
                       "filesystem": "ext4"}}
        with tempfile.TemporaryDirectory(prefix="p115-receipt-") as temp:
            root = Path(temp)
            name = "p115_oracle_regressions"
            receipt_path = root / f"results/quality/p115/{name}.json"
            log_path = root / f"results/quality/p115/{name}.log"
            receipt_path.parent.mkdir(parents=True)
            log_path.parent.mkdir(parents=True)
            raw_log = b"gate exited zero\n"
            log_path.write_bytes(raw_log)
            argv = ["python", "controls/test_p115_oracle.py"]
            receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P115",
                "gate": name, "argv": argv, "cwd": ".", "timeout_s": 600.0,
                "status": "completed", "child_exit_code": 0,
                "log_path": f"target/p115-{name}.log", "log_sha256": _sha(raw_log),
                "resources": resources, "error": None}
            raw_receipt = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
            receipt_path.write_bytes(raw_receipt)
            gate = {"name": name, "argv": argv, "exit_code": 0,
                "log_path": f"results/quality/p115/{name}.log", "log_sha256": _sha(raw_log),
                "receipt_path": f"results/quality/p115/{name}.json",
                "receipt_sha256": _sha(raw_receipt), "resources": resources}
            with patch.object(verdict, "ROOT", root):
                self.assertTrue(verdict._receipt_ok(name, gate))
                self.assertFalse(verdict._receipt_ok(name, {**gate, "exit_code": False}))
                self.assertFalse(verdict._receipt_ok(name, {**gate, "argv": ["undefined", "x"]}))
                wrong_cwd = {**receipt, "cwd": "controls"}
                receipt_path.write_text(json.dumps(wrong_cwd), encoding="utf-8")
                self.assertFalse(verdict._receipt_ok(name, gate))
                receipt_path.write_bytes(raw_receipt)
                over_timeout = {**receipt, "timeout_s": 601}
                over_timeout_bytes = (json.dumps(over_timeout, sort_keys=True, indent=2) + "\n").encode()
                receipt_path.write_bytes(over_timeout_bytes)
                self.assertFalse(verdict._receipt_ok(name, {**gate, "receipt_sha256": _sha(over_timeout_bytes)}))
                receipt_path.write_bytes(raw_receipt)
                bad_fs = {**resources, "tmpdir": {**resources["tmpdir"], "filesystem": "devtmpfs"}}
                self.assertFalse(verdict._receipt_ok(name, {**gate, "resources": bad_fs}))
                bad_resources = {**resources, "cgroup_limits": {**resources["cgroup_limits"],
                                                                    "memory.max": "6442450943"}}
                self.assertFalse(verdict._receipt_ok(name, {**gate, "resources": bad_resources}))
                changed_receipt = {**receipt, "child_exit_code": False}
                receipt_path.write_text(json.dumps(changed_receipt), encoding="utf-8")
                self.assertFalse(verdict._receipt_ok(name, gate))
                receipt_path.write_bytes(raw_receipt)
                log_path.write_bytes(b"post-receipt mutation\n")
                self.assertFalse(verdict._receipt_ok(name, gate))

    def test_resource_inspection_requires_recorded_file_and_exact_limits(self):
        limits = {"memory.max": "6442450944", "memory.swap.max": "0",
                  "pids.max": "128", "cpu.max": "200000 100000"}
        with tempfile.TemporaryDirectory(prefix="p115-resource-") as temp:
            root = Path(temp)
            path = root / "results/quality/p115/resource_inspection.log"
            path.parent.mkdir(parents=True)
            raw = b"limits inspected\n"
            path.write_bytes(raw)
            evidence = {"exit_code": 0, "log_path": "results/quality/p115/resource_inspection.log",
                        "log_sha256": _sha(raw), "limits": limits}
            with patch.object(verdict, "ROOT", root):
                self.assertTrue(verdict._resource_inspection_ok(evidence))
                self.assertFalse(verdict._resource_inspection_ok({**evidence, "limits": {**limits, "pids.max": "129"}}))
                self.assertFalse(verdict._resource_inspection_ok({**evidence, "log_sha256": "0" * 64}))

    def test_current_p115_round_is_zero_only(self):
        with tempfile.TemporaryDirectory(prefix="p115-round-") as temp:
            root = Path(temp)
            with patch.object(controls, "ROOT", root), patch.object(
                    controls, "P115_AMENDMENT_A", root / "protocols/ACTINV-P115_AMENDMENT_A.md"):
                (root / "protocols").mkdir()
                (root / "protocols/protocol_hash.txt").write_text("", encoding="utf-8")
                self.assertTrue(controls._current_repair_round_valid())
                (root / "protocols/ACTINV-P115_AMENDMENT_A.md").write_text("x", encoding="utf-8")
                self.assertFalse(controls._current_repair_round_valid())

    def test_g1_request_evidence_requires_each_identity_digest(self):
        counts = [4] * 33 + [3, 3]
        ids = [f"case-{index:02}" for index in range(35)]
        g0 = {"request_ids": ids, "case_targets": counts}
        evidence = [{"id": case_id, "component_target_count": count,
                     "input_sha256": "1" * 64, "output_sha256": "2" * 64,
                     "ordinary_waste_sha256": "3" * 64}
                    for case_id, count in zip(ids, counts)]
        g1 = {"schema": "actinv-p115-twin-waste-g1-1", "phase": "P115", "pass": True,
              "protocol_sha256": verdict.PROTOCOL_SHA256, "request_count": 35,
              "component_target_count": 138, "independent_comparison_count": 138,
              "request_evidence": evidence, "failures": [], "repeat_byte_identical": True,
              "mutations_rejected": {f"m{index}": True for index in range(30)},
              "refusal_controls": {"pass": True, "checks": {f"r{index}": True for index in range(30)}}}
        self.assertTrue(verdict._g1_ok(g1, g0))
        for field in ("input_sha256", "output_sha256", "ordinary_waste_sha256"):
            changed = json.loads(json.dumps(g1))
            changed["request_evidence"][0][field] = None
            self.assertFalse(verdict._g1_ok(changed, g0), field)

    def test_both_historical_failure_adapters_require_verified_records(self):
        import types

        valid = types.ModuleType("check_p113_history")
        valid.verify = lambda: {"pass": True}
        invalid = types.ModuleType("check_p113_history")
        invalid.verify = lambda: {"pass": False}
        with patch.dict(sys.modules, {"check_p113_history": valid}):
            self.assertTrue(verdict._history_ok("check_p113_history"))
        with patch.dict(sys.modules, {"check_p114_history": valid}):
            self.assertTrue(verdict._history_ok("check_p114_history"))
        with patch.dict(sys.modules, {"check_p113_history": invalid}):
            self.assertFalse(verdict._history_ok("check_p113_history"))
        with patch.dict(sys.modules, {"check_p114_history": invalid}):
            self.assertFalse(verdict._history_ok("check_p114_history"))

    def test_history_source_validation_uses_full_implementation_tree(self):
        historical = b"checkpoint source"
        source_hash = _sha(historical)
        paths = {f"crates/mock/src/{index}.rs" for index in range(100)}
        hashes = {path: source_hash for path in paths}
        g3 = {"production_rust_sha256": hashes}
        record = {"commit_sha": "c" * 40}
        with patch("check_p115._rust_paths_at_commit", return_value=paths), \
             patch("check_p115._checkpoint_source_hashes", return_value=hashes), \
             patch("check_p115._git_blob", return_value=historical):
            self.assertTrue(verdict._source_commit_matches(g3, record))
        with patch("check_p115._rust_paths_at_commit", return_value=paths | {"crates/extra.rs"}), \
             patch("check_p115._checkpoint_source_hashes", return_value=hashes), \
             patch("check_p115._git_blob", return_value=historical):
            self.assertFalse(verdict._source_commit_matches(g3, record))


if __name__ == "__main__":
    unittest.main()
