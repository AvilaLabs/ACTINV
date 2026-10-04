"""Source-only P114 disposition, workflow, and durable-receipt regressions."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p114_verdict as verdict
import check_p114 as controls


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P114VerdictTests(unittest.TestCase):
    def test_exact_frozen_quality_gate_set(self):
        self.assertEqual(len(verdict.REQUIRED_GATES), 28)
        required = {"rust_fmt", "workspace_check", "workspace_clippy", "workspace_tests",
                    "release_build", "p114_oracle_regressions", "p114_seal_regressions",
                    "p114_verdict_regressions", "p113_history_regressions",
                    "gate_recorder_regressions", "p105_child_lifecycle_regressions",
                    "historical_p113_replay", "full_p114_read_only_replay", "handbook_chromium"}
        self.assertTrue(required.issubset(verdict.REQUIRED_GATES))

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
        with tempfile.TemporaryDirectory(prefix="p114-receipt-") as temp:
            root = Path(temp)
            name = "p114_oracle_regressions"
            receipt_path = root / f"results/quality/p114/{name}.json"
            log_path = root / f"results/quality/p114/{name}.log"
            receipt_path.parent.mkdir(parents=True)
            log_path.parent.mkdir(parents=True)
            raw_log = b"gate exited zero\n"
            log_path.write_bytes(raw_log)
            argv = ["python", "controls/test_p114_oracle.py"]
            receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P114",
                "gate": name, "argv": argv, "cwd": ".", "timeout_s": 600.0,
                "status": "completed", "child_exit_code": 0,
                "log_path": f"target/p114-{name}.log", "log_sha256": _sha(raw_log),
                "resources": resources, "error": None}
            raw_receipt = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
            receipt_path.write_bytes(raw_receipt)
            gate = {"name": name, "argv": argv, "exit_code": 0,
                "log_path": f"results/quality/p114/{name}.log", "log_sha256": _sha(raw_log),
                "receipt_path": f"results/quality/p114/{name}.json",
                "receipt_sha256": _sha(raw_receipt), "resources": resources}
            with patch.object(verdict, "ROOT", root):
                self.assertTrue(verdict._receipt_ok(name, gate))
                self.assertFalse(verdict._receipt_ok(name, {**gate, "exit_code": False}))
                self.assertFalse(verdict._receipt_ok(name, {**gate, "argv": ["undefined", "x"]}))
                wrong_cwd = {**receipt, "cwd": "controls"}
                receipt_path.write_text(json.dumps(wrong_cwd), encoding="utf-8")
                self.assertFalse(verdict._receipt_ok(name, gate))
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
        with tempfile.TemporaryDirectory(prefix="p114-resource-") as temp:
            root = Path(temp)
            path = root / "results/quality/p114/resource_inspection.log"
            path.parent.mkdir(parents=True)
            raw = b"limits inspected\n"
            path.write_bytes(raw)
            evidence = {"exit_code": 0, "log_path": "results/quality/p114/resource_inspection.log",
                        "log_sha256": _sha(raw), "limits": limits}
            with patch.object(verdict, "ROOT", root):
                self.assertTrue(verdict._resource_inspection_ok(evidence))
                self.assertFalse(verdict._resource_inspection_ok({**evidence, "limits": {**limits, "pids.max": "129"}}))
                self.assertFalse(verdict._resource_inspection_ok({**evidence, "log_sha256": "0" * 64}))

    def test_repair_policy_requires_registered_round_and_retained_bytes(self):
        with tempfile.TemporaryDirectory(prefix="p114-repair-policy-") as temp:
            root = Path(temp)
            protocol_dir = root / "protocols"
            archive = root / "results/failures/p114_initial"
            protocol_dir.mkdir(parents=True)
            archive.mkdir(parents=True)
            amendment = protocol_dir / "ACTINV-P114_AMENDMENT_A.md"
            discovery_path = archive / "discovery.json"
            with patch.object(controls, "ROOT", root), patch.object(controls, "AMENDMENT_A", amendment), \
                 patch.object(controls, "P114_DISCOVERY", discovery_path):
                self.assertTrue(controls._repair_policy({"repair_rounds": 0}))
            amendment.write_bytes(b"registered repair scope\n")
            amendment_hash = _sha(amendment.read_bytes())
            (protocol_dir / "protocol_hash.txt").write_text(
                f"{amendment_hash}  protocols/ACTINV-P114_AMENDMENT_A.md\n", encoding="utf-8")
            retained_log_rel = "results/failures/p114_initial/target/p114-p114_oracle_regressions.log"
            retained_log = root / retained_log_rel
            retained_log.parent.mkdir(parents=True)
            retained_log.write_bytes(b"exact first-gate failure\n")
            receipt_rel = "results/failures/p114_initial/results/quality/p114/p114_oracle_regressions.json"
            receipt_path = root / receipt_rel
            receipt_path.parent.mkdir(parents=True)
            receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P114",
                "gate": "p114_oracle_regressions", "argv": ["python3", "controls/test_p114_oracle.py"],
                "cwd": ".", "status": "child_failed", "child_exit_code": 1, "error": None,
                "log_path": "target/p114-p114_oracle_regressions.log",
                "log_sha256": _sha(retained_log.read_bytes())}
            receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            file_map = {retained_log_rel: _sha(retained_log.read_bytes()),
                        receipt_rel: _sha(receipt_path.read_bytes())}
            for index in range(174):
                relative = f"results/failures/p114_initial/retained/{index:03}.bin"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"retained-{index}".encode())
                file_map[relative] = _sha(path.read_bytes())
            discovery = {"schema": "actinv-p114-initial-failure-1", "phase": "P114",
                "gate": "p114_oracle_regressions", "file_count": 176, "files_sha256": file_map,
                "failure_receipt_sha256": file_map[receipt_rel], "exit_code": 1,
                "g0_executed": False, "g1_executed": False, "g2_executed": False,
                "observed_test_count": 28, "observed_error_count": 2,
                "initial_successful_gate_names": ["gate_recorder_regressions", "p113_history_regressions"]}
            discovery_bytes = (json.dumps(discovery, sort_keys=True, indent=2) + "\n").encode()
            discovery_path.write_bytes(discovery_bytes)
            discovery_hash = _sha(discovery_bytes)
            with patch.object(controls, "ROOT", root), patch.object(controls, "AMENDMENT_A", amendment), \
                 patch.object(controls, "AMENDMENT_A_SHA256", amendment_hash), \
                 patch.object(controls, "P114_DISCOVERY", discovery_path), \
                 patch.object(controls, "P114_DISCOVERY_SHA256", discovery_hash):
                self.assertFalse(controls._repair_policy({"repair_rounds": 0}))
                round_one = {"repair_rounds": 1, "repair_amendment_sha256": amendment_hash,
                    "repair_discovery_sha256": discovery_hash,
                    "repair_evidence_sha256": file_map,
                    "repair_evidence_matches": True}
                self.assertTrue(controls._repair_policy(round_one))
                self.assertFalse(controls._repair_policy({**round_one, "repair_rounds": True}))
                self.assertFalse(controls._repair_policy({**round_one, "repair_evidence_sha256": {}}))
                retained_log.write_bytes(b"mutated failure evidence\n")
                self.assertFalse(controls._repair_policy(round_one))

    def test_g1_request_evidence_requires_each_identity_digest(self):
        counts = [4] * 33 + [3, 3]
        ids = [f"case-{index:02}" for index in range(35)]
        g0 = {"request_ids": ids, "case_targets": counts}
        evidence = [{"id": case_id, "component_target_count": count,
                     "input_sha256": "1" * 64, "output_sha256": "2" * 64,
                     "ordinary_waste_sha256": "3" * 64}
                    for case_id, count in zip(ids, counts)]
        g1 = {"schema": "actinv-p114-twin-waste-g1-1", "phase": "P114", "pass": True,
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

    def test_p113_history_adapter_requires_verified_failure_record(self):
        import types

        valid = types.ModuleType("check_p113_history")
        valid.verify = lambda: {"pass": True}
        invalid = types.ModuleType("check_p113_history")
        invalid.verify = lambda: {"pass": False}
        with patch.dict(sys.modules, {"check_p113_history": valid}):
            self.assertTrue(verdict._p113_history_ok())
        with patch.dict(sys.modules, {"check_p113_history": invalid}):
            self.assertFalse(verdict._p113_history_ok())

    def test_history_source_validation_uses_full_implementation_tree(self):
        historical = b"checkpoint source"
        source_hash = _sha(historical)
        paths = {f"crates/mock/src/{index}.rs" for index in range(100)}
        hashes = {path: source_hash for path in paths}
        g3 = {"production_rust_sha256": hashes}
        record = {"commit_sha": "c" * 40}
        with patch("check_p114._rust_paths_at_commit", return_value=paths), \
             patch("check_p114._checkpoint_source_hashes", return_value=hashes), \
             patch("check_p114._git_blob", return_value=historical):
            self.assertTrue(verdict._source_commit_matches(g3, record))
        with patch("check_p114._rust_paths_at_commit", return_value=paths | {"crates/extra.rs"}), \
             patch("check_p114._checkpoint_source_hashes", return_value=hashes), \
             patch("check_p114._git_blob", return_value=historical):
            self.assertFalse(verdict._source_commit_matches(g3, record))


if __name__ == "__main__":
    unittest.main()
