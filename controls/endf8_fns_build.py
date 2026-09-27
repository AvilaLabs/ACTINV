#!/usr/bin/env python3
"""Iteratively build the ENDF/B-VIII.1 FNS-arm library, excluding tapes the
builder fails closed on. Each excluded file is moved to stage-excluded/ and
logged in the stage manifest. Bounded retries.

Usage: python3 controls/endf8_fns_build.py [--max-exclude N]
Must run inside the enforced cgroup.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target" / "release" / "actinv"
ARM = Path("/home/connoravila/nuclear-data/endfb-viii.1-fns-arm")
STAGE = ARM / "stage"
EXCLUDED = ARM / "stage-excluded"
NPZ = ARM / "endfb8_fns_709g.npz"
CACHE = ARM / "cache"
MANIFEST = ARM / "stage_manifest.json"

CMD = [
    str(ACTINV), "build-library", str(STAGE), str(NPZ),
    "--format", "auto", "--projectile", "neutron",
    "--groups", "fispact-709", "--temperature-K", "0",
    "--workers", "2", "--cache", str(CACHE),
    "--strict-states", "false",
]


def main() -> int:
    max_exclude = 40
    if "--max-exclude" in sys.argv:
        max_exclude = int(sys.argv[sys.argv.index("--max-exclude") + 1])
    EXCLUDED.mkdir(exist_ok=True)
    excluded = []
    for attempt in range(max_exclude + 1):
        r = subprocess.run(CMD, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode == 0:
            m = json.loads(MANIFEST.read_text())
            m["build_excluded"] = excluded
            m["build_outcome"] = "ok" if not excluded else \
                f"ok_with_{len(excluded)}_excluded"
            MANIFEST.write_text(json.dumps(m, indent=1))
            print(f"BUILD OK after {len(excluded)} exclusions: {excluded}")
            return 0
        m = re.search(r"stage/([A-Za-z0-9_.-]+\.endf)", r.stderr)
        if not m:
            print("FAILED w/o file attribution:\n" + r.stderr[-3000:])
            return 1
        f = m.group(1)
        src = STAGE / f
        if not src.exists():
            print(f"failure names missing file {f}:\n{r.stderr[-2000:]}")
            return 1
        reason = r.stderr.strip().splitlines()[-1][:300]
        shutil.move(str(src), EXCLUDED / f)
        excluded.append({"file": f, "reason": reason})
        print(f"excluded {f}: {reason[:120]}", flush=True)
    print(f"exceeded max_exclude={max_exclude}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
