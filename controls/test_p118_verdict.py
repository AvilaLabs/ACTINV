#!/usr/bin/env python3
"""Source-only P118 portable verdict and receipt-binding regressions."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p116
import check_p117
import check_p118
import check_p118_verdict as verdict


def closure_fixture():
    gates = {name: "PASS" for name in (
        "protocol", "predecessor_history", "G0", "G1", "G2", "G3_local",
        "source_commit", "CI_evidence_consistent", "G3_CI")}
    terminal = {"schema": "actinv-p118-verdict-1", "phase": "P118",
                "verdict": "P118-PASS", "gates": gates, "repair_rounds": 1,
                "implementation_record_sha256": "1" * 64,
                "ci_evidence_sha256": "2" * 64, "implementation_commit": "a" * 40,
                "evidence_sha256": {f"results/g{index}_p118.json": "3" * 64
                                    for index in range(4)}}
    local = {**terminal, "verdict": "P118-LOCAL-PASS",
             "gates": {**gates, "G3_CI": "PENDING"},
             "implementation_record_sha256": None, "ci_evidence_sha256": None,
             "implementation_commit": None}
    return local, terminal


class P118VerdictTests(unittest.TestCase):
    def test_quality_producer_emits_nested_handbook_and_selected_adoptions(self):
        prior = check_p118._read(check_p118.P116_G3)
        p117_initial = check_p117._read(
            check_p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
        self.assertIsInstance(prior, dict)
        self.assertIsInstance(p117_initial, dict)
        old_gates = prior["gates"]
        all_p117 = p117_initial["fresh_gates"]
        expected_adopted = {name: all_p117[name] for name in check_p118.P117_ADOPTED_GATES}
        handbook = {"docs/guide/index.md": hashlib.sha256(b"handbook").hexdigest()}
        rust = {f"crates/actinv-core/src/{i}.rs": "a" * 64 for i in range(100)}
        initial_failure = {"pass": True, "actual_outer_exit_code": 1,
                           "preserved_files_sha256": {"x": "e" * 64}}
        history = {"historical_p116_verified": True, "pass": True}
        terminal_archive = {"results/failures/p117_terminal/discovery.json": "f" * 64}
        initial_archive = {"results/failures/p117_terminal/prior_initial_archive/discovery.json": "1" * 64}
        evidence = {
            "pass": True, "history": history, "initial_failure": initial_failure,
            "amendment_registered": True, "amendment_sha256": check_p118.AMENDMENT_SHA256,
            "terminal_archive_files_sha256": terminal_archive,
            "initial_archive_files_sha256": initial_archive,
        }
        g0 = {
            "pass": True, "control_sha256": {"controls/check_p118.py": "b" * 64},
            "p117_history": history,
            "p117_history_evidence": {"terminal_archive_files_sha256": terminal_archive,
                                      "initial_archive_files_sha256": initial_archive},
            "initial_failure": initial_failure,
            "initial_failure_sha256": initial_failure["preserved_files_sha256"],
            "inherited_rust_sha256": rust,
        }
        fresh_report = {"request_count": 35, "component_target_count": 138,
                        "independent_comparison_count": 138, "repeat_byte_identical": True}
        g1 = {"pass": True, "fresh_p118_report": fresh_report}
        g2 = {"pass": True, "g0_result_sha256": "3" * 64,
              "g1_result_sha256": "4" * 64}
        binary = prior["release_build"]["binary_sha256"]
        log_bytes = b"gate passed\n"
        log_sha = hashlib.sha256(log_bytes).hexdigest()
        resources = {
            "platform": "linux", "cgroup_path": "/user.slice/user-1000.slice/job.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1",
                            "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/",
                       "filesystem": "ext4"},
        }
        receipts = {}
        descriptors = {}
        for name in check_p118.FRESH_GATES:
            receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P118",
                       "gate": name, "status": "completed", "child_exit_code": 0,
                       "cwd": ".", "timeout_s": 600, "elapsed_s": 1.0,
                       "log_path": f"target/p118-{name}.log", "log_sha256": log_sha,
                       "argv": ["true"], "resources": resources, "error": None}
            receipts[name] = receipt
            descriptors[name] = {
                "name": name, "receipt_path": f"results/quality/p118/{name}.json",
                "receipt_sha256": hashlib.sha256(check_p118._canonical(receipt)).hexdigest(),
                "log_path": f"results/quality/p118/{name}.log", "log_sha256": log_sha,
                "argv": receipt["argv"], "exit_code": 0, "resources": resources}
        old_artifact_hashes = {check_p118.G0: "3" * 64, check_p118.G1: "4" * 64,
                               check_p118.G2: "5" * 64}
        original_sha = check_p118._sha

        def fake_sha(path):
            if path == check_p118.ACTINV:
                return binary
            if path in old_artifact_hashes:
                return old_artifact_hashes[path]
            if path == ROOT / "docs/guide/index.md":
                return handbook["docs/guide/index.md"]
            return original_sha(path)

        def fake_safe_file(relative):
            if relative.endswith(".log") and ("p118/" in relative or relative.startswith("target/p118-")):
                return SimpleNamespace(read_bytes=lambda: log_bytes)
            return check_p118.ROOT / relative

        reads = {check_p118.P116_G3: prior, check_p118.P116_G0: {"pass": True},
                 check_p118.G0: g0, check_p118.G1: g1, check_p118.G2: g2,
                 check_p118.P116_G1: fresh_report}
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(check_p118, "_repair_evidence", return_value=evidence), \
             patch.object(check_p118, "_read", side_effect=lambda path: reads.get(path)), \
             patch.object(check_p118.p117, "_read", return_value=p117_initial), \
             patch.object(check_p118, "_adopted_initial_entry_matches", return_value=True), \
             patch.object(check_p118, "_safe_receipt",
                          side_effect=lambda name: (receipts[name], descriptors[name])), \
             patch.object(check_p118, "_safe_file", side_effect=fake_safe_file), \
             patch.object(check_p118, "_source_population_ok", return_value=(True, g0["control_sha256"], rust)), \
             patch.object(check_p118.check_p116_verdict, "_quality_ok", return_value=True), \
             patch.object(check_p118.check_p112_verdict, "_handbook_paths",
                          side_effect=lambda commit=None: set(handbook)), \
             patch.object(check_p118.p116, "_git_blob", return_value=b"handbook"), \
             patch.object(check_p118, "_handbook_snapshot", return_value=(handbook, True)), \
             patch.object(check_p118, "_sha", side_effect=fake_sha), \
             patch.object(check_p118, "_persist", return_value=(True, b"")):
            code, produced = check_p118.quality()

        self.assertEqual(code, 0)
        self.assertTrue(produced["pass"])
        self.assertEqual(set(produced["p117_adopted_initial_gates"]), check_p118.P117_ADOPTED_GATES)
        self.assertEqual(set(produced["p116_adopted"]["public_handbook_sha256"]), set(handbook))
        self.assertEqual(produced["public_handbook_sha256"], handbook)
        with patch.object(check_p118, "_safe_receipt",
                          side_effect=lambda name: (receipts[name], descriptors[name])), \
             patch.object(check_p117, "_read", return_value=p117_initial), \
             patch.object(check_p118, "_adopted_initial_entry_matches", return_value=True), \
             patch.object(verdict, "read", side_effect=lambda path: prior if path == check_p118.P116_G3 else None), \
             patch.object(check_p118, "_handbook_snapshot", return_value=(handbook, True)), \
             patch.object(verdict, "sha", side_effect=lambda path: old_artifact_hashes.get(path)), \
             patch.object(check_p118, "_sha", side_effect=fake_sha):
            self.assertTrue(verdict._g3_ok(produced, g0))
            changed = copy.deepcopy(produced)
            changed["p116_adopted"]["public_handbook_sha256"]["docs/guide/index.md"] = "0" * 64
            self.assertFalse(verdict._g3_ok(changed, g0))
            changed = copy.deepcopy(produced)
            changed["public_handbook_sha256"] = {"docs/guide/index.md": "0" * 64}
            self.assertFalse(verdict._g3_ok(changed, g0))
            changed = copy.deepcopy(produced)
            changed["p117_adopted_initial_gates"][sorted(expected_adopted)[0]]["exit_code"] = 1
            self.assertFalse(verdict._g3_ok(changed, g0))
            changed = copy.deepcopy(produced)
            changed["source_sha256"]["controls/check_p118.py"] = "0" * 64
            self.assertFalse(verdict._g3_ok(changed, g0))
            changed = copy.deepcopy(produced)
            changed["initial_failure"]["actual_outer_exit_code"] = True
            self.assertFalse(verdict._g3_ok(changed, g0))
            changed = copy.deepcopy(produced)
            changed["fresh_gates"][sorted(check_p118.FRESH_GATES)[0]]["exit_code"] = False
            self.assertFalse(verdict._g3_ok(changed, g0))
            missing = copy.deepcopy(produced)
            missing_name = sorted(check_p118.FRESH_GATES)[0]
            missing["fresh_gates"][missing_name] = None
            with patch.object(check_p118, "_safe_receipt", return_value=(None, None)):
                self.assertFalse(verdict._g3_ok(missing, g0))

    def test_resource_snapshot_is_strict_and_disk_backed(self):
        resources = {
            "platform": "linux", "cgroup_path": "/user.slice/user-1000.slice/job.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/", "filesystem": "ext4"},
        }
        self.assertTrue(check_p118._archive_resource_ok(resources))
        for change in (
            {"platform": "darwin"},
            {"cgroup_limits": {**resources["cgroup_limits"], "memory.max": "1"}},
            {"environment": {**resources["environment"], "RAYON_NUM_THREADS": "8"}},
            {"tmpdir": {**resources["tmpdir"], "filesystem": "tmpfs"}},
            {"tmpdir": {**resources["tmpdir"], "mount_point": ""}},
            {"cgroup_path": "/user.slice/../escape.scope"},
        ):
            changed = copy.deepcopy(resources)
            changed.update(change)
            self.assertFalse(check_p118._archive_resource_ok(changed))

    def test_implementation_sha_must_bind_all_four_artifact_blobs(self):
        with tempfile.TemporaryDirectory(prefix="p118-impl-record-") as temp:
            root = Path(temp)
            artifacts = [root / f"g{index}.json" for index in range(4)]
            for index, path in enumerate(artifacts):
                path.write_bytes(f"p118-artifact-{index}".encode())
            record = {"schema": "actinv-p118-implementation-1", "commit_sha": "a" * 40,
                      **{f"g{index}_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                         for index, path in enumerate(artifacts)}}
            names = ("G0", "G1", "G2", "G3")
            patches = [patch.object(check_p118, name, path) for name, path in zip(names, artifacts)]
            with patch.object(verdict, "ROOT", root), patch.object(verdict, "_source_commit_ok", return_value=True):
                for item in patches:
                    item.start()
                try:
                    def blob(_commit, rel):
                        return artifacts[int(rel[1])].read_bytes()
                    with patch.object(check_p116, "_git_blob", side_effect=blob):
                        self.assertTrue(verdict._implementation_ok(record, {}, {}, {}, {}))
                    for index in range(4):
                        def changed_blob(_commit, rel, changed=index):
                            return b"mutated" if int(rel[1]) == changed else artifacts[int(rel[1])].read_bytes()
                        with patch.object(check_p116, "_git_blob", side_effect=changed_blob):
                            self.assertFalse(verdict._implementation_ok(record, {}, {}, {}, {}))
                finally:
                    for item in reversed(patches):
                        item.stop()

    def test_g0_seal_rejects_missing_source_and_changed_authority_before_campaign(self):
        source_map = {"controls/p118.py": "a" * 64}
        rust = {f"crates/core/{i}.rs": "b" * 64 for i in range(100)}
        handbook = {"docs/guide/index.md": "c" * 64}
        expected = {"schema": "actinv-p118-twin-waste-g0-1", "phase": "P118",
                    "protocol_sha256": check_p118.PROTOCOL_SHA256, "pass": True,
                    "protocol_registered": True, "seed_authorities_match": True,
                    "historical_p116_verified": True, "historical_p117_verified": True,
                    "predecessor_p117_fail_preserved": True, "initial_failure_matches": True,
                    "repair_rounds": 1, "amendment_sha256": check_p118.AMENDMENT_SHA256,
                    "amendment_registered": True, "control_sha256": source_map,
                    "inherited_rust_sha256": rust, "source_population_matches_p116_commit": True,
                    "public_handbook_sha256": handbook, "public_handbook_matches_p116": True}
        sealed = copy.deepcopy(expected)
        with patch.object(check_p118, "_read", return_value=sealed), \
             patch.object(check_p118, "_control_hashes", return_value=source_map), \
             patch.object(check_p116, "_current_rust_source_hashes", return_value=rust), \
             patch.object(check_p118, "_handbook_snapshot", return_value=(handbook, True)), \
             patch.object(check_p118, "_g0_base", return_value=expected):
            self.assertTrue(check_p118._sealed_g0_present(expected))
            sealed["seed_authorities_match"] = False
            self.assertFalse(check_p118._sealed_g0_present(expected))
            sealed.clear()
            sealed.update(expected)
            with tempfile.TemporaryDirectory(prefix="p118-g1-mutated-seal-") as temp, \
                 patch.object(check_p118, "G1", Path(temp) / "g1.json"), \
                 patch.object(check_p118, "_campaign", side_effect=AssertionError("native launch")):
                # Current source evidence omits one frozen path; the altered G0 cannot authorize a run.
                with patch.object(check_p118, "_control_hashes", return_value={}):
                    code, report = check_p118.g1(quiet=True)
                self.assertEqual(code, 1)
                self.assertFalse(report["pass"])

    def test_exact_science_wrapper_is_required_and_complete(self):
        old = check_p118._read(check_p118.P116_G1)
        report = check_p118._g1_report(old, True)
        with tempfile.TemporaryDirectory(prefix="p118-g1-verdict-") as temp:
            path = Path(temp) / "g1.json"
            path.write_bytes(check_p118._canonical(report))
            with patch.object(check_p118, "G1", path):
                self.assertTrue(verdict._g1_ok(report))
                changed = copy.deepcopy(report)
                changed["fresh_p118_report"]["request_count"] = 34
                path.write_bytes(check_p118._canonical(changed))
                self.assertFalse(verdict._g1_ok(changed))

    def test_ci_requires_six_unique_green_workflows_on_exact_commit(self):
        commit = "a" * 40
        rows = [{"workflowName": name, "headSha": commit, "status": "completed",
                 "conclusion": "success", "databaseId": index + 1,
                 "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{index + 1}"}
                for index, name in enumerate(sorted(verdict.WORKFLOWS))]
        record = {"commit_sha": commit}
        self.assertEqual(verdict._ci_state(True, record, rows, True), (True, True))
        for mutate in (
            lambda v: v[0].update(conclusion="failure"),
            lambda v: v[0].update(headSha="b" * 40),
            lambda v: v[0].update(databaseId=True),
            lambda v: v[0].update(workflowName=[]),
        ):
            changed = copy.deepcopy(rows)
            mutate(changed)
            self.assertEqual(verdict._ci_state(True, record, changed, True), (False, False))
        self.assertEqual(verdict._ci_state(True, None, None, False), (True, False))

    def test_writer_allows_only_exact_local_to_green_ci_closure(self):
        prior, result = closure_fixture()
        self.assertTrue(verdict._write_transition_allowed(prior, result))
        for mutate in (
            lambda value: value.update(repair_rounds=True),
            lambda value: value["gates"].update(G1="FAIL"),
            lambda value: value.update(implementation_record_sha256="bad"),
        ):
            changed = copy.deepcopy(prior)
            mutate(changed)
            self.assertFalse(verdict._write_transition_allowed(changed, result))
        failed_prior = copy.deepcopy(prior)
        failed_prior["verdict"] = "P118-FAIL"
        self.assertFalse(verdict._write_transition_allowed(failed_prior, result))
        self.assertFalse(verdict._write_transition_allowed({}, result))

    def test_writer_persists_exact_closure_and_refuses_failed_or_dangling_destination(self):
        local, terminal = closure_fixture()
        with tempfile.TemporaryDirectory(prefix="p118-verdict-writer-") as temp:
            root = Path(temp)
            path = root / "results/p118_verdict.json"
            path.parent.mkdir()
            path.write_bytes(verdict._canonical(local))
            with patch.object(verdict, "ROOT", root), \
                 patch.object(check_p118, "ROOT", root), \
                 patch.object(verdict, "VERDICT", path), \
                 patch.object(verdict, "derive", return_value=terminal), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verdict.main(["--write"]), 0)
                self.assertEqual(path.read_bytes(), verdict._canonical(terminal))
                self.assertEqual(verdict.main([]), 0)
                failed = {**local, "verdict": "P118-FAIL"}
                path.write_bytes(verdict._canonical(failed))
                retained = path.read_bytes()
                self.assertEqual(verdict.main(["--write"]), 1)
                self.assertEqual(path.read_bytes(), retained)
                path.unlink()
                path.symlink_to(root / "missing.json")
                self.assertEqual(verdict.main(["--write"]), 1)
                self.assertTrue(path.is_symlink())

    def test_fresh_receipt_requires_valid_exit_elapsed_and_durable_log(self):
        resources = {
            "platform": "linux", "cgroup_path": "/user.slice/job.scope",
            "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                              "pids.max": "128", "cpu.max": "200000 100000"},
            "environment": {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1",
                            "RAYON_NUM_THREADS": "2"},
            "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/",
                       "filesystem": "ext4"},
        }
        name, log = "g0_seal", b"gate complete\n"
        receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P118",
                   "gate": name, "status": "completed", "child_exit_code": 0,
                   "cwd": ".", "timeout_s": 600, "elapsed_s": 1.0,
                   "log_path": "target/p118-g0_seal.log",
                   "log_sha256": hashlib.sha256(log).hexdigest(),
                   "argv": ["python3", "controls/check_p118.py", "--g0-only"],
                   "resources": resources, "error": None}
        with tempfile.TemporaryDirectory(prefix="p118-receipt-verdict-") as temp:
            root = Path(temp)
            path = root / "results/quality/p118/g0_seal.json"
            path.parent.mkdir(parents=True)
            (path.parent / "g0_seal.log").write_bytes(log)
            path.write_bytes(check_p118._canonical(receipt))
            with patch.object(check_p118, "ROOT", root):
                loaded, descriptor = check_p118._safe_receipt(name)
                self.assertEqual(loaded, receipt)
                self.assertIsInstance(descriptor, dict)
                for mutate in (
                    lambda value: value.update(child_exit_code=False),
                    lambda value: value.update(child_exit_code=1),
                    lambda value: value.update(status="child_failed"),
                    lambda value: value.update(elapsed_s=float("nan")),
                    lambda value: value.update(elapsed_s=616),
                    lambda value: value.update(argv=["undefined"]),
                    lambda value: value["resources"]["tmpdir"].update(mount_point=""),
                ):
                    changed = copy.deepcopy(receipt)
                    mutate(changed)
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    self.assertIsNone(check_p118._safe_receipt(name)[1])
                path.write_bytes(check_p118._canonical(receipt))
                (path.parent / "g0_seal.log").write_bytes(b"changed")
                self.assertIsNone(check_p118._safe_receipt(name)[1])


if __name__ == "__main__":
    unittest.main()
