"""Regression checks for the mapping API against the unchanged native JSON API."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import actinv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controls"))
from p11_fixtures import make_fixture, specification


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


if __name__ == "__main__":
    unittest.main()
