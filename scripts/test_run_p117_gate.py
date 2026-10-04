"""Regression coverage for P117 gate bounds and durable failure receipts."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_p117_gate as recorder


def resources():
    return {
        "platform": "linux", "cgroup_path": "/user.slice/test.scope",
        "cgroup_limits": {"memory.max": "6442450944", "memory.swap.max": "0",
                          "pids.max": "128", "cpu.max": "200000 100000"},
        "environment": dict(recorder.REQUIRED_ENV),
        "tmpdir": {"path": "target/preflight-tmp", "mount_point": "/", "filesystem": "ext4"},
    }


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p117-recorder-")
        self.root = Path(self.temp.name)
        (self.root / "target/preflight-tmp").mkdir(parents=True)
        (self.root / "results/quality/p117").mkdir(parents=True)
        self.root_patch = patch.object(recorder, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def invocation(self, name="seed_regressions", timeout=600, argv=None):
        return ("P117", name, timeout, f"target/p117-{name}.log",
                f"results/quality/p117/{name}.json", argv or ["python3", "test.py"])

    def test_all_nineteen_gates_have_exact_paths_and_limits(self):
        self.assertEqual(len(recorder.GATES), 19)
        for name in recorder.GATES:
            bound = 1200 if name in recorder.QUALITY_GATES else 600
            paths = recorder.validate_invocation(*self.invocation(name, bound))
            self.assertEqual(paths[0], self.root / f"target/p117-{name}.log")
            self.assertEqual(paths[1], self.root / f"results/quality/p117/{name}.json")
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation(*self.invocation(name, bound + 1))

    def test_wrong_phase_unknown_gate_and_placeholder_rejected(self):
        args = list(self.invocation())
        for index, value in ((0, "P116"), (1, "unknown"), (2, True),
                             (2, float("nan")), (2, -1), (5, []),
                             (5, ["undefined"]), (5, ["python3", "--option=None"])):
            changed = args.copy()
            changed[index] = value
            with self.subTest(index=index, value=value):
                with self.assertRaises(recorder.GateError):
                    recorder.validate_invocation(*changed)

    def test_symlink_traversal_and_existing_evidence_rejected(self):
        args = list(self.invocation())
        for value in ("target/../outside", "target/p116-seed_regressions.log", "/outside"):
            args[3] = value
            with self.assertRaises(recorder.GateError):
                recorder.validate_invocation(*args)
        args = self.invocation()
        path = self.root / args[3]
        path.symlink_to(self.root / "absent")
        with self.assertRaises(recorder.GateError):
            recorder.validate_invocation(*args)
        path.unlink()
        path.write_text("preserve")
        with self.assertRaises(recorder.GateError):
            recorder.validate_invocation(*args)
        self.assertEqual(path.read_text(), "preserve")

    def test_actual_exit_and_logs_are_durable_and_cannot_be_overwritten(self):
        args = self.invocation()
        receipt, code = recorder.run_gate(*args,
            inspector=lambda: (resources(), []),
            runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 7, "out", "err"))
        self.assertEqual(code, 7)
        self.assertEqual(receipt["status"], "child_failed")
        self.assertIs(type(receipt["child_exit_code"]), int)
        raw = (self.root / args[3]).read_bytes()
        self.assertEqual(receipt["log_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(json.loads((self.root / args[4]).read_bytes()), receipt)
        with self.assertRaises(recorder.GateError):
            recorder.run_gate(*args, runner=lambda *a, **k: self.fail("must not spawn"))

    def test_setup_failure_blocks_child(self):
        bad = resources()
        bad["cgroup_limits"]["memory.max"] = "max"
        receipt, code = recorder.run_gate(*self.invocation(),
            inspector=lambda: (bad, []),
            runner=lambda *a, **k: self.fail("must not spawn"))
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "setup_failed")
        self.assertIsNone(receipt["child_exit_code"])

    def test_boolean_exit_is_failure_not_integer_zero(self):
        receipt, code = recorder.run_gate(*self.invocation(),
            inspector=lambda: (resources(), []),
            runner=lambda argv, **kw: subprocess.CompletedProcess(argv, False, "", ""))
        self.assertEqual(code, 1)
        self.assertIsNone(receipt["child_exit_code"])
        self.assertIn("non-integer", receipt["error"])

    def test_timeout_exception_is_retained_as_failure(self):
        def timeout_runner(*args, **kwargs):
            raise RuntimeError("command timed out after reviewed kill/reap")
        receipt, code = recorder.run_gate(*self.invocation(),
            inspector=lambda: (resources(), []), runner=timeout_runner)
        self.assertEqual(code, 1)
        self.assertIn("timed out", receipt["error"])
        self.assertEqual(receipt["status"], "child_failed")


if __name__ == "__main__":
    unittest.main()
