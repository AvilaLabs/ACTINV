#!/usr/bin/env python3
"""Pure-Python unit tests of controls/p178_ce_rates.py (no OpenMC, no library files, no subprocess).
Run: python3 -m unittest controls/test_p178_ce_rates.py"""
import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p178_ce_rates as p178  # noqa: E402

p173 = p178.p173
P173_REGIONS = {f"{m}:{a:g}-{b:g}" for m, shells in {"S": [(10, 12), (20, 25), (40, 50)],
                                                      "W": [(10, 10.5), (10.5, 12), (15, 20)],
                                                      "C": [(10, 12), (20, 25)],
                                                      "K": [(50, 60), (100, 120)]}.items() for a, b in shells}


def element_of(nuclide):
    return nuclide.rstrip("0123456789")


class Models(unittest.TestCase):
    def test_model_tables_agree(self):
        keys = set(p178.MODEL_ORDER)
        for table in (p178.SHELLS, p178.BODY_RADIUS, p178.MODEL_MATERIAL, p178.REACTIONS):
            self.assertEqual(set(table), keys)
        self.assertEqual(set(p178.MODEL_MATERIAL.values()), set(p178.MATERIALS))

    def test_installed_into_p173(self):
        self.assertEqual(p173.MODEL_ORDER, p178.MODEL_ORDER)
        self.assertEqual(p173.SCHEMA, "actinv-p178-ce-rates-1")
        self.assertNotEqual(p178.SEED_BASE, 173000)
        seeds = [p173.model_spec(m)["seed"] for m in p178.MODEL_ORDER]
        self.assertEqual(len(set(seeds)), len(seeds))
        self.assertTrue(all(s >= 178000 for s in seeds))

    def test_compositions_normalise(self):
        for name, spec in p178.MATERIALS.items():
            raw = math.fsum(spec["wo"].values())
            self.assertAlmostEqual(raw, 1.0, delta=0.01, msg=name)  # Inconel 718 nominal values sum to 1.006
            self.assertAlmostEqual(math.fsum(p173.final_wo(name).values()), 1.0, places=12, msg=name)
            self.assertTrue(all(w > 0 for w in spec["wo"].values()), name)
            self.assertGreater(spec["density_g_cm3"], 1.0)
        for name in ("eurofer97", "w_heavy_alloy", "copper"):
            self.assertAlmostEqual(math.fsum(p178.MATERIALS[name]["wo"].values()), 1.0, places=12, msg=name)

    def test_densities(self):
        self.assertEqual([p178.MATERIALS[p178.MODEL_MATERIAL[m]]["density_g_cm3"] for m in p178.MODEL_ORDER],
                         [7.75, 8.19, 17.0, 8.96])

    def test_elements_known(self):
        for spec in p178.MATERIALS.values():
            for el in spec["wo"]:
                self.assertIn(el, p178.ELEMENT_Z, el)
        for rows in p178.REACTIONS.values():
            for target, _, _ in rows:
                self.assertIn(element_of(target), p178.ELEMENT_Z, target)

    def test_regions_inside_bodies_and_disjoint(self):
        for model, shells in p178.SHELLS.items():
            body = p178.BODY_RADIUS[model]
            prev = 0.0
            for a, b in shells:
                self.assertGreater(a, 0)
                self.assertLess(a, b)
                self.assertGreaterEqual(a, prev, f"{model} {a}-{b} overlaps the previous region")
                prev = b
            self.assertLess(prev, body, model)

    def test_layout_is_contiguous_and_reaches_body(self):
        for model in p178.MODEL_ORDER:
            lay = p173.layout(p173.model_spec(model)["shells"])
            for (_, b1, _), (a2, _, _) in zip(lay, lay[1:]):
                self.assertEqual(b1, a2)
            self.assertEqual(lay[-1][1], p178.BODY_RADIUS[model])
            self.assertEqual(lay[0][0], p178.SHELLS[model][0][0])
            self.assertEqual([(a, b) for a, b, t in lay if t], [tuple(s) for s in p178.SHELLS[model]])
            self.assertFalse(lay[-1][2])

    def test_p173_layout_unchanged(self):
        self.assertEqual(p178.layout([(10, 12), (20, 25)]), [(10, 12, True), (12, 20, False), (20, 25, True)])

    def test_region_names_unique_and_disjoint_from_p173(self):
        names = [r for m in p178.MODEL_ORDER for r in p173.regions_of(m)]
        self.assertEqual(len(names), 11)
        self.assertEqual(len(set(names)), len(names))
        self.assertFalse(set(names) & P173_REGIONS)
        self.assertIn("E:5-7", names)
        self.assertIn("H:1-2", names)

    def test_rows(self):
        for model, rows in p178.REACTIONS.items():
            composition = p173.final_wo(p178.MODEL_MATERIAL[model])
            keys = [(t, mt) for t, mt, _ in rows]
            self.assertEqual(len(set(keys)), len(keys), f"duplicate row in {model}")
            for target, mt, product in rows:
                self.assertIn(element_of(target), composition, f"{model}: {target} not in the composition")
                self.assertIn(mt, p173.MT_SCORE)
                self.assertTrue(re.fullmatch(r"[A-Z][a-z]?\d+", target), target)
                p173.product_za(target, mt)  # raises if the target or MT is malformed
                self.assertEqual(p173.product_za(target, mt), p173.za(product), f"{model}: {target} {mt} -> {product}")

    def test_expected_rows_count(self):
        rows = list(p173.expected_rows())
        self.assertEqual(len(rows), sum(len(p173.regions_of(m)) * len(p178.REACTIONS[m]) for m in p178.MODEL_ORDER))
        self.assertEqual(len(rows), len(set(rows)))

    def test_isotope_abundance_ok_for_listed_targets(self):
        # every target nuclide's mass number lies in the natural range of its element (guards typos like Ni85)
        natural = {"Fe": range(54, 59), "Cr": range(50, 55), "Mn": (55,), "W": (180, 182, 183, 184, 186),
                   "Ta": (180, 181), "V": (50, 51), "N": (14, 15), "Ni": (58, 60, 61, 62, 64), "Nb": (93,),
                   "Mo": (92, 94, 95, 96, 97, 98, 100), "Co": (59,), "Ti": (46, 47, 48, 49, 50), "Al": (27,),
                   "Cu": (63, 65)}
        for rows in p178.REACTIONS.values():
            for target, _, _ in rows:
                el = element_of(target)
                self.assertIn(int(target[len(el):]), natural[el], target)



