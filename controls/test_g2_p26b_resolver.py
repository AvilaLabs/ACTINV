#!/usr/bin/env python3
"""Regression controls for the anonymous-isotope resolver: ALARA's stdout
tables strip the element symbol, so a '-53' row must resolve on a unique
half-life signature and never by table order (P42's Ti-53/Ti-55 finding)."""
import unittest

import g2_p26b_leg as leg


def row(a, liso=0, t_half=-1.0, values=None):
    return {"a": a, "liso": liso, "t_half": t_half,
            "pre_irrad": 0.0, "values": values or [1.0]}


# KZA = Z*10000 + A*10 + liso
TI53, V53, CR53, FE53 = 220530, 230530, 240530, 260530
FE55, FE59 = 260550, 260590


class ResolverTests(unittest.TestCase):
    def test_isobar_row_resolves_on_unique_half_life(self):
        # A '-53' row printed with V-53's half-life must land on V-53,
        # not on the lowest-Z candidate (Ti-53).
        union = sorted([TI53, V53, CR53, FE53])
        hl = {TI53: 32.7, V53: 96.0, CR53: 1e30, FE53: 511.0}
        rows = [row(53, t_half=95.5)]
        out = leg.resolve_isotope_names(rows, union, hl)
        self.assertEqual(out[0]["kza"], V53)

    def test_ambiguous_isobar_stays_unresolved(self):
        # Two candidates, neither half-life matching: no order pick.
        union = sorted([TI53, V53])
        hl = {TI53: 32.7, V53: 96.0}
        out = leg.resolve_isotope_names([row(53, t_half=10.0)], union, hl)
        self.assertTrue(out[0].get("unresolved"))
        self.assertNotIn("kza", out[0])

    def test_two_close_half_lives_stay_unresolved(self):
        # Both candidates inside the 2% window: the row is genuinely
        # indistinguishable — unresolved beats a silent wrong element.
        union = sorted([TI53, V53])
        hl = {TI53: 100.0, V53: 101.0}
        out = leg.resolve_isotope_names([row(53, t_half=100.5)], union, hl)
        self.assertTrue(out[0].get("unresolved"))

    def test_unambiguous_row_still_resolves(self):
        union = sorted([FE55, FE59])
        hl = {FE55: 9.86e7, FE59: 3.85e6}
        out = leg.resolve_isotope_names([row(59, t_half=3.9e6)], union, hl)
        self.assertEqual(out[0]["kza"], FE59)

    def test_stable_signature_resolves_when_unique(self):
        # t_half=-1 (stable/unknown print) resolves only when a single
        # isobar candidate lacks an idx half-life.
        union = sorted([TI53, V53])
        hl = {V53: 96.0}  # TI53 absent from the half-life table
        out = leg.resolve_isotope_names([row(53, t_half=-1.0)], union, hl)
        self.assertEqual(out[0]["kza"], TI53)


if __name__ == "__main__":
    unittest.main()
