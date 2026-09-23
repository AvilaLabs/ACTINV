#!/usr/bin/env python3
"""Independent identities and rejection tests for the reduced-chain control."""

import copy
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import fusion_isotope_chain as chain


class ReducedChainTests(unittest.TestCase):
    def test_single_decay_and_stable_sink(self):
        actual = chain.analytic([100.0, 0.0], [0.2, 0.0], [0.2], 3.0)
        self.assertAlmostEqual(actual[0], 100 * math.exp(-0.6), places=12)
        self.assertAlmostEqual(sum(actual), 100.0, places=12)

    def test_two_step_daughter_and_nonunit_branch(self):
        # Ordinary two-member Bateman formula, independently written.
        actual = chain.analytic([100.0, 0.0], [0.2, 0.5], [0.1], 3.0)
        expect = 100 * 0.1 * (math.exp(-0.6) - math.exp(-1.5)) / 0.3
        self.assertAlmostEqual(actual[1], expect, places=12)

    def test_cooling_zero_rate_keeps_feed_and_all_initial_states(self):
        initial = [100.0, 20.0, 3.0, 4.0, 5.0]
        rates = [0.0, 0.01, 0.02, 0.03, 0.0]
        self.assertEqual(chain.analytic(initial, rates, rates[:-1], 0.0), initial)
        after = chain.analytic(initial, rates, rates[:-1], 10.0)
        self.assertEqual(after[0], initial[0])
        self.assertAlmostEqual(sum(after), sum(initial), places=12)

    def test_wrong_transfer_is_detected(self):
        good = chain.analytic([100.0, 0.0], [0.2, 0.0], [0.2], 3.0)
        bad = chain.analytic([100.0, 0.0], [0.2, 0.0], [0.1], 3.0)
        tolerance = {"absolute_atoms": 1e-8, "relative": 1e-9, "conservation_relative": 1e-10}
        self.assertFalse(chain.score(bad, good, 100.0, tolerance)["passed"])
        with self.assertRaises(ValueError):
            chain.score([float("nan"), 0.0], good, 100.0, tolerance)

    def test_normalization_uses_all_emitted_neutrons(self):
        case = json.loads(chain.CASE.read_text())
        reference = json.loads(chain.REFERENCE.read_text())
        feed, production, rates = chain.model(case, reference)
        self.assertEqual(production, 2.34e17)
        self.assertAlmostEqual(rates[0] * feed / production, 1.0)
        # No half-space factor may be applied a second time.
        reference["published"]["target_facing_fraction"] = 0.123
        self.assertEqual(chain.model(case, reference)[1], production)

    def test_matrix_conserves_each_column(self):
        rates = [0.1, 0.2, 0.3, 0.4, 0.0]
        for column in range(5):
            self.assertEqual(sum(v for _, j, v in chain.matrix(rates) if j == column), 0.0)

    def test_timeout_propagates_and_temporary_files_are_removed(self):
        # run() owns child killing/reaping; no custom asynchronous cancellation
        # path exists. Ensure its timeout is mandatory and errors cannot become
        # cached success, even when a stale output exists beside the work area.
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            stale = work / "output.txt"
            stale.write_text("1\n2\n")
            with patch.object(chain.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 60)) as child:
                with self.assertRaises(subprocess.TimeoutExpired):
                    chain.probe_step(Path("/unused/probe"), work, [1.0, 0.0], [0.1, 0.0], 1.0)
                self.assertEqual(child.call_args.kwargs["timeout"], 60)
                self.assertTrue(child.call_args.kwargs["check"])
            self.assertEqual(list(work.iterdir()), [stale])

    def test_false_reproduction_claim_rejected(self):
        with self.assertRaises(ValueError):
            chain.verify({"overall_status": "reproduced"})

    def test_recorded_mutations_rejected(self):
        path = chain.RECEIPT
        original = json.loads(path.read_text())
        chain.check_recorded(original, original)
        for mutation in ("inventory", "reference", "normalization", "omissions"):
            changed = copy.deepcopy(original)
            if mutation == "inventory":
                changed["histories"][0]["atoms"][1] *= 0.5
            elif mutation == "reference":
                changed["table6_comparison"][0]["published_th229_ci"] = 542
            elif mutation == "normalization":
                changed["initial_th229_production_atoms_per_s"] *= 0.5
            else:
                changed["omissions"] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                chain.check_recorded(changed, original)


if __name__ == "__main__":
    unittest.main()
