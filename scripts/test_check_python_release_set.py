#!/usr/bin/env python3
"""Regression tests for ACTINV's immutable Python release-set validator."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import check_python_release_set


class WheelTagTests(unittest.TestCase):
    def make_linux_wheel(self, directory: Path, tags: list[str]) -> Path:
        version = check_python_release_set.VERSION
        name = (
            f"actinv-{version}-cp39-abi3-"
            "manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
        )
        path = directory / name
        prefix = f"actinv-{version}.dist-info"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{prefix}/METADATA",
                "Metadata-Version: 2.4\n"
                "Name: actinv\n"
                f"Version: {version}\n"
                "Requires-Python: >=3.9\n"
                "License-Expression: MIT OR Apache-2.0\n",
            )
            archive.writestr(
                f"{prefix}/WHEEL",
                "Wheel-Version: 1.0\n"
                "Root-Is-Purelib: false\n"
                + "".join(f"Tag: {tag}\n" for tag in tags),
            )
            archive.writestr(f"{prefix}/entry_points.txt", "[console_scripts]\nactinv=actinv:_cli\n")
            archive.writestr(f"{prefix}/licenses/LICENSE-MIT", "MIT\n")
            archive.writestr(f"{prefix}/licenses/LICENSE-APACHE", "Apache-2.0\n")
            archive.writestr(f"{prefix}/sboms/actinv.cyclonedx.json", "{}\n")
        return path

    def test_dot_compressed_manylinux_filename_matches_separate_internal_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = self.make_linux_wheel(
                Path(temporary),
                [
                    "cp39-abi3-manylinux_2_17_aarch64",
                    "cp39-abi3-manylinux2014_aarch64",
                ],
            )
            self.assertEqual(
                check_python_release_set.validate_wheel(wheel),
                "manylinux_2_17_aarch64.manylinux2014_aarch64",
            )

    def test_missing_internal_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = self.make_linux_wheel(
                Path(temporary),
                ["cp39-abi3-manylinux_2_17_aarch64"],
            )
            with self.assertRaisesRegex(ValueError, "filename and internal compatibility tag"):
                check_python_release_set.validate_wheel(wheel)


if __name__ == "__main__":
    unittest.main()
