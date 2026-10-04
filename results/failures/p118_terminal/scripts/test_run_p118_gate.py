"""Source-only regressions for the bounded P118 gate recorder."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_p118_gate as recorder


def valid_resources() -> dict:
    return {
        "platform": "linux",
        "cgroup_path": "/user.slice/test.scope",
        "cgroup_limits": {
            "memory.max": "6442450944",
            "memory.swap.max": "0",
            "pids.max": "128",
            "cpu.max": "200000 100000",
        },
        "environment": dict(recorder.REQUIRED_ENV),
        "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/", "filesystem": "ext4"},
    }


class P118GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p118-gate-recorder-")
        self.root = Path(self.temp.name)
        (self.root / "target").mkdir()
        (self.root / "results").mkdir()
        (self.root / "target/preflight-tmp").mkdir()
        (self.root / "results/quality/p118").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def paths(self, name: str = "rust_fmt") -> tuple[str, str]:
        return f"target/p118-{name}.log", f"results/quality/p118/{name}.json"

    def run_mocked(self, *, name="rust_fmt", timeout=1200, inspector=None, runner=None):
        log, receipt = self.paths(name)
        return recorder.run_gate(
            "P118", name, timeout, log, receipt,
            ["python3", "-c", "print('isolated')"],
            inspector=inspector or (lambda: (valid_resources(), [])), runner=runner,
        )

    def test_protocol_names_paths_and_timeout_budgets_are_fixed(self):
        for name in recorder.GATES:
            log, receipt = self.paths(name)
            maximum = 1200 if name == "rust_fmt" else 600
            with patch.object(recorder, "ROOT", self.root):
                paths = recorder.validate_invocation(
                    "P118", name, maximum, log, receipt, ["python3", "gate.py"]
                )
            self.assertEqual(paths[0], self.root / log)
            self.assertEqual(paths[1], self.root / receipt)
        with patch.object(recorder, "ROOT", self.root):
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation("P118", "g1", 600.1,
                    "target/p118-g1.log", "results/quality/p118/g1.json", ["python3", "g1.py"])
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation("P118", "rust_fmt", 1200.1,
                    *self.paths(), ["cargo", "fmt"])
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation("P117", "rust_fmt", 10,
                    *self.paths(), ["cargo", "fmt"])

    def test_rejects_placeholder_traversal_malformed_argv_and_unknown_gate(self):
        with patch.object(recorder, "ROOT", self.root):
            for name, log, receipt, argv in (
                ("unknown", "target/p118-unknown.log", "results/quality/p118/unknown.json", ["x"]),
                ("g1", "target/p118-g1.log", "results/quality/p118/g1.json", []),
                ("g1", "target/p118-g1.log", "results/quality/p118/g1.json", ["undefined", "cmd"]),
                ("g1", "target/p118-g1.log", "results/quality/p118/g1.json", ["cmd", "--value=None"]),
                ("g1", "target/../p118-g1.log", "results/quality/p118/g1.json", ["cmd"]),
            ):
                with self.subTest(name=name, log=log, argv=argv), self.assertRaises(recorder.GateError):
                    recorder.validate_invocation("P118", name, 600, log, receipt, argv)
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation("P118", "g1", True,
                    "target/p118-g1.log", "results/quality/p118/g1.json", ["cmd"])

    def test_resource_snapshot_requires_exact_caps_environment_and_disk_tmp(self):
        good = valid_resources()
        self.assertEqual(recorder._resource_snapshot_errors(good), [])
        for edit in (
            lambda value: value["cgroup_limits"].update({"memory.max": "max"}),
            lambda value: value["cgroup_limits"].update({"memory.swap.max": "1"}),
            lambda value: value["cgroup_limits"].update({"pids.max": "129"}),
            lambda value: value["cgroup_limits"].update({"cpu.max": "100000 100000"}),
            lambda value: value["environment"].update({"RUST_TEST_THREADS": "8"}),
            lambda value: value["tmpdir"].update({"filesystem": "tmpfs"}),
            lambda value: value["tmpdir"].update({"filesystem": "  "}),
            lambda value: value["tmpdir"].update({"mount_point": "  "}),
        ):
            altered = json.loads(json.dumps(good))
            edit(altered)
            self.assertTrue(recorder._resource_snapshot_errors(altered))

    def test_success_records_actual_argv_elapsed_resources_and_log_digest(self):
        def runner(argv, *, cwd, timeout_s):
            self.assertEqual(cwd, self.root)
            self.assertEqual(timeout_s, 1200)
            self.assertEqual(argv, ["python3", "-c", "print('isolated')"])
            return subprocess.CompletedProcess(argv, 0, "stdout marker\n", "stderr marker\n")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "completed")
        self.assertIs(type(receipt["child_exit_code"]), int)
        self.assertEqual(receipt["child_exit_code"], 0)
        self.assertIsInstance(receipt["elapsed_s"], float)
        self.assertTrue(math.isfinite(receipt["elapsed_s"]))
        self.assertGreaterEqual(receipt["elapsed_s"], 0.0)
        self.assertLessEqual(receipt["elapsed_s"], receipt["timeout_s"] + recorder.CLEANUP_HEADROOM_S)
        self.assertEqual(receipt["resources"], valid_resources())
        log_data = (self.root / receipt["log_path"]).read_bytes()
        self.assertEqual(receipt["log_sha256"], hashlib.sha256(log_data).hexdigest())
        saved = json.loads((self.root / "results/quality/p118/rust_fmt.json").read_bytes())
        self.assertEqual(saved, receipt)
        self.assertEqual(receipt["schema"], "actinv-roadmap-gate-receipt-1")
        self.assertEqual(receipt["phase"], "P118")
        self.assertEqual(receipt["cwd"], ".")
        self.assertEqual(receipt["timeout_s"], 1200)

    def test_nonzero_integer_exit_is_retained_as_failure(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 9, "partial", "failure")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(name="g1", timeout=600, runner=runner)
        self.assertEqual(code, 9)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertEqual(receipt["child_exit_code"], 9)
        self.assertIs(type(receipt["child_exit_code"]), int)

    def test_deadline_exception_is_durably_recorded_without_invented_exit(self):
        def runner(argv, *, cwd, timeout_s):
            self.assertEqual(timeout_s, 600)
            raise TimeoutError("bounded runner deadline; child cleanup complete")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(name="g2", timeout=600, runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertIn("TimeoutError", receipt["error"])
        self.assertEqual(json.loads((self.root / "results/quality/p118/g2.json").read_bytes()), receipt)

    def test_elapsed_over_timeout_and_cleanup_headroom_fails_closed(self):
        times = iter((10.0, 36.0))

        with patch.object(recorder, "ROOT", self.root), patch.object(recorder.time, "monotonic", side_effect=lambda: next(times)):
            receipt, code = self.run_mocked(timeout=10,
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "done", ""))
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertEqual(receipt["child_exit_code"], 0)
        self.assertEqual(receipt["elapsed_s"], 26.0)
        self.assertIn("cleanup headroom", receipt["error"])

    def test_nonfinite_monotonic_duration_is_not_published_as_valid_elapsed(self):
        times = iter((1.0, float("nan")))

        with patch.object(recorder, "ROOT", self.root), patch.object(recorder.time, "monotonic", side_effect=lambda: next(times)):
            receipt, code = self.run_mocked(timeout=10,
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "done", ""))
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "setup_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertEqual(receipt["elapsed_s"], 0.0)
        self.assertTrue(math.isfinite(receipt["elapsed_s"]))
        self.assertGreaterEqual(receipt["elapsed_s"], 0.0)

    def test_setup_resource_failure_is_recorded_without_launching_runner(self):
        observed = valid_resources()
        observed["cgroup_limits"]["memory.max"] = "max"
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "unexpected", "")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(
                name="p118_regressions", timeout=600,
                inspector=lambda: (observed, ["memory limit unavailable"]), runner=runner,
            )
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "setup_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertIn("memory.max", receipt["error"])
        self.assertEqual(calls, [])

    def test_boolean_exit_is_not_accepted_as_integer(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, True, "", "")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(name="g1", timeout=600, runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertIn("non-integer", receipt["error"])

    def test_existing_log_or_receipt_refuses_before_runner(self):
        calls = []
        runner = lambda *args, **kwargs: calls.append((args, kwargs))
        log, receipt = self.paths("g1")
        (self.root / receipt).write_text("old receipt", encoding="utf-8")
        with patch.object(recorder, "ROOT", self.root), self.assertRaisesRegex(recorder.GateError, "overwrite"):
            self.run_mocked(name="g1", timeout=600, runner=runner)
        self.assertEqual(calls, [])
        (self.root / receipt).unlink()
        (self.root / log).write_text("old log", encoding="utf-8")
        with patch.object(recorder, "ROOT", self.root), self.assertRaisesRegex(recorder.GateError, "overwrite"):
            self.run_mocked(name="g1", timeout=600, runner=runner)
        self.assertEqual(calls, [])

    def test_symlinked_output_parent_is_refused(self):
        external = self.root.parent / f"{self.root.name}-external"
        external.mkdir()
        quality = self.root / "results/quality"
        quality.rename(self.root / "results/quality-original")
        quality.symlink_to(external, target_is_directory=True)
        try:
            with patch.object(recorder, "ROOT", self.root), self.assertRaisesRegex(recorder.GateError, "symlink"):
                recorder.validate_invocation("P118", "rust_fmt", 120,
                    *self.paths(), ["cargo", "fmt"])
        finally:
            quality.unlink()
            (self.root / "results/quality-original").rename(quality)
            external.rmdir()

    def test_log_publication_error_is_recorded_and_returns_failure(self):
        original = recorder._atomic_create
        log, _ = self.paths("g1")

        def broken(path, data):
            if path == self.root / log:
                raise OSError("disk full")
            return original(path, data)

        with patch.object(recorder, "ROOT", self.root), patch.object(recorder, "_atomic_create", side_effect=broken):
            receipt, code = self.run_mocked(name="g1", timeout=600,
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""))
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "setup_failed")
        self.assertIsNone(receipt["log_sha256"])
        self.assertIn("log persistence failed", receipt["error"])
        self.assertTrue((self.root / "results/quality/p118/g1.json").is_file())

    def test_receipt_publication_error_never_replaces_prior_log(self):
        original = recorder._atomic_create
        _, receipt_rel = self.paths("g1")

        def broken(path, data):
            if path == self.root / receipt_rel:
                raise OSError("receipt filesystem failure")
            return original(path, data)

        with patch.object(recorder, "ROOT", self.root), patch.object(recorder, "_atomic_create", side_effect=broken):
            receipt, code = self.run_mocked(name="g1", timeout=600,
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""))
        self.assertEqual(code, 1)
        self.assertTrue((self.root / "target/p118-g1.log").is_file())
        self.assertFalse((self.root / receipt_rel).exists())
        self.assertIn("log_sha256", receipt)


if __name__ == "__main__":
    unittest.main()
