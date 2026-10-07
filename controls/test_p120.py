#!/usr/bin/env python3
"""P120 E2: the converted history checks still fail when the pinned history does not match.

Each converted check now reads the pinned commit's Git objects. Pointing it at a different commit (whose objects
differ from the frozen maps) must fail it, exactly as a corrupted checkpoint would.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "controls"), str(ROOT / "scripts")]

import check_p116
import check_p116_history
import check_p117_history
import check_p118_history

# A real commit whose control and Rust objects differ from every pinned P116-P118 map.
OTHER_COMMIT = "6db45f75b96fbcdfd2bfda0f6a603c10fbf88672"


class P120PastStillGuarded(unittest.TestCase):
    def test_p118_maps_fail_against_a_different_commit(self):
        discovery = check_p118_history._json(check_p118_history._read(check_p118_history.DISCOVERY))
        check_p118_history._verify_source_maps(discovery)
        with patch.object(check_p118_history, "CHECKPOINT", OTHER_COMMIT):
            with self.assertRaises(ValueError):
                check_p118_history._verify_source_maps(discovery)

    def test_p118_rust_and_handbook_digest_mutations_fail(self):
        discovery = check_p118_history._json(check_p118_history._read(check_p118_history.DISCOVERY))
        for key in ("unchanged_rust_sha256", "unchanged_public_handbook_sha256"):
            changed = copy.deepcopy(discovery)
            path = next(iter(changed[key]))
            changed[key][path] = "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_p118_history._verify_source_maps(changed)

    def test_p117_maps_fail_against_a_different_commit(self):
        discovery, _terminal, _initial = check_p117_history._terminal_archive()
        check_p117_history._verify_terminal_sources(discovery)
        with patch.object(check_p117_history, "CHECKPOINT", OTHER_COMMIT):
            with self.assertRaises(ValueError):
                check_p117_history._verify_terminal_sources(discovery)

    def test_p117_rust_digest_mutation_fails(self):
        discovery, _terminal, _initial = check_p117_history._terminal_archive()
        changed = copy.deepcopy(discovery)
        path = next(iter(changed["amended_g0_rust_sha256"]))
        changed["amended_g0_rust_sha256"][path] = "0" * 64
        with self.assertRaises(ValueError):
            check_p117_history._verify_terminal_sources(changed)

    def test_p116_controls_fail_against_a_different_commit(self):
        g0 = check_p116_history._read("results/g0_p116_twin_waste.json")
        self.assertTrue(check_p116_history._check_control_sources(g0)[1])
        with patch.object(check_p116_history, "COMMIT", OTHER_COMMIT):
            self.assertFalse(check_p116_history._check_control_sources(g0)[1])

    def test_p116_g0_no_write_fails_against_a_different_commit(self):
        # A control file absent from the other commit refuses outright; any other difference changes the report.
        sealed = check_p116.G0.read_bytes()
        with patch.object(check_p116, "P116_IMPLEMENTATION_COMMIT", OTHER_COMMIT):
            try:
                report = check_p116._g0_base(verify_current_sources=False)
            except ValueError:
                return
        self.assertNotEqual(check_p116._json_bytes(report), sealed)

    def test_p116_g0_no_write_rejects_malformed_digests(self):
        hashes = {name: "0" * 64 for name in check_p116.CONTROL_FILES}
        self.assertTrue(check_p116._safe_control_hashes(hashes, live=False))
        first = next(iter(hashes))
        self.assertFalse(check_p116._safe_control_hashes({**hashes, first: "not-a-digest"}, live=False))
        self.assertFalse(check_p116._safe_control_hashes({**hashes, "../outside": "0" * 64}, live=False))


if __name__ == "__main__":
    unittest.main()
