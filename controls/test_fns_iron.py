#!/usr/bin/env python3
"""Unit/time/coverage and failure-path controls for the public iron example."""
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import fns_iron as benchmark


def fixture():
    rows = benchmark.measurements("\n".join(f"{i} 2 0.1" for i in range(1, 21)))
    steps = [{"t_s": 300}]
    steps += [{"t_s": 300 + r["cooling_seconds"], "flux": 0,
               "heat_W_per_g": {"total": 2e-6, "alpha": 0, "beta": 0.5e-6, "gamma": 1.5e-6}}
              for r in rows]
    return rows, {"steps": steps}


class IronTests(unittest.TestCase):
    def test_minutes_and_incremental_schedule(self):
        rows, _ = fixture()
        steps = benchmark.schedule(rows)
        self.assertEqual(len(steps), 21)
        self.assertEqual(steps[0], {"dt": "300 s", "flux": 1.0})
        self.assertTrue(all(float(s["dt"].split()[0]) == 60 and s["flux"] == 0 for s in steps[1:]))

    def test_decimal_time_differences(self):
        steps = benchmark.schedule([{"cooling_seconds": 66.0}, {"cooling_seconds": 82.2}])
        self.assertEqual(steps[-1]["dt"], "16.2 s")

    def test_units_and_score_identities(self):
        rows, result = fixture()
        comparison, summary = benchmark.score(result, rows)
        self.assertEqual(summary["geometric_mean_CE"], 1)
        self.assertEqual(summary["points_inside_reported_error_bars"], 20)
        self.assertTrue(all(r["calculated_microW_per_g"] == 2 and r["relative_residual"] == 0 for r in comparison))
        result["steps"][1]["heat_W_per_g"] = {"total": 1e-6, "alpha": 0, "beta": 0, "gamma": 1e-6}
        comparison, summary = benchmark.score(result, rows)
        self.assertEqual(comparison[0]["calculated_over_measured"], 0.5)
        self.assertEqual(comparison[0]["residual_over_reported_error"], -10)
        self.assertAlmostEqual(summary["geometric_mean_CE"], 0.5**(1/20))

    def test_bad_measurements_rejected(self):
        good = "\n".join(f"{i} 2 0.1" for i in range(1, 21))
        for bad in (good.replace("1 2 0.1", "1 nan 0.1", 1), good + "\n21 2 0.1",
                    good.replace("2 2 0.1", "1 2 0.1", 1), good.replace("1 2 0.1", "1 2 0", 1)):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValueError):
                benchmark.measurements(bad)

    def test_time_shift_nonfinite_missing_point_and_heat_closure(self):
        for mutation in ("time", "nonfinite", "missing", "closure", "flux"):
            rows, result = fixture()
            if mutation == "time":
                result["steps"][1]["t_s"] -= 300
            elif mutation == "nonfinite":
                result["steps"][1]["heat_W_per_g"]["total"] = math.nan
            elif mutation == "missing":
                result["steps"].pop()
            elif mutation == "closure":
                result["steps"][1]["heat_W_per_g"]["alpha"] = 1
            else:
                result["steps"][1]["flux"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                benchmark.score(result, rows)

    def test_hash_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_text("changed")
            with self.assertRaises(ValueError):
                benchmark.pinned(path, "0" * 64)

    def test_download_identification_and_verification(self):
        content = b"public archive fixture"
        case = {"archive_url": "https://example.invalid/fns.zip",
                "archive_sha256": hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fns.zip"
            with patch.object(benchmark.urllib.request, "urlopen", return_value=io.BytesIO(content)) as fetch:
                benchmark.fetch_archive(path, case)
                request = fetch.call_args.args[0]
                self.assertTrue(request.get_header("User-agent").startswith("ACTINV-FNS-Iron/"))
                self.assertEqual(fetch.call_args.kwargs["timeout"], 60)
            self.assertEqual(path.read_bytes(), content)
            path.unlink()
            with patch.object(benchmark.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaises(ValueError):
                    benchmark.fetch_archive(path, case)
            self.assertFalse(path.exists())

    def test_timeout_and_child_failure_cannot_reuse_stale_result(self):
        for error in (subprocess.TimeoutExpired("actinv", 180), subprocess.CalledProcessError(1, "actinv")):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                stale = output / "cli.json"
                stale.write_text('{"stale": true}')
                with patch.object(benchmark.subprocess, "run", side_effect=error) as child:
                    with self.assertRaises(type(error)):
                        benchmark.execute(Path("/unused/actinv"), {}, output)
                    self.assertEqual(child.call_args.kwargs["timeout"], 180)
                    self.assertTrue(child.call_args.kwargs["check"])
                self.assertEqual(list(output.iterdir()), [stale])

    def test_recorded_mutations(self):
        original = json.loads(benchmark.RECORDED.read_text())
        benchmark.check_recorded(original, original)
        for mutation in ("heat", "units", "verdict", "summary", "hash"):
            changed = copy.deepcopy(original)
            if mutation == "heat":
                changed["comparison"][0]["calculated_microW_per_g"] *= 0.5
            elif mutation == "units":
                changed["comparison"][0]["cooling_seconds"] /= 60
            elif mutation == "verdict":
                changed["physical_agreement"] = "validation_passed"
            elif mutation == "summary":
                changed["summary"]["points"] = 19
            else:
                changed["input_hashes"]["case"] = "0"*64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                benchmark.check_recorded(changed, original)


if __name__ == "__main__":
    unittest.main()
