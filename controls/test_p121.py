#!/usr/bin/env python3
"""Unit tests for the pure functions of controls/check_p121.py. No data or binaries needed."""
import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p121 as c  # noqa: E402

EV = 1.602176634e-19


def response():
    return {
        "air_mass_energy_absorption": {"energy_eV": [1e3, 1e4, 1e6, 1e7], "values_cm2_g": [3000.0, 10.0, 0.03, 0.02]},
        "element_mass_attenuation": {
            "H": {"energy_eV": [1e3, 1e4, 1e6, 1e7], "values_cm2_g": [4500.0, 5.0, 0.06, 0.03]},
            "Li": {"energy_eV": [1e3, 1e4, 1e6, 1e7], "values_cm2_g": [900.0, 1.0, 0.1, 0.05]},
        },
    }


def group(e, p):
    return {"centroid_eV": e, "power_W_g": p}


def nuclide(name, groups):
    return {"nuclide": name, "source_power_W_g": sum(g["power_W_g"] for g in groups),
            "contact_gamma_air_dose_proxy_Gy_h": None, "groups": groups}


def result(sub_power, ref=False):
    """One step: a covered 10 keV and 1 MeV pair plus a sub-keV group of `sub_power` W/g."""
    by_nuclide = [nuclide("A", [group(500.0, sub_power), group(1e4, 1e-10), group(1e6, 2e-10)])]
    fr = c.mass_fractions({"Li6": 1.0}, "atom_fraction")
    proxy = c.recompute_proxy(by_nuclide, response(), fr, 2.0)
    total = c.total_response_power(by_nuclide)
    share_ok = sub_power <= c.TOLERANCE * total
    sub_present = sub_power > 0.0
    ps = {"by_nuclide": by_nuclide,
          "contact_gamma_air_dose_proxy_Gy_h": None if ref or not share_ok else proxy}
    diag = {"response_excluded_power_W_g": sub_power if ref else 0.0, "response_missing_elements": [],
            "group_underflow_power_W_g": 0.0, "group_overflow_power_W_g": 0.0}
    if not ref and sub_present:
        ps["dose_response_subthreshold_power_W_g"] = sub_power
        diag["response_subthreshold_power_W_g"] = sub_power
    return {"ms": 1.0 if ref else 2.0, "steps": [{"photon_source": ps}], "ledger": {"photon_spectra": [diag]}}


