"""Regression checks for the mapping API against the unchanged native JSON API."""
import contextlib
import json
import os
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


# Smallest real waste inputs: artificial single-nuclide components, as the
# P105 / P107 / P108 checkers build them. None needs a nuclear-data library.
_C14 = {"z": 6, "half_life_s": 3.0e8, "alpha_emitting": False}
_RUN_RESULT = {"entry_point": "cli", "mode": "coupled",
               "steps": [{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 1.0}}]}


def _classify_spec():
    return {
        "schema": "actinv-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "input": "run.json", "targets": [1], "nuclide_properties": {"C14": _C14},
        "components": [{"id": "part", "mass_g": 1.0, "displaced_volume_cm3": 1.0,
                        "waste_type": "general",
                        "external_tritium": {"status": "not_applicable"}}],
    }


def _bounds_spec():
    return {
        "schema": "actinv-waste-bounds-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "components": [{
            "id": "part", "mass_g": 1.0, "displaced_volume_cm3": 1.0,
            "waste_type": "general", "nuclide_properties": {"C14": _C14},
            "external_tritium": {"status": "not_applicable"},
            "targets": [{"step": 1, "t_s": 0.0,
                         "activity_bounds_bq": {"C14": {"lower_bq": 0.0, "upper_bq": 1.0}},
                         "inventory_coverage": "complete", "unbounded_inventory_reasons": [],
                         "bounds_source": "artificial", "bounds_assumptions": "rectangular"}]}],
    }


def _composition_spec():
    return {
        "schema": "actinv-waste-composition-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "response_model": "fixed_rate_affine_activity",
        "components": [{
            "id": "part", "mass_g": 1.0, "displaced_volume_cm3": 1.0,
            "waste_type": "general", "nuclide_properties": {"C14": _C14},
            "external_tritium": {"status": "not_applicable"},
            "composition_wt_percent_bounds": {
                "Si": {"lower_wt_percent": 0.0, "upper_wt_percent": 50.0},
                "Fe": {"lower_wt_percent": 50.0, "upper_wt_percent": 100.0}},
            "targets": [{"step": 1, "t_s": 0.0,
                         "element_activity_bq_per_g": {"Si": {}, "Fe": {"C14": 1.0}},
                         "inventory_coverage": "complete", "unbounded_inventory_reasons": [],
                         "bounds_source": "artificial",
                         "bounds_assumptions": "fixed affine response model"}]}],
    }


def _intrusion_spec():
    waste = _classify_spec()
    waste["components"][0].update(id="whole-container", mass_g=1000.0,
                                  displaced_volume_cm3=128.2)
    return {
        "schema": "actinv-waste-intrusion-screen-spec-1",
        "draft_rules": "us-nrc-nureg1556-v22-draft-2026-02-v1",
        "package_basis": "single_component_container", "waste_form": "equipment",
        "inventory_coverage": "complete", "unbounded_inventory_reasons": [],
        "waste_spec": waste,
    }


def _package_spec():
    return {
        "schema": "actinv-waste-package-spec-1", "rules": "us-nrc-10cfr61.55-v1",
        "targets": [1], "nuclide_properties": {"C14": _C14},
        "items": [{"id": "part", "waste_type": "contaminated_material", "mass_g": 10.0,
                   "displaced_volume_cm3": 100.0,
                   "activity": {"declared_bq": {"1": {"C14": 1.0}}}}],
    }


def _text(spec):
    return json.dumps(spec, allow_nan=False)


class _Captured:
    data = b""


@contextlib.contextmanager
def _captured_fds():
    """Redirect file descriptors 1 and 2 into a temporary file; whatever any
    code (Rust included) writes there ends up in `.data` on exit."""
    sys.stdout.flush()
    sys.stderr.flush()
    captured = _Captured()
    with tempfile.TemporaryFile() as sink:
        saved = (os.dup(1), os.dup(2))
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            yield captured
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            sink.seek(0)
            captured.data = sink.read()


