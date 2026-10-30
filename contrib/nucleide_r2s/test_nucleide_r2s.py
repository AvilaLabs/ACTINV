"""Focused tests for the ACTINV -> ALARA -> Nucleide round-trip demo.

The demo uses the committed corpus source, runs only the ACTINV exporter, and
checks that Nucleide reconstructs each cell's declared photon strength.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

if __package__:
    from . import demo
else:
    import demo

ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE = ROOT / "results/p52_r2s_source.ndjson"

try:
    import nucleide  # noqa: F401
    HAVE_NUCLEIDE = True
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # skip only when the optional package itself is absent
    if exc.name != "nucleide":
        raise
    HAVE_NUCLEIDE = False
    _IMPORT_ERROR = exc


def _disk_temp_root() -> Path:
    root = ROOT / "target" / "preflight-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


@unittest.skipUnless(HAVE_NUCLEIDE, f"nucleide not importable: {_IMPORT_ERROR}")
class NucleideR2SDemoTests(unittest.TestCase):
    def _actinv_bin(self) -> str:
        try:
            return demo.find_actinv()
        except FileNotFoundError:
            if os.environ.get("ACTINV_BIN"):
                raise
            self.skipTest("no actinv binary built — `cargo build --release -p actinv-cli` first")

    def test_demo_runs_and_conserves(self):
        actinv_bin = self._actinv_bin()
        with tempfile.TemporaryDirectory(prefix="nucleide-r2s-", dir=_disk_temp_root()) as temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = demo.run_demo(
                    DEMO_SOURCE, 300.0, Path(temporary) / "output", actinv_bin
                )
        self.assertEqual(result, 0, output.getvalue())

        lines = output.getvalue().splitlines()
        self.assertIn("8 cells, group grid length", "\n".join(lines))

        max_err_line = next(line for line in lines if line.startswith("max relative error"))
        max_err = float(max_err_line.split(":")[1].strip())
        self.assertLessEqual(max_err, 1e-9)

        total_line = next(line for line in lines if line.startswith("total declared"))
        tag_line = next(line for line in lines if line.startswith("nucleide r2s"))
        total = float(total_line.split(":")[1].strip())
        tagged = float(tag_line.split(":")[1].strip())
        self.assertAlmostEqual(total, tagged, delta=1e-6 * max(1.0, abs(total)))

    def test_demo_nonunit_volume(self):
        configured = os.environ.get("ACTINV_BIN")
        if not configured:
            self.skipTest("set ACTINV_BIN to test the ALARA exporter")
        records = [json.loads(line) for line in DEMO_SOURCE.read_text().splitlines()]
        for record in records:
            if record["record"] == "cell":
                record["volume_cm3"] *= 2
                lo, hi = record["bounds_cm"][0]
                record["bounds_cm"][0] = [lo, lo + 2 * (hi - lo)]

        with tempfile.TemporaryDirectory(prefix="nucleide-volume-", dir=_disk_temp_root()) as temporary:
            source = Path(temporary) / "source.ndjson"
            source.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = demo.run_demo(source, 300.0, Path(temporary) / "output", configured)
        self.assertEqual(result, 0, output.getvalue())
        lines = output.getvalue().splitlines()
        total = float(next(line for line in lines if line.startswith("total declared")).split(":")[1])
        tagged = float(next(line for line in lines if line.startswith("nucleide r2s")).split(":")[1])
        self.assertAlmostEqual(total, tagged, delta=1e-6 * abs(total))


if __name__ == "__main__":
    unittest.main()
