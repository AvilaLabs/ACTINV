"""Focused safety and fidelity tests for the P120 historical replay runner."""
from __future__ import annotations

import hashlib
import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_p120_history as replay


class HistoryReplayTests(unittest.TestCase):
    def test_base_workflow_hash_and_complete_command_sequence_are_pinned(self):
        result = subprocess.run(
            ["git", "show", f"{replay.BASE}:.github/workflows/ci.yml"],
            cwd=replay.ROOT, check=True, capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout.encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), replay.WORKFLOW_SHA256)
        commands = replay._frozen_commands(raw)
        self.assertEqual(commands, replay._expected_commands())
        self.assertEqual(len(commands), 50)
        self.assertEqual(commands[0], ["python", "controls/check_p103.py", "--g0-only", "--no-write"])
        self.assertEqual(commands[-1], ["python", "controls/check_p118_history.py"])

    def test_missing_or_changed_workflow_boundaries_are_rejected(self):
        for raw in (b"", b"P103 block\nP117 block"):
            with self.subTest(raw=raw), self.assertRaises(replay.ReplayError):
                replay._frozen_commands(raw)
        pinned = subprocess.run(
            ["git", "show", f"{replay.BASE}:.github/workflows/ci.yml"],
            cwd=replay.ROOT, check=True, capture_output=True, text=True, timeout=30,
        ).stdout.encode()
        commands = replay._frozen_commands(pinned)
        self.assertEqual(commands, replay._expected_commands())
        mutated = pinned.replace(b"python controls/check_p118_history.py",
                                 b"python controls/check_p118_history.py --extra", 1)
        self.assertNotEqual(replay._frozen_commands(mutated), replay._expected_commands())

    def test_gate_receipt_records_failure_and_never_overwrites_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(replay, "ROOT", root):
                fake = lambda argv, cwd, env, timeout: subprocess.CompletedProcess(argv, 9, "out", "err")
                receipt = replay._record_command(root, "gate-01", ["python", "x.py"],
                                                 root, {}, 1200, fake)
                log = (root / "gate-01.log").read_bytes()
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["child_exit_code"], 9)
                self.assertEqual(receipt["log_sha256"], hashlib.sha256(log).hexdigest())
                with self.assertRaises(FileExistsError):
                    replay._record_command(root, "gate-01", ["python", "x.py"], root, {}, 1200, fake)

    def test_timeout_terminates_process_group_and_reaps_even_on_exit_race(self):
        child = Mock()
        child.pid = 71
        child.communicate.side_effect = [subprocess.TimeoutExpired(["cmd"], 3), ("done", "")]
        with patch.object(replay.subprocess, "Popen", return_value=child), \
             patch.object(replay.os, "killpg", side_effect=[ProcessLookupError, ProcessLookupError]) as killpg:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                replay._run(["cmd"], Path("."), {}, 0)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(71, replay.signal.SIGTERM), (71, replay.signal.SIGKILL)])
        self.assertEqual(child.communicate.call_count, 2)

    def test_timeout_escalates_to_kill_and_reaps(self):
        child = Mock()
        child.pid = 72
        child.communicate.side_effect = [subprocess.TimeoutExpired(["cmd"], 3), ("done", "")]
        with patch.object(replay.subprocess, "Popen", return_value=child), \
             patch.object(replay.os, "killpg") as killpg:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                replay._run(["cmd"], Path("."), {}, 0)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(72, replay.signal.SIGTERM), (72, replay.signal.SIGKILL)])
        self.assertEqual(child.communicate.call_count, 2)

    def test_kill_signal_exit_race_still_reaps(self):
        child = Mock()
        child.pid = 73
        child.communicate.side_effect = [subprocess.TimeoutExpired(["cmd"], 3), ("done", "")]
        with patch.object(replay.subprocess, "Popen", return_value=child), \
             patch.object(replay.os, "killpg", side_effect=[None, ProcessLookupError]) as killpg:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                replay._run(["cmd"], Path("."), {}, 0)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(73, replay.signal.SIGTERM), (73, replay.signal.SIGKILL)])
        self.assertEqual(child.communicate.call_count, 2)

    def test_real_timed_out_child_is_reaped(self):
        created = []
        popen = replay.subprocess.Popen

        def capture(*args, **kwargs):
            child = popen(*args, **kwargs)
            created.append(child)
            return child

        with patch.object(replay.subprocess, "Popen", side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                replay._run([sys.executable, "-c", "import time; time.sleep(30)"],
                            Path("."), {}, 0.05)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].returncode)

    def test_real_child_normal_exit_is_captured(self):
        result = replay._run([sys.executable, "-c", "print('bounded')"], Path("."), {}, 5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "bounded")

    def _run_cancelled_child(self, source: str, *, with_guard=True,
                             parent_signal=replay.signal.SIGTERM) -> subprocess.Popen:
        created = []
        popen = replay.subprocess.Popen
        ready_root = os.environ.get("TMPDIR") or None
        with tempfile.TemporaryDirectory(prefix="p120-cancel-ready-", dir=ready_root) as temp:
            ready = Path(temp) / "ready"

            def wait_then_cancel():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not ready.is_file():
                    threading.Event().wait(0.01)
                os.kill(os.getpid(), parent_signal)

            def capture(*args, **kwargs):
                child = popen(*args, **kwargs)
                created.append(child)
                return child

            source += (f"\nfrom pathlib import Path; Path({str(ready)!r}).write_text('ready')"
                       "\nimport time; time.sleep(30)\n")
            timer = threading.Thread(target=wait_then_cancel, daemon=True)
            guard = replay._cancellation_guard() if with_guard else contextlib.nullcontext()
            with patch.object(replay.subprocess, "Popen", side_effect=capture), guard:
                timer.start()
                try:
                    if with_guard:
                        with self.assertRaisesRegex(replay.ReplayCancelled, "SIGTERM"):
                            replay._run([sys.executable, "-c", source], Path("."), {}, 15)
                    else:
                        with self.assertRaises(KeyboardInterrupt):
                            replay._run([sys.executable, "-c", source], Path("."), {}, 15)
                finally:
                    timer.join(timeout=7)
            self.assertFalse(timer.is_alive(), "readiness/cancellation thread exceeded its bound")
            self.assertTrue(ready.is_file(), "child did not reach signal-handler readiness")
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].returncode)
        return created[0]

    def test_caller_sigterm_stops_and_reaps_active_child(self):
        child = self._run_cancelled_child("pass")
        self.assertEqual(child.returncode, -15)

    def test_caller_sigterm_escalates_when_child_ignores_term(self):
        child = self._run_cancelled_child(
            "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)"
        )
        self.assertEqual(child.returncode, -9)

    def test_direct_keyboard_interrupt_stops_and_reaps_without_guard(self):
        child = self._run_cancelled_child("pass", with_guard=False,
                                          parent_signal=replay.signal.SIGINT)
        self.assertEqual(child.returncode, -15)

    def test_cancel_handler_tolerates_child_exit_race(self):
        child = Mock(pid=74)
        with replay._cancellation_guard(), \
             patch.object(replay, "_ACTIVE_CHILD", child), \
             patch.object(replay.os, "killpg", side_effect=ProcessLookupError):
            replay._cancel_handler(replay.signal.SIGTERM, None)
            with self.assertRaisesRegex(replay.ReplayCancelled, "SIGTERM"):
                replay._check_cancelled(0)


if __name__ == "__main__":
    unittest.main()
