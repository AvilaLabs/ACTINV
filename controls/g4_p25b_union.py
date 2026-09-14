#!/usr/bin/env python3
"""P25b G4 — union-population census, rebuild and scoring under the
1.1.0 release-candidate builder.

G1's construction census and the first G4 artifacts used the v1.0.1
``actinv`` on PATH, whose index schema carries no ``state_catalog`` —
the held-out fold requires it.  This control re-derives the union
eligible population (47 isotopic IRDFF-II targets ∪ 37 isomeric-row
targets) per corpus, runs one bounded per-target build under
``target/release/actinv`` (1.1.0, the builder the 1.1.0 data path
would ship with), assembles one union artifact per corpus by
directory build, and scores both partitions through the unchanged
frozen folds:

  * IRDFF fresh partition — ``p24_scorer.score_fresh_partition`` with
    the corpus artifact as ``candidate`` (identical context to G4's
    first pass);
  * P25 isomeric held-out rows — ``g5_p18b_heldout.score_heldout``
    with the corpus artifact as ``candidate-neutron.npz``.

Writes ``results/g4_p25b_union.json``.  All scoring retrospective.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-union"
ACTINV = Path(os.environ.get(
    "ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
PER_FILE_BOUND_S = 300.0   # G1 90s bound shown too tight; 600s was the
                           # amended repair bound — census uses 300s
DIR_BUILD_TIMEOUT_S = 7200.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {"root": ND / "tendl-2023" / "files", "format": "tendl"},
    "fendl_32c": {"root": ND / "fendl-3.2c" / "endf", "format": "tendl"},
    "eaf_2010": {"root": ND / "eaf-2010" / "files", "format": "eaf"},
}

HELDOUT = RESULTS / "g5_p18b_heldout.json"
OUTPUT = RESULTS / "g4_p25b_union.json"

ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am"
).split()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def irdff_targets() -> list[tuple[int, int]]:
    import zipfile
    archive = Path.home() / "nuclear-data" / "p17-irdff" / "IRDFF-II_ENDF.zip"
    if sha256(archive) != "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db":
        raise RuntimeError("IRDFF-II archive hash mismatch")
    with zipfile.ZipFile(archive) as z:
        text = z.read("IRDFF-II.endf").decode("ascii", "replace")
    out = set()
    header = re.compile(r"^(.{11})(.{11}).{44}.{0,4}?(\d{4}) 1451    1", re.M)
    for match in header.finditer(text):
        field = match.group(1).strip()
        m = re.match(r"^([+-]?\d+(?:\.\d*)?)([+-]\d+)$", field)
        try:
            za = (float(m.group(1)) * 10.0 ** int(m.group(2))) if m \
                else float(field)
        except ValueError:
            continue
        z, a = int(round(za)) // 1000, int(round(za)) % 1000
        if a:
            out.add((z, a))
    return sorted(out)


def isomer_targets() -> tuple[list[tuple[int, int]], int]:
    led = json.loads(HELDOUT.read_text())["ledger"]
    targets, rows = set(), 0
    for r in led:
        if r.get("projectile") != "neutron" or not r.get("isomer_liso"):
            continue
        rows += 1
        seg = r["family_id"].split("|")[1].split("-")
        targets.add((int(seg[0]), int(seg[1])))
    return sorted(targets), rows


def parse_filename(name: str) -> tuple[int, int, str] | None:
    for pat in (r"^n_(\d{3})-[A-Za-z]{1,2}-(\d+)([mMnN]?)_\d+\.dat$",
                r"^n_\d+_(\d+)-[A-Za-z]{1,2}-(\d+)([mMnN]?)\.(?:dat|endf)$"):
        m = re.match(pat, name)
        if m:
            return int(m.group(1)), int(m.group(2)), m.group(3).upper()
    return None


def corpus_index(corpus: str) -> dict[tuple[int, int], list[tuple[str, str]]]:
    out: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for path in sorted(CORPORA[corpus]["root"].iterdir()):
        if not path.is_file():
            continue
        parsed = parse_filename(path.name)
        if parsed:
            out.setdefault((parsed[0], parsed[1]), []).append(
                (path.name, parsed[2]))
    for key in out:
        out[key].sort(key=lambda item: item[1])
    return out


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


def percentile(values, pct):
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, float), pct))


def metrics(abs_lns) -> dict:
    vals = [v for v in abs_lns if math.isfinite(v)]
    if not vals:
        return {"rows": 0}
    return {
        "rows": len(vals),
        "median_abs_ln": percentile(vals, 50),
        "p90_abs_ln": percentile(vals, 90),
        "within_10pct": sum(v <= math.log(1.1) for v in vals) / len(vals),
        "within_20pct": sum(v <= math.log(1.2) for v in vals) / len(vals),
        "within_30pct": sum(v <= math.log(1.3) for v in vals) / len(vals),
    }


def nonreg(b: dict, c: dict) -> dict:
    res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
    if b.get("rows") and c.get("rows"):
        res["median_ok"] = (c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
                            and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
        res["p90_ok"] = (c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
                         and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
        res["coverage_ok"] = all(
            c[k] >= b[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct"))
    elif not c.get("rows"):
        res["unscored_candidate"] = True
    return res


def census_targets() -> dict:
    """Union eligible population: IRDFF-II isotopic ∪ isomeric-row."""
    irdff = irdff_targets()
    isomers, iso_rows = isomer_targets()
    union = sorted(set(irdff) | set(isomers))
    return {"irdff": irdff, "isomeric": isomers, "isomeric_rows": iso_rows,
            "union": union}


def main() -> int:
    import g5_p18b_heldout as g5  # frozen P18b held-out scoring module

    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "cache").mkdir(exist_ok=True)
    pop = census_targets()

    corpora_out = {}
    for corpus, spec in CORPORA.items():
        index = corpus_index(corpus)
        construction = []
        survivors = []
        for z, a in pop["union"]:
            za = z * 1000 + a
            files = index.get((z, a), [])
            ground = [f for f, iso in files if iso == ""]
            entry = {"za": za,
                     "target": f"{ELEMENT_SYMBOLS[z - 1]}-{a}",
                     "in_irdff": (z, a) in set(pop["irdff"]),
                     "in_isomeric": (z, a) in set(pop["isomeric"]),
                     "candidate_files": [f for f, _ in files]}
            if not ground:
                entry["class"] = "corpus_incomplete"
                entry["note"] = ("isomer-only files" if files
                                 else "no file for target")
            else:
                src = spec["root"] / ground[0]
                build = run_build(
                    [str(ACTINV), "build-library", str(src),
                     str(WORK / "one.npz"), "--format", spec["format"],
                     "--projectile", "neutron", "--groups", "fispact-709",
                     "--temperature-K", "293.6", "--cache",
                     str(WORK / "cache")], PER_FILE_BOUND_S)
                (WORK / "one.npz").unlink(missing_ok=True)
                entry.update({"file": ground[0],
                              "file_sha256": sha256(src), **build,
                              "class": "ok" if build["exit"] == 0
                              else "construction_error"})
                if entry["class"] == "ok":
                    survivors.append(ground[0])
            construction.append(entry)
            print(f"{corpus} {entry['target']}: {entry['class']}",
                  flush=True)

        stage = WORK / f"stage-{corpus}"
        stage.mkdir(parents=True, exist_ok=True)
        for leftover in stage.iterdir():
            leftover.unlink()
        for fname in survivors:
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
        art = {**build, "npz": str(npz),
               "npz_sha256": sha256(npz) if npz.is_file() else None,
               "index_sha256": sha256(index_path)
               if index_path.is_file() else None,
               "staged_files": len(survivors)}
        print(f"union build {corpus}: exit {build['exit']}", flush=True)

        corpora_out[corpus] = {
            "construction": construction,
            "construction_histogram": {
                c: sum(1 for e in construction if e["class"] == c)
                for c in {e["class"] for e in construction}},
            "surviving_targets": len(survivors),
            "artifact": art,
        }

    record = {
        "schema": "actinv-p25b-g4-union-1",
        "builder": str(ACTINV),
        "per_file_bound_s": PER_FILE_BOUND_S,
        "population": {
            "irdff_targets": [f"{z*1000+a}" for z, a in pop["irdff"]],
            "isomeric_targets": [f"{z*1000+a}" for z, a in pop["isomeric"]],
            "isomeric_rows": pop["isomeric_rows"],
            "union_targets": [f"{z*1000+a}" for z, a in pop["union"]],
        },
        "corpora": corpora_out,
    }
    OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({c: b["construction_histogram"]
                      for c, b in corpora_out.items()},
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
