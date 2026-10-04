"""Source-only regressions for the read-only P111 terminal-failure verifier."""
from __future__ import annotations

import hashlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p111_history as history


class P111HistoryTests(unittest.TestCase):
    def _fixture(self, root: Path):
        contents = {name: f"retained:{index}".encode() for index, name in enumerate(history.ARTIFACT_HASHES)}
        hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
        log = b"historical P108 invocation failed with exit 2\n"
        log_hash = hashlib.sha256(log).hexdigest()
        record = {
            "schema": "actinv-p111-failure-implementation-1",
            "commit_sha": "c" * 40,
            "verdict": "P111-FAIL",
            "scientific_screen_evidence_obtained": True,
            "ci_qualification": "none; local failed checkpoint",
            "failed_gate": "historical_p108_replay",
            "failure_exit_code": 2,
            "failure_log_path": "results/failures/p111_initial/historical_p108_invocation_after_repair.log",
            "failure_log_sha256": log_hash,
            "artifact_sha256": hashes,
        }
        for relative, raw in contents.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        log_path = root / record["failure_log_path"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(log)
        return record, contents, log

    def test_failure_record_binds_all_artifacts_log_and_checkpoint_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record, contents, log = self._fixture(root)
            blobs = {**contents, history.FAILURE_LOG: log}
            with patch.object(history, "CHECKPOINT", "c" * 40), \
                 patch.object(history, "ARTIFACT_HASHES", record["artifact_sha256"]), \
                 patch.object(history, "FAILURE_LOG_SHA256", record["failure_log_sha256"]):
                verified = history._validate_failure_record(
                    record, root, blob_reader=lambda _commit, path: blobs[path])
                self.assertEqual(set(verified), set(contents))

                wrong_commit = dict(record, commit_sha="d" * 40)
                with self.assertRaises(ValueError):
                    history._validate_failure_record(wrong_commit, root,
                        blob_reader=lambda _commit, path: blobs[path])

                wrong_hash = json.loads(json.dumps(record))
                wrong_hash["artifact_sha256"][next(iter(contents))] = "0" * 64
                with self.assertRaises(ValueError):
                    history._validate_failure_record(wrong_hash, root,
                        blob_reader=lambda _commit, path: blobs[path])

                changed_blobs = dict(blobs)
                changed_blobs[next(iter(contents))] = b"changed Git blob"
                with self.assertRaises(ValueError):
                    history._validate_failure_record(record, root,
                        blob_reader=lambda _commit, path: changed_blobs[path])

                changed_log_blob = dict(blobs)
                changed_log_blob[history.FAILURE_LOG] = b"wrong failure log"
                with self.assertRaises(ValueError):
                    history._validate_failure_record(record, root,
                        blob_reader=lambda _commit, path: changed_log_blob[path])

                wrong_disposition = dict(record, verdict="P111-PASS")
                with self.assertRaises(ValueError):
                    history._validate_failure_record(wrong_disposition, root,
                        blob_reader=lambda _commit, path: blobs[path])

    def test_source_materializer_rejects_unsafe_paths_and_changed_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_digest = hashlib.sha256(b"expected").hexdigest()
            quality = {"production_rust_sha256": {"../escape.rs": source_digest}}
            with self.assertRaises(ValueError):
                history._source_hashes(quality)

            safe = {f"crates/example/src/file{index}.rs": source_digest for index in range(99)}
            quality = {"production_rust_sha256": safe}
            with patch.object(history, "EXPECTED_RUST_SOURCES", 99):
                with self.assertRaises(ValueError):
                    history._materialize_sources(
                        quality, root / "snapshot", commit="c" * 40, root=root,
                        blob_reader=lambda _commit, _path, **_kwargs: b"changed source")

    def test_quality_root_and_method_are_restored_even_after_exception(self):
        original_root = Path("checkout-root")
        snapshot = Path("historical-snapshot")
        calls = []
        fake = types.SimpleNamespace(ROOT=original_root)

        def original_quality(_quality, record=None):
            calls.append((fake.ROOT, record))
            raise RuntimeError("controlled exception")

        fake._quality = original_quality
        fake.derive = lambda: fake._quality({"fixture": True}, {"ignored": True})
        with patch.object(history.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "controlled exception"):
                history._derive_with_snapshot(snapshot)
        self.assertEqual(calls, [(snapshot, None)])
        self.assertIs(fake._quality, original_quality)
        self.assertEqual(fake.ROOT, original_root)

    def test_failure_disposition_requires_exact_phase_order_and_recorded_exit(self):
        derived = {"gates": {"G0": "PASS", "G1": "PASS", "G2": "PASS",
                             "G3_local": "FAIL", "G3_CI": "PENDING"}}
        quality = {
            "repair_rounds": 1,
            "gates": {
                "historical_p108_replay": {"exit_code": 2, "log_sha256": history.FAILURE_LOG_SHA256},
                "historical_p109_replay": {"exit_code": None, "status": "not_run_after_terminal_failure"},
                "historical_p110_replay": {"exit_code": None, "status": "not_run_after_terminal_failure"},
            },
            "terminal_failure_after_repair": {
                "exit_code": 2, "retained_log_sha256": history.FAILURE_LOG_SHA256,
                "reason": "coordinator invoked nonexistent controls/check_p108_history.py after the registered repair",
            },
        }
        self.assertTrue(history._failure_disposition(quality, derived))
        changed = json.loads(json.dumps(quality))
        changed["gates"]["historical_p108_replay"]["exit_code"] = 0
        self.assertFalse(history._failure_disposition(changed, derived))
        changed = json.loads(json.dumps(quality))
        changed["gates"]["historical_p109_replay"]["status"] = "passed"
        self.assertFalse(history._failure_disposition(changed, derived))


if __name__ == "__main__":
    unittest.main()