def endf_line(c1, c2, l1, l2, n1, n2, mf, mt, seq=1):
    """One 80-column ENDF-6 record line (MAT 9999)."""
    def flt(v):
        return f"{v:.4e}" if v else "0.0"
    return f"{flt(c1):>11}{flt(c2):>11}{l1:11d}{l2:11d}{n1:11d}{n2:11d}{9999:4d}{mf:2d}{mt:3d}{seq:5d}"


def tape(lru=1, nis=1, extra_mf2=(), with_mf2=True):
    lines = [endf_line(0, 0, 0, 0, 0, 0, 1, 451)]
    if with_mf2:
        lines += [endf_line(26056, 55.5, 0, 0, nis, 0, 2, 151), endf_line(26056, 1.0, 0, 0, 1, 0, 2, 151),
                  endf_line(1e-5, 8e5, lru, 3, 0, 0, 2, 151), endf_line(0, 0, 0, 0, 0, 0, 2, 0)]
        lines += [endf_line(0, 0, 0, 0, 1, 0, 2, mt) for mt in extra_mf2]
    return "\n".join(lines) + "\n"


class Mixture(unittest.TestCase):
    def test_channel_mapping_follows_the_solver(self):
        self.assertEqual([p178.channel_of_mt(mt) for mt in (2, 18, 19, 102, 16, 103, 107, 1, 4)],
                         [1, 2, 2, 3, 0, 0, 0, 0, 0])
        self.assertEqual(p178.CHANNELS, ("total", "elastic", "fission", "capture"))

    def test_nuclide_names(self):
        self.assertEqual(p178.actinv_nuclide("Fe56"), "Fe56")
        self.assertEqual(p178.actinv_nuclide("Ta180_m1"), "Ta180m1")
        with self.assertRaises(ValueError):
            p178.actinv_nuclide("Fe")

    def test_composition_is_openmc_expansion(self):
        comp = p178.composition_from_openmc({"Fe56": 0.07, "Ta180_m1": 1e-9, "Ni58": 0.0, "Fe54": 0.005})
        self.assertEqual(comp, {"Fe54": 0.005, "Fe56": 0.07, "Ta180m1": 1e-9})

    def test_tape_check(self):
        self.assertEqual(p178.tape_resolved_check(tape()), (True, None))
        self.assertFalse(p178.tape_resolved_check(tape(lru=0))[0])
        self.assertFalse(p178.tape_resolved_check(tape(lru=2))[0])
        self.assertFalse(p178.tape_resolved_check(tape(nis=2))[0])
        self.assertFalse(p178.tape_resolved_check(tape(with_mf2=False))[0])
        ok, reason = p178.tape_resolved_check(tape(extra_mf2=(152,)))
        self.assertFalse(ok)
        self.assertIn("152", reason)

    def test_select_tapes(self):
        with tempfile.TemporaryDirectory() as d:
            stage = Path(d)
            texts = {"Fe56.endf": tape(), "Ta180_m1.endf": tape(), "H1.endf": tape(lru=0), "Cr52.endf": tape()}
            manifest = {}
            for name, text in texts.items():
                (stage / name).write_text(text)
                manifest[name] = {"sha256": p173.sha256_file(stage / name)}
            (stage / "Cr52.endf").unlink()  # in the manifest, absent on disk
            tapes, skipped = p178.select_tapes(["Fe56", "Ta180_m1", "H1", "Cr52", "Zz9"], stage, manifest)
            self.assertEqual([(t["name"], t["nuclide"]) for t in tapes], [("Fe56", "Fe56"), ("Ta180_m1", "Ta180m1")])
            self.assertEqual(tapes[0]["sha256"], manifest["Fe56.endf"]["sha256"])
            self.assertEqual(tapes[0]["path"], str(stage / "Fe56.endf"))
            self.assertEqual(set(skipped), {"H1", "Cr52", "Zz9"})
            self.assertIn("LRU=0", skipped["H1"])
            manifest["Fe56.endf"] = {"sha256": "0" * 64}
            with self.assertRaises(ValueError):
                p178.select_tapes(["Fe56"], stage, manifest)

    def spec(self, **kw):
        tapes = [{"nuclide": "Fe56", "name": "Fe56", "path": "/s/Fe56.endf", "sha256": "ab" * 32}]
        args = dict(model="E", region="E:5-7", flux=[1, 2, 3], atom_densities={"Fe56": 0.07, "Fe54": 0.004},
                    tapes=tapes, bounds=[1.0, 2.0, 3.0, 4.0], library={"path": "/l.npz", "sha256": "cd" * 32},
                    decay={"primary": "/a", "fallback": "/b"})
        args.update(kw)
        return p178.build_mixture_spec(**args)

    def test_spec_shape(self):
        spec = self.spec()
        self.assertEqual(spec["self_shielding"],
                         {"mixture": {"evaluation_files": [{"path": "/s/Fe56.endf", "sha256": "ab" * 32}],
                                      "profile": "endfb8"}})
        self.assertEqual(spec["options"]["temperature_K"], 293.6)
        self.assertEqual(spec["material"]["basis"], "atom_fraction")
        self.assertEqual(spec["material"]["composition"], {"Fe54": 0.004, "Fe56": 0.07})
        self.assertEqual(spec["spectrum"], {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0],
                                            "flux_per_group": [1.0, 2.0, 3.0], "descending": False})
        self.assertIn("ledger", spec["options"]["outputs"])
        self.assertEqual(spec["library"]["sha256"], "cd" * 32)
        json.dumps(spec)  # serialisable
        self.assertNotIn("table", spec["self_shielding"])
        self.assertNotIn("geometry", spec["self_shielding"])

    def test_channel_scales(self):
        self.assertIsNone(p178.channel_scales(None, 5))
        sc = p178.channel_scales({"1": [0.9, 0.8, 1.0, 0.5], "3": [0.7, 1.0, 1.0, 0.25]}, 5)
        self.assertEqual(sc[3], [1.0, 0.5, 1.0, 0.25, 1.0])
        self.assertEqual(sc[0], [1.0, 0.9, 1.0, 0.7, 1.0])
        with self.assertRaises(ValueError):
            p178.channel_scales({"9": [1, 1, 1, 1]}, 5)
        with self.assertRaises(ValueError):
            p178.channel_scales({"1": [1, 1]}, 5)

    def library(self):
        # target 0 = Fe56; MT 102 has two product rows, MT 16 one, MT 2 only a total row (ZAP -1)
        targets = [{"za": 26056, "liso": 0}]
        rows = [[0, 102, 26057], [0, 102, 26058], [0, 16, 26055], [0, 2, -1]]
        sig = [[1.0, 2.0, 3.0], [0.5, 0.5, 0.5], [0.0, 1.0, 1.0], [10.0, 10.0, 10.0]]
        return rows, sig, targets, p173.library_selection(rows)

    def test_mixture_reaction_applies_channel_scale(self):
        rows, sig, targets, sel = self.library()
        flux, sd = [1.0, 1.0, 2.0], [0.1, 0.1, 0.1]
        applied = {"1": [0.5, 0.4, 1.0, 0.5], "2": [0.2, 0.1, 1.0, 0.25]}
        cap = p178.mixture_reaction(rows, sig, targets, sel, "Fe56", 102, flux, sd, applied)
        self.assertAlmostEqual(cap["R_mixture"], (1 + 1 + 1.5) + (0.5 + 0.25 + 0.25))
        self.assertAlmostEqual(cap["R_infinite_dilution"], (1 + 2 + 6) + (0.5 + 0.5 + 1.0))
        self.assertEqual((cap["channel"], cap["groups_scaled"], cap["min_scale"], cap["target_covered"]),
                         ("capture", 2, 0.25, True))
        n2n = p178.mixture_reaction(rows, sig, targets, sel, "Fe56", 16, flux, sd, applied)  # total channel
        self.assertAlmostEqual(n2n["R_mixture"], 1.0 * 0.5 * 1 + 1.0 * 0.2 * 2)
        self.assertEqual(n2n["channel"], "total")
        el = p178.mixture_reaction(rows, sig, targets, sel, "Fe56", 2, flux, sd, applied)  # elastic channel
        self.assertAlmostEqual(el["R_mixture"], 10 * (1 + 0.4 + 0.1 * 2))
        self.assertEqual(el["rows"], [3])

    def test_uncovered_target_is_unscaled(self):
        rows, sig, targets, sel = self.library()
        res = p178.mixture_reaction(rows, sig, targets, sel, "Fe56", 102, [1.0, 1.0, 2.0], [0.0] * 3, None)
        self.assertFalse(res["target_covered"])
        self.assertEqual(res["R_mixture"], res["R_infinite_dilution"])
        self.assertEqual(res["scale_effective"], 1.0)
        self.assertEqual(res["groups_scaled"], 0)
        base = p173.actinv_reaction(rows, sig, targets, sel, "Fe56", 102, [1.0, 1.0, 2.0], [0.0] * 3)
        self.assertEqual(res["R_infinite_dilution"], base["R_actinv"])

    def test_absent_reaction(self):
        rows, sig, targets, sel = self.library()
        for target, mt in (("Fe56", 107), ("Ni58", 102)):
            res = p178.mixture_reaction(rows, sig, targets, sel, target, mt, [1.0] * 3, [0.0] * 3, None)
            self.assertEqual(res["status"], "not_scored")


