#!/usr/bin/env python3
"""Unit tests for the regulatory table converters (no downloaded data needed).

Run: python3 -I scripts/regulatory/test_regulatory_converters.py
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = load("regulatory_common")
CFR = load("convert_cfr_173435")
EURATOM = load("convert_euratom_annex_vii")
ICRP = load("fetch_icrp119")


class Common(unittest.TestCase):
    def test_nuclide_spellings(self):
        self.assertEqual(COMMON.parse_nuclide("Ag-108m")[0], "Ag108m1")
        self.assertEqual(COMMON.parse_nuclide("Mn-52 m")[0], "Mn52m1")
        self.assertEqual(COMMON.parse_nuclide("Te132")[0], "Te132")
        self.assertEqual(COMMON.parse_nuclide("Co-60")[0], "Co60")
        self.assertIsNone(COMMON.parse_nuclide("Xx-12"))
        self.assertIsNone(COMMON.parse_nuclide("U (nat)"))

    def test_scaled_float_is_exact_decimal(self):
        self.assertEqual(COMMON.scaled_float("4.0", -1, 12), 4e11)
        self.assertEqual(COMMON.scaled_float("1", 2, 3), 1e5)


class Cfr(unittest.TestCase):
    def test_values_keep_zero_exponent_distinct_from_hundred(self):
        self.assertEqual(CFR.parse_value("9.0 × 10^{0}", "t"), ("9.0", 0))
        self.assertEqual(CFR.parse_value("9.0 × 10^{2}", "t"), ("9.0", 2))
        self.assertEqual(CFR.parse_value("6.7 × 10 -^{10}", "t"), ("6.7", -10))
        self.assertEqual(CFR.parse_value("2.0", "t"), ("2.0", 0))
        self.assertIsNone(CFR.parse_value("Unlimited", "t"))
        with self.assertRaises(ValueError):
            CFR.parse_value("garbage", "t")

    def test_labels(self):
        self.assertEqual(CFR.parse_label("Ac-225 (a)")["nuclide"], "Ac225")
        self.assertEqual(CFR.parse_label("Mo-99(a)(i)")["footnotes"], ["a", "i"])
        self.assertEqual(CFR.parse_label("T(H-3)")["nuclide"], "H3")
        self.assertEqual(CFR.parse_label("Eu-150 (short lived)")["nuclide"], "Eu150m1")
        self.assertEqual(CFR.parse_label("Eu-150 (long lived)")["nuclide"], "Eu150")
        self.assertEqual(CFR.parse_label("Ta-178 (long-lived)")["nuclide"], "Ta178m1")
        lung = CFR.parse_label("U-235 (all lung absorption types) (a),(d),(e),(f)")
        self.assertEqual((lung["kind"], lung["variant"]), ("lung", "all"))
        self.assertEqual(CFR.parse_label("U (enriched to 20% or less)(g)")["kind"], "element")
        self.assertEqual(CFR.parse_label("Rb(nat)")["kind"], "element")
        with self.assertRaises(ValueError):
            CFR.parse_label("Mystery material")

    def test_progeny_footnote(self):
        lines = ["intro", "Pb-212 Bi-212, Tl-208 (0.36), Po-212 (0.64)", "Ra-224 Rn-220, Po-216,"]
        parents = CFR.parse_progeny(lines)
        self.assertEqual(parents["Pb-212"]["progeny"], ["Bi-212", "Tl-208 (0.36)", "Po-212 (0.64)"])
        self.assertEqual(parents["Ra-224"]["progeny"], ["Rn-220", "Po-216"])


class Euratom(unittest.TestCase):
    def test_numbers_with_decimal_comma_and_spaces(self):
        self.assertEqual(EURATOM.parse_number("0,1", "t"), ("0.1", 0))
        self.assertEqual(EURATOM.parse_number("10 000", "t"), ("10000", 0))
        self.assertEqual(EURATOM.parse_number("1 × 10^{12}", "t"), ("1", 12))
        with self.assertRaises(ValueError):
            EURATOM.parse_number("n/a", "t")

    def test_names(self):
        self.assertEqual(EURATOM.parse_name("Ag-110 m (1)", "t")[:2], ("Ag110m1", "1"))
        self.assertEqual(EURATOM.parse_name("Te132", "t")[0], "Te132")
        self.assertEqual(EURATOM.tidy("Fe-52 (^{1})"), "Fe-52 (1)")


class Icrp(unittest.TestCase):
    def test_ingestion_variants_and_minus_sign(self):
        block = [
            "H-3            12.35 y            1.0*      1.2E-10     1.0             1.2E-10   7.3E-11   5.7E-11       4.2E-11      4.2E-11",
            "                                   1.0       6.4E-11     1.0             4.8E-11   3.1E-11   2.3E-11       1.8E-11      1.8E-11",
            "Sc-44m          58.6 h           0.001      9.4E-08   0.0001          1.6E-08   8.3E-09   5.1E-09     3.1E-09    2.4E-09",
        ]
        parsed = ICRP.parse_ingestion(block)
        self.assertEqual([row["adult"] for row in parsed["H-3"]], [4.2e-11, 1.8e-11])
        self.assertEqual(parsed["Sc-44m"][0]["adult"], 2.4e-09)

    def test_inhalation_repeated_type_is_kept(self):
        block = [
            "Hg-193       3.5 h          F§     0.04      2.7E-10     0.02       2.0E-10   8.9E-11   5.5E-11       3.1E-11     2.6E-11",
            "                            F–     0.8       2.2E-10     0.4        1.8E-10   8.2E-11   5.0E-11       2.9E-11     2.4E-11",
        ]
        rows = ICRP.parse_inhalation(block)["Hg-193"]
        self.assertEqual([row["type"] for row in rows], ["F", "F"])
        self.assertEqual(rows[1]["adult"], 2.4e-11)


if __name__ == "__main__":
    unittest.main()
