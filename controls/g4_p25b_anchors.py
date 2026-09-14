#!/usr/bin/env python3
"""P25b G4 — product-state anchor augmentation.

The frozen held-out fold gates every isomeric row on
``(product_za, liso) in catalog_states(candidate_index)``; the catalog
carries *target* states, so the candidate artifact must also build the
product nuclides' isomeric evaluations (e.g. ``n_027-Co-62M_…``) for
the gate to be reachable.  The union-target artifact alone cannot
express those states.

This control enumerates the product (za, liso) set required by the
eligible neutron isomeric rows of the sealed held-out ledger, stages
every corpus file for those product nuclides (ground and isomer
targets), runs one bounded per-target build each under the release
candidate builder, and rebuilds the union artifact per corpus over
``stage-<corpus>`` = union survivors + anchor survivors.

EAF sources never emit a ``state_catalog`` (the catalog builder runs
only for ``LibraryFormat::Tendl``); the EAF anchor leg still runs so
the record shows file availability vs. builder capability separately.

Appends ``anchor_augmentation`` to ``results/g4_p25b_union.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-union"
ACTINV = Path(os.environ.get(
    "ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PER_FILE_BOUND_S = 300.0
DIR_BUILD_TIMEOUT_S = 7200.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {"root": ND / "tendl-2023" / "files", "format": "tendl"},
    "fendl_32c": {"root": ND / "fendl-3.2c" / "endf", "format": "tendl"},
    "eaf_2010": {"root": ND / "eaf-2010" / "files", "format": "eaf"},
}
HELDOUT = RESULTS / "g5_p18b_heldout.json"
RECORD = RESULTS / "g4_p25b_union.json"

FILE_RE = re.compile(
    r"^n_(?:(\d{3})-|\d+_(\d+)-)[A-Za-z]{1,2}-(\d+)([mMnN]?)"
    r"(?:_\d+)?\.(?:dat|endf)$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def needed_products() -> dict[tuple[int, int], set]:
    """Eligible neutron isomeric rows -> required product (z,a)->liso."""
    need: dict[tuple[int, int], set] = defaultdict(set)
    for r in json.loads(HELDOUT.read_text())["ledger"]:
        if r["projectile"] != "neutron" or r["status"] != "eligible":
            continue
        if not r.get("isomer_liso"):
            continue
        seg = r["family_id"].split("|")
        pz, pa = seg[2].split("-")
        need[(int(pz), int(pa))].add(r["isomer_liso"])
    return dict(need)


def corpus_files(corpus: str) -> dict[tuple[int, int], list[str]]:
    out: dict[tuple[int, int], list[str]] = defaultdict(list)
    for p in sorted(CORPORA[corpus]["root"].iterdir()):
        if not p.is_file():
            continue
        m = FILE_RE.match(p.name)
        if m:
            z = int(m.group(1) or m.group(2))
            out[(z, int(m.group(3)))].append(p.name)
    return dict(out)


def run_build(args: list[str], timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, cwd=ROOT)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() or
                            proc.stdout.strip())[-600:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout",
                "seconds": round(time.monotonic() - t0, 3),
                "message": f"timeout {timeout}s"}


def main() -> int:
    record = json.loads(RECORD.read_text())
    need = needed_products()
    aug = {"schema": "actinv-p25b-g4-anchors-1",
           "needed_product_states": {
               f"{z*1000+a}": sorted(l) for (z, a), l in sorted(need.items())},
           "per_file_bound_s": PER_FILE_BOUND_S,
           "corpora": {}}

    for corpus, spec in CORPORA.items():
        files = corpus_files(corpus)
        anchor_builds = []
        survivors = []
        for (z, a), lisos in sorted(need.items()):
            names = files.get((z, a), [])
            if not names:
                anchor_builds.append(
                    {"product_za": z * 1000 + a,
                     "needed_liso": sorted(lisos),
                     "class": "corpus_incomplete"})
                continue
            for fname in names:
                src = spec["root"] / fname
                build = run_build(
                    [str(ACTINV), "build-library", str(src),
                     str(WORK / "anchor.npz"), "--format", spec["format"],
                     "--projectile", "neutron", "--groups", "fispact-709",
                     "--temperature-K", "293.6", "--cache",
                     str(WORK / "cache")], PER_FILE_BOUND_S)
                (WORK / "anchor.npz").unlink(missing_ok=True)
                anchor_builds.append(
                    {"product_za": z * 1000 + a, "file": fname,
                     "file_sha256": sha256(src),
                     "needed_liso": sorted(lisos), **build,
                     "class": "ok" if build["exit"] == 0
                     else "construction_error"})
                if build["exit"] == 0:
                    survivors.append(fname)
        stage = WORK / f"stage-{corpus}"
        for fname in survivors:
            if not (stage / fname).exists():
                shutil.copyfile(spec["root"] / fname, stage / fname)

        npz = WORK / f"union_{corpus}.npz"
        npz.unlink(missing_ok=True)
        build = run_build(
            [str(ACTINV), "build-library", str(stage), str(npz),
             "--format", spec["format"], "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--workers", "2", "--cache", str(WORK / "cache")],
            DIR_BUILD_TIMEOUT_S)
        index_path = npz.with_name(npz.stem + "_index.json")
        catalog_liso = None
        if index_path.is_file():
            idx = json.loads(index_path.read_text())
            catalog_liso = sorted(
                {e["liso"] for e in idx.get("state_catalog", [])})
        hist = {}
        for b in anchor_builds:
            hist[b["class"]] = hist.get(b["class"], 0) + 1
        aug["corpora"][corpus] = {
            "anchor_builds": anchor_builds,
            "anchor_histogram": hist,
            "anchor_survivors": len(survivors),
            "staged_files": len(list(stage.iterdir())),
            "artifact": {**build, "npz": str(npz),
                         "npz_sha256": sha256(npz) if npz.is_file() else None,
                         "index_sha256": sha256(index_path)
                         if index_path.is_file() else None,
                         "catalog_liso_values": catalog_liso},
        }
        print(f"{corpus}: anchors {hist} staged={aug['corpora'][corpus]['staged_files']} "
              f"build exit {build['exit']} catalog_liso={catalog_liso}",
              flush=True)

    record["anchor_augmentation"] = aug
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
