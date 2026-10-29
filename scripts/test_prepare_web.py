"""Regression checks for combining the handbook and the example results with the browser bundle."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from prepare_web import ROOT, prepare
from web_examples import EXAMPLES, workspace_version


class HandbookPackaging(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "handbook"
        self.docs.mkdir()
        (self.docs / "index.html").write_text("current handbook")
        (self.docs / "searchindex.js").write_text("current search")
        self.bundle = self.root / "web"
        self.bundle.mkdir()
        (self.bundle / "index.html").write_text("existing app")

    def test_rebuild_retires_old_chapters_without_replacing_app(self):
        old = self.bundle / "docs"
        old.mkdir()
        (old / "draft-email.html").write_text("retired content")
        prepare(self.bundle, self.docs)
        self.assertFalse((old / "draft-email.html").exists())
        self.assertEqual((old / "index.html").read_text(), "current handbook")
        self.assertEqual((old / "searchindex.js").read_text(), "current search")
        self.assertEqual((self.bundle / "index.html").read_text(), "existing app")
        self.assertTrue((self.bundle / "download/releases.json").is_file())

    def test_missing_handbook_fails_before_modifying_bundle(self):
        with self.assertRaises(ValueError):
            prepare(self.bundle, self.root / "missing")
        self.assertFalse((self.bundle / "download").exists())
        self.assertEqual((self.bundle / "index.html").read_text(), "existing app")

    def test_overlapping_source_is_rejected_without_deleting_it(self):
        overlapping = self.bundle / "docs"
        overlapping.mkdir()
        (overlapping / "index.html").write_text("keep me")
        with self.assertRaises(ValueError):
            prepare(self.bundle, overlapping)
        self.assertEqual((overlapping / "index.html").read_text(), "keep me")


class ExampleResults(unittest.TestCase):
    def test_examples_are_current_and_served(self):
        manifest = json.loads((ROOT / "web/examples/manifest.json").read_text())
        self.assertEqual(manifest["actinv_version"], workspace_version(),
                         "recompute the browser examples: scripts/web_examples.py")
        self.assertEqual(sorted(manifest["files"]), sorted(name for name, _ in EXAMPLES))
        for name, entry in manifest["files"].items():
            data = (ROOT / "web/examples" / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], name)
        with tempfile.TemporaryDirectory() as temp:
            prepare(Path(temp))
            for name in manifest["files"]:
                self.assertTrue((Path(temp) / "examples" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