class Pins(unittest.TestCase):
    def test_library_rebound_and_pinned(self):
        self.assertEqual(p173.ENDF8_NPZ, Path.home() / "nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz")
        self.assertEqual(p173.ENDF8_INDEX, Path.home() / "nuclear-data/endfb-viii.1-p178/endfb81_all_709g_index.json")
        self.assertIs(p173.PINNED, p178.PINNED)
        for name, path in (("endf8_library", p173.ENDF8_NPZ), ("endf8_index", p173.ENDF8_INDEX),
                           ("stage_manifest", p178.STAGE_MANIFEST)):
            self.assertEqual(p173.PINNED[name][0], path)
            self.assertRegex(p173.PINNED[name][1], r"^[0-9a-f]{64}$")
        self.assertEqual(p178.PLACEHOLDER, "TO-BE-FILLED-AT-REGISTRATION")
        self.assertEqual(p178.unregistered_pins(), [])
        self.assertRegex(p173.PINNED["fispact_709_groups"][1], r"^[0-9a-f]{64}$")  # P173's other pins are kept

    def test_scored_mode_refuses_with_placeholders(self):
        with mock.patch.object(p178, "rehearsal", return_value=False), \
                mock.patch.object(p178, "ACTINV_BIN_SHA256_P178", p178.PLACEHOLDER):
            with self.assertRaises(SystemExit) as cm:
                p178.require_registered()
            self.assertIn("actinv_bin", str(cm.exception))

    def test_binary_pin_is_registered_and_rebound(self):
        self.assertRegex(p178.ACTINV_BIN_SHA256_P178, r"^[0-9a-f]{64}$")
        self.assertEqual(p173.ACTINV_BIN_SHA256, p178.ACTINV_BIN_SHA256_P178)
        self.assertNotIn("actinv_bin", p178.PINNED)
        with mock.patch.object(p178, "ACTINV_BIN_SHA256_P178", p178.PLACEHOLDER):
            self.assertIn("actinv_bin", p178.unregistered_pins())

    def test_rehearsal_does_not_refuse(self):
        with mock.patch.object(p178, "rehearsal", return_value=True):
            p178.require_registered()

    def test_registered_pin_must_match_file(self):
        with tempfile.TemporaryDirectory() as d:
            files = {}
            for name in p178.VERIFIED_PINS:
                f = Path(d) / name
                f.write_text(name)
                files[name] = (f, p173.sha256_file(f))
            with mock.patch.object(p178, "rehearsal", return_value=False), \
                    mock.patch.object(p178, "PINNED", {**p178.PINNED, **files}):
                with mock.patch.object(p178, "ACTINV_BIN_SHA256_P178", p178.PLACEHOLDER):
                    with self.assertRaises(SystemExit) as cm:  # every file pin filled in, the binary pin not
                        p178.require_registered()
                    self.assertIn("actinv_bin", str(cm.exception))
                binary = Path(d) / "actinv"
                binary.write_text("binary")
                with mock.patch.object(p173, "ACTINV_BIN", binary), \
                        mock.patch.object(p178, "ACTINV_BIN_SHA256_P178", p173.sha256_file(binary)):
                    p178.require_registered()
                    binary.write_text("another binary")  # the file no longer matches the pin
                    with self.assertRaises(SystemExit) as cm:
                        p178.require_registered()
                    self.assertIn("actinv binary", str(cm.exception))
                    binary.write_text("binary")
                    (Path(d) / "endf8_index").write_text("changed")
                    with self.assertRaises(SystemExit) as cm:
                        p178.require_registered()
                    self.assertIn("endf8_index", str(cm.exception))


