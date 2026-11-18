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
        self.assertTrue((self.bundle / "learn/first-irradiation/index.html").is_file())
        self.assertFalse((self.bundle / "learn/build").exists())

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


class Course(unittest.TestCase):
    def test_chapter_data_matches_its_problem(self):
        chapter = ROOT / "web/learn/first-irradiation"
        problem = (chapter / "files/bracket.json").read_bytes()
        data = json.loads((chapter / "data/bracket-data.json").read_text())
        self.assertEqual(data["problem_sha256"], hashlib.sha256(problem).hexdigest(),
                         "recompute the chapter: web/learn/build/make_bracket.py")
        response = json.loads(problem)["photon"]["response"]
        self.assertEqual(response["sha256"],
                         hashlib.sha256((chapter / "files" / response["path"]).read_bytes()).hexdigest())

    def test_later_chapters_data_match_their_files(self):
        for name, data_file, script in (("where-from", "routes-data.json", "make_routes.py"),
                                        ("impurities", "impurities-data.json", "make_impurities.py"),
                                        ("maintenance", "maintenance-data.json", "make_maintenance.py"),
                                        ("waste", "waste-data.json", "make_waste.py")):
            chapter = ROOT / "web/learn" / name
            data = json.loads((chapter / "data" / data_file).read_text())
            pinned = data.get("files_sha256") or {"routes.json": data["problem_sha256"]}
            for file, sha in pinned.items():
                self.assertEqual(sha, hashlib.sha256((chapter / "files" / file).read_bytes()).hexdigest(),
                                 f"recompute the chapter: web/learn/build/{script}")


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
