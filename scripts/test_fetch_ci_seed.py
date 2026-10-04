#!/usr/bin/env python3
"""Offline fault regressions for the immutable CI seed installer."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fetch_ci_seed as seed


class FaultStream(io.BytesIO):
    def __init__(self, payload, fail_after=None):
        super().__init__(payload)
        self.fail_after = fail_after

    def read(self, size=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise TimeoutError("injected interrupted response")
        if self.fail_after is not None:
            size = min(size, self.fail_after - self.tell())
        return super().read(size)

    def read1(self, size=-1):
        return self.read(size)


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="actinv-seed-test-")
        self.root = Path(self.temp.name)
        names = [f"tendl/{name}" for name in seed._tendl_names()]
        names += ["decay/endf-b-viii-0_decay.dat", "decay/jeff-3-3_decay.dat", seed.FNS_PATH]
        self.payloads = {name: (f"test bytes: {name}\n".encode()) for name in names}
        self.manifest = {
            "schema": "actinv-ci-data-seed-1", "release": seed.RELEASE,
            "total_bytes": sum(len(payload) for payload in self.payloads.values()), "files": [],
        }
        authorities = {}
        for name, payload in self.payloads.items():
            digest = hashlib.sha256(payload).hexdigest()
            basename = Path(name).name
            if name.startswith("tendl/"):
                provider, source = seed.PROVIDERS["tendl"], "https://source.invalid/" + basename
            elif name.endswith("endf-b-viii-0_decay.dat"):
                provider, source = seed.PROVIDERS["endf"], seed.DECAY_SOURCES[name][0]
            elif name.endswith("jeff-3-3_decay.dat"):
                provider, source = seed.PROVIDERS["jeff"], seed.DECAY_SOURCES[name][0]
            else:
                provider, source = seed.PROVIDERS["fns"], "https://source.invalid/fns.zip"
            authorities[name] = (digest, source, provider, len(payload))
            self.manifest["files"].append({
                "path": name, "bytes": len(payload), "sha256": digest,
                "url": seed.RELEASE_BASE + basename, "provider": provider,
                "source_url": source,
            })
        self.authorities = authorities
        self.manifest_path = self.root / "seed.json"
        self.write_manifest()
        self.pinned_authorities = seed._expected_authorities()
        self.patches = [
            patch.object(seed, "MANIFEST_PATH", self.manifest_path),
            patch.object(seed, "_expected_authorities", return_value=self.authorities),
            patch.object(seed, "TOTAL_BYTES", self.manifest["total_bytes"]),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for item in reversed(self.patches):
            item.stop()

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_manifest_and_tracked_authorities_are_exact(self):
        self.assertTrue(seed.validate_authorities(seed.load_manifest()))
        with patch.object(seed, "_expected_authorities", return_value=self.pinned_authorities), \
                patch.object(seed, "TOTAL_BYTES", 166_789_318):
            real_manifest = seed.load_manifest(seed.ROOT / "scripts/ci_data_seed.json")
            self.assertTrue(seed.validate_authorities(real_manifest))
        bad = copy.deepcopy(self.manifest)
        bad["files"][0]["url"] = "https://example.invalid/mutable"
        with self.assertRaises(seed.SeedError):
            seed.validate_authorities(bad)
        bad = copy.deepcopy(self.manifest)
        bad["files"].pop()
        with self.assertRaises(seed.SeedError):
            seed.validate_authorities(bad)
        bad = copy.deepcopy(self.manifest)
        bad["files"][1] = copy.deepcopy(bad["files"][0])
        with self.assertRaises(seed.SeedError):
            seed.validate_authorities(bad)
        bad = copy.deepcopy(self.manifest)
        bad["files"][0]["provider"] = "unattributed"
        with self.assertRaises(seed.SeedError):
            seed.validate_authorities(bad)
        bad = copy.deepcopy(self.manifest)
        bad["files"][0]["bytes"] += 1
        bad["files"][1]["bytes"] -= 1
        with self.assertRaises(seed.SeedError):
            seed.validate_authorities(bad)

    def test_manifest_rejects_duplicate_keys_and_symlink(self):
        self.manifest_path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        with self.assertRaises(seed.SeedError):
            seed.load_manifest()
        self.manifest_path.unlink()
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        self.manifest_path.symlink_to(outside)
        with self.assertRaises(seed.SeedError):
            seed.load_manifest()

    def test_online_install_is_verified_atomic_and_reused(self):
        destination = self.root / "installed"
        entry = self.manifest["files"][0]
        with patch.object(seed, "_open_release", return_value=io.BytesIO(self.payloads[entry["path"]])) as open_url:
            status = seed._install_file(entry, destination / entry["path"], None)
        self.assertEqual(status, "installed")
        self.assertEqual(open_url.call_args.args[0].full_url, entry["url"])
        with patch.object(seed, "_open_release") as open_url:
            self.assertEqual(seed._install_file(entry, destination / entry["path"], None), "reused")
            open_url.assert_not_called()
        target = destination / entry["path"]
        target.write_bytes(b"x" * len(target.read_bytes()))
        corrupted = target.read_bytes()
        with patch.object(seed, "_open_release") as open_url, self.assertRaises(seed.SeedError):
            seed._install_file(entry, target, None)
        open_url.assert_not_called()
        self.assertEqual(target.read_bytes(), corrupted)

    def test_bad_and_interrupted_streams_leave_no_partial_destination(self):
        entry = self.manifest["files"][0]
        for label, response in (
            ("short", io.BytesIO(b"x")),
            ("long", io.BytesIO(self.payloads[entry["path"]] + b"x")),
            ("interrupted", FaultStream(self.payloads[entry["path"]], fail_after=2)),
        ):
            with self.subTest(label=label):
                destination = self.root / label / entry["path"]
                with patch.object(seed, "_open_release", return_value=response), self.assertRaises(seed.SeedError):
                    seed._install_file(entry, destination, None)
                self.assertFalse(destination.exists())
                self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_deadline_is_checked_after_a_slow_read(self):
        entry = self.manifest["files"][0]
        ticks = iter((0.0, 0.0, float(seed.TOTAL_DEADLINE_S + 1)))
        destination = self.root / "deadline" / entry["path"]
        with patch.object(seed, "_open_release", return_value=io.BytesIO(self.payloads[entry["path"]])), \
                patch.object(seed.time, "monotonic", side_effect=lambda: next(ticks)), \
                self.assertRaises(seed.SeedError):
            seed._install_file(entry, destination, None)
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_unsafe_logical_paths_refused(self):
        for path in ("../escape", "a//b", "a/./b", "a\\b", "/absolute"):
            with self.subTest(path=path), self.assertRaises(seed.SeedError):
                seed._safe_relative(path)

    def test_offline_install_uses_identical_checks_and_fns_placement(self):
        offline = self.root / "offline"
        for logical, payload in self.payloads.items():
            path = offline / logical
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        controls = self.root / "controls-data"
        control_report = seed.install("controls", controls, offline_root=offline)
        self.assertEqual(control_report["files"], 11)
        for entry in self.manifest["files"]:
            if entry["path"].startswith("tendl/") or entry["path"] == "decay/endf-b-viii-0_decay.dat":
                self.assertEqual((controls / entry["path"]).read_bytes(), self.payloads[entry["path"]])
        all_destination = self.root / "all-data"
        all_report = seed.install("all", all_destination, offline_root=offline)
        self.assertEqual(all_report["files"], 13)
        for logical, payload in self.payloads.items():
            self.assertEqual((all_destination / logical).read_bytes(), payload)
        destination = self.root / "fns-data"
        archive = self.root / "fns" / "fns.zip"
        report = seed.install("fns", destination, archive, offline)
        self.assertEqual(report["files"], 3)
        self.assertEqual((destination / "v1.1.0/decay/endf-b-viii-0_decay.dat").read_bytes(),
                         self.payloads["decay/endf-b-viii-0_decay.dat"])
        self.assertEqual((destination / "v1.1.0/decay/jeff-3-3_decay.dat").read_bytes(),
                         self.payloads["decay/jeff-3-3_decay.dat"])
        self.assertEqual(archive.read_bytes(), self.payloads[seed.FNS_PATH])
        archive.write_bytes(b"wrong")
        with self.assertRaises(seed.SeedError):
            seed.install("fns", destination, archive, offline)
        self.assertEqual(archive.read_bytes(), b"wrong")

    def test_offline_seed_rejects_unexpected_files_before_install(self):
        offline = self.root / "offline-extra"
        for logical, payload in self.payloads.items():
            path = offline / logical
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (offline / "extra.dat").write_bytes(b"unexpected")
        destination = self.root / "no-install"
        with self.assertRaises(seed.SeedError):
            seed.install("controls", destination, offline_root=offline)
        self.assertFalse(destination.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_offline_symlink_and_destination_symlink_refused(self):
        entry = self.manifest["files"][0]
        offline = self.root / "offline-symlink"
        offline.mkdir()
        target = self.root / "payload"
        target.write_bytes(self.payloads[entry["path"]])
        (offline / Path(entry["path"]).parent).mkdir(parents=True)
        (offline / entry["path"]).symlink_to(target)
        with self.assertRaises(seed.SeedError):
            seed._install_file(entry, self.root / "destination" / entry["path"], offline)
        dest = self.root / "dest-link"
        dest.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(seed.SeedError):
            seed._ensure_parent(dest, seed._safe_relative(entry["path"]))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_existing_verified_file_through_symlinked_parent_is_refused(self):
        entry = self.manifest["files"][0]
        actual = self.root / "actual-parent"
        target = actual / Path(entry["path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(self.payloads[entry["path"]])
        link = self.root / "linked-parent"
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(seed.SeedError):
            seed._install_file(entry, link / Path(entry["path"]), None)

    def test_http_redirect_downgrade_and_foreign_host_refused(self):
        from urllib.request import Request

        handler = seed._HttpsReleaseRedirect()
        request = Request(seed.RELEASE_BASE + "asset")
        for url in ("http://github.com/asset", "https://example.invalid/asset"):
            with self.subTest(url=url), self.assertRaises(seed.SeedError):
                handler.redirect_request(request, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
