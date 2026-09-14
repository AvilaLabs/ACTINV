#!/usr/bin/env python3
"""P24-G0: bounded candidate-artifact build over the IRDFF-II target set.

Derives the IRDFF-II reaction catalog's incident-neutron target list from
the hash-pinned pointwise archive's MF=1/MT=451 headers (target ZA is
evaluation metadata, not a held-out measurement value), maps each
isotopic target to its sealed ``n-working`` TENDL-2025 file, verifies
every staged file against the P18b source manifest, and builds each file
individually so a construction failure is an explicit ``construction_failed``
outcome — never a silent exclusion and never a batch abort.

Surviving files are assembled by a single directory ``build-library``
run into ``target/p24-g0/candidate-neutron.npz`` with the same recipe as
the pinned v1.0.1 artifact (tendl format, fispact-709 groups,
293.6 K). Elemental (natural-composition) targets have no TENDL file
and build nothing; they score ``variant_target_unavailable`` identically
on baseline and candidate.

Writes ``results/g0_p24_candidate_build.json``. Run under the enforced
systemd cgroup (see AGENTS.md); the control itself performs no scoring.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p24_candidate_build.json"
DATA_ROOT = Path(
    os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff")
)
POINTWISE_ARCHIVE = DATA_ROOT / "IRDFF-II_ENDF.zip"
POINTWISE_SHA256 = "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db"
CORPUS_MANIFEST = ROOT / "results" / "p18b_source_manifest.json.gz"
CORPUS_ROOT = Path(
    os.environ.get(
        "ACTINV_TENDL", Path.home() / "nuclear-data" / "tendl-2025" / "files"
    )
)
NEUTRON_DIR = CORPUS_ROOT / "n-working"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
WORK = ROOT / "target" / "p24-g0"
PER_TARGET = WORK / "per-target"
STAGED = WORK / "input-n"
CACHE = WORK / "cache-n"
CANDIDATE_NPZ = WORK / "candidate-neutron.npz"
CANDIDATE_INDEX = WORK / "candidate-neutron_index.json"
PER_FILE_TIMEOUT_S = 3600.0
DIRECTORY_TIMEOUT_S = 1800.0

ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am"
).split()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def endf_float(field: str) -> float:
    return float(re.sub(r"([+-])(\d+)$", r"e\1\2", field.strip()))


def irdff_targets() -> dict[str, object]:
    """Return the IRDFF-II MF1-declared target ZA set (metadata only)."""
    actual = sha256(POINTWISE_ARCHIVE)
    if actual != POINTWISE_SHA256:
        raise RuntimeError(
            f"IRDFF-II pointwise archive changed: {actual}"
        )
    with zipfile.ZipFile(POINTWISE_ARCHIVE) as archive:
        members = archive.namelist()
        if members != ["IRDFF-II.endf"]:
            raise RuntimeError(f"unexpected archive members {members}")
        text = archive.read(members[0]).decode("ascii", "replace")
    targets: dict[tuple[int, int], set[int]] = {}
    header = re.compile(r"^(.{11})(.{11}).{44}.{0,4}?(\d{4}) 1451    1", re.M)
    for match in header.finditer(text):
        try:
            za = endf_float(match.group(1))
        except ValueError:
            continue
        z_value, a_value = int(round(za)) // 1000, int(round(za)) % 1000
        targets.setdefault((z_value, a_value), set()).add(int(match.group(3)))
    return {
        "targets": {
            f"{z_value}-{a_value}": sorted(mats)
            for (z_value, a_value), mats in sorted(targets.items())
        },
        "isotopic_count": sum(1 for key in targets if key[1] != 0),
        "elemental_count": sum(1 for key in targets if key[1] == 0),
    }


def tendl_name(z_value: int, a_value: int) -> str:
    return f"n-{ELEMENT_SYMBOLS[z_value - 1]}{a_value:03d}.tendl"


def manifest_entries() -> dict[str, dict[str, object]]:
    manifest = json.loads(gzip.open(CORPUS_MANIFEST, "rt").read())
    entries = {}
    for record in manifest["corpora"]["neutron"]["files"]:
        entries[record["name"]] = record
    return entries


def run_build(args: list[str], timeout: float) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "command": args,
        "exit_status": completed.returncode,
        "elapsed_s": round(time.monotonic() - started, 3),
        "stdout_tail": completed.stdout.strip().splitlines()[-3:],
        "stderr_tail": completed.stderr.strip().splitlines()[-3:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    arguments = parser.parse_args()

    catalog = irdff_targets()
    isotopic = sorted(
        (int(key.split("-")[0]), int(key.split("-")[1]))
        for key in catalog["targets"]
        if int(key.split("-")[1]) != 0
    )
    entries = manifest_entries()

    per_file: list[dict[str, object]] = []
    staged_names: list[str] = []
    if not arguments.report_only:
        WORK.mkdir(parents=True, exist_ok=True)
        PER_TARGET.mkdir(parents=True, exist_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        STAGED.mkdir(parents=True, exist_ok=True)
        for z_value, a_value in isotopic:
            name = tendl_name(z_value, a_value)
            source = NEUTRON_DIR / name
            record: dict[str, object] = {
                "name": name,
                "target_za": z_value * 1000 + a_value,
            }
            expected = entries.get(name)
            if expected is None or not source.is_file():
                record["outcome"] = "construction_failed"
                record["reason"] = "source absent from sealed neutron corpus"
                per_file.append(record)
                continue
            digest = sha256(source)
            record["source_sha256"] = digest
            record["bytes"] = source.stat().st_size
            if digest != expected["source_sha256"]:
                record["outcome"] = "construction_failed"
                record["reason"] = "source digest differs from sealed manifest"
                per_file.append(record)
                continue
            output = PER_TARGET / f"{name}.npz"
            if not output.exists():
                outcome = run_build(
                    [
                        str(ACTINV),
                        "build-library",
                        str(source),
                        str(output),
                        "--format",
                        "tendl",
                        "--projectile",
                        "neutron",
                        "--groups",
                        "fispact-709",
                        "--temperature-K",
                        "293.6",
                        "--workers",
                        "2",
                        "--cache",
                        str(CACHE),
                    ],
                    PER_FILE_TIMEOUT_S,
                )
                record["build"] = outcome
                if outcome["exit_status"] != 0:
                    record["outcome"] = "construction_failed"
                    record["reason"] = "build-library exit " + str(outcome["exit_status"])
                    per_file.append(record)
                    continue
            record["outcome"] = "built"
            record["per_target_npz_sha256"] = sha256(output)
            destination = STAGED / name
            if not destination.exists() or sha256(destination) != digest:
                shutil.copyfile(source, destination)
            staged_names.append(name)
            per_file.append(record)

        # Assemble the bounded candidate artifact over the surviving set.
        for leftover in STAGED.iterdir():
            if leftover.name not in staged_names:
                leftover.unlink()
        directory = run_build(
            [
                str(ACTINV),
                "build-library",
                str(STAGED),
                str(CANDIDATE_NPZ),
                "--format",
                "tendl",
                "--projectile",
                "neutron",
                "--groups",
                "fispact-709",
                "--temperature-K",
                "293.6",
                "--workers",
                "2",
                "--cache",
                str(CACHE),
            ],
            DIRECTORY_TIMEOUT_S,
        )
    else:
        directory = {"command": None, "exit_status": None}

    index = None
    if CANDIDATE_INDEX.is_file():
        index = json.loads(CANDIDATE_INDEX.read_text(encoding="utf-8"))
    built_names = [record["name"] for record in per_file if record["outcome"] == "built"]
    failed_names = [
        record["name"] for record in per_file if record["outcome"] != "built"
    ]
    index_targets = {
        int(target["za"]) for target in (index or {}).get("targets", [])
    }
    expected_targets = {
        z_value * 1000 + a_value for z_value, a_value in isotopic
    } - {
        int(record["target_za"])
        for record in per_file
        if record["outcome"] != "built"
    }

    record_out = {
        "schema": "actinv-p24-candidate-build-1",
        "irdff_catalog": catalog,
        "per_file": per_file,
        "directory_build": directory,
        "artifact": {
            "npz": str(CANDIDATE_NPZ),
            "index": str(CANDIDATE_INDEX),
            "npz_sha256": sha256(CANDIDATE_NPZ) if CANDIDATE_NPZ.is_file() else None,
            "index_sha256": sha256(CANDIDATE_INDEX)
            if CANDIDATE_INDEX.is_file()
            else None,
            "index_targets": sorted(index_targets),
            "index_npz_sha256": (index or {}).get("sha256_npz"),
            "n_rows": (index or {}).get("n_rows"),
        },
        "counts": {
            "isotopic_targets": len(isotopic),
            "elemental_targets": catalog["elemental_count"],
            "built": len(built_names),
            "construction_failed": len(failed_names),
            "staged": len(staged_names),
        },
        "construction_failed_names": failed_names,
        "pass": bool(
            CANDIDATE_NPZ.is_file()
            and CANDIDATE_INDEX.is_file()
            and index_targets == expected_targets
            and not arguments.report_only
        ),
    }
    if not arguments.no_write:
        RESULT.write_text(
            json.dumps(record_out, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "pass": record_out["pass"],
                "counts": record_out["counts"],
                "construction_failed_names": failed_names,
                "npz_sha256": record_out["artifact"]["npz_sha256"],
                "index_sha256": record_out["artifact"]["index_sha256"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    return 0 if record_out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
