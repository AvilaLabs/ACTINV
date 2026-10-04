"""Source-only regressions for the read-only P109 historical FAIL verifier."""
from __future__ import annotations

import hashlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p109_history as history


class P109HistoryTests(unittest.TestCase):
    def _record_fixture(self, root: Path):
        contents = {
            "results/g0_p109_composition_solve.json": b"g0",
            "results/g1_p109_composition_solve.json": b"g1",
            "results/g3_p109_quality.json": b"g3",
            "results/p109_verdict.json": b"verdict",
        }
        hashes = {key: hashlib.sha256(value).hexdigest() for key, value in contents.items()}
        record = {"schema": "actinv-p109-failure-implementation-1",
                  "commit_sha": "c" * 40, "artifact_sha256": hashes,
                  "verdict": "P109-FAIL", "native_solver_evidence_obtained": False,
                  "ci_qualification": "none; local failed checkpoint"}
        for relative, content in contents.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        record_path = root / "results/p109_failure_commit.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        blobs = dict(contents)
        return record, hashes, blobs

    def test_failure_record_binds_current_and_git_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record, hashes, blobs = self._record_fixture(root)
            with (patch.object(history, "CHECKPOINT", "c" * 40),
                  patch.object(history, "ARTIFACT_HASHES", hashes),
                  patch.object(history, "_registered", return_value=True)):
                verified = history._validate_failure_record(record, root,
                    blob_reader=lambda _commit, path: blobs[path])
                self.assertEqual(set(verified), set(hashes))
                wrong_commit = dict(record, commit_sha="d" * 40)
                with self.assertRaises(ValueError):
                    history._validate_failure_record(wrong_commit, root,
                        blob_reader=lambda _commit, path: blobs[path])
                wrong_hash = json.loads(json.dumps(record))
                wrong_hash["artifact_sha256"][next(iter(hashes))] = "0" * 64
                with self.assertRaises(ValueError):
                    history._validate_failure_record(wrong_hash, root,
                        blob_reader=lambda _commit, path: blobs[path])
                changed_blobs = dict(blobs)
                changed_blobs[next(iter(hashes))] = b"changed git blob"
                with self.assertRaises(ValueError):
                    history._validate_failure_record(record, root,
                        blob_reader=lambda _commit, path: changed_blobs[path])

    def test_source_materializer_rejects_unsafe_paths_and_changed_blobs(self):
        digest = hashlib.sha256(b"source").hexdigest()
        unsafe = {"production_rust_sha256": {"../escape.rs": digest}}
        with self.assertRaises(ValueError):
            history._source_hashes(unsafe)
        hashes = {f"crates/example/src/file{i}.rs": digest for i in range(97)}
        quality = {"production_rust_sha256": hashes}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(history, "EXPECTED_RUST_SOURCES", len(hashes)):
                with self.assertRaises(ValueError):
                    history._materialize_sources(
                        quality, Path(temporary), commit="c" * 40,
                        blob_reader=lambda _commit, _path, **_kwargs: b"changed")

    def test_quality_wrapper_restores_module_state_even_on_exception(self):
        original_root = Path("original-root")
        snapshot = Path("historical-snapshot")
        calls = []
        fake = types.SimpleNamespace(ROOT=original_root)

        def original_quality(_quality, *, record):
            calls.append((fake.ROOT, record))
            raise RuntimeError("controlled test failure")

        fake._quality = original_quality
        fake.derive = lambda: fake._quality({"fixture": True}, record=None)
        with patch.object(history.importlib, "import_module", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "controlled test failure"):
                history._derive_with_snapshot(snapshot)
        self.assertEqual(calls, [(snapshot, None)])
        self.assertIs(fake._quality, original_quality)
        self.assertEqual(fake.ROOT, original_root)


if __name__ == "__main__":
    unittest.main()