class Assembly(unittest.TestCase):
    def rows(self):
        base = {"model": "B", "region": "B:3-5", "target": "Cu63", "mt": 102, "status": "scored", "R_openmc": 2.0,
                "R_openmc_se": 0.02, "R_actinv": 4.0, "R_actinv_se_bound": 0.04, "r": math.log(2.0)}
        return [dict(base), dict(base, target="Cu65", mt=16), dict(base, target="Cu63", mt=103),
                dict(base, status="not_scored", target="Cu63", mt=107), dict(base, target="Cu63", mt=16)]

    def reactions(self):
        ok = {"status": "ok", "R_mixture": 2.2, "R_mixture_se_bound": 0.022, "scale_effective": 0.55,
              "target_covered": True, "groups_scaled": 12, "min_scale": 0.1}
        return {"Cu63|102": ok,
                "Cu65|16": dict(ok, R_mixture=4.0, scale_effective=1.0, groups_scaled=0, min_scale=None),
                "Cu63|103": {"status": "not_scored", "reason": "no row"}, "Cu63|107": ok,
                "Cu63|16": dict(ok, R_mixture=0.0)}

    def test_rows_gain_mixture_fields(self):
        rows = self.rows()
        mixtures = {"B": {"regions": {"B:3-5": {"reactions": self.reactions()}}}}
        p178.add_mixture_to_rows(rows, mixtures)
        capture = rows[0]
        self.assertAlmostEqual(capture["r_mix"], math.log(2.2 / 2.0))
        self.assertAlmostEqual(capture["r"], math.log(2.0))
        self.assertEqual((capture["R_openmc"], capture["R_actinv"], capture["R_mixture"]), (2.0, 4.0, 2.2))
        self.assertAlmostEqual(capture["se_r_mix"], math.hypot(0.022 / 2.2, 0.02 / 2.0))
        self.assertEqual((capture["mixture_status"], capture["mixture_groups_scaled"],
                          capture["mixture_target_covered"]), ("scored", 12, True))
        self.assertAlmostEqual(rows[1]["r_mix"], math.log(2.0))
        self.assertIsNone(rows[2]["r_mix"])
        self.assertIn("no row", rows[2]["mixture_reason"])
        self.assertIsNone(rows[3]["r_mix"])  # a row P173 did not score stays unscored
        self.assertEqual(rows[3]["mixture_reason"], "row not scored")
        self.assertIsNone(rows[4]["r_mix"])
        self.assertIn("zero", rows[4]["mixture_reason"])
        for row in rows:
            for key in ("R_mixture", "r_mix", "se_r_mix", "mixture_status", "mixture_target_covered"):
                self.assertIn(key, row)

    def test_assemble_wraps_p173(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir, record = Path(d), Path(d) / "record.json"
            for m in p178.MODEL_ORDER:
                doc = {"tapes": [{"nuclide": "Cu63", "path": "/p", "sha256": "0" * 64}],
                       "nuclides_without_tape": {"H1": "x"}, "actinv_bin_sha256": "1" * 64, "actinv_version": "v",
                       "regions": {"B:3-5": {"spec_sha256": "2" * 64, "mixture": {"covered": ["Cu63"]},
                                             "reactions": self.reactions(), "applied_factors": {}}}}
                (out_dir / f"actinv_mixture_{m}.json").write_text(json.dumps(doc))
            manifest = out_dir / "stage_manifest.json"
            manifest.write_text("{}")

            def fake_p173_assemble(path):
                Path(path).write_text(json.dumps({"schema": "x", "rows": self.rows()}))
            with mock.patch.object(p178, "OUT_DIR", out_dir), \
                    mock.patch.object(p173, "assemble", fake_p173_assemble), \
                    mock.patch.object(p178, "STAGE_MANIFEST", manifest):
                p178.assemble_p178(record)
            result = json.loads(record.read_text())
            self.assertEqual(len(result["rows"]), 5)
            self.assertAlmostEqual(result["rows"][0]["r_mix"], math.log(1.1))
            arm = result["mixture_arm"]
            self.assertEqual(arm["temperature_K"], 293.6)
            self.assertEqual(arm["p178_constants_sha256"], p178.constants_digest())
            self.assertEqual(arm["p173_harness_sha256"], p173.sha256_file(p178.P173_HARNESS))
            self.assertEqual([c["where"] for c in result["environment"]["cgroups"]],
                             [f"actinv_mixture:{m}" for m in p178.MODEL_ORDER])
            self.assertEqual(sorted(arm["models"]), sorted(p178.MODEL_ORDER))
            self.assertEqual(arm["models"]["B"]["nuclides_without_tape"], {"H1": "x"})
            self.assertNotIn("reactions", arm["models"]["B"]["regions"]["B:3-5"])

    def test_mixture_digest_stable(self):
        self.assertRegex(p178.mixture_constants_digest(), r"^[0-9a-f]{64}$")
        self.assertEqual(p178.mixture_constants_digest(), p178.mixture_constants_digest())


class Digest(unittest.TestCase):
    def test_stable_and_hex(self):
        d = p178.constants_digest()
        self.assertEqual(d, p178.constants_digest())
        self.assertRegex(d, r"^[0-9a-f]{64}$")

    def test_differs_from_p173_digest(self):
        self.assertNotEqual(p178.constants_digest(), p173.constants_digest())

    def test_sensitive_to_models(self):
        before = p178.constants_digest()
        saved = p178.REACTIONS["B"]
        try:
            p178.REACTIONS["B"] = saved[:-1]
            self.assertNotEqual(p178.constants_digest(), before)
        finally:
            p178.REACTIONS["B"] = saved
        self.assertEqual(p178.constants_digest(), before)


class Rehearsal(unittest.TestCase):
    def test_paths(self):
        if p178.rehearsal():
            self.assertEqual(p178.OUT_DIR, p178.ROOT / "target" / "p178-rehearsal")
            self.assertLessEqual(p178.PARTICLES_PER_BATCH, 1000)
        else:
            self.assertEqual(p178.OUT_DIR, p178.ROOT / "results" / "p178")
            self.assertEqual(p178.RECORD, p178.ROOT / "results" / "p178_ce_rates.json")
            self.assertEqual(p178.PARTICLES_PER_BATCH, 100_000)
            self.assertEqual(p178.BATCH_MAX, 256)
        self.assertEqual(p173.OUT_DIR, p178.OUT_DIR)
        self.assertEqual(p173.WORK_TAG, p178.WORK_TAG)


if __name__ == "__main__":
    unittest.main()
