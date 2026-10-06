"""Focused boundary checks for the optional Nucleide -> ACTINV example.

The translation tests use Nucleide's public material, ALARA deck, flux, and
schedule APIs against the checked-in inputs. The full calculation is opt-in
because it requires a built ACTINV binary and provisioned evaluated data.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"

try:
    import nucleide
except ModuleNotFoundError as exc:  # skip only when the optional package itself is absent
    if exc.name != "nucleide":
        raise
    nucleide = None  # type: ignore[assignment]
    _NUCLEIDE_IMPORT_ERROR = exc
else:
    _NUCLEIDE_IMPORT_ERROR = None

if __package__:
    from . import e2e
else:
    import e2e


@unittest.skipIf(nucleide is None, f"optional Nucleide dependency unavailable: {_NUCLEIDE_IMPORT_ERROR}")
class InputTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.material_doc = json.loads((INPUTS / "material.json").read_text())
        cls.manifest = json.loads((INPUTS / "neutron_groups.json").read_text())
        cls.deck_text = (INPUTS / "activation.alara").read_text()
        cls.flux_text = (INPUTS / "flux.txt").read_text()
        cls.deck = nucleide.alara.alara_parse_deck(cls.deck_text)
        cls.parsed_flux = nucleide.alara.alara_parse_flux(cls.flux_text, e2e.ALARA_FLUX_NAME)

    def test_nucleide_material_maps_density_volume_and_weight_basis(self) -> None:
        # This fixture uses Nucleide's actual mass-mixture API. Independently
        # assert its fixed physical meaning at the ACTINV material boundary.
        mixed = nucleide.material.mix_by_mass(
            [(component["composition_g"], component["mass_fraction"])
             for component in self.material_doc["components"]]
        )
        self.assertEqual(mixed, {"Fe56": 1.0})

        material = e2e.translate_material(self.material_doc)
        self.assertEqual(material["basis"], "wt_percent")
        self.assertEqual(material["composition"], {"FE56": 100.0})
        self.assertAlmostEqual(material["density_g_cm3"], 7.874, places=12)
        self.assertAlmostEqual(material["volume_cm3"], 2.0, places=12)
        self.assertAlmostEqual(material["mass_g"], 15.748, places=12)
        self.assertAlmostEqual(
            material["mass_g"], material["density_g_cm3"] * material["volume_cm3"], places=12
        )

    def test_unsupported_metastable_name_is_rejected(self) -> None:
        material_doc = dict(self.material_doc)
        material_doc["components"] = [{
            "composition_g": {"Fe56m": 1.0}, "mass_fraction": 1.0,
        }]
        with self.assertRaisesRegex(ValueError, "no explicit.*mapping"):
            e2e.translate_material(material_doc)

    def test_material_rejects_multicomponent_or_weighted_component_inputs(self) -> None:
        base = self.material_doc["components"][0]
        multiple = dict(self.material_doc, components=[base, base])
        fractional = dict(self.material_doc, components=[dict(base, mass_fraction=0.5)])
        for material_doc in (multiple, fractional):
            with self.subTest(material_doc=material_doc):
                with self.assertRaises(ValueError):
                    e2e.translate_material(material_doc)

    def _copy_inputs(self, parent: Path) -> Path:
        copied = parent / "inputs"
        shutil.copytree(INPUTS, copied)
        return copied

    @staticmethod
    def _input_scratch() -> Path:
        scratch = ROOT / "target" / "preflight-tmp" / "nucleide-input-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def _load_copied_inputs(self, input_dir: Path) -> None:
        ascending_bounds = list(reversed(self.manifest["boundaries_eV"]))
        with patch.object(e2e, "library_bounds", return_value=ascending_bounds):
            e2e.load_nucleide_inputs(input_dir, Path("fixture-library.npz"))

    def test_input_loader_rejects_zone_link_to_a_different_mixture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="zone-link-", dir=self._input_scratch()) as temporary:
            input_dir = self._copy_inputs(Path(temporary))
            deck_path = input_dir / "activation.alara"
            deck_text = deck_path.read_text()
            deck_text = deck_text.replace("zone1 mix1", "zone1 mix2", 1)
            deck_text += "\nmixture mix2\n    material fe56 1.0 1.0\nend\n"
            deck_path.write_text(deck_text)
            with self.assertRaisesRegex(ValueError, "mat_loading|zone1|mixture"):
                self._load_copied_inputs(input_dir)

    def test_input_loader_rejects_flux_path_that_differs_from_loaded_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="flux-path-", dir=self._input_scratch()) as temporary:
            input_dir = self._copy_inputs(Path(temporary))
            deck_path = input_dir / "activation.alara"
            deck_path.write_text(deck_path.read_text().replace(
                "flux neutron_flux flux.txt", "flux neutron_flux nested/flux.txt", 1
            ))
            with self.assertRaisesRegex(ValueError, "flux|path"):
                self._load_copied_inputs(input_dir)

    def test_flux_reverses_values_and_exact_boundaries_and_applies_scale_once(self) -> None:
        raw = [float(value) for value in self.parsed_flux["intervals"][0]]
        physical_alara = [value * 2.0 for value in raw]
        ascending_bounds = list(reversed(self.manifest["boundaries_eV"]))
        translated = e2e.translate_flux(
            self.deck, self.parsed_flux, self.manifest, ascending_bounds
        )

        self.assertEqual(translated["energy_boundaries_eV"], ascending_bounds)
        self.assertEqual(translated["flux_per_group"], list(reversed(physical_alara)))
        self.assertEqual(translated["source_scale"], 2.0)
        self.assertAlmostEqual(sum(raw), 5.0e11, delta=1e-3)
        self.assertAlmostEqual(translated["flux_total"], 1.0e12, delta=1e-3)
        # ALARA group 88 (descending) becomes ACTINV index 620 (ascending).
        self.assertEqual(translated["flux_per_group"][709 - 1 - 88], raw[88] * 2.0)

    def test_flux_rejects_nonmatching_edges_and_units(self) -> None:
        ascending_bounds = list(reversed(self.manifest["boundaries_eV"]))
        wrong_edges = ascending_bounds.copy()
        wrong_edges[88] += 0.01
        with self.assertRaisesRegex(ValueError, "exactly match"):
            e2e.translate_flux(self.deck, self.parsed_flux, self.manifest, wrong_edges)

        wrong_units = dict(self.manifest, flux_units="n cm^-2 per source particle")
        with self.assertRaisesRegex(ValueError, "physical group-integrated"):
            e2e.translate_flux(self.deck, self.parsed_flux, wrong_units, ascending_bounds)

    def test_flux_rejects_overflow_after_deck_scalar(self) -> None:
        deck = dict(self.deck, fluxes=[dict(self.deck["fluxes"][0], scale=1e308)])
        manifest = dict(self.manifest, physical_flux_total_n_cm2_s=float("inf"))
        with self.assertRaisesRegex(ValueError, "finite"):
            e2e.translate_flux(deck, self.parsed_flux, manifest,
                               list(reversed(self.manifest["boundaries_eV"])))

    def test_schedule_uses_nucleide_expansion_then_cooling_interval(self) -> None:
        expanded = nucleide.alara.alara_expand_schedule(self.deck_text)
        self.assertEqual(len(expanded), 1)
        self.assertAlmostEqual(expanded[0]["duration_s"], 300.0, places=12)
        self.assertFalse(expanded[0]["is_cooling"])

        schedule = e2e.translate_schedule(self.deck_text, self.deck)
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[0], {"dt": "300 s", "flux": 1.0})
        self.assertEqual(schedule[1], {"dt": "3600 s", "flux": 0.0})
        self.assertEqual(sum(float(step["dt"].split()[0]) for step in schedule), 3900.0)

    def test_schedule_preserves_repeated_history_delay_as_zero_flux(self) -> None:
        # Turn the checked-in deck into ten 1-second pulses with 5-second
        # between-pulse delays, retaining its valid single 3600-second cooldown.
        deck_text = self.deck_text.replace(
            "300 s neutron_flux once 0 s", "1 s neutron_flux once 0 s"
        ).replace("    1 0 s", "    10 5 s")
        deck = nucleide.alara.alara_parse_deck(deck_text)
        expanded = nucleide.alara.alara_expand_schedule(deck_text)
        self.assertEqual(len(expanded), 19)
        self.assertTrue(expanded[1]["is_cooling"])
        self.assertAlmostEqual(expanded[1]["duration_s"], 5.0, places=12)

        schedule = e2e.translate_schedule(deck_text, deck)
        self.assertEqual(schedule[0], {"dt": "1 s", "flux": 1.0})
        self.assertEqual(schedule[1], {"dt": "5 s", "flux": 0.0})
        self.assertEqual(schedule[2], {"dt": "1 s", "flux": 1.0})
        self.assertEqual(sum(float(step["dt"].split()[0]) for step in schedule[:19]), 55.0)

    def test_fixture_validator_rejects_changed_schedule(self) -> None:
        ascending_bounds = list(reversed(self.manifest["boundaries_eV"]))
        with patch.object(e2e, "library_bounds", return_value=ascending_bounds):
            translated = e2e.load_nucleide_inputs(INPUTS, Path("fixture-library.npz"))
        self.assertIsNone(e2e.validate_fixture(translated))
        changed = dict(translated, schedule=[{"dt": "301 s", "flux": 1.0},
                                             {"dt": "3600 s", "flux": 0.0}])
        with self.assertRaisesRegex(ValueError, "schedule|300|fixture"):
            e2e.validate_fixture(changed)
        same_volume_box = dict(translated, material=dict(translated["material"],
                                                         bounds_cm=[[0, 1], [0, 2], [0, 1]]))
        with self.assertRaisesRegex(ValueError, "fixture"):
            e2e.validate_fixture(same_volume_box)


class ResultComparisonTests(unittest.TestCase):
    @staticmethod
    def _step() -> dict[str, Any]:
        boundaries = [float(index) for index in range(25)]
        groups = [
            {"low_eV": boundaries[index], "high_eV": boundaries[index + 1],
             "centroid_eV": boundaries[index] + 0.5, "photons_s": 1.0}
            for index in range(24)
        ]
        return {
            "step": 2,
            "t_s": 3900.0,
            "activity_Bq_per_g": {"Mn56": 1.0},
            "photon_source": {
                "group_structure": "fispact-24",
                "boundaries_eV": boundaries,
                "groups": groups,
                "total_photons_s": 24.0,
            },
        }

    def test_compare_results_accepts_matching_finite_steps(self) -> None:
        comparison = e2e.compare_results(self._step(), self._step())
        self.assertEqual(comparison["max_group_relative_error"], 0.0)
        self.assertEqual(comparison["relative_error"], 0.0)

    def test_compare_results_preserves_zero_centroid_for_empty_actinv_group(self) -> None:
        step = self._step()
        step["photon_source"]["groups"][-1].update(photons_s=0.0, centroid_eV=0.0)
        step["photon_source"]["total_photons_s"] = 23.0
        comparison = e2e.compare_results(step, step)
        self.assertEqual(len(comparison["mesh_photon_groups_s"]), 24)
        self.assertEqual(comparison["mesh_photon_groups_s"][-1], 0.0)

    def test_compare_results_rejects_shared_wrong_endpoint(self) -> None:
        step = self._step()
        step.update(step=1, t_s=300.0)
        with self.assertRaisesRegex(ValueError, "step 2|3900"):
            e2e.compare_results(step, step)

    def test_compare_results_rejects_missing_activity_and_group_vectors(self) -> None:
        direct = self._step()
        direct["activity_Bq_per_g"] = {}
        with self.assertRaisesRegex(ValueError, "Mn56"):
            e2e.compare_results(direct, self._step())

        direct = self._step()
        direct["photon_source"]["groups"] = []
        with self.assertRaises(ValueError):
            e2e.compare_results(direct, self._step())

    def test_compare_results_rejects_nonfinite_yields_and_changed_group_grid(self) -> None:
        direct = self._step()
        direct["photon_source"]["groups"][0]["photons_s"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite|photon|group"):
            e2e.compare_results(direct, self._step())

        mesh = self._step()
        photon = mesh["photon_source"]
        photon["boundaries_eV"][1] += 0.25
        photon["groups"][0]["high_eV"] += 0.25
        photon["groups"][0]["centroid_eV"] += 0.125
        photon["groups"][1]["low_eV"] += 0.25
        photon["groups"][1]["centroid_eV"] += 0.125
        with self.assertRaisesRegex(RuntimeError, "grid|bound|group"):
            e2e.compare_results(self._step(), mesh)


class EndToEndActivationTests(unittest.TestCase):
    """Real CLI solve is enabled by the data and binary environment variables."""

    def test_fresh_mesh_photon_source_round_trip(self) -> None:
        if nucleide is None:
            self.skipTest(f"optional Nucleide dependency unavailable: {_NUCLEIDE_IMPORT_ERROR}")
        actinv = os.environ.get("ACTINV_BIN")
        data_root = os.environ.get("ACTINV_DATA_DIR")
        if actinv is None and data_root is None:
            self.skipTest("set ACTINV_BIN and ACTINV_DATA_DIR to run the evaluated-data example")
        if not actinv or not data_root:
            self.fail("ACTINV_BIN and ACTINV_DATA_DIR must both name configured paths")
        data = Path(data_root)
        library = data / "v1.1.0/activation/tendl-2025-neutron-709g.npz"
        decay = data / "v1.1.0/decay/endf-b-viii-0_decay.dat"
        fallback = data / "v1.1.0/decay/jeff-3-3_decay.dat"
        missing = [path for path in (Path(actinv), library, decay, fallback) if not path.is_file()]
        if missing:
            self.fail("configured ACTINV executable/data missing: " + ", ".join(map(str, missing)))

        # Use a disk-backed target directory: local safety instructions forbid
        # putting build or solver temporaries on RAM-backed /tmp.
        scratch = ROOT / "target" / "preflight-tmp" / "nucleide-e2e-test"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="run-", dir=scratch) as temp_dir:
            output = Path(temp_dir) / "output"
            args = Namespace(
                actinv_bin=actinv, data_root=str(data), inputs=str(INPUTS), out=str(output),
            )
            with patch.dict(os.environ, {"TMPDIR": str(scratch.resolve())}):
                report_out = e2e.run_example(args)
            report = json.loads((output / "report.json").read_text())
            mesh_result = output / "mesh_result.ndjson"
            photon_dir = output / "photon-source"
            self.assertTrue(mesh_result.is_file() and mesh_result.stat().st_size > 0)
            self.assertTrue(any(photon_dir.glob("*.photonSrc")))
            self.assertGreater(report["declared_photons_s"], 0.0)
            self.assertAlmostEqual(
                report["declared_photons_s"],
                report["reconstructed_density_times_volume_photons_s"],
                delta=1e-10 * report["declared_photons_s"],
            )
            self.assertGreater(report["photon_reader_group_count"], 0)
            self.assertEqual(report["cooling_time_s"], 3600.0)
            self.assertEqual(report["material_mass_g"], 15.748)
            self.assertEqual(report["physical_flux_total_n_cm2_s"], 1.0e12)
            self.assertEqual(report, report_out)

            comparison = report["direct_comparison"]
            self.assertLess(comparison["relative_error"], 1e-10)
            self.assertLess(comparison["max_group_relative_error"], 1e-10)
            direct_groups = comparison["direct_photon_groups_s"]
            mesh_groups = comparison["mesh_photon_groups_s"]
            self.assertEqual(len(direct_groups), 24)
            self.assertEqual(len(mesh_groups), len(direct_groups))
            for direct, mesh in zip(direct_groups, mesh_groups):
                self.assertAlmostEqual(direct, mesh, delta=1e-10 * max(1.0, abs(direct)))
            self.assertAlmostEqual(
                comparison["direct_mn56_activity_bq_g"], 20_622_094.12329952,
                delta=1e-8 * 20_622_094.12329952,
            )
            self.assertAlmostEqual(
                comparison["direct_mn56_activity_bq_g"],
                comparison["mesh_mn56_activity_bq_g"],
                delta=1e-10 * comparison["direct_mn56_activity_bq_g"],
            )


class SubprocessBoundaryTests(unittest.TestCase):
    def test_timed_out_leaf_process_is_terminated_and_reaped(self) -> None:
        """Exercise the adapter timeout using one leaf process with no children."""
        scratch = ROOT / "target" / "preflight-tmp" / "nucleide-e2e-timeout-test"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="timeout-", dir=scratch) as temp_dir:
            pid_path = Path(temp_dir) / "child.pid"
            source = (
                "import os, pathlib, time; "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                "time.sleep(10)"
            )
            with self.assertRaisesRegex(RuntimeError, "command timed out"):
                e2e._call([sys.executable, "-c", source], timeout_s=0.5)
            self.assertTrue(pid_path.is_file(), "child did not start before timeout")
            child_pid = int(pid_path.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_completed_leaf_process_returns_normally(self) -> None:
        completed = e2e._call([sys.executable, "-c", "print('done')"], timeout_s=2.0)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "done")

    def test_timeout_reported_as_child_exits_still_reaps_process(self) -> None:
        """Cover the timeout/exit race where kill is attempted after child exit."""
        real_popen = subprocess.Popen
        observed: dict[str, object] = {}

        class ExitRacePopen:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
                self.command = args[0] if args else []
                self.first_communicate = True
                self.kill_attempted = False
                observed["wrapper"] = self

            def __enter__(self) -> "ExitRacePopen":
                self.process.__enter__()
                return self

            def __exit__(self, *args: object) -> object:
                return self.process.__exit__(*args)

            def communicate(self, input: object = None, timeout: float | None = None) -> tuple[bytes, bytes]:
                if self.first_communicate:
                    self.first_communicate = False
                    self.process.wait(timeout=2.0)
                    raise subprocess.TimeoutExpired(self.command, timeout)
                return self.process.communicate(input=input, timeout=timeout)

            def kill(self) -> None:
                self.kill_attempted = True
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass

            def __getattr__(self, name: str) -> object:
                return getattr(self.process, name)

        with patch.object(subprocess, "Popen", ExitRacePopen):
            with self.assertRaisesRegex(RuntimeError, "command timed out"):
                e2e._call([sys.executable, "-c", "print('exit-race')"], timeout_s=1.0)

        wrapper = observed["wrapper"]
        self.assertTrue(wrapper.kill_attempted)  # type: ignore[attr-defined]
        self.assertIsNotNone(wrapper.process.returncode)  # type: ignore[attr-defined]
        with self.assertRaises(ProcessLookupError):
            os.kill(wrapper.process.pid, 0)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
