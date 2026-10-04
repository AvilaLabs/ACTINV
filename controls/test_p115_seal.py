"""Source-only P115 G0 seal and immutable predecessor regressions."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p115 as controls


class P115SealTests(unittest.TestCase):
    def test_g0_binds_fixture_p113_and_p114_history_and_checkpoint(self):
        report = controls._g0_base()
        self.assertTrue(report["pass"], report)
        self.assertEqual(report["phase"], "P115")
        self.assertEqual(report["repair_rounds"], 1)
        self.assertEqual(report["repair_amendment_sha256"], controls.P115_AMENDMENT_A_SHA256)
        self.assertEqual(report["repair_discovery_sha256"], controls.P115_DISCOVERY_SHA256)
        self.assertTrue(report["repair_evidence_matches"])
        self.assertEqual(len(report["repair_source_sha256"]), controls.P115_SOURCE_FILE_COUNT)
        self.assertEqual(len(report["repair_evidence_sha256"]), controls.P115_RETAINED_FILE_COUNT)
        self.assertEqual(report["request_count"], 35)
        self.assertEqual(report["component_target_count"], 138)
        self.assertTrue(report["historical_p113_verified"])
        self.assertTrue(report["historical_p114_verified"])
        self.assertEqual(report["p113_inherited_g0_sha256"], controls.INITIAL_G0_SHA256)
        self.assertTrue(report["p113_failure_record_matches"])
        self.assertTrue(report["p114_failure_record_matches"])
        self.assertEqual(len(report["p114_initial_archive_sha256"]), 176)
        self.assertEqual(len(report["p114_final_archive_sha256"]), 196)
        self.assertTrue(report["p113_initial_archive_matches"])
        self.assertEqual(report["inherited_rust_checkpoint"], controls.P114_CHECKPOINT)
        self.assertEqual(report["inherited_rust_source_count"], 100)
        self.assertTrue(report["inherited_rust_population_matches"])
        self.assertEqual(set(report["control_sha256"]), set(controls.CONTROL_FILES))

    def test_round_one_archive_verifier_rejects_missing_changed_extra_and_git_drift(self):
        with tempfile.TemporaryDirectory(prefix="p115-repair-archive-") as temp:
            root = Path(temp)
            prefix = "results/failures/p115_initial/"
            archive = root / "results/failures/p115_initial"
            receipt_rel = prefix + "results/quality/p115/p115_verdict_regressions.json"
            log_rel = prefix + "target/p115-p115_verdict_regressions.log"
            extra_rel = prefix + "writer.log"
            receipt = {"schema": "actinv-roadmap-gate-receipt-1", "phase": "P115",
                "gate": "p115_verdict_regressions", "argv": ["python3", "controls/test_p115_verdict.py"],
                "cwd": ".", "status": "child_failed", "child_exit_code": 1,
                "log_path": "target/p115-p115_verdict_regressions.log",
                "log_sha256": "pending"}
            receipt_path = root / receipt_rel
            log_path = root / log_rel
            extra_path = root / extra_rel
            receipt_path.parent.mkdir(parents=True)
            log_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
            log_path.write_bytes(b"actual failed test output\n")
            extra_path.write_bytes(b"writer record\n")
            log_hash = controls._sha(log_path)
            receipt["log_sha256"] = log_hash
            receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
            source_files = {"crates/mock/src/lib.rs": b"rust source", "controls/mock.py": b"control source"}
            retained = {rel: controls._sha(root / rel) for rel in (receipt_rel, log_rel, extra_rel)}
            gate_codes = {"p115_verdict_regressions": 1, "other_success": 0}
            discovery = {"schema": "actinv-p115-initial-failure-1", "phase": "P115",
                "gate": "p115_verdict_regressions", "exit_code": 1, "failed_gate_count": 1,
                "source_file_count": 2, "rust_source_file_count": 1, "retained_file_count": 3,
                "observed_test_count": 8, "observed_error_count": 1, "g0_sealed": False,
                "g1_executed": False, "g2_executed": False,
                "source_sha256": {name: controls._sha_bytes(raw) for name, raw in source_files.items()},
                "files_sha256": retained, "observed_gate_exit_codes": gate_codes}
            discovery_path = archive / "discovery.json"
            discovery_path.parent.mkdir(parents=True, exist_ok=True)
            discovery_path.write_text(json.dumps(discovery, sort_keys=True), encoding="utf-8")
            discovery_hash = controls._sha(discovery_path)
            git_map = {**source_files,
                       **{rel: (root / rel).read_bytes() for rel in retained}}

            def git_blob(_commit, path):
                return git_map[path]

            patches = (
                patch.object(controls, "ROOT", root),
                patch.object(controls, "P115_DISCOVERY", discovery_path),
                patch.object(controls, "P115_DISCOVERY_SHA256", discovery_hash),
                patch.object(controls, "P115_ARCHIVE_PREFIX", prefix),
                patch.object(controls, "P115_FAILURE_LOG_SHA256", log_hash),
                patch.object(controls, "P115_SOURCE_FILE_COUNT", 2),
                patch.object(controls, "P115_RUST_SOURCE_COUNT", 1),
                patch.object(controls, "P115_RETAINED_FILE_COUNT", 3),
                patch.object(controls, "P115_OBSERVED_GATE_COUNT", 2),
                patch.object(controls, "P115_SUCCESSFUL_GATE_COUNT", 1),
                patch.object(controls, "_git_blob", side_effect=git_blob),
            )
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                self.assertTrue(controls._p115_repair_evidence()[2])
                unexpected_path = archive / "unexpected.log"
                unexpected_path.write_bytes(b"unlisted physical file\n")
                self.assertFalse(controls._p115_repair_evidence()[2])
                unexpected_path.unlink()
                extra_path.unlink()
                self.assertFalse(controls._p115_repair_evidence()[2])
                extra_path.write_bytes(b"writer record\n")
                log_path.write_bytes(b"changed bytes\n")
                self.assertFalse(controls._p115_repair_evidence()[2])
                log_path.write_bytes(b"actual failed test output\n")
                git_map["controls/mock.py"] = b"changed checkpoint blob"
                self.assertFalse(controls._p115_repair_evidence()[2])

    def test_control_hash_seal_rejects_missing_traversal_and_symlink_entries(self):
        with tempfile.TemporaryDirectory(prefix="p115-control-seal-") as temp:
            root = Path(temp) / "repo"
            (root / "controls").mkdir(parents=True)
            (root / "data").mkdir()
            paths = ("controls/check.py", "controls/oracle.py", "data/pack.json")
            hashes = {}
            for relative in paths:
                (root / relative).write_bytes(relative.encode())
                hashes[relative] = controls._sha(root / relative)
            with patch.object(controls, "ROOT", root), patch.object(controls, "CONTROL_FILES", paths):
                self.assertTrue(controls._safe_control_hashes(hashes))
                self.assertFalse(controls._safe_control_hashes({
                    key: value for key, value in hashes.items() if key != paths[-1]}))
                self.assertFalse(controls._safe_control_hashes({**hashes, "../outside": "0" * 64}))
                outside = Path(temp) / "outside.py"
                outside.write_bytes(b"outside")
                (root / "controls/escape.py").symlink_to(outside)
                with patch.object(controls, "CONTROL_FILES", (*paths, "controls/escape.py")):
                    self.assertFalse(controls._safe_control_hashes({
                        **hashes, "controls/escape.py": controls._sha(outside)}))

    def test_g0_seal_write_read_replay_and_prior_history_mutation(self):
        with tempfile.TemporaryDirectory(prefix="p115-g0-") as temp:
            path = Path(temp) / "g0.json"
            with patch.object(controls, "G0", path):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(controls.g0(seal=True), 0)
                    persisted = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(controls.g0(no_write=True), 0)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), persisted)
                    changed = copy.deepcopy(persisted)
                    changed["historical_p114_verified"] = False
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)
                    changed = copy.deepcopy(persisted)
                    changed["p113_failure_verdict_sha256"] = "0" * 64
                    path.write_bytes(controls._json_bytes(changed))
                    self.assertNotEqual(controls.g0(no_write=True), 0)


if __name__ == "__main__":
    unittest.main()
