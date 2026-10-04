"""Source-only regressions for the durable P114 gate recorder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_roadmap_gate as recorder


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


class RoadmapGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p114-gate-recorder-")
        self.root = Path(self.temp.name)
        (self.root / "target").mkdir()
        (self.root / "results").mkdir()
        with patch.object(recorder, "ROOT", self.root):
            (self.root / "target/preflight-tmp").mkdir()
            (self.root / "results/quality/p114").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def paths(self, name: str = "rust_fmt") -> tuple[str, str]:
        return f"target/p114-{name}.log", f"results/quality/p114/{name}.json"

    def run_mocked(self, *, inspector=None, runner=None, name="rust_fmt"):
        log, receipt = self.paths(name)
        return recorder.run_gate("P114", name, 120, log, receipt,
                                 ["cargo", "fmt", "--all", "--", "--check"],
                                 inspector=inspector or (lambda: (valid_resources(), [])),
                                 runner=runner)

    def test_cgroup_limits_require_exact_memory_swap_tasks_and_200_percent_cpu(self):
        valid = {"memory.max": "6442450944", "memory.swap.max": "0",
                 "pids.max": "128", "cpu.max": "200000 100000"}
        self.assertEqual(recorder._cgroup_limit_errors(valid), [])
        for key, value in (("memory.max", "max"), ("memory.swap.max", "1"),
                           ("pids.max", "129"), ("cpu.max", "max 100000"),
                           ("cpu.max", "0 0"), ("cpu.max", "100000 100000")):
            with self.subTest(key=key, value=value):
                altered = {**valid, key: value}
                self.assertTrue(recorder._cgroup_limit_errors(altered))

    def test_tmpdir_mount_inspection_rejects_ram_backed_filesystems(self):
        mountinfo = f"36 25 0:32 / {self.root} rw - tmpfs tmpfs rw\n"
        with self.assertRaisesRegex(recorder.GateError, "non-disk"):
            recorder._filesystem_for(self.root, mountinfo)
        disk_mountinfo = f"36 25 8:1 / {self.root} rw - ext4 /dev/sda1 rw\n"
        self.assertEqual(recorder._filesystem_for(self.root, disk_mountinfo), (str(self.root), "ext4"))

    def test_invocation_rejects_wrong_phase_gate_timeout_paths_and_argv(self):
        log, receipt = self.paths()
        cases = [
            ("P113", "rust_fmt", 120, log, receipt, ["cargo", "fmt"]),
            ("P114", "not_a_gate", 120, log, receipt, ["cargo", "fmt"]),
            ("P114", "rust_fmt", 1201, log, receipt, ["cargo", "fmt"]),
            ("P114", "historical_p113_replay", 601, "target/p114-historical_p113_replay.log",
             "results/quality/p114/historical_p113_replay.json", ["python3", "control.py"]),
            ("P114", "rust_fmt", 120, log, receipt, []),
            ("P114", "rust_fmt", 120, log, receipt, ["cargo", "fmt", "undefined"]),
            ("P114", "rust_fmt", 120, log, receipt,
             ["undefined", "timeout", "--kill-after=30s", "1200s", "bash", "-c", "cargo test"]),
            ("P114", "rust_fmt", 120, log, receipt,
             ["undefined timeout --kill-after=30s 1200s", "bash", "-c", "cargo test"]),
            ("P114", "rust_fmt", 120, log, receipt, ["cargo", "fmt", "--config=None"]),
        ]
        for phase, name, timeout, out_log, out_receipt, argv in cases:
            with self.subTest(phase=phase, name=name, timeout=timeout, argv=argv):
                with patch.object(recorder, "ROOT", self.root):
                    with self.assertRaises(recorder.GateError):
                        recorder.validate_invocation(phase, name, timeout, out_log, out_receipt, argv)
        with patch.object(recorder, "ROOT", self.root):
            with self.assertRaisesRegex(recorder.GateError, "traversal"):
                recorder.validate_invocation("P114", "rust_fmt", 120, "target/../escape",
                    receipt, ["cargo", "fmt"])
            with self.assertRaisesRegex(recorder.GateError, "timeout"):
                recorder.validate_invocation("P114", "rust_fmt", True, log, receipt,
                    ["cargo", "fmt"])

    def test_atomic_create_is_durable_and_never_overwrites(self):
        destination = self.root / "target/receipt.bin"
        recorder._atomic_create(destination, b"first")
        self.assertEqual(destination.read_bytes(), b"first")
        with self.assertRaises(FileExistsError):
            recorder._atomic_create(destination, b"second")
        self.assertEqual(destination.read_bytes(), b"first")
        self.assertFalse(list(destination.parent.glob(".p114-*.tmp")))

    def test_actual_mocked_integer_exit_and_log_digest_are_recorded(self):
        def runner(argv, *, cwd, timeout_s):
            self.assertEqual(argv, ["cargo", "fmt", "--all", "--", "--check"])
            self.assertEqual(cwd, self.root)
            self.assertEqual(timeout_s, 120)
            return subprocess.CompletedProcess(argv, 7, "stdout marker", "stderr marker")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(runner=runner)
        self.assertEqual(code, 7)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertIs(type(receipt["child_exit_code"]), int)
        self.assertEqual(receipt["child_exit_code"], 7)
        raw_log = (self.root / receipt["log_path"]).read_bytes()
        self.assertEqual(receipt["log_sha256"], hashlib.sha256(raw_log).hexdigest())
        receipt_path = self.root / "results/quality/p114/rust_fmt.json"
        self.assertEqual(json.loads(receipt_path.read_bytes()), receipt)
        self.assertEqual(receipt["schema"], "actinv-roadmap-gate-receipt-1")
        self.assertEqual(receipt["resources"], valid_resources())

    def test_zero_exit_is_completed_only_for_actual_integer_zero(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "completed")
        self.assertIs(type(receipt["child_exit_code"]), int)
        self.assertEqual(receipt["child_exit_code"], 0)

    def test_setup_limit_mismatch_is_durably_failed_without_spawning(self):
        observed = valid_resources()
        observed["cgroup_limits"]["memory.max"] = "max"
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "must not run", "")

        inspector = lambda: (observed, ["memory.max must equal 6442450944, observed 'max'"])
        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(inspector=inspector, runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "setup_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertEqual(calls, [])
        self.assertIn("memory.max", receipt["error"])
        self.assertTrue((self.root / receipt["log_path"]).is_file())
        self.assertTrue((self.root / "results/quality/p114/rust_fmt.json").is_file())

    def test_empty_malformed_and_raising_resource_inspections_fail_closed(self):
        inspections = [
            lambda: ({}, []),
            lambda: ({"platform": "linux", "cgroup_limits": {}, "environment": {}, "tmpdir": {}}, []),
            lambda: (_ for _ in ()).throw(OSError("read denied")),
        ]
        for index, inspector in enumerate(inspections):
            name = "p114_seal_regressions"
            log, receipt_path = self.paths(name)
            calls = []
            with self.subTest(index=index), patch.object(recorder, "ROOT", self.root):
                receipt, code = recorder.run_gate(
                    "P114", name, 120, log, receipt_path,
                    ["python3", "controls/test_p114_seal.py"],
                    inspector=inspector,
                    runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                )
                self.assertEqual(code, 1)
                self.assertEqual(receipt["status"], "setup_failed")
                self.assertIsNone(receipt["child_exit_code"])
                self.assertTrue(receipt["error"])
                self.assertEqual(calls, [])
            # Keep each attempt distinct while still checking fixed gate paths.
            (self.root / log).unlink(missing_ok=True)
            (self.root / receipt_path).unlink(missing_ok=True)

    def test_boolean_exit_is_not_accepted_as_integer_child_status(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, True, "", "")

        with patch.object(recorder, "ROOT", self.root):
            receipt, code = self.run_mocked(runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertIsNone(receipt["child_exit_code"])
        self.assertIn("non-integer", receipt["error"])

    def test_receipt_or_log_existing_refuses_before_command(self):
        log, receipt_path = self.paths()
        receipt = self.root / receipt_path
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("prior", encoding="utf-8")
        called = []
        with patch.object(recorder, "ROOT", self.root):
            with self.assertRaisesRegex(recorder.GateError, "overwrite"):
                self.run_mocked(runner=lambda *a, **kw: called.append((a, kw)))
        self.assertEqual(called, [])
        self.assertEqual(receipt.read_text(encoding="utf-8"), "prior")
        receipt.unlink()
        (self.root / log).write_text("prior log", encoding="utf-8")
        with patch.object(recorder, "ROOT", self.root):
            with self.assertRaisesRegex(recorder.GateError, "overwrite"):
                self.run_mocked(runner=lambda *a, **kw: called.append((a, kw)))
        self.assertEqual(called, [])

    def test_output_path_refuses_symlink_parent_escape(self):
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        quality = self.root / "results/quality"
        quality.rename(self.root / "results/quality-original")
        quality.symlink_to(outside, target_is_directory=True)
        log, receipt = self.paths()
        try:
            with patch.object(recorder, "ROOT", self.root):
                with self.assertRaisesRegex(recorder.GateError, "symlink"):
                    recorder.validate_invocation("P114", "rust_fmt", 120, log, receipt,
                                                 ["cargo", "fmt"])
        finally:
            quality.unlink()
            (self.root / "results/quality-original").rename(quality)
            outside.rmdir()


if __name__ == "__main__":
    unittest.main()
