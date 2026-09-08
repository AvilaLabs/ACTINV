#!/usr/bin/env python3
"""Install/extract into a disposable location and test the shipped executable."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    release = ROOT / "dist/desktop" / args.label
    evidence = ROOT / "target/package-verification" / args.label
    evidence.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    def smoke(binary, name, require=False):
        command = [sys.executable, str(ROOT / "scripts/desktop_smoke.py"), str(binary), str(evidence / name)]
        if require:
            command.append("--require-render")
        subprocess.run(command, check=True, env=dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", APPIMAGELAUNCHER_DISABLE="1"))
    with tempfile.TemporaryDirectory(prefix="actinv-installed-") as directory:
        work = Path(directory)
        if system == "Windows":
            with zipfile.ZipFile(next(release.glob("*-portable.zip"))) as archive:
                archive.extractall(work / "portable")
            smoke(work / "portable/ACTINV.exe", "portable")
            install = work / "installed"
            subprocess.run([str(next(release.glob("*-setup.exe"))), "/S", f"/D={install}"], check=True, timeout=120)
            assert (install / "actinv-gui.exe").is_file(), "installer payload missing"
            smoke(install / "actinv-gui.exe", "installed")
            subprocess.run([str(install / "uninstall.exe"), "/S", f"_?={install}"], check=True, timeout=120)
            assert not (install / "actinv-gui.exe").exists(), "uninstaller left executable"
        elif system == "Darwin":
            mount = work / "mounted"
            subprocess.run(["hdiutil", "attach", str(next(release.glob("*.dmg"))), "-readonly", "-nobrowse", "-mountpoint", str(mount)], check=True)
            try:
                source = next(mount.glob("*.app"))
                installed = work / source.name
                subprocess.run(["ditto", str(source), str(installed)], check=True)
            finally:
                subprocess.run(["hdiutil", "detach", str(mount)], check=True)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], check=True)
            smoke(installed / "Contents/MacOS/actinv-gui", "installed")
        else:
            app = work / "ACTINV.AppImage"
            shutil.copy2(next(release.glob("*.AppImage")), app)
            app.chmod(0o755)
            smoke(app, "appimage", require=True)
            subprocess.run([str(app), "--appimage-extract"], cwd=work, check=True, stdout=subprocess.DEVNULL,
                           env=dict(os.environ, APPIMAGELAUNCHER_DISABLE="1"))
            assert list((work / "squashfs-root/usr/share/applications").glob("*.desktop"))
            assert list((work / "squashfs-root/usr/share/icons").rglob("*.png"))
            local_data = work / "user data"
            subprocess.run(["bash", str(release / "install-linux.sh"), str(app)], check=True,
                           env=dict(os.environ, XDG_DATA_HOME=str(local_data)))
            entry = local_data / "applications/com.avilalabs.actinv.desktop"
            assert "Terminal=false" in entry.read_text()
            assert (local_data / "actinv-desktop/ACTINV.AppImage").is_file()
    summaries = {p.parent.name: json.loads(p.read_text()) for p in evidence.glob("*/smoke.json")}
    (release / f"verification-{args.label}.json").write_text(json.dumps(summaries, indent=2) + "\n")
    sums = release / f"SHA256SUMS-{args.label}.txt"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                            for p in sorted(release.iterdir()) if p != sums))


if __name__ == "__main__":
    main()