class Waste(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(
            tempfile.TemporaryDirectory(prefix="actinv-python-waste-")))
        # The native binding makes its private directory under TMPDIR; point it
        # at an empty directory so a leftover is visible.
        self.scratch = Path(stack.enter_context(
            tempfile.TemporaryDirectory(prefix="actinv-python-waste-tmp-")))
        saved = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(self.scratch)
        stack.callback(lambda: os.environ.pop("TMPDIR", None) if saved is None
                       else os.environ.__setitem__("TMPDIR", saved))
        (self.root / "run.json").write_text(_text(_RUN_RESULT), encoding="utf-8")

    def assertClean(self, *names):
        """Only the named files remain in the base directory; no private
        temporary directory remains under TMPDIR."""
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(names))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def path_for(self, name, spec):
        path = self.root / name
        path.write_text(_text(spec), encoding="utf-8")
        return path

    def test_namespace(self):
        self.assertEqual(
            sorted(n for n in vars(actinv.waste) if not n.startswith("_")),
            ["bounds", "budget", "budget_uncertainty", "classify", "composition",
             "composition_solve", "intrusion_screen", "package", "summary", "uncertainty"])
        self.assertTrue(callable(actinv.twin))
        self.assertIn("waste", actinv.__all__)
        self.assertIn("twin", actinv.__all__)
        # The P79 impurity budget is a different function.
        self.assertIsNot(actinv.budget, actinv.waste.budget)

    def test_dict_and_path_forms_match_byte_for_byte(self):
        cases = [("classify", _classify_spec(), "actinv-waste-result-1"),
                 ("bounds", _bounds_spec(), "actinv-waste-bounds-result-1"),
                 ("composition", _composition_spec(), "actinv-waste-composition-result-1"),
                 ("intrusion_screen", _intrusion_spec(), "actinv-waste-intrusion-screen-result-1"),
                 ("package", _package_spec(), "actinv-waste-package-1")]
        for name, spec, schema in cases:
            with self.subTest(name):
                function = getattr(actinv.waste, name)
                path = self.path_for(f"{name}.json", spec)
                from_path = function(path, text=True)
                from_dict = function(spec, base_dir=self.root, text=True)
                self.assertEqual(from_dict, from_path)
                self.assertEqual(function(str(path)), json.loads(from_path))
                self.assertEqual(json.loads(from_path)["schema"], schema)
                self.assertClean("run.json", f"{name}.json")
                path.unlink()

    def test_dict_form_resolves_paths_against_base_dir(self):
        other = self.root / "other"
        other.mkdir()
        run = dict(_RUN_RESULT, steps=[{"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 2.0}}])
        (other / "run.json").write_text(_text(run), encoding="utf-8")
        here = actinv.waste.classify(_classify_spec(), base_dir=self.root)
        there = actinv.waste.classify(_classify_spec(), base_dir=other)
        by_path = actinv.waste.classify(self.path_for("spec.json", _classify_spec()))
        self.assertEqual(here["input_sha256"], by_path["input_sha256"])
        self.assertNotEqual(here["input_sha256"], there["input_sha256"])
        self.assertEqual(list(other.iterdir()), [other / "run.json"])

    def test_refusal_raises_value_error_and_leaves_nothing(self):
        bad = _bounds_spec()
        bad["components"][0]["targets"][0]["activity_bounds_bq"]["C14"]["lower_bq"] = -1.0
        with self.assertRaises(ValueError) as caught:
            actinv.waste.bounds(bad, base_dir=self.root)
        self.assertTrue(str(caught.exception))
        self.assertClean("run.json")
        path = self.path_for("bad.json", bad)
        with self.assertRaises(ValueError) as from_path:
            actinv.waste.bounds(path)
        self.assertEqual(str(from_path.exception), str(caught.exception))
        self.assertClean("run.json", "bad.json")
        with self.assertRaises(ValueError):
            actinv.waste.classify(self.root / "missing.json")
        with self.assertRaises(ValueError):
            actinv.waste.composition(dict(_composition_spec(), schema="nope"),
                                     base_dir=self.root)
        self.assertClean("run.json", "bad.json")

    def test_unserializable_mapping_leaves_nothing(self):
        with self.assertRaises(ValueError):
            actinv.waste.classify({"schema": float("nan")}, base_dir=self.root)
        self.assertClean("run.json")

    def test_calls_are_quiet(self):
        spec = _classify_spec()
        path = self.path_for("spec.json", spec)
        bad = _bounds_spec()
        bad["components"][0]["targets"][0]["activity_bounds_bq"]["C14"]["lower_bq"] = -1.0
        with _captured_fds() as captured:
            actinv.waste.classify(path)
            actinv.waste.classify(spec, base_dir=self.root, text=True)
            actinv.waste.bounds(_bounds_spec(), base_dir=self.root)
            actinv.waste.composition(_composition_spec(), base_dir=self.root)
            with self.assertRaises(ValueError):
                actinv.waste.bounds(bad, base_dir=self.root)
        self.assertEqual(captured.data, b"")
        self.assertClean("run.json", "spec.json")

    def test_summary_flattens_classification(self):
        result = actinv.waste.classify(self.path_for("spec.json", _classify_spec()))
        rows = actinv.waste.summary(result)
        target = result["components"][0]["targets"][0]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["component"], "part")
        self.assertEqual(row["step"], 1)
        self.assertEqual(row["class"], target["class"])
        self.assertEqual(row["status"], target["status"])
        self.assertEqual(row["coverage"], target["coverage"])
        binding = target["evaluation"]["binding_constraints"]
        self.assertEqual(row["binding_constraint_count"], len(binding))
        if binding:
            self.assertEqual(row["binding_table"], binding[0]["table"])
            self.assertEqual(row["binding_column"], binding[0]["column"])
            self.assertEqual(row["binding_normalized_margin"], binding[0]["normalized_margin"])
            self.assertTrue(set(binding[0]["contributors"]) <= set(row["binding_nuclides"]))
        self.assertEqual(actinv.waste.summary({"classification": result}), rows)
        self.assertEqual(actinv.waste.summary({"waste_classification": result}), rows)
        with self.assertRaises(ValueError):
            actinv.waste.summary({"schema": "something-else"})

    def test_summary_of_intrusion_screen_classification(self):
        screen = actinv.waste.intrusion_screen(_intrusion_spec(), base_dir=self.root)
        rows = actinv.waste.summary(screen["classification"])
        self.assertEqual([r["component"] for r in rows], ["whole-container"])
        self.assertEqual(actinv.waste.summary(screen), rows)

    def test_package_result_and_refusal(self):
        result = actinv.waste.package(_package_spec(), base_dir=self.root)
        self.assertEqual(result["schema"], "actinv-waste-package-1")
        self.assertEqual(result["btp"], "us-nrc-ca-btp-2015-rev1-v1")
        self.assertEqual(result["steps"][0]["package_class"], "A")
        self.assertEqual(result["steps"][0]["groups"][0]["waste_type"], "contaminated_material")
        self.assertIn("alternative approaches (BTP 3.8)", result["not_claimed"])
        self.assertClean("run.json")
        bad = _package_spec()
        bad["items"][0]["waste_type"] = "rubble"
        with self.assertRaises(ValueError) as caught:
            actinv.waste.package(bad, base_dir=self.root)
        self.assertIn("unknown waste_type", str(caught.exception))
        self.assertClean("run.json")
        path = self.path_for("bad-package.json", bad)
        with self.assertRaises(ValueError) as from_path:
            actinv.waste.package(path)
        self.assertEqual(str(from_path.exception), str(caught.exception))
        self.assertClean("run.json", "bad-package.json")

    def test_uncertainty_refuses_too_few_samples_before_reading_data(self):
        spec = {
            "schema": "actinv-waste-uncertainty-spec-1",
            "problem": "missing-problem.json",
            "rules": "us-nrc-10cfr61.55-v1",
            "targets": [1],
            "nuclide_properties": "from_decay_data",
            "components": [{
                "id": "part", "mass_g": 2.5, "displaced_volume_cm3": 1.0,
                "waste_type": "activated_metal",
                "external_tritium": {"status": "not_applicable"},
            }],
            "sampling": {"channels": {"decay_constants": True}, "samples": 5, "seed": 1},
        }
        with self.assertRaises(ValueError) as caught:
            actinv.waste.uncertainty(spec, base_dir=self.root)
        self.assertIn("at least 20", str(caught.exception))
        self.assertClean("run.json")

    @staticmethod
    def _budget_uncertainty_spec(**sampling):
        return {
            "schema": "actinv-waste-budget-uncertainty-spec-1",
            "budget": {
                "schema": "actinv-waste-budget-1",
                "base_spec": {
                    "spec": "actinv-spec-1",
                    "library": {"path": "missing-activation.npz"},
                    "decay": {"primary": "missing-decay.endf"},
                    "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
                    "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0],
                                 "flux_per_group": [1.0]},
                    "schedule": [{"dt": "1 s", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}],
                },
                "balance": "Fe",
                "matrix": {"Cr": 18.0},
                "impurities": {"Co": 0.01},
                "targets": [2],
                "rules": "us-nrc-10cfr61.55-v1",
                "target_class": "A",
                "mass_g": 2.5,
                "displaced_volume_cm3": 1.0,
                "waste_type": "activated_metal",
                "nuclide_properties": {},
                "external_tritium": {"status": "not_applicable"},
            },
            "sampling": dict({"channels": {"decay_constants": True}, "samples": 100, "seed": 1},
                             **sampling),
        }

    def test_budget_uncertainty_refuses_before_reading_data(self):
        refusals = [
            (dict(samples=5), "samples must be between 20 and 4096"),
            (dict(samples=50), "at least 72 are needed"),
            (dict(channels={"composition_rel_std": {"Fe": 0.1}}),
             "composition channel is refused"),
            (dict(channels={}), "sampling enables no channel"),
        ]
        for sampling, message in refusals:
            with self.subTest(message):
                with self.assertRaises(ValueError) as caught:
                    actinv.waste.budget_uncertainty(
                        self._budget_uncertainty_spec(**sampling), base_dir=self.root)
                self.assertIn(message, str(caught.exception))
                self.assertClean("run.json")
        spec = self._budget_uncertainty_spec(samples=100, keep_samples="kept")
        spec["confidence_level"] = 1.5
        with self.assertRaises(ValueError) as caught:
            actinv.waste.budget_uncertainty(spec, base_dir=self.root)
        self.assertIn("confidence_level must be in (0, 1)", str(caught.exception))
        self.assertClean("run.json")
        # the same refusal from a path
        path = self.path_for("budget-uncertainty.json", spec)
        with self.assertRaises(ValueError) as from_path:
            actinv.waste.budget_uncertainty(path)
        self.assertEqual(str(from_path.exception), str(caught.exception))
        self.assertClean("run.json", "budget-uncertainty.json")

    # Not exercised here: budget, composition_solve, uncertainty and budget_uncertainty
    # runs and twin need a nuclear-data library or an activation mesh to produce their inputs.


if __name__ == "__main__":
    unittest.main()
