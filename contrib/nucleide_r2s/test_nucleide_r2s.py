"""Fast test for the ACTINV -> ALARA -> nucleide round-trip demo (P102).

Runs the demo against the repository's corpus r2s-source document and
checks the printed conservation holds to machine precision. No transport
and no activation solve — the demo itself only runs `actinv export-source
alara` and nucleide's own readers.

If nucleide is not importable, the test is skipped (not failed, not
errored), following the `contrib/openmc_r2s` pattern.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = Path(__file__).resolve().parent / "demo.py"

try:
    import nucleide  # noqa: F401
    HAVE_NUCLEIDE = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment-dependent
    HAVE_NUCLEIDE = False
    _IMPORT_ERROR = exc


@unittest.skipUnless(HAVE_NUCLEIDE, f"nucleide not importable: {_IMPORT_ERROR}")
class NucleideR2SDemoTests(unittest.TestCase):
    def test_demo_runs_and_conserves(self):
        actinv_bin = next(
            (p for p in (ROOT / "target/release/actinv", ROOT / "target/debug/actinv")
             if p.exists()), None)
        if actinv_bin is None:
            self.skipTest("no actinv binary built — "
                          "`cargo build --release -p actinv-cli` first")

        result = subprocess.run(
            [sys.executable, str(DEMO), "--shutdown-t-s", "0"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)

        lines = result.stdout.splitlines()
        self.assertIn("8 cells, group grid length", "\n".join(lines))

        max_err_line = next(l for l in lines if l.startswith("max relative error"))
        max_err = float(max_err_line.split(":")[1].strip())
        self.assertLessEqual(max_err, 1e-9)

        total_line = next(l for l in lines if l.startswith("total declared"))
        tag_line = next(l for l in lines if l.startswith("nucleide r2s"))
        total = float(total_line.split(":")[1].strip())
        tagged = float(tag_line.split(":")[1].strip())
        self.assertAlmostEqual(total, tagged, delta=1e-6 * max(1.0, abs(total)))


if __name__ == "__main__":
    unittest.main()
