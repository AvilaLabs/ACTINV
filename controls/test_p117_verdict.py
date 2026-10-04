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

    def test_actual_quality_producer_g3_shape_validates_and_nested_handbook_tampering_fails(self):
        old_gates = {f"old-gate-{index}": {"exit_code": 0} for index in range(32)}
        rust_bytes = b"old Rust source"
        rust_hash = hashlib.sha256(rust_bytes).hexdigest()
        old_rust = {f"crates/core/src/file-{index}.rs": rust_hash for index in range(100)}
        handbook_bytes = b"pinned handbook source"
        handbook_hash = hashlib.sha256(handbook_bytes).hexdigest()
        handbook = {"docs/guide/index.md": handbook_hash}
        release_hash = "2" * 64
        old_g3 = {"pass": True, "gates": old_gates, "release_build": {"binary_sha256": release_hash},
                  "workspace_tests": {"passed": 485, "failed": 0, "ignored": 2},
                  "production_rust_sha256": old_rust}
        source_map = {"controls/check_p117.py": "4" * 64}
        artifact_hashes = {check_p117.G0: "5" * 64, check_p117.G1: "6" * 64,
                           check_p117.G2: "7" * 64}
        old_science = {"schema": "immutable-p116-report"}
        g0 = {"pass": True, "control_sha256": source_map,
              "public_handbook_sha256": handbook}
        g1 = {"pass": True, "fresh_p117_report": old_science}
        g2 = {"pass": True, "g0_result_sha256": artifact_hashes[check_p117.G0],
              "g1_result_sha256": artifact_hashes[check_p117.G1]}
        adopted_initial = {name: {"name": name, "receipt_sha256": "8" * 64,
                                  "log_sha256": "9" * 64}
                           for name in check_p117.ADOPTED_INITIAL_GATES}
        evidence_hashes = {
            f"results/failures/p117_initial/file-{index}.json": "a" * 64
            for index in range(70)
        }
        initial_failure = {
            "checkpoint_commit": check_p117.INITIAL_CHECKPOINT, "verdict": "P117-FAIL",
            "verdict_exit_code": 1, "verdict_status": "completed",
            "adopted_gate_names": sorted(check_p117.ADOPTED_INITIAL_GATES),
            "rerun_gate_names": sorted(check_p117.RERUN_GATES),
            "adopted_initial_gates": adopted_initial,
        }
        repair = {
            "pass": True, "amendment_sha256": check_p117.AMENDMENT_SHA256,
            "amendment_registered": True, "discovery_sha256": check_p117.INITIAL_DISCOVERY_SHA256,
            "evidence_sha256": evidence_hashes, "initial_failure": initial_failure,
        }
        g0.update({
            "repair_rounds": 1, "repair_amendment_sha256": repair["amendment_sha256"],
            "repair_amendment_registered": True, "repair_discovery_sha256": repair["discovery_sha256"],
            "repair_evidence_sha256": evidence_hashes, "repair_evidence_matches": True,
            "initial_failure": initial_failure,
        })
        live_log = b"fresh rerun gate output\n"
        live_log_sha = hashlib.sha256(live_log).hexdigest()
        rerun_entries = {name: {"name": name, "log_sha256": live_log_sha}
                         for name in check_p117.RERUN_GATES}
        with tempfile.TemporaryDirectory(prefix="p117-producer-g3-") as temp:
            log_path = Path(temp) / "gate.log"
            log_path.write_bytes(live_log)
            handbook_file = ROOT / "docs/guide/index.md"

            def source_bytes(_commit, path):
                return rust_bytes if path in old_rust else handbook_bytes

            def source_hash(path):
                if path == check_p117.ACTINV:
                    return release_hash
                if path in artifact_hashes:
                    return artifact_hashes[path]
                if path == handbook_file:
                    return handbook_hash
                return None

            def read_p117(path):
                return {check_p117.P116_G3: old_g3, check_p117.G0: g0,
                        check_p117.G1: g1, check_p117.G2: g2,
                        check_p117.P116_G1: old_science}.get(path)

            def fake_safe_file(relative):
                if relative.startswith("target/p117-") or relative.startswith("results/quality/p117/"):
                    return log_path
                raise AssertionError(f"unexpected file read by producer: {relative}")

            with patch.object(check_p117, "_read", side_effect=read_p117), \
                 patch.object(check_p117, "_repair_evidence", return_value=repair), \
                 patch.object(check_p117, "_source_population_ok", return_value=(True, source_map, old_rust)), \
                 patch.object(check_p117, "_safe_receipt", side_effect=lambda name: (None, rerun_entries[name])), \
                 patch.object(check_p117, "_adopted_initial_entry_matches", return_value=True), \
                 patch.object(check_p117, "_safe_file", side_effect=fake_safe_file), \
                 patch.object(check_p117, "_persist", return_value=(True, b"")), \
                 patch.object(check_p117, "_sha", side_effect=source_hash), \
                 patch.object(check_p116, "_rust_paths_at_commit", return_value=set(old_rust)), \
                 patch.object(check_p116, "_git_blob", side_effect=source_bytes), \
                 patch.object(check_p117.check_p116_history, "verify", return_value={"pass": True}), \
                 patch.object(check_p117.check_p116_verdict, "_quality_ok", return_value=True), \
                 patch.object(check_p117.check_p112_verdict, "_handbook_paths", return_value=set(handbook)), \
                 patch("builtins.print"):
                # The P117 quality builder must emit its genuine nested shape;
                # the verdict then checks that object without an invented copy.
                code, produced = check_p117.quality()
            self.assertEqual(code, 0)
            self.assertNotIn("public_handbook_sha256", produced)
            self.assertEqual(produced["p116_adopted"]["public_handbook_sha256"], handbook)

            def verdict_read(path):
                return {check_p117.P116_G3: old_g3, check_p117.G1: g1}.get(path)

            with patch.object(verdict, "read", side_effect=verdict_read), \
                 patch.object(verdict, "sha", side_effect=lambda path: artifact_hashes.get(path)), \
                 patch.object(verdict, "_handbook_from_commit", return_value=handbook), \
                 patch.object(verdict, "_safe_p117_receipt", return_value=True), \
                 patch.object(check_p117, "_adopted_initial_entry_matches", return_value=True):
                self.assertTrue(verdict._g3_ok(produced, g0, None))
                changed = copy.deepcopy(produced)
                first_source = next(iter(changed["source_sha256"]))
                changed["source_sha256"][first_source] = "0" * 64
                self.assertFalse(verdict._g3_ok(changed, g0, None))
                changed = copy.deepcopy(produced)
                changed["p116_adopted"]["public_handbook_sha256"]["docs/guide/index.md"] = "0" * 64
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
