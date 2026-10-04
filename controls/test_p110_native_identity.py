"""Source-only regressions for P110 native source identity controls."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import p110_composition_solve_control as fixture
from p110_native_identity import (_certificate_errors, inspect_decay_bytes,
                                  nb94_omission_evidence_valid, positive_missing_target_evidence)


def _mutate(raw: bytes, *, mat: int, sequence: int, field: int,
            value: float | int, integer: bool = False) -> bytes:
    lines = raw.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if (int(line[66:70]) == mat and int(line[70:72]) == 8
                and int(line[72:75]) == 457 and int(line[75:80]) == sequence):
            rendered = f"{value:11d}" if integer else f"{float(value):11.4E}"
            lines[index] = line[:field * 11] + rendered + line[(field + 1) * 11:]
            return ("\n".join(lines) + "\n").encode("ascii")
    raise AssertionError(f"MF8/MT457 record not found: MAT={mat}, sequence={sequence}")


class NativeIdentityTests(unittest.TestCase):
    def test_fe58_missing_target_requires_positive_atoms_on_fe_basis(self):
        rows=[{"nuclide":"Fe58","atoms_per_g":2.5e19,
               "target":{"activation_target_present":False}}]
        self.assertTrue(positive_missing_target_evidence(rows,"Fe58"))
        self.assertFalse(positive_missing_target_evidence(
            [{**rows[0],"atoms_per_g":0.0}],"Fe58"))
        self.assertFalse(positive_missing_target_evidence(
            [{**rows[0],"target":{"activation_target_present":True}}],"Fe58"))

    def test_nb94_omission_requires_literal_positive_rate_and_matching_defect(self):
        defect={"class":"products_no_evaluated_decay_data","rate_coefficient_per_s":1e-12,
                "largest":[{"name":"41094_0","rate_coefficient_per_s":1e-12}]}
        completeness={"status":"incomplete","reaction_channel":{"defects":[defect]},
                      "unquantified":[{"class":"targets_absent_from_decay_library","names":["41094_0"]}]}
        omission={"products_no_evaluated_decay_data":{"41094_0":1e-12},
                  "targets_absent_from_decay_library":["41094_0"],"completeness":completeness}
        self.assertTrue(nb94_omission_evidence_valid(omission,completeness,completeness))
        for changed in (
            {**omission,"products_no_evaluated_decay_data":{}},
            {**omission,"products_no_evaluated_decay_data":{"41094_0":0.0}},
            {**omission,"products_no_evaluated_decay_data":{"41094_0":float("inf")}},
            {**omission,"targets_absent_from_decay_library":[]},
            {**omission,"completeness":{"status":"complete"}},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(nb94_omission_evidence_valid(changed,completeness,completeness))
        mismatched={**completeness,"reaction_channel":{"defects":[{**defect,"rate_coefficient_per_s":0.0}]}}
        self.assertFalse(nb94_omission_evidence_valid(omission,mismatched,mismatched))
        for malformed in (None,[],"not-an-object"):
            with self.subTest(malformed=malformed):
                self.assertFalse(nb94_omission_evidence_valid(malformed,malformed,malformed))

    def test_index_file_uses_native_library_stem_convention(self):
        with tempfile.TemporaryDirectory(prefix="p110-index-") as temporary:
            root = Path(temporary)
            generated = fixture.write_fixture(root)
            for variant in ("complete", "omit_fe58_target", "omit_nb94_decay"):
                paths = generated[variant]["paths"]
                library = Path(paths["library"])
                stem = library.with_suffix("")
                expected = stem.with_name(stem.name + "_index.json")
                self.assertEqual(Path(paths["index"]), expected)
                self.assertTrue((root / expected).is_file())
                self.assertEqual(hashlib.sha256((root / expected).read_bytes()).hexdigest(),
                                 generated[variant]["sha256"]["index"])
                self.assertEqual(json.loads((root / expected).read_text())["sha256_npz"],
                                 generated[variant]["sha256"]["library"])

    def test_frozen_decay_population_and_omitted_nb94_variant(self):
        self.assertEqual(inspect_decay_bytes(fixture.decay_bytes(), False), [])
        self.assertEqual(inspect_decay_bytes(fixture.decay_bytes(omit_nb94=True), True), [])

    def test_decay_parser_rejects_planted_identity_and_mode_mutations(self):
        raw = fixture.decay_bytes()
        mutations = {
            "head_nst": _mutate(raw, mat=2601, sequence=1, field=4, value=0, integer=True),
            "half_life": _mutate(raw, mat=4102, sequence=2, field=0, value=2.0e11),
            "mean_energy_payload": _mutate(raw, mat=4102, sequence=3, field=0, value=1.0),
            "rtyp": _mutate(raw, mat=4102, sequence=5, field=0, value=2.0),
            "rfs": _mutate(raw, mat=4102, sequence=5, field=1, value=1.0),
            "branch_ratio": _mutate(raw, mat=4102, sequence=5, field=4, value=0.5),
        }
        for name, changed in mutations.items():
            with self.subTest(name=name):
                self.assertTrue(inspect_decay_bytes(changed, False))

    def test_certificate_fixture_hash_is_bound_to_actual_bytes(self):
        payloads = {"library": b"npz", "index": b"index", "decay": b"decay"}
        paths = {name: f"target/{name}.dat" for name in payloads}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
        variant = {"paths": paths, "sha256": hashes}
        inputs = {
            "library": {"path": paths["library"], "sha256": hashes["library"]},
            "library_index": {"path": paths["index"], "sha256": hashes["index"]},
            "decay_primary": {"path": paths["decay"], "sha256": hashes["decay"]},
            "decay_fallback": None,
            "decay_overrides": None,
            "fission_yields": [],
        }
        record = {"run_certificate": {"inputs": inputs, "mode": "coupled", "prune": "reach",
                    "bmin_atoms_per_g": 0.0, "cram": "CRAM-16, frozen", "material_basis": "wt_percent"}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, path in paths.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payloads[name])
            self.assertEqual(_certificate_errors(record, variant, root, "fixture"), [])
            (root / paths["library"]).write_bytes(b"mutated")
            self.assertTrue(_certificate_errors(record, variant, root, "fixture"))


if __name__ == "__main__":
    unittest.main()
