"""Source-only P113 fixture and G0 seal/replay regressions."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p113 as control
import p113_twin_control as oracle


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class P113SealTests(unittest.TestCase):
    def test_fixture_check_accepts_exact_independent_population_and_rejects_mutations(self):
        fixture = oracle.frozen_fixture()
        with tempfile.TemporaryDirectory(prefix="p113-fixture-seal-") as temp:
            path = Path(temp) / "cases.json"
            path.write_bytes(control._json_bytes(fixture))
            with patch.object(control, "FIXTURE", path):
                checked = control._fixture_check()
                self.assertTrue(checked["pass"], checked)
                self.assertEqual(checked["request_count"], len(fixture["requests"]))
                for mutate in (
                    lambda value: value["requests"].pop(),
                    lambda value: value["requests"][0].update({"id": ""}),
                    lambda value: value["requests"][0]["expected"].update(
                        {"request_component_target_count": True}),
                    lambda value: value["requests"][0]["expected"].pop("waste_facility_coverage"),
                ):
                    changed = copy.deepcopy(fixture)
                    mutate(changed)
                    path.write_bytes(control._json_bytes(changed))
                    self.assertFalse(control._fixture_check()["pass"])

    def test_safe_control_hashes_reject_missing_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="p113-control-hashes-") as temp:
            root = Path(temp) / "repo"
            (root / "controls").mkdir(parents=True)
            (root / "data").mkdir()
            files = ("controls/check.py", "controls/oracle.py", "data/pack.json")
            hashes = {}
            for relative in files:
                path = root / relative
                path.write_bytes(relative.encode())
                hashes[relative] = _sha(relative.encode())
            with patch.object(control, "ROOT", root), patch.object(control, "CONTROL_FILES", files):
                self.assertTrue(control._safe_control_hashes(hashes))
                self.assertFalse(control._safe_control_hashes({**hashes, "../outside": "0" * 64}))
                self.assertFalse(control._safe_control_hashes({key: value for key, value in hashes.items()
                                                               if key != files[-1]}))
                outside = Path(temp) / "outside.json"
                outside.write_bytes(b"outside")
                (root / "controls/escape.py").symlink_to(outside)
                linked = {**hashes, "controls/escape.py": _sha(b"outside")}
                patched_files = (*files, "controls/escape.py")
                with patch.object(control, "CONTROL_FILES", patched_files):
                    self.assertFalse(control._safe_control_hashes(linked))

    def test_g0_base_captures_prior_verdicts_and_source_seals(self):
        # This is the real source-only G0 derivation; historical results and
        # the frozen P105 vector seals are read from their retained artifacts.
        report = control._g0_base()
        self.assertTrue(report["pass"], report)
        self.assertTrue(report["p105_verified"])
        self.assertTrue(report["p112_verified"])
        self.assertTrue(report["p105_source_vector_seals_match"])
        self.assertEqual(report["repair_rounds"], 0)
        expected_verdicts = {"P105": "P105-PASS", "P107": "P107-PASS",
                             "P108": "P108-PASS", "P109": "P109-FAIL",
                             "P110": "P110-PASS", "P111": "P111-FAIL",
                             "P112": "P112-PASS"}
        for phase, wanted in expected_verdicts.items():
            self.assertEqual(report["prior_verdict_details"][phase]["verdict"], wanted)
            self.assertTrue(report["prior_verdict_sha256"][f"results/{phase.lower()}_verdict.json"])

    def test_g0_stable_write_read_and_prior_reply_tampering_use_temporary_path(self):
        with tempfile.TemporaryDirectory(prefix="p113-g0-seal-") as temp:
            path = Path(temp) / "g0.json"
            with patch.object(control, "G0", path):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(control.g0(seal=True), 0)
                    persisted = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(control.g0(no_write=True), 0)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), persisted)
                    changed = copy.deepcopy(persisted)
                    changed.update({"p105_verified": False, "p112_verified": False,
                                    "prior_verdicts_match": False,
                                    "p105_source_vector_seals_match": False})
                    changed["prior_verdict_details"]["P105"]["verdict"] = "P105-LOCAL-PASS"
                    path.write_bytes(control._json_bytes(changed))
                    self.assertNotEqual(control.g0(no_write=True), 0)


if __name__ == "__main__":
    unittest.main()
