"""Regression: cached staging must neither block packaging nor supply stale assets."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("package_desktop", Path(__file__).with_name("package_desktop.py"))
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackageStaging(unittest.TestCase):
    def test_cached_staging_is_ignored_and_fresh_staging_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("packaging", "target/release", "target/desktop-packages", "docs", "crates/actinv-gui/assets"):
                (root / name).mkdir(parents=True)
            (root / "packaging/desktop.json").write_text(json.dumps({"version": "0.1.0-preview.1"}))
            for name in ("target/release/actinv-gui", "LICENSE-MIT", "LICENSE-APACHE", "docs/DESKTOP.md",
                         "packaging/install-linux.sh", "crates/actinv-gui/assets/avila-labs-logo.png"):
                (root / name).write_bytes(b"fixture")
            stale = root / "target/desktop-packages/stale.AppImage"
            stale.write_bytes(b"stale")
            staging = []

            def package(command, **kwargs):
                path = Path(command[command.index("--out-dir") + 1])
                staging.append(path)
                self.assertFalse(path.is_relative_to(root / "target"))
                (path / "fresh.AppImage").write_bytes(b"fresh")

            with patch.object(packaging, "ROOT", root), patch.object(packaging.platform, "system", return_value="Linux"), \
                    patch.object(packaging.subprocess, "run", side_effect=package), \
                    patch.object(packaging.subprocess, "check_output", side_effect=["commit\n", "", "cargo-packager 0.11.8\n"]), \
                    patch("sys.argv", ["package_desktop.py", "--label", "linux-x86_64"]):
                packaging.main()
                output = next((root / "dist/desktop/linux-x86_64").glob("*.AppImage"))
                self.assertEqual(output.read_bytes(), b"fresh")
                self.assertEqual(stale.read_bytes(), b"stale")
                self.assertFalse(staging[0].exists())
                with self.assertRaises(SystemExit):
                    packaging.main()
                self.assertEqual(output.read_bytes(), b"fresh")


if __name__ == "__main__":
    unittest.main()
