#!/usr/bin/env python3
"""Exercise bounded control-child termination and exit races without Rust tests."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import p104_budget_control as control


ROOT = Path(__file__).resolve().parents[1]


class ChildLifecycle(unittest.TestCase):
    def run_case(self, program: str, race: str | None = None) -> subprocess.Popen:
        spawned = []
        original_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            spawned.append(child)
            if race == "terminate":
                def already_exited():
                    child.wait(timeout=1)
                    raise ProcessLookupError("child exited during cancellation")
                child.terminate = already_exited
            elif race == "kill":
                original_kill = child.kill
                def already_killed():
                    original_kill()
                    child.wait(timeout=1)
                    raise ProcessLookupError("child exited during kill")
                child.kill = already_killed
            return child

        try:
            with patch.object(control.subprocess, "Popen", spawn):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    control._run([sys.executable, "-c", program], ROOT, timeout_s=0.2)
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].returncode, "child must be reaped")
            return spawned[0]
        finally:
            for child in spawned:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()

    def test_normal_exit(self):
        result = control._run([sys.executable, "-c", "print('finished')"], ROOT, timeout_s=3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "finished\n")

    def test_timeout_terminates_and_reaps(self):
        child = self.run_case("import time; time.sleep(10)")
        self.assertLess(child.returncode, 0)

    def test_exit_during_terminate_is_reaped(self):
        child = self.run_case("import time; time.sleep(0.3)", race="terminate")
        self.assertEqual(child.returncode, 0)

    def test_exit_during_kill_is_reaped(self):
        child = self.run_case(
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
            race="kill",
        )
        self.assertLess(child.returncode, 0)


if __name__ == "__main__":
    unittest.main()
