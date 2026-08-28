#!/usr/bin/env python3
"""Validate the complete immutable ACTINV Python distribution set before upload."""

from __future__ import annotations

import argparse
import configparser
import email.parser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "python" / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]
MAX_PYPI_FILE_BYTES = 100_000_000
WHEEL_PATTERN = re.compile(
    rf"actinv-{re.escape(VERSION)}-cp39-abi3-(?P<platform>.+)\.whl"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def validate_wheel(path: Path) -> str:
    match = WHEEL_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError(f"wheel lacks the cp39 stable-ABI tag: {path.name}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not all(safe_name(name) for name in names):
            raise ValueError(f"wheel has duplicate or unsafe members: {path.name}")
        prefix = f"actinv-{VERSION}.dist-info"
        metadata = email.parser.Parser().parsestr(archive.read(f"{prefix}/METADATA").decode())
        wheel_metadata = email.parser.Parser().parsestr(archive.read(f"{prefix}/WHEEL").decode())
        if metadata["Name"] != "actinv" or metadata["Version"] != VERSION:
            raise ValueError(f"wheel metadata identity mismatch: {path.name}")
        if metadata["Requires-Python"] != ">=3.9":
            raise ValueError(f"wheel Python requirement mismatch: {path.name}")
        if metadata["License-Expression"] != "MIT OR Apache-2.0":
            raise ValueError(f"wheel licence expression mismatch: {path.name}")
        filename_platforms = match.group("platform").split(".")
        expected_tags = {f"cp39-abi3-{platform}" for platform in filename_platforms}
        internal_tags = set(wheel_metadata.get_all("Tag") or [])
        if internal_tags != expected_tags:
            raise ValueError(f"wheel filename and internal compatibility tag disagree: {path.name}")
        entry_points = configparser.ConfigParser()
        entry_points.read_string(archive.read(f"{prefix}/entry_points.txt").decode())
        if entry_points.get("console_scripts", "actinv", fallback=None) != "actinv:_cli":
            raise ValueError(f"wheel does not install the ACTINV console command: {path.name}")
        required = {
            f"{prefix}/licenses/LICENSE-MIT",
            f"{prefix}/licenses/LICENSE-APACHE",
        }
        if not required.issubset(names):
            raise ValueError(f"wheel is missing licence files: {path.name}")
        if not any(name.startswith(f"{prefix}/sboms/") and name.endswith(".cyclonedx.json") for name in names):
            raise ValueError(f"wheel is missing its CycloneDX SBOM: {path.name}")
        if any(PurePosixPath(name).suffix in {".npz", ".endf", ".h5"} for name in names):
            raise ValueError(f"wheel contains a prohibited bulk-data file: {path.name}")
    return match.group("platform")


def validate_sdist(path: Path) -> None:
    prefix = f"actinv-{VERSION}"
    required = {
        f"{prefix}/pyproject.toml",
        f"{prefix}/python/Cargo.toml",
        f"{prefix}/python/Cargo.lock",
        f"{prefix}/python/src/lib.rs",
        f"{prefix}/crates/actinv-cli/Cargo.toml",
        f"{prefix}/crates/actinv-cli/src/lib.rs",
        f"{prefix}/crates/actinv-cli/src/command.rs",
        f"{prefix}/crates/actinv-cli/src/bin/actinv.rs",
        f"{prefix}/crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json",
        f"{prefix}/crates/actinv-core/Cargo.toml",
        f"{prefix}/crates/actinv-core/src/run.rs",
        f"{prefix}/crates/actinv-data/Cargo.toml",
        f"{prefix}/crates/actinv-data/src/library.rs",
        f"{prefix}/crates/actinv-data/src/prepared.rs",
        f"{prefix}/LICENSE-MIT",
        f"{prefix}/LICENSE-APACHE",
    }
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or not all(safe_name(name) for name in names):
            raise ValueError("source distribution has duplicate or unsafe members")
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("source distribution contains a link or special file")
        if not required.issubset(names):
            raise ValueError(f"source distribution is incomplete: {sorted(required - set(names))}")
        if any(PurePosixPath(name).suffix in {".npz", ".endf", ".h5"} for name in names):
            raise ValueError("source distribution contains a prohibited bulk-data file")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    directory = arguments.directory.resolve()
    files = sorted(path for path in directory.iterdir() if path.is_file())
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name == f"actinv-{VERSION}.tar.gz"]
    unexpected = [path.name for path in files if path not in wheels and path not in sdists]
    if len(wheels) != 5 or len(sdists) != 1 or unexpected:
        raise ValueError(
            f"expected five wheels and one source distribution; found wheels={len(wheels)}, "
            f"sdists={len(sdists)}, unexpected={unexpected}"
        )
    if any(path.stat().st_size > MAX_PYPI_FILE_BYTES for path in files):
        raise ValueError("a distribution exceeds PyPI's default 100 MB per-file limit")

    platforms = [validate_wheel(path) for path in wheels]
    platform_checks = {
        "linux_x86_64": any("manylinux" in value and value.endswith("x86_64") for value in platforms),
        "linux_aarch64": any("manylinux" in value and value.endswith("aarch64") for value in platforms),
        "macos_x86_64": any("macosx" in value and value.endswith("x86_64") for value in platforms),
        "macos_arm64": any("macosx" in value and value.endswith("arm64") for value in platforms),
        "windows_x86_64": any(value == "win_amd64" for value in platforms),
    }
    if not all(platform_checks.values()) or len(set(platforms)) != len(platforms):
        raise ValueError(f"wheel platform set is incomplete or duplicated: {platforms}")
    validate_sdist(sdists[0])

    result = {
        "version": VERSION,
        "platforms": platforms,
        "platform_checks": platform_checks,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)} for path in files
        },
        "pass": True,
    }
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
