"""Focused refusal tests for immutable P115 failure verification."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p115_history as history


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _receipt(log_path: str) -> dict:
    return {
        "argv": ["python3", "controls/test_p115_verdict.py"],
        "child_exit_code": 1,
        "cwd": ".",
        "error": None,
        "gate": "p115_verdict_regressions",
        "log_path": log_path,
        "log_sha256": history.INITIAL_FAILURE_LOG_SHA,
        "phase": "P115",
        "resources": {
            "cgroup_limits": {"cpu.max": "200000 100000", "memory.max": "6442450944",
                              "memory.swap.max": "0", "pids.max": "128"},
            "cgroup_path": "/user.slice/user-1000.slice/user@1000.service/app.slice/example.scope",
            "environment": {"CARGO_BUILD_JOBS": "1", "RAYON_NUM_THREADS": "2",
                            "RUST_TEST_THREADS": "1"},
            "platform": "linux",
            "tmpdir": {"filesystem": "ext4", "mount_point": "/", "path": "target/preflight-tmp"},
        },
        "schema": "actinv-roadmap-gate-receipt-1",
        "status": "child_failed",
        "timeout_s": 600.0,
    }


class P115HistoryTests(unittest.TestCase):
    def test_paths_reject_traversal_and_non_posix_aliases(self) -> None:
        for value in ("/tmp/file", "../outside", "results/../outside", "results\\outside"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                history._safe_rel(value)

    def test_record_rejects_integer_zero_for_historical_false(self) -> None:
        original = history._json(history.FAILURE_RECORD)
        altered = json.loads(json.dumps(original))
        altered["checker_error"]["original_derivation_completed"] = 0
        with mock.patch.object(history, "_json", return_value=altered):
            with self.assertRaisesRegex(ValueError, "checker error typing"):
                history._validate_artifacts_and_record()

    def test_receipt_path_is_relative_even_when_g3_path_is_archived(self) -> None:
        rel_receipt = history.INITIAL_PREFIX + "results/quality/p115/p115_verdict_regressions.json"
        rel_log = history.INITIAL_PREFIX + "target/p115-p115_verdict_regressions.log"
        receipt = _receipt("target/p115-p115_verdict_regressions.log")
        raw = json.dumps(receipt, sort_keys=True).encode("utf-8")
        gate = {"name": "p115_verdict_regressions", "round": "initial",
                "receipt_path": rel_receipt, "log_path": rel_log,
                "receipt_sha256": _sha(raw), "log_sha256": history.INITIAL_FAILURE_LOG_SHA,
                "argv": receipt["argv"]}
        archive = {rel_receipt: _sha(raw), rel_log: history.INITIAL_FAILURE_LOG_SHA}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(raw)
            with mock.patch.object(history, "_regular", return_value=path):
                self.assertEqual(history._validate_receipt(gate, archive=archive, failure=True), receipt)
                receipt["log_path"] = rel_log
                raw = json.dumps(receipt, sort_keys=True).encode("utf-8")
                path.write_bytes(raw)
                gate["receipt_sha256"] = _sha(raw)
                archive[rel_receipt] = _sha(raw)
                with self.assertRaisesRegex(ValueError, "receipt contents differ"):
                    history._validate_receipt(gate, archive=archive, failure=True)

    def test_archive_map_rejects_incomplete_population_before_reading(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 68"):
            history._validate_map({}, commit=history.ORIGINAL,
                                  prefix=history.INITIAL_PREFIX, count=68)

    def test_archive_map_accepts_exact_tree_and_rejects_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "archive"
            base.mkdir()
            first = b"receipt bytes"
            second = b"log bytes"
            (base / "receipt.json").write_bytes(first)
            (base / "run.log").write_bytes(second)
            mapping = {"archive/receipt.json": _sha(first), "archive/run.log": _sha(second)}
            with mock.patch.object(history, "ROOT", root), mock.patch.object(
                    history, "_blob", side_effect=lambda _commit, path: {
                        "archive/receipt.json": first, "archive/run.log": second}[path]):
                self.assertEqual(history._validate_map(mapping, commit=history.ORIGINAL,
                                                       prefix="archive/", count=2), mapping)
                (base / "extra.log").write_bytes(b"unexpected")
                with self.assertRaisesRegex(ValueError, "physical archive members"):
                    history._validate_map(mapping, commit=history.ORIGINAL,
                                          prefix="archive/", count=2)
                (base / "extra.log").unlink()
                (base / "run.log").unlink()
                with self.assertRaisesRegex(ValueError, "physical archive members"):
                    history._validate_map(mapping, commit=history.ORIGINAL,
                                          prefix="archive/", count=2)
                (base / "run.log").write_bytes(b"altered")
                with self.assertRaisesRegex(ValueError, "archive differs"):
                    history._validate_map(mapping, commit=history.ORIGINAL,
                                          prefix="archive/", count=2)
                with mock.patch.object(history, "_blob", return_value=b"different Git blob"):
                    with self.assertRaisesRegex(ValueError, "archive differs"):
                        history._validate_map(mapping, commit=history.ORIGINAL,
                                              prefix="archive/", count=2)

    def test_original_name_error_is_required_and_only_fail_projection_is_patched(self) -> None:
        class FakeVerdict:
            def __init__(self, error_name: str = "historical_p114") -> None:
                self.calls = 0
                self._g0_ok = lambda _g0: True
                self._source_commit_matches = lambda _g3, _record: True
                self.original_g0 = self._g0_ok
                self.original_source = self._source_commit_matches
                self.error_name = error_name

            def derive(self) -> dict:
                self.calls += 1
                if self.calls == 1:
                    message = f"name '{self.error_name}' is not defined"
                    raise NameError(message, name=self.error_name)
                if self._g0_ok({}) is not False:
                    raise AssertionError("fail-only projection did not force G0 false")
                return {"verdict": "P115-FAIL", "gates": {"G3_local": "FAIL", "G3_CI": "PENDING"}}

        fake = FakeVerdict()
        with mock.patch.object(history.importlib, "import_module", return_value=fake):
            projected = history._derive_failure({}, Path("/unused"))
        self.assertEqual(projected["checker_error"], {
            "exception_type": "NameError", "name": "historical_p114",
            "message": "name 'historical_p114' is not defined",
            "failure_projection": "unsealed_G0_forced_false_only",
            "original_derivation_completed": False,
        })
        self.assertIs(fake._g0_ok, fake.original_g0)
        self.assertIs(fake._source_commit_matches, fake.original_source)

        wrong = FakeVerdict("historical_p113")
        with mock.patch.object(history.importlib, "import_module", return_value=wrong):
            with self.assertRaisesRegex(ValueError, "different NameError"):
                history._derive_failure({}, Path("/unused"))
        self.assertIs(wrong._g0_ok, wrong.original_g0)


if __name__ == "__main__":
    unittest.main()
