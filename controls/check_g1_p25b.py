#!/usr/bin/env python3
"""P25b G1 independent checker for the construction census.

Imports no production module.  Re-derives the IRDFF-II isotopic
target set from the sealed archive, re-derives each corpus's
filename index, independently reclassifies every ledger row from its
recorded fields, replays every failed build plus a hash-selected
subsample of `ok` builds through `actinv build-library --format
auto`, recomputes the histograms and dosimetry sub-census, and
rejects planted mutations of the census record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
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
RECORD = RESULTS / "g1_p25b_census.json"
DATA_ROOT = Path(
    os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff")
)
POINTWISE_ARCHIVE = DATA_ROOT / "IRDFF-II_ENDF.zip"
POINTWISE_SHA256 = "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db"
ACTINV = os.environ.get("ACTINV_BIN", "actinv")
WORK = ROOT / "target" / "preflight-tmp" / "p25b-g1-check"
TIMEOUT_S = 90.0

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": ND / "tendl-2023" / "files",
    "fendl_32c": ND / "fendl-3.2c" / "endf",
    "eaf_2010": ND / "eaf-2010" / "files",
}
CLASSES = {"ok", "construction_error", "format_unsupported",
           "corpus_incomplete"}
DOSIMETRY_CRITICAL = {(28, 58), (79, 197), (47, 109), (41, 93), (49, 113),
                      (49, 115)}

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def endf_float(field: str) -> float:
    text = field.strip()
    match = re.match(r"^([+-]?\d+(?:\.\d*)?)([+-]\d+)$", text)
    if match:
        return float(match.group(1)) * 10.0 ** int(match.group(2))
    return float(text)


def irdff_targets() -> set[tuple[int, int]]:
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
    return targets


def parse_filename(name: str) -> tuple[int, int] | None:
    for pat in (r"^n_(\d{3})-[A-Za-z]{1,2}-(\d+)[mMnN]?_\d+\.dat$",
                r"^n_\d+_(\d+)-[A-Za-z]{1,2}-(\d+)[mMnN]?\.(?:dat|endf)$"):
        m = re.match(pat, name)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def corpus_index(corpus: str) -> dict[tuple[int, int], list[str]]:
    out: dict[tuple[int, int], list[str]] = {}
    for path in sorted(CORPORA[corpus].iterdir()):
        if not path.is_file():
            continue
        parsed = parse_filename(path.name)
        if parsed:
            out.setdefault(parsed, []).append(path.name)
    return out


def replay(src: Path) -> dict:
    out = WORK / "replay.npz"
    out.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [ACTINV, "build-library", str(src), str(out),
             "--format", "auto"],
            capture_output=True, text=True, timeout=TIMEOUT_S)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3)}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout", "seconds": round(time.monotonic() - t0, 3)}
    finally:
        out.unlink(missing_ok=True)


def check_record(record: dict, targets: set[tuple[int, int]],
                 indexes: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p25b-g1-census-1":
        local.append("schema")
    if record.get("pointwise_archive_sha256") != POINTWISE_SHA256:
        local.append("archive hash field")
    if record.get("target_count") != len(targets):
        local.append("target_count")
    if set(record.get("targets", [])) != {
            f"{'H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am'.split()[z - 1]}-{a}"
            for z, a in targets}:
        local.append("targets list")
    hist: dict[str, dict[str, int]] = {}
    crit: dict[str, dict[str, int]] = {}
    for row in record.get("ledger", []):
        corpus = row["corpus"]
        if row.get("class") not in CLASSES:
            local.append(f"{corpus} {row['target']}: bad class")
            continue
        hist.setdefault(corpus, {})[row["class"]] = \
            hist.setdefault(corpus, {}).get(row["class"], 0) + 1
        z, a = divmod(row["za"], 1000)
        if (z, a) in DOSIMETRY_CRITICAL:
            c = crit.setdefault(corpus, {"ok": 0, "total": 0})
            c["total"] += 1
            if row["class"] == "ok":
                c["ok"] += 1
        if (z, a) not in targets:
            local.append(f"{corpus} {row['target']}: not an IRDFF target")
        files = indexes.get(corpus, {}).get((z, a), [])
        if row["class"] == "corpus_incomplete":
            if row.get("file"):
                local.append(f"{corpus} {row['target']}: incomplete w/ file")
        else:
            src = CORPORA[corpus] / row.get("file", "")
            if not src.is_file():
                local.append(f"{corpus} {row['target']}: missing file")
            elif sha256(src) != row.get("file_sha256"):
                local.append(f"{corpus} {row['target']}: file hash")
            if row["class"] == "ok" and row.get("exit") != 0:
                local.append(f"{corpus} {row['target']}: ok w/ nonzero exit")
            if row["class"] != "ok":
                if not row.get("message"):
                    local.append(f"{corpus} {row['target']}: no message")
                if row.get("exit") == 0:
                    local.append(f"{corpus} {row['target']}: fail w/ exit 0")
    if record.get("histograms") != hist:
        local.append("histograms")
    if record.get("dosimetry_critical") != crit:
        local.append("dosimetry_critical")
    return local


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    record = json.loads(RECORD.read_text())
    targets = irdff_targets()
    indexes = {c: corpus_index(c) for c in CORPORA}
    if record.get("target_count") != len(targets):
        failures.append("independent target count mismatch")

    # Independent replay: every non-ok row plus a deterministic
    # hash-selected slice of ok rows.
    replays = 0
    for row in record.get("ledger", []):
        if row["class"] == "corpus_incomplete":
            continue
        digest = hashlib.sha256(
            f"{row['corpus']}:{row['target']}".encode()).hexdigest()
        if row["class"] == "ok" and int(digest[:2], 16) % 16:
            continue
        src = CORPORA[row["corpus"]] / row["file"]
        got = replay(src)
        replays += 1
        want_ok = row["class"] == "ok"
        if (got["exit"] == 0) != want_ok:
            failures.append(
                f"replay {row['corpus']} {row['target']}: "
                f"exit {got['exit']} vs class {row['class']}")

    failures.extend(check_record(record, targets, indexes))

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r["ledger"][0].update({"class": "corpus_incomplete"}),
        lambda r: r["histograms"]["eaf_2010"].update({"ok": 1}),
        lambda r: r.update({"target_count": 1}),
        lambda r: r["dosimetry_critical"]["eaf_2010"].update({"ok": 0}),
        lambda r: r.update({"pointwise_archive_sha256": "0" * 64}),
        lambda r: r["ledger"][5].update({"file_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_record(m, targets, indexes):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25b-g1-check-1",
        "pass": not failures,
        "failures": failures,
        "replayed_builds": replays,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    (RESULTS / "g1_p25b_check.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
