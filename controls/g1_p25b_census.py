#!/usr/bin/env python3
"""P25b G1 construction census over the IRDFF-II target set.

Re-derives the 49 isotopic IRDFF-II targets from the sealed pointwise
archive (hash checked), resolves each target to a source file inside
each hash-pinned candidate corpus, and runs one bounded
`actinv build-library --format auto` invocation per resolved file.
Every failure's underlying builder message is recorded verbatim;
`format_unsupported` is reserved for format-detection failures,
`corpus_incomplete` for targets with no ground-state file in the
corpus.

No repair code is added or changed by this control (protocol: no
repair before G1 commits).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
DATA_ROOT = Path(
    os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff")
)
POINTWISE_ARCHIVE = DATA_ROOT / "IRDFF-II_ENDF.zip"
POINTWISE_SHA256 = "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db"
ACTINV = os.environ.get("ACTINV_BIN", "actinv")
WORK = ROOT / "target" / "preflight-tmp" / "p25b-g1"
TIMEOUT_S = 90.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": ND / "tendl-2023" / "files",
    "fendl_32c": ND / "fendl-3.2c" / "endf",
    "eaf_2010": ND / "eaf-2010" / "files",
}

ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am"
).split()

DOSIMETRY_CRITICAL = {(28, 58), (79, 197), (47, 109), (41, 93), (49, 113),
                      (49, 115)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def endf_float(field: str) -> float:
    text = field.strip()
    if not text:
        raise ValueError("empty ENDF field")
    match = re.match(r"^([+-]?\d+(?:\.\d*)?)([+-]\d+)$", text)
    if match:
        return float(match.group(1)) * 10.0 ** int(match.group(2))
    return float(text)


def irdff_targets() -> list[tuple[int, int]]:
    """Isotopic IRDFF-II MF1 target ZA set from the sealed archive."""
    if sha256(POINTWISE_ARCHIVE) != POINTWISE_SHA256:
        raise RuntimeError("IRDFF-II pointwise archive hash mismatch")
    with zipfile.ZipFile(POINTWISE_ARCHIVE) as archive:
        text = archive.read("IRDFF-II.endf").decode("ascii", "replace")
    targets: set[tuple[int, int]] = set()
    header = re.compile(r"^(.{11})(.{11}).{44}.{0,4}?(\d{4}) 1451    1", re.M)
    for match in header.finditer(text):
        try:
            za = endf_float(match.group(1))
        except ValueError:
            continue
        z_value, a_value = int(round(za)) // 1000, int(round(za)) % 1000
        if a_value != 0:
            targets.add((z_value, a_value))
    return sorted(targets)


# Filename indexers: each returns (z, a, isomer) or None.
def index_tendl(name: str) -> tuple[int, int, str] | None:
    m = re.match(r"^n_(\d{3})-([A-Za-z]{1,2})-(\d+)([mMnN]?)_\d+\.dat$", name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3)), m.group(4).upper()


def index_mat_first(name: str) -> tuple[int, int, str] | None:
    m = re.match(r"^n_\d+_(\d+)-([A-Za-z]{1,2})-(\d+)([mMnN]?)"
                 r"\.(?:dat|endf)$", name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3)), m.group(4).upper()


INDEXERS = {
    "tendl_2023": index_tendl,
    "fendl_32c": index_mat_first,
    "eaf_2010": index_mat_first,
}


def corpus_index(corpus: str) -> dict[tuple[int, int], list[tuple[str, str]]]:
    """{(z, a): [(filename, isomer)]} ground states first."""
    root = CORPORA[corpus]
    out: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        parsed = INDEXERS[corpus](path.name)
        if not parsed:
            continue
        z, a, iso = parsed
        out.setdefault((z, a), []).append((path.name, iso))
    for key in out:
        out[key].sort(key=lambda item: item[1])
    return out


def classify_failure(stderr: str) -> str:
    tail = stderr.lower()
    if ("auto-detect" in tail or "identifies as" in tail
            or "eaf format" in tail or "expected auto, tendl or eaf" in tail):
        return "format_unsupported"
    return "construction_error"


def run_build(src: Path) -> dict:
    out = WORK / "one.npz"
    out.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [ACTINV, "build-library", str(src), str(out),
             "--format", "auto"],
            capture_output=True, text=True, timeout=TIMEOUT_S)
        rec = {
            "exit": proc.returncode,
            "seconds": round(time.monotonic() - t0, 3),
            "message": proc.stderr.strip()[-600:] or
                       proc.stdout.strip()[-600:],
        }
    except subprocess.TimeoutExpired:
        rec = {"exit": "timeout", "seconds": round(time.monotonic() - t0, 3),
               "message": f"timeout {TIMEOUT_S}s"}
    out.unlink(missing_ok=True)
    return rec


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    targets = irdff_targets()
    indexes = {c: corpus_index(c) for c in CORPORA}

    ledger = []
    histograms: dict[str, dict[str, int]] = {}
    for corpus in CORPORA:
        hist: dict[str, int] = {}
        for z, a in targets:
            files = indexes[corpus].get((z, a), [])
            ground = [f for f, iso in files if iso == ""]
            row = {
                "corpus": corpus,
                "target": f"{ELEMENT_SYMBOLS[z - 1]}-{a}",
                "za": z * 1000 + a,
                "dosimetry_critical": (z, a) in DOSIMETRY_CRITICAL,
                "candidate_files": [f for f, _ in files],
            }
            if not ground:
                row["class"] = "corpus_incomplete"
                row["note"] = ("isomer-only files" if files
                               else "no file for target")
            else:
                src = CORPORA[corpus] / ground[0]
                row["file"] = ground[0]
                row["file_sha256"] = sha256(src)
                build = run_build(src)
                row["exit"] = build["exit"]
                row["seconds"] = build["seconds"]
                if build["exit"] == 0:
                    row["class"] = "ok"
                else:
                    row["class"] = classify_failure(build["message"])
                    row["message"] = build["message"]
            hist[row["class"]] = hist.get(row["class"], 0) + 1
            ledger.append(row)
            print(f"{corpus} {row['target']}: {row['class']}", flush=True)
        histograms[corpus] = hist

    critical = {
        corpus: {
            "ok": sum(1 for r in ledger
                      if r["corpus"] == corpus and r["dosimetry_critical"]
                      and r["class"] == "ok"),
            "total": sum(1 for r in ledger
                         if r["corpus"] == corpus and r["dosimetry_critical"]),
        }
        for corpus in CORPORA
    }

    record = {
        "schema": "actinv-p25b-g1-census-1",
        "pointwise_archive_sha256": POINTWISE_SHA256,
        "targets": [f"{ELEMENT_SYMBOLS[z - 1]}-{a}" for z, a in targets],
        "target_count": len(targets),
        "histograms": histograms,
        "dosimetry_critical": critical,
        "ledger": ledger,
    }
    (RESULTS / "g1_p25b_census.json").write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"histograms": histograms,
                      "dosimetry_critical": critical},
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
