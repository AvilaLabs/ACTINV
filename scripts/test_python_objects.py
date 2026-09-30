"""Regression checks for the mapping API against the unchanged native JSON API."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import actinv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controls"))
from p11_fixtures import make_fixture, specification


def _strip_timing(value):
    """Recursively drop the `ms` / `elapsed_ms` timing keys from a decoded
    JSON document, so two runs of the same computation compare equal."""
    if isinstance(value, dict):
        return {k: _strip_timing(v) for k, v in value.items()
                if k not in ("ms", "elapsed_ms")}
    if isinstance(value, list):
        return [_strip_timing(v) for v in value]
    return value


def _budget_fixture(root):
    """An actinv-budget-1 mapping whose base_spec is an embedded fixture
    spec (P11 synthetic library), so it runs without the real nuclear data.
    Elements are real (natural-abundance) but only Fe56 has cross sections
    in the tiny synthetic library, so every clearance-index response comes
    back "no clearance-index response" — this is about binding parity
    between the mapping and path forms, not about physics."""
    base = specification(make_fixture(root), mode="coupled", cram_order=16,
                          uncertainty=False)
    return {
        "schema": "actinv-budget-1",
        "base_spec": base,
        "balance": "Fe",
        "matrix": {"Cr": 1.0},
        "impurities": {"Co": 0.01, "Ni": 0.02},
        "targets": [2, 4],
    }


class Objects(unittest.TestCase):
    def test_example_and_builders(self):
        problem = actinv.Problem.example(Path("missing-data"))
        problem["material"] = actinv.Material({"Fe": 100})
        problem["schedule"] = actinv.Schedule().irradiate("5m").cool("1h")
        problem["spectrum"] = actinv.Spectrum([1] * 709, total=1e12)
        self.assertIn("709 groups", problem.validate())
        self.assertEqual(problem["schedule"][1]["flux"], 0)

    def test_paths_and_exact_result_parity(self):
        with tempfile.TemporaryDirectory(prefix="actinv-python-objects-") as directory:
            root = Path(directory)
            problem = actinv.Problem(specification(make_fixture(root), mode="trace", cram_order=16))
            expected = json.loads(actinv.run(problem.to_json()))
            actual = problem.run()
            # Wall time is measured independently; all scientific output must match.
            self.assertEqual({k: v for k, v in actual.items() if k != "ms"},
                             {k: v for k, v in expected.items() if k != "ms"})
            self.assertEqual(actual.ledger, expected["ledger"])
            self.assertEqual(actual.certificate, expected["certificate"])
            self.assertEqual(actual.activity()[0][1], sum(expected["steps"][0]["activity_Bq_per_g"].values()))
            self.assertEqual(actual.heat()[0][1], expected["steps"][0]["heat_W_per_g"]["total"])
            problem["library"]["path"] = Path(problem["library"]["path"])
            path = root / "saved.json"
            problem.save(path)
            self.assertEqual({k: v for k, v in actinv.solve(path).items() if k != "ms"},
                             {k: v for k, v in expected.items() if k != "ms"})
            actual.save(root / "result.json")
            self.assertEqual(json.loads((root / "result.json").read_text()), actual)

    def test_budget_schema_error_raises(self):
        bad = {"schema": "nope", "base_spec": {}, "balance": "Fe",
               "impurities": {"Co": 0.01}, "targets": [1]}
        with self.assertRaises(RuntimeError):
            actinv.budget(bad)

    def test_budget_mapping_and_path_parity(self):
        with tempfile.TemporaryDirectory(prefix="actinv-python-budget-") as directory:
            root = Path(directory)
            doc = _budget_fixture(root)
            from_mapping = actinv.budget(doc)
            self.assertEqual(from_mapping["schema"], "actinv-budget-result-1")
            self.assertTrue(from_mapping["verification"]["verified"])

            path = root / "budget.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            from_path = actinv.budget(path)

            self.assertEqual(_strip_timing(from_mapping), _strip_timing(from_path))

    def test_budget_file_hash_is_the_file_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix="actinv-python-budget-") as directory:
            root = Path(directory)
            path = root / "budget.json"
            # Formatting json.dumps would not reproduce: the hash must cover these exact bytes.
            path.write_bytes(json.dumps(_budget_fixture(root), indent=3).encode() + b"\n\n")
            doc = actinv.budget(path, verify=False)
            self.assertEqual(doc["budget_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
