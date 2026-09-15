#!/usr/bin/env python3
"""P25c G4 — union-artifact build for the patched corpus.

Stages every file that built ``ok`` in the G3 patched leg (union targets
plus enumerated product-state/ground anchors) into
``target/p25c-g4/stage-tendl-2025-patched`` and runs one bounded
directory build under the shipped 1.1.0 builder, producing the
``tendl-2025-patched`` union candidate NPZ.  Records per-file staging
hashes, the artifact/index SHA-256, index target list and the
``state_catalog`` LISO values.

Frozen Amendment-1 floor F5: the artifact must contain every union-set
file that built in G3.  Writes ``results/g4_p25c_build.json``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-g4"
ACTINV = ROOT / "target" / "release" / "actinv"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
CENSUS = RESULTS / "g3_p25c_census.json"
DIR_BUILD_TIMEOUT_S = 7200.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    census = json.loads(CENSUS.read_text())
    jobs = {j["file"]: j["role"] for j in census["jobs"]}
    survivors = {f: jobs[f] for f, b in census["builds"]["patched"].items()
                 if b["class"] == "ok"}
    union_ok = sorted(f for f, r in survivors.items()
                      if r in ("irdff", "isomeric"))

    stage = WORK / "stage-tendl-2025-patched"
    stage.mkdir(parents=True, exist_ok=True)
    for leftover in stage.iterdir():
        leftover.unlink()
    staged = {}
    for fname in sorted(survivors):
        src, dst = PATCHED_ROOT / fname, stage / fname
        shutil.copyfile(src, dst)
        staged[fname] = {"role": survivors[fname],
                         "sha256": sha256(dst)}

    npz = WORK / "candidate_tendl_2025_patched.npz"
    npz.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-library", str(stage), str(npz),
             "--format", "tendl", "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--workers", "2", "--cache", str(WORK / "cache")],
            capture_output=True, text=True, timeout=DIR_BUILD_TIMEOUT_S,
            cwd=ROOT)
        build = {"exit": proc.returncode,
                 "seconds": round(time.monotonic() - t0, 3),
                 "message": (proc.stderr.strip() or
                             proc.stdout.strip())[-600:]}
    except subprocess.TimeoutExpired:
        build = {"exit": "timeout",
                 "seconds": round(time.monotonic() - t0, 3),
                 "message": f"timeout {DIR_BUILD_TIMEOUT_S}s"}

    index = npz.with_name(npz.stem + "_index.json")
    if not index.is_file():
        index = Path(str(npz) + ".index.json")
    idx = json.loads(index.read_text()) if index.is_file() else None
    index_targets = sorted(t["za"] for t in idx["targets"]) if idx else None
    catalog_liso = sorted({e["liso"] for e in idx.get("state_catalog", [])}) \
        if idx else None

    record = {
        "schema": "actinv-p25c-g4-build-1",
        "gate": "P25c-G4",
        "census_sha256": sha256(CENSUS),
        "builder": str(ACTINV),
        "staged_files": staged,
        "union_survivors_expected": union_ok,
        "build": build,
        "npz": str(npz),
        "npz_sha256": sha256(npz) if npz.is_file() else None,
        "index": str(index) if index.is_file() else None,
        "index_sha256": sha256(index) if index.is_file() else None,
        "index_targets": index_targets,
        "catalog_liso_values": catalog_liso,
    }
    out = RESULTS / "g4_p25c_build.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"staged": len(staged), "exit": build["exit"],
                      "index_targets": len(index_targets or []),
                      "catalog_liso": catalog_liso,
                      "seconds": build["seconds"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
