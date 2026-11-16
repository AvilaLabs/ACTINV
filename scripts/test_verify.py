#!/usr/bin/env python3
"""Regression checks for the verification runner's process/resource safety."""
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import verify
from check_verification_lock import production_graph


class VerificationRunnerTests(unittest.TestCase):
    def test_default_kani_campaign_keeps_every_sparse_partition(self):
        source = (verify.ROOT / "verification/kernels/src/proofs.rs").read_text()
        partitions = re.findall(r"csc_pattern_proof!\((\w+),\s*(0x[0-9a-f]+)\);",source)
        self.assertEqual(len(partitions),16)
        self.assertEqual({int(mask,16) for _,mask in partitions},set(range(16)))
        commands = verify.commands("kani",False)
        selected = [command[command.index("--harness")+1] for command,_ in commands]
        self.assertEqual(len(selected),len(set(selected)))
        for name,mask in partitions:
            self.assertEqual(name,f"csc_shift_pattern_{int(mask,16):02x}")
            self.assertIn(name,selected)

    def test_experimental_kani_campaign_keeps_every_short_record_length(self):
        source = (verify.ROOT / "verification/kernels/src/proofs.rs").read_text()
        partitions = re.findall(r"short_record_proof!\((\w+),\s*(\d+)\);",source)
        self.assertEqual(len(partitions),6)
        lengths = [length for _,low in partitions for length in range(int(low),int(low)+11)]
        self.assertEqual(sorted(lengths),list(range(66)))
        selected = verify.KANI_EXPERIMENTAL_HARNESSES
        self.assertTrue(set(verify.KANI_HARNESSES).isdisjoint(selected))
        for name,low in partitions:
            self.assertEqual(name,f"endf_short_ascii_records_{int(low):02d}_{int(low)+10:02d}")
            self.assertIn(name,selected)

    def test_empty_or_timed_out_mutation_campaign_cannot_pass(self):
        self.assertFalse(verify.checked_mutation_summary({}))
        summary = dict(total_mutants=2,caught=2,missed=0,timeout=0,end_time="finished")
        self.assertTrue(verify.checked_mutation_summary(summary))
        self.assertFalse(verify.checked_mutation_summary({**summary,"timeout":1}))
        self.assertFalse(verify.checked_mutation_summary({**summary,"missed":1}))

    def test_lock_graph_follows_version_qualified_edges(self):
        lock = {"package":[
            {"name":"actinv-core","version":"1","dependencies":["shared 1"]},
            {"name":"actinv-data","version":"1","dependencies":["shared 2"]},
            {"name":"shared","version":"1","checksum":"first"},
            {"name":"shared","version":"2","checksum":"second"},
        ]}
        graph = production_graph(lock)
        self.assertIn(("shared","1","","first"),graph)
        self.assertIn(("shared","2","","second"),graph)

    def test_real_limits_are_enforced(self):
        limits = verify.verify_limits()
        self.assertEqual(limits["memory.swap.max"], "0")

    def test_unlimited_scope_is_rejected(self):
        real = Path.read_text
        def read(path, *args, **kwargs):
            return "max\n" if path.name == "memory.max" else real(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
            with self.assertRaisesRegex(RuntimeError, "memory.max"):
                verify.verify_limits()

    def test_failed_command_is_not_a_pass(self):
        result = verify.run([sys.executable, "-c", "raise SystemExit(7)"], 5, os.environ.copy())
        self.assertFalse(result["passed"])
        self.assertEqual(result["returncode"], 7)

    def test_wrong_tool_version_is_not_a_pass(self):
        result = verify.run([sys.executable,"-c","print('tool 1.0')"],5,os.environ.copy(),"tool 2.0")
        self.assertFalse(result["passed"])

    def test_version_prefix_does_not_satisfy_an_exact_pin(self):
        result = verify.run([sys.executable,"-c","print('tool 1.0.1')"],5,os.environ.copy(),"1.0")
        self.assertFalse(result["passed"])

    def test_deadline_terminates_child_and_descendant(self):
        with tempfile.TemporaryDirectory(dir=verify.WORK / "tmp") as directory:
            pidfile = Path(directory) / "pid"
            child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
            # The child is a Python interpreter, never a Rust test executable.
            parent = ("import subprocess,sys,time; from pathlib import Path; "
                      "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
                      "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)")
            result = verify.run([sys.executable,"-c",parent,str(pidfile),child], 1, os.environ.copy())
            self.assertTrue(result["timed_out"])
            self.assertFalse(result["passed"])
            pid = int(pidfile.read_text())
            status = Path(f"/proc/{pid}/stat")
            deadline = time.monotonic()+2
            while status.exists() and status.read_text().split()[2] != "Z" and time.monotonic() < deadline:
                time.sleep(0.01)
            if status.exists():
                self.assertEqual(status.read_text().split()[2], "Z", "descendant must not remain running")


if __name__ == "__main__":
    unittest.main()
