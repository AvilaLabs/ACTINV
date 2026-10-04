#!/usr/bin/env python3
"""Source-only P117 verdict identity and receipt-validation regressions."""
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

import check_p116
import check_p117
import check_p117_verdict as verdict


class P117VerdictTests(unittest.TestCase):
    def test_exact_p116_report_wrapper_is_required(self):
        original = check_p117._read(check_p117.P116_G1)
        report = check_p117._g1_report(original, True)
        with tempfile.TemporaryDirectory(prefix="p117-verdict-g1-") as temp:
            path = Path(temp) / "g1.json"
            path.write_bytes(check_p117._canonical(report))
            with patch.object(check_p117, "G1", path):
                self.assertTrue(verdict._g1_ok(report))
                changed = copy.deepcopy(report)
                changed["fresh_p117_report"]["request_count"] = 34
                path.write_bytes(check_p117._canonical(changed))
                self.assertFalse(verdict._g1_ok(changed))
                changed = copy.deepcopy(report)
                changed["exact_canonical_match"] = False
                path.write_bytes(check_p117._canonical(changed))
                self.assertFalse(verdict._g1_ok(changed))

    def test_receipt_resources_require_the_exact_bounded_scope(self):
        resources = {
            "platform": "linux", "cgroup_path": "/user.slice/user-1000.slice/run-x.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/",
                       "filesystem": "ext4"},
        }
        self.assertTrue(verdict._resources_ok(resources))
        for mutation in (
            {**resources, "platform": "darwin"},
            {**resources, "environment": {**resources["environment"], "RUST_TEST_THREADS": "8"}},
            {**resources, "cgroup_limits": {**resources["cgroup_limits"], "memory.swap.max": "1"}},
            {**resources, "tmpdir": {**resources["tmpdir"], "filesystem": "tmpfs"}},
            {**resources, "cgroup_path": "/user.slice/../escape.scope"},
            {**resources, "cgroup_path": "/user.slice//job.scope"},
        ):
            self.assertFalse(verdict._resources_ok(mutation))

    def test_ci_requires_all_six_unique_successes_on_implementation_sha(self):
        commit = "a" * 40
        names = sorted(verdict.WORKFLOWS)
        runs = [{"workflowName": name, "headSha": commit, "status": "completed",
                 "conclusion": "success", "databaseId": index + 1,
                 "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{index + 1}"}
                for index, name in enumerate(names)]
        record = {"commit_sha": commit}
        self.assertEqual(verdict._ci_state(True, record, runs, True), (True, True))
        changed = copy.deepcopy(runs)
        changed[0]["conclusion"] = "failure"
        self.assertEqual(verdict._ci_state(True, record, changed, True), (False, False))
        changed = copy.deepcopy(runs)
        changed[0]["headSha"] = "b" * 40
        self.assertEqual(verdict._ci_state(True, record, changed, True), (False, False))
        self.assertEqual(verdict._ci_state(True, record, runs[:-1], True), (False, False))
        self.assertEqual(verdict._ci_state(True, None, None, False), (True, False))

    def test_source_commit_requires_every_sealed_file_blob(self):
        blob_hash = hashlib.sha256(b"source").hexdigest()
        g0 = {"control_sha256": {"controls/example.py": blob_hash}}
        self.assertFalse(verdict._source_commit_matches(g0, None))
        with patch.object(verdict, "_bound_control_sources", return_value=True):
            self.assertTrue(verdict._source_commit_matches(g0, None))
        paths = {f"crates/unit/{index}.rs" for index in range(100)}
        rust_map = {path: blob_hash for path in paths}
        with patch.object(check_p116, "_rust_paths_at_commit", return_value=paths), \
             patch.object(check_p116, "_current_rust_source_hashes", return_value=rust_map), \
             patch.object(check_p116, "_git_blob", return_value=b"source"):
            old_g3 = {"production_rust_sha256": rust_map}
            with patch.object(verdict, "read", side_effect=lambda path: old_g3 if path == check_p117.P116_G3 else None):
                record = {"commit_sha": "a" * 40}
                self.assertTrue(verdict._source_commit_matches(g0, record))
                with patch.object(check_p116, "_git_blob", return_value=b"changed"):
                    self.assertFalse(verdict._source_commit_matches(g0, record))

    def test_g3_validation_accepts_bound_report_and_rejects_changed_source_map(self):
        old_gates = {f"old-gate-{index}": {"exit_code": 0} for index in range(32)}
        old_rust = {"crates/core/src/lib.rs": "1" * 64}
        old_g3 = {"gates": old_gates, "release_build": {"binary_sha256": "2" * 64},
                  "workspace_tests": {"passed": 485, "failed": 0, "ignored": 2},
                  "production_rust_sha256": old_rust}
        handbook = {"docs/guide/intro.md": "3" * 64}
        source_map = {"controls/check_p117.py": "4" * 64}
        g0 = {"pass": True, "control_sha256": source_map,
              "public_handbook_sha256": handbook}
        fresh = {name: {"name": name} for name in check_p117.FRESH_GATES}
        artifacts = {path: "5" * 64 for path in
                     (check_p117.G0, check_p117.G1, check_p117.G2)}
        adopted = {
            "quality_sha256": check_p117.P116_G3_SHA256,
            "implementation_commit": check_p117.IMPLEMENTATION_COMMIT,
            "verdict_sha256": check_p117.P116_VERDICT_SHA256,
            "gates": old_gates, "gate_names": sorted(old_gates), "successful_gate_count": 32,
            "workspace_tests": old_g3["workspace_tests"],
            "release_build": old_g3["release_build"],
            "production_rust_sha256": old_rust,
            "public_handbook_sha256": handbook,
            "quality_validated": True, "not_rerun": True,
        }
        g3 = {
            "schema": "actinv-p117-quality-1", "phase": "P117", "pass": True,
            "repair_rounds": 0, "resource_limits": check_p117.RESOURCES,
            "p116_adopted": adopted, "adopted_p116_gates": old_gates,
            "fresh_gates": fresh, "fresh_gate_names": sorted(fresh),
            "p116_history_verified": True, "current_rust_matches_p116": True,
            "current_rust_sha256": old_rust, "source_sha256": source_map,
            "p117_science_evidence_matches": True,
            "public_handbook_matches_p116": True, "public_handbook_sha256": handbook,
            "qualified_binary_matches_p116": True, "qualified_binary_sha256": "2" * 64,
            "failures": [], "g0_sha256": "5" * 64, "g1_sha256": "5" * 64,
            "g2_sha256": "5" * 64,
        }

        def read_stub(path):
            if path == check_p117.P116_G3:
                return old_g3
            if path == check_p117.G1:
                return {"pass": True}
            return None

        with patch.object(verdict, "read", side_effect=read_stub), \
             patch.object(verdict, "sha", side_effect=lambda path: artifacts.get(path)), \
             patch.object(verdict, "_handbook_from_commit", return_value=handbook), \
             patch.object(verdict, "_safe_p117_receipt", return_value=True):
            self.assertTrue(verdict._g3_ok(g3, g0, None))
            changed = copy.deepcopy(g3)
            changed["source_sha256"]["controls/check_p117.py"] = "0" * 64
            self.assertFalse(verdict._g3_ok(changed, g0, None))

    def test_implementation_record_binds_all_four_artifact_git_blobs(self):
        with tempfile.TemporaryDirectory(prefix="p117-impl-record-") as temp:
            root = Path(temp)
            files = [root / f"g{index}.json" for index in range(4)]
            for index, path in enumerate(files):
                path.write_bytes(f"artifact-{index}".encode())
            record = {"schema": "actinv-p117-implementation-1", "commit_sha": "a" * 40,
                      **{f"g{index}_sha256": hashlib.sha256(files[index].read_bytes()).hexdigest()
                         for index in range(4)}}
            names = ("G0", "G1", "G2", "G3")
            patches = [patch.object(check_p117, name, path) for name, path in zip(names, files)]
            with patch.object(verdict, "ROOT", root), patch.object(verdict, "_source_commit_matches", return_value=True):
                for item in patches:
                    item.start()
                try:
                    def read_git(_commit, relative):
                        return files[int(relative[1])].read_bytes()
                    with patch.object(check_p116, "_git_blob", side_effect=read_git):
                        self.assertTrue(verdict._implementation_ok(record, {}, {}, {}, {}))
                    for index in range(4):
                        def changed_git(_commit, relative, changed=index):
                            return b"altered" if int(relative[1]) == changed else files[int(relative[1])].read_bytes()
                        with patch.object(check_p116, "_git_blob", side_effect=changed_git):
                            self.assertFalse(verdict._implementation_ok(record, {}, {}, {}, {}))
                finally:
                    for item in reversed(patches):
                        item.stop()

    def test_fresh_receipt_rejects_bad_status_types_argv_and_timeout(self):
        base_resources = {
            "platform": "linux", "cgroup_path": "/user.slice/user-1000.slice/job.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/", "filesystem": "ext4"},
        }
        with tempfile.TemporaryDirectory(prefix="p117-receipt-") as temp:
            root = Path(temp)
            receipt_dir = root / "results/quality/p117"
            target = root / "target"
            receipt_dir.mkdir(parents=True)
            target.mkdir()
            raw_log = b"bounded gate output\n"
            (target / "p117-g0_seal.log").write_bytes(raw_log)
            (receipt_dir / "g0_seal.log").write_bytes(raw_log)
            receipt = {
                "schema": "actinv-roadmap-gate-receipt-1", "phase": "P117", "gate": "g0_seal",
                "argv": ["python3", "controls/check_p117.py", "--g0-only"], "cwd": ".",
                "timeout_s": 600.0, "status": "completed", "child_exit_code": 0,
                "log_path": "target/p117-g0_seal.log", "log_sha256": hashlib.sha256(raw_log).hexdigest(),
                "resources": base_resources, "error": None,
            }
            receipt_path = receipt_dir / "g0_seal.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with patch.object(check_p117, "ROOT", root), patch.object(verdict.check_p117, "ROOT", root):
                _loaded, entry = check_p117._safe_receipt("g0_seal")
                self.assertIsNotNone(entry)
                self.assertTrue(verdict._safe_p117_receipt("g0_seal", entry))
                for changes in (
                    {"child_exit_code": True},
                    {"argv": ["python3", "--x=undefined"]},
                    {"argv": []},
                    {"timeout_s": 601.0},
                    {"log_path": "target/elsewhere.log"},
                ):
                    changed = dict(receipt)
                    changed.update(changes)
                    receipt_path.write_text(json.dumps(changed), encoding="utf-8")
                    self.assertFalse(verdict._safe_p117_receipt("g0_seal", entry))


if __name__ == "__main__":
    unittest.main()
