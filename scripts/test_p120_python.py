#!/usr/bin/env python3
"""Python object/native regression checks for P120 spectrum rebinning."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import actinv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from check_p120 import (  # noqa: E402
    check_analytic,
    close_vectors,
    make_fixture,
    scientific_steps,
    strip_timing,
    write_json,
)
from p105_budget_control import _run  # noqa: E402


class SpectrumBuilderTests(unittest.TestCase):
    def test_rebin_and_source_errors_are_optional_serialized_fields(self):
        ordinary = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0]
        )
        self.assertNotIn("rebin", ordinary)
        self.assertNotIn("relative_error", ordinary)

        opted_in = actinv.Spectrum(
            [1.0, 3.0], structure="custom", boundaries_eV=[1.0, 4.0, 16.0],
            total=1.0e24, descending=True, rebin="equal_lethargy",
            relative_error=[0.1, 0.2],
        )
        self.assertEqual(opted_in["rebin"], "equal_lethargy")
        # Relative errors use the same declared source-group order as values.
        self.assertEqual(opted_in["relative_error"], [0.1, 0.2])
        self.assertTrue(opted_in["descending"])
        self.assertEqual(opted_in["total"], 1.0e24)

    def test_omitted_keywords_leave_legacy_mapping_shape_unchanged(self):
        before = {
            "structure": "fispact-709",
            "flux_per_group": [1.0, 2.0],
            "descending": False,
            "total": 3.0,
        }
        self.assertEqual(
            dict(actinv.Spectrum([1.0, 2.0], total=3.0)),
            before,
        )


class SpectrumRebinPythonNativeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = ROOT / "target" / "preflight-tmp"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="p120-python-", dir=self.scratch)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.fine_spec = make_fixture(self.work / "fixture")

    def _solve(self, spec):
        problem = actinv.Problem(spec)
        return problem, actinv.solve(problem)

    def _assert_same_science(self, actual, expected):
        failures = close_vectors(
            scientific_steps(actual), scientific_steps(expected),
            rel=1e-12, abs_=1e-12, path="steps",
        )
        self.assertEqual(failures, [])

    def test_one_source_group_rebin_matches_fine_grid_and_analytic_kinetics(self):
        _, fine_result = self._solve(copy.deepcopy(self.fine_spec))
        coarse_spec = copy.deepcopy(self.fine_spec)
        coarse_spec["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        rebinned_problem, rebinned_result = self._solve(coarse_spec)
        self._assert_same_science(rebinned_result, fine_result)
        self.assertEqual(check_analytic(rebinned_result), [])

        diagnostics = rebinned_result["ledger"]["spectrum_rebin"]
        self.assertEqual(diagnostics["method"], "equal_lethargy")
        self.assertEqual(diagnostics["within_group_assumption"], "constant lethargy density")
        self.assertEqual(diagnostics["source_spectrum"], dict(coarse_spec["spectrum"]))
        self.assertAlmostEqual(diagnostics["source_total"], 1.0e24, delta=1e9)
        self.assertAlmostEqual(diagnostics["destination_total"], 1.0e24, delta=1e9)
        self.assertEqual(diagnostics["underflow"], 0.0)
        self.assertEqual(diagnostics["overflow"], 0.0)
        self.assertEqual(diagnostics["source_boundaries_eV"], [1.0, 16.0])
        self.assertEqual(diagnostics["destination_boundaries_eV"], [1.0, 4.0, 16.0])
        self.assertLessEqual(diagnostics["relative_closure_error"], 1e-12)
        self.assertEqual(
            rebinned_result["certificate"]["spectrum_rebin"], diagnostics
        )

        # The Python mapping and the native JSON entry point share the exact
        # solver path. Only per-run timings may differ.
        native = json.loads(actinv.run(rebinned_problem.to_json()))
        self.assertEqual(strip_timing(rebinned_result), strip_timing(native))

    def test_total_normalization_and_descending_order_preserve_source_identity(self):
        fine_spec = copy.deepcopy(self.fine_spec)
        fine_spec["spectrum"]["flux_per_group"] = [2.5e23, 7.5e23]
        fine_result = actinv.solve(actinv.Problem(fine_spec))

        descending_spec = copy.deepcopy(self.fine_spec)
        descending_spec["spectrum"] = actinv.Spectrum(
            [3.0, 1.0], structure="custom", boundaries_eV=[1.0, 4.0, 16.0],
            total=1.0e24, descending=True, rebin="equal_lethargy",
            relative_error=[0.1, 0.2],
        )
        descending_result = actinv.solve(actinv.Problem(descending_spec))
        self._assert_same_science(descending_result, fine_result)
        source = descending_result["ledger"]["spectrum_rebin"]["source_spectrum"]
        self.assertEqual(source["flux_per_group"], [3.0, 1.0])
        self.assertEqual(source["relative_error"], [0.1, 0.2])
        self.assertTrue(source["descending"])
        self.assertEqual(source["total"], 1.0e24)
        self.assertLessEqual(
            descending_result["ledger"]["spectrum_rebin"]["relative_closure_error"], 1e-12
        )

    def test_absent_option_retains_strict_custom_grid_refusal(self):
        strict_spec = copy.deepcopy(self.fine_spec)
        strict_spec["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0], total=1.0e24
        )
        with self.assertRaisesRegex(
            (ValueError, RuntimeError), "spectrum has .* groups|custom spectrum boundaries"
        ):
            actinv.solve(actinv.Problem(strict_spec))

    def test_python_and_cli_emit_the_same_rebinned_scientific_result(self):
        binary_text = os.environ.get("ACTINV_BIN")
        if not binary_text:
            self.skipTest("set ACTINV_BIN to run the bounded CLI parity check")
        binary = Path(binary_text).resolve()
        self.assertTrue(binary.is_file(), f"ACTINV_BIN does not name a file: {binary}")

        spec = copy.deepcopy(self.fine_spec)
        spec["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        problem, python_result = self._solve(spec)
        spec_path = self.work / "cli-problem.json"
        result_path = self.work / "cli-result.json"
        write_json(spec_path, json.loads(problem.to_json()))
        completed = _run(
            [str(binary), "run", str(spec_path), str(result_path)],
            cwd=ROOT, timeout_s=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(result_path.is_file() and result_path.stat().st_size > 0)
        cli_result = json.loads(result_path.read_text())
        self.assertEqual(
            close_vectors(
                scientific_steps(cli_result), scientific_steps(python_result),
                rel=1e-12, abs_=1e-12, path="steps",
            ),
            [],
        )
        self.assertEqual(
            cli_result["ledger"]["spectrum_rebin"],
            python_result["ledger"]["spectrum_rebin"],
        )

    def test_invalid_options_and_boundaries_are_refused(self):
        bad_method = copy.deepcopy(self.fine_spec)
        bad_method["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0],
            total=1.0e24, rebin="nearest",
        )
        with self.assertRaisesRegex(ValueError, "unknown variant.*nearest.*equal_lethargy"):
            actinv.Problem(bad_method).validate()

        malformed = copy.deepcopy(self.fine_spec)
        malformed["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 4.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        with self.assertRaisesRegex(ValueError, "boundaries_eV"):
            actinv.Problem(malformed).validate()

        zero_edge = copy.deepcopy(self.fine_spec)
        zero_edge["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[0.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        with self.assertRaisesRegex(ValueError, "positive|boundary"):
            actinv.Problem(zero_edge).validate()

        outside = copy.deepcopy(self.fine_spec)
        outside["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[0.5, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        with self.assertRaisesRegex((ValueError, RuntimeError), "range|outside|library"):
            actinv.solve(actinv.Problem(outside))

    def test_rebin_rejects_non_neutron_and_step_spectrum_inputs(self):
        charged = copy.deepcopy(self.fine_spec)
        charged["projectile"] = "proton"
        charged["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        with self.assertRaisesRegex(ValueError, "rebin.*neutron|neutron.*rebin"):
            actinv.Problem(charged).validate()

        step_override = copy.deepcopy(self.fine_spec)
        step_override["spectrum"] = actinv.Spectrum(
            [1.0], structure="custom", boundaries_eV=[1.0, 16.0],
            total=1.0e24, rebin="equal_lethargy",
        )
        step_override["schedule"][0]["spectrum"] = {
            "structure": "custom", "boundaries_eV": [1.0, 16.0], "flux_per_group": [1.0]
        }
        with self.assertRaisesRegex(ValueError, "step.*spectrum|spectrum.*step"):
            actinv.Problem(step_override).validate()


if __name__ == "__main__":
    unittest.main()
