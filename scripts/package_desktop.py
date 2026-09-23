#!/usr/bin/env python3
"""Build native packages, normalize names, and record exact artifact provenance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def workspace_version():
    """The solver version actually compiled into this build: the workspace package version."""
    with (ROOT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--packager", default="cargo-packager")
    args = parser.parse_args()
    config = json.loads((ROOT / "packaging/desktop.json").read_text())
    system = platform.system()
    formats = {"Linux": "appimage", "Darwin": "app,dmg", "Windows": "nsis"}[system]
    release = ROOT / "dist/desktop" / args.label
    if release.exists():
        raise SystemExit("Package output exists; choose a fresh checkout or remove only prior generated package output.")
    environment = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", NO_STRIP="1")
    stem = f"ACTINV-Desktop-{config['version']}-{args.label}"
    extension = {"Linux": ".AppImage", "Darwin": ".dmg", "Windows": ".exe"}[system]
    suffix = "-setup" if system == "Windows" else ""
    # Rust's cache can restore target/desktop-packages from an older run.
    # Build in fresh staging outside that cache, retaining the final-output guard.
    with tempfile.TemporaryDirectory(prefix="actinv-packaging-") as directory:
        raw = Path(directory)
        subprocess.run([args.packager, "--config", "packaging/desktop.json", "--formats", formats,
                        "--out-dir", str(raw)], cwd=ROOT, env=environment, check=True)
        files = list(raw.glob("*" + extension))
        assert len(files) == 1, files
        release.mkdir(parents=True)
        shutil.copy2(files[0], release / (stem + suffix + extension))
    if system == "Linux":
        shutil.copy2(ROOT / "packaging/install-linux.sh", release / "install-linux.sh")
        shutil.copy2(ROOT / "crates/actinv-gui/assets/avila-labs-logo.png", release / "actinv.png")
    binary = ROOT / "target/release" / ("actinv-gui.exe" if system == "Windows" else "actinv-gui")
    if system == "Windows":
        with zipfile.ZipFile(release / (stem + "-portable.zip"), "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(binary, "ACTINV.exe")
            for name in ("LICENSE-MIT", "LICENSE-APACHE", "docs/DESKTOP.md"):
                archive.write(ROOT / name, Path(name).name)
    for name in ("LICENSE-MIT", "LICENSE-APACHE", "docs/DESKTOP.md"):
        shutil.copy2(ROOT / name, release / Path(name).name)
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = {
        "desktop_version": config["version"], "solver_version": workspace_version(), "target": args.label,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()),
        "packager": subprocess.check_output([args.packager, "--version"], text=True).strip(),
        "signing": "ad-hoc; not notarized" if system == "Darwin" else "unsigned",
        "compiled_binary_sha256": digest(binary),
        "files": {p.name: digest(p) for p in sorted(release.iterdir())},
    }
    (release / f"build-{args.label}.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (release / f"SHA256SUMS-{args.label}.txt").write_text(
        "".join(f"{digest(p)}  {p.name}\n" for p in sorted(release.iterdir())))
    print(release)


if __name__ == "__main__":
    main()
