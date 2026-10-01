#!/usr/bin/env python3
"""Add the download page and optional handbook to an ACTINV browser bundle.

Desktop asset names follow scripts/package_desktop.py. The release version is
read from packaging/desktop.json so the site and installers stay together.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination: Path, docs: Path | None = None) -> None:
    if docs is not None and not (docs / "index.html").is_file():
        raise ValueError(f"Handbook is not built at {docs}; run mdbook build first")
    if docs is not None:
        source = docs.resolve()
        target = (destination / "docs").resolve()
        if source.is_relative_to(target) or target.is_relative_to(source):
            raise ValueError("Handbook source and packaged destination must not overlap")
    desktop = json.loads((ROOT / "packaging/desktop.json").read_text())
    version = desktop["version"]
    release = f"https://github.com/AvilaLabs/ACTINV/releases/download/desktop-v{version}"
    platforms = {}
    for key, label, extension, detail in [
        ("windows-x86_64", "Windows", "-setup.exe", "64-bit Intel / AMD · Installer"),
        ("macos-aarch64", "macOS · Apple Silicon", ".dmg", "M-series chip · Disk image"),
        ("macos-x86_64", "macOS · Intel", ".dmg", "Intel processor · Disk image"),
        ("linux-x86_64", "Linux", ".AppImage", "64-bit Intel / AMD · AppImage"),
    ]:
        platforms[key] = {
            "label": label,
            "detail": detail,
            "url": f"{release}/ACTINV-Desktop-{version}-{key}{extension}",
        }
    download = destination / "download"
    download.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "download.mjs", "platform.mjs"):
        shutil.copyfile(ROOT / "web/download" / name, download / name)
    (download / "releases.json").write_text(
        json.dumps({"version": version, "platforms": platforms}, indent=2) + "\n"
    )
    (destination / ".nojekyll").touch()
    if docs is not None:
        handbook = destination / "docs"
        # Replacing the generated directory removes chapters retired since the
        # previous build, including their old search index and assets.
        if handbook.exists():
            shutil.rmtree(handbook)
        shutil.copytree(docs, handbook)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--docs", type=Path, help="Built mdBook directory to serve at /docs/")
    args = parser.parse_args()
    prepare(args.destination, args.docs)
