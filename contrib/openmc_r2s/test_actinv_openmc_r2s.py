"""Fast tests for the ACTINV R2S adapter's pure logic.

No neutron/photon transport and no ``actinv`` binary is invoked. These tests exercise:

- ``material_to_actinv``: OpenMC material -> ACTINV explicit-nuclide composition.
- ``flux_record_lines``: the ``actinv-flux-1`` NDJSON writer.
- ``build_schedule``: timestep/source-rate -> ACTINV schedule conversion.
- ``parse_photon_groups`` / ``build_results_shim``: the results shim OpenMC's step 3 reads,
  including that index 0 is pre-irradiation (no photon source).
- ``library_bounds``: reading group boundaries out of an ACTINV library archive.

If OpenMC is not importable, every test in this module is skipped (not failed, not errored).
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import numpy as np
    import openmc
    HAVE_OPENMC = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment-dependent
    HAVE_OPENMC = False
    _IMPORT_ERROR = exc

if HAVE_OPENMC:
    import actinv_openmc_r2s as m


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class MaterialToActinvTests(unittest.TestCase):
    def test_composition_and_mass(self):
        mat = openmc.Material(name="steel")
        mat.set_density("g/cm3", 7.93)
        mat.add_nuclide("Fe56", 0.9, "wo")
        mat.add_nuclide("Cr52", 0.1, "wo")
        volume_cm3 = 8.0

        result = m.material_to_actinv(mat, volume_cm3)

        self.assertEqual(result["basis"], "atoms_per_g")
        self.assertAlmostEqual(result["mass_g"], volume_cm3 * 7.93)
        self.assertEqual(set(result["composition"]), {"Fe56", "Cr52"})

        density = mat.get_mass_density()
        for nuclide, atoms_b_cm in mat.get_nuclide_atom_densities().items():
            expected = atoms_b_cm * 1.0e24 / density
            self.assertAlmostEqual(result["composition"][nuclide], expected, delta=abs(expected) * 1e-9)

    def test_metastable_isomer_naming(self):
        mat = openmc.Material()
        mat.set_density("g/cm3", 1.0)
        mat.add_nuclide("Am242_m1", 1.0, "ao")

        result = m.material_to_actinv(mat, 1.0)

        self.assertEqual(set(result["composition"]), {"Am242m1"})
        self.assertNotIn("Am242_m1", result["composition"])

    def test_zero_density_raises(self):
        mat = openmc.Material()
        mat.add_nuclide("Fe56", 1.0, "ao")
        mat.set_density("g/cm3", 0.0)

        with self.assertRaises(ValueError):
            m.material_to_actinv(mat, 1.0)

    def test_bad_nuclide_name_raises(self):
        class FakeMaterial:
            id = 99

            def get_mass_density(self):
                return 1.0

            def get_nuclide_atom_densities(self):
                return {"not-a-nuclide": 0.01}

        with self.assertRaises(ValueError):
            m.material_to_actinv(FakeMaterial(), 1.0)

    def test_unmatched_names_accumulate_into_one_actinv_key(self):
        # material_to_actinv sums into `composition[key]` rather than assigning, so if two
        # distinct source entries ever normalise to the same ACTINV key, their densities add
        # instead of one clobbering the other. No real OpenMC nuclide name pair collides this
        # way today, but the accumulation is defensive code worth pinning down directly.
        class FakeMaterial:
            id = 1

            def get_mass_density(self):
                return 2.0

            def get_nuclide_atom_densities(self):
                return {"Fe56": 0.01, "Cr52": 0.02}

        fake = FakeMaterial()
        result = m.material_to_actinv(fake, 1.0)
        self.assertEqual(result["composition"]["Fe56"], 0.01 * 1.0e24 / 2.0)
        self.assertEqual(result["composition"]["Cr52"], 0.02 * 1.0e24 / 2.0)
        self.assertEqual(sum(result["composition"].values()),
                         (0.01 + 0.02) * 1.0e24 / 2.0)


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class FirstTallyNuclideTests(unittest.TestCase):
    class _FakeMaterial:
        def __init__(self, nuclides):
            self._nuclides = nuclides

        def get_nuclide_atom_densities(self):
            return self._nuclides

    class _BrokenMaterial:
        def get_nuclide_atom_densities(self):
            raise RuntimeError("not fillable")

    def test_returns_first_nuclide_of_first_material_with_any(self):
        materials = [self._FakeMaterial({}), self._FakeMaterial({"Fe56": 0.1, "Cr52": 0.2})]

        self.assertEqual(m.first_tally_nuclide(materials), "Fe56")

    def test_skips_materials_that_raise(self):
        materials = [self._BrokenMaterial(), self._FakeMaterial({"Am242m1": 0.01})]

        self.assertEqual(m.first_tally_nuclide(materials), "Am242m1")

    def test_no_material_with_any_nuclide_raises_valueerror_not_stopiteration(self):
        materials = [self._FakeMaterial({}), self._BrokenMaterial()]

        with self.assertRaises(ValueError) as ctx:
            m.first_tally_nuclide(materials)
        self.assertIn("micro_kwargs", str(ctx.exception))

    def test_empty_material_list_raises_valueerror(self):
        with self.assertRaises(ValueError):
            m.first_tally_nuclide([])


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class RequireActivationRegionsTests(unittest.TestCase):
    def test_zero_regions_raises_valueerror(self):
        with self.assertRaises(ValueError):
            m.require_activation_regions([])

    def test_nonzero_regions_does_not_raise(self):
        m.require_activation_regions(["placeholder-material"])  # must not raise


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class BuildScheduleTests(unittest.TestCase):
    def test_scalar_rate_broadcast_and_units(self):
        schedule, reference = m.build_schedule([1.0, 2.0], 5.0, timestep_units="min")

        self.assertEqual(reference, 5.0)
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[0]["dt"], f"{60.0!r} s")
        self.assertEqual(schedule[1]["dt"], f"{120.0!r} s")
        self.assertEqual(schedule[0]["flux"], 1.0)
        self.assertEqual(schedule[1]["flux"], 1.0)

    def test_per_step_rates_and_reference_is_first_positive(self):
        schedule, reference = m.build_schedule([1.0, 1.0, 1.0], [0.0, 4.0, 2.0])

        self.assertEqual(reference, 4.0)
        self.assertEqual([s["flux"] for s in schedule], [0.0, 1.0, 0.5])

    def test_tuple_timesteps_with_mixed_units(self):
        schedule, _ = m.build_schedule([(1.0, "h"), (1.0, "d")], [1.0, 1.0])

        self.assertEqual(schedule[0]["dt"], f"{3600.0!r} s")
        self.assertEqual(schedule[1]["dt"], f"{86400.0!r} s")

    def test_all_zero_rates_raises(self):
        with self.assertRaises(ValueError):
            m.build_schedule([1.0], [0.0])

    def test_all_negative_rates_raises(self):
        with self.assertRaises(ValueError):
            m.build_schedule([1.0, 1.0], [-1.0, 0.0])

    def test_unsupported_unit_raises(self):
        with self.assertRaises(ValueError):
            m.build_schedule([1.0], [1.0], timestep_units="fortnight")


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class FluxRecordLinesTests(unittest.TestCase):
    def _parse(self, lines):
        return [json.loads(line) for line in lines]

    def test_header_schema_and_normalisation(self):
        bounds = [1.0, 10.0, 100.0]  # 2 groups
        fluxes = [[4.0, 8.0]]
        lines = m.flux_record_lines(bounds, ["region-0"], [2.0], fluxes, reference=10.0)
        records = self._parse(lines)

        header, cell, footer = records
        self.assertEqual(header["record"], "header")
        self.assertEqual(header["schema"], "actinv-flux-1")
        self.assertEqual(header["energy_boundaries_eV"], bounds)
        self.assertEqual(header["flux_units"], "n cm^-2 s^-1")
        self.assertEqual(header["cell_count"], 1)

        # phi = f / V * reference
        self.assertEqual(cell["flux_per_group"], [4.0 / 2.0 * 10.0, 8.0 / 2.0 * 10.0])
        self.assertEqual(cell["id"], "0")
        self.assertEqual(cell["ordinal"], 0)
        self.assertAlmostEqual(cell["flux_total"], sum(cell["flux_per_group"]))

        self.assertEqual(footer["record"], "footer")
        self.assertEqual(footer["cell_count"], 1)
        self.assertAlmostEqual(footer["flux_sum_over_cells"], cell["flux_total"])

    def test_multiple_regions_order_and_ids(self):
        bounds = [1.0, 2.0]  # 1 group
        fluxes = [[1.0], [2.0], [3.0]]
        lines = m.flux_record_lines(bounds, ["a", "b", "c"], [1.0, 1.0, 1.0], fluxes, reference=1.0)
        records = self._parse(lines)
        cells = [r for r in records if r["record"] == "cell"]

        self.assertEqual([c["id"] for c in cells], ["0", "1", "2"])
        self.assertEqual([c["ordinal"] for c in cells], [0, 1, 2])
        self.assertEqual([c["flux_per_group"] for c in cells], [[1.0], [2.0], [3.0]])

    def test_group_count_mismatch_raises(self):
        bounds = [1.0, 2.0, 3.0]  # 2 groups
        with self.assertRaises(RuntimeError):
            m.flux_record_lines(bounds, ["a"], [1.0], [[1.0]], reference=1.0)  # only 1 group given

    def test_nonpositive_volume_raises(self):
        bounds = [1.0, 2.0]
        with self.assertRaises(ValueError):
            m.flux_record_lines(bounds, ["a"], [0.0], [[1.0]], reference=1.0)


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class ResultsShimTests(unittest.TestCase):
    def _fake_mats(self, n):
        class FakeMat:
            def __init__(self, mat_id):
                self.id = mat_id
        return [FakeMat(100 + i) for i in range(n)]

    def _write_result(self, path, records):
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_parse_photon_groups_two_regions_two_steps(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "mesh_result.ndjson"
            records = [
                {"record": "cell", "id": "0", "result": {"steps": [
                    {"photon_source": {"groups": [{"centroid_eV": 1.0e6, "photons_s": 5.0},
                                                   {"centroid_eV": 2.0e6, "photons_s": 0.0}]}},
                    {"photon_source": {"groups": []}},
                ]}},
                {"record": "cell", "id": "1", "result": {"steps": [
                    {"photon_source": {"groups": [{"centroid_eV": 3.0e6, "photons_s": 7.0}]}},
                    {"photon_source": None},
                ]}},
                {"record": "footer", "cell_count": 2},
            ]
            self._write_result(result_path, records)
            mats = self._fake_mats(2)

            per_step = m.parse_photon_groups(result_path, mats, n_steps=2)

            self.assertEqual(len(per_step), 2)
            # zero-strength group filtered out; region 0 step 0 keeps only the 1 MeV line
            self.assertEqual(per_step[0]["100"], [(1.0e6, 5.0)])
            self.assertEqual(per_step[0]["101"], [(3.0e6, 7.0)])
            # empty/None photon_source -> None, not an empty list
            self.assertIsNone(per_step[1]["100"])
            self.assertIsNone(per_step[1]["101"])

    def test_parse_photon_groups_reads_the_lean_default_selection(self):
        # P90: the manager's default cell_result_fields is
        # ["mode", "ledger", "steps.photon_source.groups"], so a real mesh result carries only
        # "step" plus "photon_source" (itself pruned to "groups") in each step object, and no
        # "photon_source" key at all in a step whose photon output is unset (the dotted route
        # omits it, exactly like the full route's skip_serializing_if). parse_photon_groups must
        # not depend on any of the keys P90 strips (inventory, activity_Bq_per_g, heat_W_per_g, ...).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "mesh_result.ndjson"
            records = [
                {"record": "cell", "id": "0", "result": {
                    "mode": "auto", "ledger": {}, "steps": [
                        {"step": 0, "photon_source": {
                            "groups": [{"centroid_eV": 1.0e6, "photons_s": 5.0},
                                       {"centroid_eV": 2.0e6, "photons_s": 0.0}]}},
                        {"step": 1},  # photon output unset for this step: key omitted entirely
                    ]}},
                {"record": "footer", "cell_count": 1},
            ]
            self._write_result(result_path, records)
            mats = self._fake_mats(1)

            per_step = m.parse_photon_groups(result_path, mats, n_steps=2)

            self.assertEqual(per_step[0]["100"], [(1.0e6, 5.0)])
            self.assertIsNone(per_step[1]["100"])

    def test_build_results_shim_index_zero_is_pre_irradiation(self):
        per_step = [{"100": [(1.0e6, 5.0)]}, {"100": None}]

        results = m.build_results_shim(per_step)

        self.assertEqual(len(results), 3)  # pre-irradiation + 2 schedule steps

        pre = results[0].get_material("100").get_decay_photon_energy()
        self.assertIsNone(pre)
        # an id never seen at all is also None, not an error
        pre_unknown = results[0].get_material("999").get_decay_photon_energy()
        self.assertIsNone(pre_unknown)

        step1 = results[1].get_material("100").get_decay_photon_energy()
        self.assertIsNotNone(step1)
        self.assertEqual(list(step1.x), [1.0e6])
        self.assertEqual(list(step1.p), [5.0])

        step2 = results[2].get_material("100").get_decay_photon_energy()
        self.assertIsNone(step2)


@unittest.skipUnless(HAVE_OPENMC, f"openmc not importable: {_IMPORT_ERROR}")
class LibraryBoundsTests(unittest.TestCase):
    def test_reads_ascending_bounds_from_npz(self):
        import tempfile
        bounds = np.array([1.0, 10.0, 100.0, 1.0e6], dtype=np.float64)
        buf = io.BytesIO()
        np.save(buf, bounds)
        with tempfile.TemporaryDirectory() as tmp:
            lib_path = Path(tmp) / "lib.npz"
            with zipfile.ZipFile(lib_path, "w") as z:
                z.writestr("bounds.npy", buf.getvalue())

            result = m.library_bounds(lib_path)

            np.testing.assert_array_equal(result, bounds)


if __name__ == "__main__":
    unittest.main()
