"""P108 exact-oracle and seal identity regressions; no application launch."""
import copy
import contextlib
import io
import json
import math
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import check_p108
import check_p108_verdict
import p108_composition_control as oracle


class CompositionOracleTests(unittest.TestCase):
    def test_all_frozen_class_envelopes_match_independent_labels(self):
        cases = oracle.generate_cases()
        rows = json.loads(oracle.PACK.read_text(encoding="utf-8"))["rows"]
        result = oracle.expected_labels(cases, rows)
        self.assertTrue(result["pass"])
        self.assertEqual(len(result["labels"]), 12)
        self.assertEqual(result["opposite_products_every_vertex_fraction"], "0.9")

    def test_nonbinary_vertices_satisfy_exact_composition_equality(self):
        weights = {
            "Fe": {"lower_wt_percent": 19.9, "upper_wt_percent": 99.9},
            "Si": {"lower_wt_percent": 0.1, "upper_wt_percent": 80.1},
        }
        points = oracle.vertices(weights)
        self.assertTrue(points)
        self.assertTrue(all(sum(point.values()) == Decimal(100) for point in points))
        infeasible = {"Fe": {"lower_wt_percent": 33.3, "upper_wt_percent": 33.3},
                      "Nb": {"lower_wt_percent": 33.3, "upper_wt_percent": 33.3},
                      "Si": {"lower_wt_percent": 33.3, "upper_wt_percent": 33.3}}
        with self.assertRaises(ValueError):
            oracle.vertices(infeasible)

    def test_projection_enclosure_rejects_endpoint_witness_and_dual_mutations(self):
        component = oracle.generate_cases()[0]
        target = component["targets"][0]
        weights = component["composition_wt_percent_bounds"]
        coefficients = {symbol: response.get("C14", 0.0)
                        for symbol, response in target["element_activity_bq_per_g"].items()}
        exact = oracle.scalar_extrema(weights, coefficients, component["mass_g"])
        value = float(exact["minimum"])
        projection = {
            "lower_bq": math.nextafter(value, -math.inf),
            "upper_bq": math.nextafter(value, math.inf),
            "min_witness_wt_percent": {"Nb": 100.0},
            "max_witness_wt_percent": {"Nb": 100.0},
            "lower_dual_lambda": 0.0,
            "upper_dual_lambda": 0.0,
        }
        self.assertEqual(oracle.verify_projection(projection, weights, coefficients, 1.0), [])
        bad_endpoint = copy.deepcopy(projection)
        bad_endpoint["upper_bq"] = 0.0
        self.assertTrue(oracle.verify_projection(bad_endpoint, weights, coefficients, 1.0))
        bad_witness = copy.deepcopy(projection)
        bad_witness["max_witness_wt_percent"]["Nb"] = 0.0
        self.assertTrue(oracle.verify_projection(bad_witness, weights, coefficients, 1.0))
        bad_dual = copy.deepcopy(projection)
        bad_dual["lower_dual_lambda"] = 1e30
        self.assertTrue(oracle.verify_projection(bad_dual, weights, coefficients, 1.0))


class CompositionSealTests(unittest.TestCase):
    def test_g0_write_replay_and_mutated_seal_rejection(self):
        report = {"schema": "actinv-p108-composition-g0-1", "phase": "P108", "pass": True,
                  "case_fixture_sha256": "fixture"}
        with tempfile.TemporaryDirectory(prefix="p108-seal-") as temp:
            seal = Path(temp) / "g0.json"
            with patch.object(check_p108, "_g0_base", side_effect=lambda: copy.deepcopy(report)), \
                 patch.object(check_p108, "P108_G0", seal):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check_p108.g0(seal=True), 0)
                    self.assertEqual(check_p108.g0(no_write=True), 0)
                    self.assertEqual(check_p108.g0(no_write=True), 0)
                altered = json.loads(seal.read_text(encoding="utf-8"))
                altered["case_fixture_sha256"] = "mutated"
                seal.write_text(json.dumps(altered), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check_p108.g0(no_write=True), 1)

    def test_g1_persisted_replay_rejects_mutation(self):
        report = {"schema": "actinv-p108-composition-g1-1", "pass": True, "failures": []}
        with tempfile.TemporaryDirectory(prefix="p108-g1-replay-") as temp:
            path = Path(temp) / "g1.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_p108._persist(copy.deepcopy(report), path, no_write=False), 0)
                self.assertEqual(check_p108._persist(copy.deepcopy(report), path, no_write=True), 0)
                self.assertEqual(check_p108._persist(copy.deepcopy(report), path, no_write=True), 0)
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["pass"] = False
            path.write_text(json.dumps(changed), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_p108._persist(copy.deepcopy(report), path, no_write=True), 1)

    def test_p108_quality_requires_integer_green_gates_and_source_set(self):
        sources = {path: check_p108.sha256(check_p108.ROOT / path)
                   for path in check_p108_verdict.REQUIRED_SOURCES}
        base = {"schema": "actinv-p108-quality-1", "phase": "P108", "pass": True,
                "gates": {name: {"exit_code": 0} for name in check_p108_verdict.REQUIRED_GATES},
                "resource_limits": check_p108_verdict.RESOURCES,
                "workspace_tests": {"passed": 1, "failed": 0},
                "production_rust_sha256": sources}
        self.assertTrue(check_p108_verdict._quality(base, None)[0])

        bad_gate = copy.deepcopy(base)
        bad_gate["gates"][next(iter(check_p108_verdict.REQUIRED_GATES))]["exit_code"] = False
        self.assertFalse(check_p108_verdict._quality(bad_gate, None)[0])
        bool_passed = copy.deepcopy(base)
        bool_passed["workspace_tests"]["passed"] = True
        self.assertFalse(check_p108_verdict._quality(bool_passed, None)[0])
        bool_failed = copy.deepcopy(base)
        bool_failed["workspace_tests"]["failed"] = False
        self.assertFalse(check_p108_verdict._quality(bool_failed, None)[0])
        missing_source = copy.deepcopy(base)
        missing_source["production_rust_sha256"].pop("crates/actinv-core/src/waste_composition.rs")
        self.assertFalse(check_p108_verdict._quality(missing_source, None)[0])


if __name__ == "__main__":
    unittest.main()