class Pure(unittest.TestCase):
    def test_interpolation(self):
        e, v = [1.0, 10.0, 100.0], [100.0, 10.0, 1.0]
        self.assertEqual(c.curve_value(e, v, 10.0), 10.0)
        self.assertAlmostEqual(c.curve_value(e, v, 3.0), 100.0 / 3.0, places=9)
        self.assertIsNone(c.curve_value(e, v, 0.5))
        self.assertIsNone(c.curve_value(e, v, 101.0))

    def test_interpolation_keeps_edges(self):
        e, v = [1.0, 2.0, 2.0, 4.0], [8.0, 4.0, 40.0, 20.0]
        self.assertEqual(c.curve_value(e, v, 2.0), 40.0)
        self.assertAlmostEqual(c.curve_value(e, v, 1.5), math.exp(math.log(8) + math.log(1.5) / math.log(2) * math.log(0.5)))

    def test_mass_fractions(self):
        f = c.mass_fractions({"Li6": 1.0, "Li7": 1.0, "Be9": 1.0}, "atom_fraction")
        self.assertAlmostEqual(sum(f.values()), 1.0, places=14)
        li = 6.0151228874 + 7.0160034366
        self.assertAlmostEqual(f["Li"], li / (li + 9.0121830650), places=12)
        self.assertEqual(c.mass_fractions({"Fe56": 3.0, "Fe54": 1.0}, "wt_percent"), {"Fe": 1.0})

    def test_mass_fractions_refuse(self):
        with self.assertRaises(ValueError) as e:
            c.mass_fractions({"Zr90": 1.0}, "atom_fraction")
        self.assertIn("ATOMIC_MASS_U", str(e.exception))
        with self.assertRaises(ValueError):
            c.mass_fractions({"Fe": 1.0}, "atom_fraction")

    def test_recompute_proxy(self):
        by = [nuclide("A", [group(1e4, 1e-10), group(1e6, 2e-10), group(500.0, 1.0)])]
        got = c.recompute_proxy(by, response(), {"H": 1.0}, 2.0)
        want = (2.0 * 1e-10 + 0.5 * 2e-10) * 1000.0 * 3600.0
        self.assertAlmostEqual(got / want, 1.0, places=12)
        self.assertIsNone(c.recompute_proxy(by, response(), {"Xx": 1.0}, 2.0))

    def test_sub_sum_and_lowest_energy(self):
        by = [nuclide("A", [group(500.0, 3.0), group(0.0, 0.0), group(2e3, 1.0)]),
              nuclide("B", [group(999.0, 4.0)])]
        self.assertEqual(c.subthreshold_sum(by, 1000.0), 7.0)
        self.assertEqual(c.response_lowest_energy(response(), {"H": 1.0}), 1e3)

    def test_raw_ms_detection(self):
        a, b = b'{\n"ms": 1.0,\n"x": 1\n}', b'{\n"ms": 2.5,\n"x": 1\n}'
        self.assertTrue(c.raw_only_ms_differs(a, b))
        self.assertFalse(c.raw_only_ms_differs(a, b.replace(b'"x": 1', b'"x": 2')))


class Gates(unittest.TestCase):
    fr = {"Li": 1.0}

    def test_g2_equal_apart_from_ms(self):
        r = result(0.0)
        r["steps"][0]["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"] = 1.0
        k = copy.deepcopy(r)
        k["ms"] = 99.0
        self.assertTrue(c.gate_g2(r, k, b"a", b"b")["pass"])
        k["steps"][0]["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"] = 1.5
        self.assertFalse(c.gate_g2(r, k, b"a", b"b")["pass"])

    def test_g2_precondition(self):
        r = result(1e-3, ref=True)
        self.assertIsNone(c.gate_g2(r, r, b"", b"")["pass"])

    def test_g3_within_tolerance(self):
        out = c.gate_g3(result(1e-18, ref=True), result(1e-18), response(), self.fr, 2.0)
        self.assertTrue(out["pass"], out)
        self.assertEqual(out["a"]["expected_nonnull"], 1)
        self.assertEqual(out["b"]["steps_with_subthreshold"], 1)
        self.assertEqual(out["c"]["steps_compared"], 1)

    def test_g3_over_tolerance_null(self):
        out = c.gate_g3(result(1e-6, ref=True), result(1e-6), response(), self.fr, 2.0)
        self.assertTrue(out["pass"], out)
        self.assertEqual(out["a"]["expected_null"], 1)
        self.assertEqual(out["a"]["got_null"], 1)

    def test_g3_detects_wrong_proxy_and_wrong_field(self):
        cand = result(1e-18)
        cand["steps"][0]["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"] *= 1.001
        self.assertFalse(c.gate_g3(result(1e-18, ref=True), cand, response(), self.fr, 2.0)["c"]["pass"])
        cand = result(1e-18)
        cand["steps"][0]["photon_source"]["dose_response_subthreshold_power_W_g"] *= 2
        self.assertFalse(c.gate_g3(result(1e-18, ref=True), cand, response(), self.fr, 2.0)["b"]["pass"])

    def test_g3_detects_stray_change(self):
        cand = result(1e-18)
        cand["steps"][0]["photon_source"]["by_nuclide"][0]["source_power_W_g"] += 1.0
        self.assertFalse(c.gate_g3(result(1e-18, ref=True), cand, response(), self.fr, 2.0)["d"]["pass"])


if __name__ == "__main__":
    unittest.main()
