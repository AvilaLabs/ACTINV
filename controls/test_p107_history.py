"""P108 regressions for the historical P107 source and evidence bindings."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import check_p107_history as history


class HistoricalP107Tests(unittest.TestCase):
    def setUp(self):
        self.commit = history.EXPECTED_COMMIT
        self.old_source = b"historical Rust source\n"
        self.source_path = "crates/actinv-core/src/lib.rs"
        self.source_hash = hashlib.sha256(self.old_source).hexdigest()
        self.quality = {"production_rust_sha256": {self.source_path: self.source_hash}}
        self.quality_bytes = json.dumps(self.quality).encode()
        self.g0_bytes, self.g3_bytes = b"g0 seal", b"g3 quality"
        self.ci = [{"workflowName": name, "headSha": self.commit,
                    "status": "completed", "conclusion": "success"}
                   for name in sorted(history.REQUIRED_WORKFLOWS)]
        self.record = {"schema": "actinv-p107-implementation-1", "commit_sha": self.commit,
                       "g0_sha256": hashlib.sha256(self.g0_bytes).hexdigest(),
                       "g3_sha256": hashlib.sha256(self.g3_bytes).hexdigest()}

    def test_current_source_changes_are_ignored_when_historical_blob_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / self.source_path
            current.parent.mkdir(parents=True)
            current.write_bytes(b"different current source")
            directory = root / "temporary"
            directory.mkdir()
            snapshot = history._materialize_history(
                self.commit, {self.source_path: self.source_hash}, directory,
                blob_reader=lambda _commit, _path: self.old_source)
            self.assertEqual((snapshot / self.source_path).read_bytes(), self.old_source)
            self.assertNotEqual(current.read_bytes(), (snapshot / self.source_path).read_bytes())

    def test_wrong_commit_or_historical_blob_hash_is_refused(self):
        bad_commit = dict(self.record, commit_sha="0" * 40)
        with patch.object(history, "QUALITY", Path("unused")):
            with self.assertRaises(ValueError):
                history._validate_record(bad_commit, self.g0_bytes, self.g3_bytes, self.ci)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(ValueError):
                history._materialize_history(
                    self.commit, {self.source_path: "0" * 64}, directory,
                    blob_reader=lambda _commit, _path: self.old_source)

    def test_changed_g0_g3_or_ci_identity_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            quality_path = Path(temp) / "quality.json"
            quality_path.write_bytes(self.quality_bytes)
            with patch.object(history, "QUALITY", quality_path):
                with self.assertRaisesRegex(ValueError, "G0"):
                    history._validate_record(self.record, b"changed G0", self.g3_bytes, self.ci)
                with self.assertRaisesRegex(ValueError, "G3"):
                    history._validate_record(self.record, self.g0_bytes, b"changed G3", self.ci)
                changed_ci = [dict(run, headSha="f" * 40) for run in self.ci]
                with self.assertRaisesRegex(ValueError, "implementation commit"):
                    history._validate_record(self.record, self.g0_bytes, self.g3_bytes, changed_ci)

    def test_historical_root_is_scoped_only_to_quality_and_restored(self):
        current_root = Path("current-evidence-root")
        snapshot = Path("historical-source-root")
        seen = []
        module = SimpleNamespace(ROOT=current_root)

        def quality(_value):
            seen.append(("quality", module.ROOT))
            return True, {"pass": True}

        def derive():
            seen.append(("derive-before", module.ROOT))
            module._quality_ok({})
            seen.append(("derive-after", module.ROOT))
            return {"pass": True}

        module._quality_ok = quality
        module.derive = derive
        with patch.object(history.importlib, "import_module", return_value=module):
            result = history._derive_with_historical_sources(snapshot)
        self.assertEqual(result, {"pass": True})
        self.assertEqual(seen, [("derive-before", current_root), ("quality", snapshot),
                                ("derive-after", current_root)])
        self.assertIs(module._quality_ok, quality)
        self.assertEqual(module.ROOT, current_root)

    def test_historical_root_and_quality_function_restore_when_derivation_raises(self):
        current_root = Path("current-evidence-root")
        snapshot = Path("historical-source-root")
        seen = []
        module = SimpleNamespace(ROOT=current_root)

        def quality(_value):
            seen.append(module.ROOT)
            return True, {"pass": True}

        def derive():
            module._quality_ok({})
            raise RuntimeError("sentinel derivation failure")

        module._quality_ok = quality
        original_quality = quality
        module.derive = derive
        with patch.object(history.importlib, "import_module", return_value=module):
            with self.assertRaisesRegex(RuntimeError, "sentinel"):
                history._derive_with_historical_sources(snapshot)
        self.assertEqual(seen, [snapshot])
        self.assertIs(module._quality_ok, original_quality)
        self.assertEqual(module.ROOT, current_root)


if __name__ == "__main__":
    unittest.main()
