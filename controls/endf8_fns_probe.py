#!/usr/bin/env python3
"""Probe-build each tape individually to collect full error text.
Usage: python3 controls/endf8_fns_probe.py [dir ...]
Must run inside the enforced cgroup."""
from __future__ import annotations
import shutil, subprocess, sys, tempfile
from pathlib import Path

ACTINV = Path(__file__).resolve().parents[1] / "target/release/actinv"
ARM = Path("/home/connoravila/nuclear-data/endfb-viii.1-fns-arm")

def probe(f: Path, tmp: Path):
    work = tmp / "w"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    shutil.copy(f, work / f.name)
    r = subprocess.run(
        [str(ACTINV), "build-library", str(work), str(work / "o.npz"),
         "--format", "auto", "--projectile", "neutron",
         "--groups", "fispact-709", "--temperature-K", "0",
         "--workers", "2", "--strict-states", "false"],
        cwd=str(work), env=None) if False else subprocess.run(
        [str(ACTINV), "build-library", str(work), str(tmp / "o.npz"),
         "--format", "auto", "--projectile", "neutron",
         "--groups", "fispact-709", "--temperature-K", "0",
         "--workers", "2", "--strict-states", "false"],
        capture_output=True, text=True, timeout=900)
    if r.returncode == 0:
        return "OK"
    return (r.stderr.strip() or r.stdout.strip())[-400:]

def main():
    dirs = [Path(d) for d in sys.argv[1:]] or [ARM / "stage-excluded"]
    tmp = Path(tempfile.mkdtemp(dir=str(ARM)))
    try:
        for d in dirs:
            for f in sorted(d.glob("*.endf")):
                err = probe(f, tmp)
                print(f"{f.name}: {err}\n---", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
