#!/usr/bin/env python3
"""P17 G0: verify provenance, access and the pre-unseal evidence partition."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tempfile
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p17_seal.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
OPENING_COMMIT = "f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d"
RELEASE_COMMIT = "0332779401363d2f39722efe7a0b7218afcfb270"
ALARA_COMMIT = "faa5b330460fe865e38fc788f1b792ea33d13d1b"
NJOY_COMMIT = "ac5adf5f33d893e42f2eed7fb286b0d51c7580da"

IRDFF_DIR = Path(
    os.environ.get(
        "ACTINV_P17_IRDFF_DIR", Path.home() / "nuclear-data" / "p17-irdff"
    )
)
ALARA_SOURCE = Path(
    os.environ.get(
        "ACTINV_ALARA_SOURCE", Path.home() / "nuclear-data" / "alara-2.9.2"
    )
)
ALARA_BIN = Path(
    os.environ.get(
        "ACTINV_ALARA_BIN",
        Path.home() / "nuclear-data" / "alara-2.9.2-build" / "src" / "alara",
    )
)
NJOY_SOURCE = Path(
    os.environ.get(
        "ACTINV_P17_NJOY_SOURCE", Path.home() / "nuclear-data" / "njoy2016.79"
    )
)
NJOY_BIN = Path(
    os.environ.get(
        "ACTINV_P17_NJOY", Path.home() / "nuclear-data" / "njoy2016.79-build" / "njoy"
    )
)
P17_TOOLCHAIN = Path(
    os.environ.get(
        "ACTINV_P17_TOOLCHAIN", Path.home() / "nuclear-data" / "p17-toolchain" / "root"
    )
)
FORTRAN_COMPILER = Path(
    os.environ.get(
        "ACTINV_P17_FC", P17_TOOLCHAIN / "usr/bin/x86_64-linux-gnu-gfortran-15"
    )
)
CMAKE = Path(os.environ.get("ACTINV_P17_CMAKE", P17_TOOLCHAIN / "usr/bin/cmake"))
FENDL_DIR = Path(
    os.environ.get(
        "ACTINV_FENDL", Path.home() / "nuclear-data" / "fendl-3.2c" / "endf"
    )
)
ACTINV_BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))

IRDFF_INPUTS: dict[str, dict[str, Any]] = {
    "primary_reference": {
        "filename": "IRDFF-II_primary_1909.03336.pdf",
        "url": "https://arxiv.org/pdf/1909.03336",
        "sha256": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
        "bytes": 21_804_821,
        "pdf_pages": 110,
    },
    "benchmark_field_list": {
        "filename": "NeutronBenchmarkFields-IRDFF-II.pdf",
        "url": "https://www-nds.iaea.org/IRDFF/NeutronBenchmarkFields-IRDFF-II.pdf",
        "sha256": "93926f4a9937ef1314ebbaa29a11a638ad9d1b3abad08596b0db57ee6bc9c304",
        "bytes": 102_405,
        "pdf_pages": 1,
    },
    "pointwise": {
        "filename": "IRDFF-II_ENDF.zip",
        "url": "https://www-nds.iaea.org/IRDFF/IRDFF-II_ENDF.zip",
        "sha256": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
        "bytes": 33_372_016,
        "members": ["IRDFF-II.endf"],
    },
    "group_725": {
        "filename": "IRDFF-II_g725.zip",
        "url": "https://www-nds.iaea.org/IRDFF/IRDFF-II_g725.zip",
        "sha256": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
        "bytes": 4_068_081,
        "members": ["IRDFF-II.g725"],
    },
    "spectra": {
        "filename": "IRDFF-II_sp_g.zip",
        "url": "https://www-nds.iaea.org/IRDFF/IRDFF-II_sp_g.zip",
        "sha256": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
        "bytes": 368_054,
        "members": ["IRDFF-II_sp.g"],
    },
    "decay": {
        "filename": "IRDFF-II_dd_ENDF.zip",
        "url": "https://www-nds.iaea.org/IRDFF/IRDFF-II_dd_ENDF.zip",
        "sha256": "397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7",
        "bytes": 301_693,
        "members": ["IRDFF-II_dd.endf"],
    },
}

FENDL_INPUTS = {
    "Fe56": (
        "n_2631_26-Fe-56.endf",
        "24a45021fb38262dd8fb598c520a807f342bd07e137a36e88d7ae97a0f38715e",
    ),
    "Ag107": (
        "n_4725_47-Ag-107.endf",
        "0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185",
    ),
    "W186": (
        "n_7443_74-W-186.endf",
        "bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744",
    ),
    "Au197": (
        "n_7925_79-Au-197.endf",
        "fb7897fdde04b68b79cfc2a44e90a7c3aba77397815a5be342648af013f39f6d",
    ),
    "Co59": (
        "n_2725_27-Co-59.endf",
        "a4c6480e200b9474ed04900e4d17d018577d6235d57f31609b75322ae9a3b75d",
    ),
    "Ni58": (
        "n_2825_28-Ni-58.endf",
        "312f5a069dbda4e0abd662a258710ea332dd749191a9bad2a0c70567644af4f4",
    ),
}

ALARA_INPUTS = {
    "activation": (
        "sample/data/truncated_fendlg-2.0_175_for_samples_only",
        "f45ced4d5676c993f6b6dd562d5e312e897eabb959dc6ebba56bbeaecde22312",
    ),
    "decay": (
        "sample/data/truncated_fendld-2.0_for_samples_only",
        "810f3b8ca46dd55b965e37b84c9793057a7ee53aa2a194a2fcb1ff0d1b681940",
    ),
    "element": (
        "sample/data/myElelib",
        "bdfcfdb255d89b4988be9fab4279c36fb9615709ee6a738e963591db6146c290",
    ),
}

NJOY_SOURCES = {
    "reconr": (
        "src/reconr.f90",
        "054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db",
    ),
    "broadr": (
        "src/broadr.f90",
        "b2dc071a0f63975cfe702f84441539cfaecbdeb7dfd74c5be70578b72184744e",
    ),
    "unresr": (
        "src/unresr.f90",
        "57a3a975566d45a8f2d0db67fed121b908e50039d9aafb25ea27f628c745d650",
    ),
    "groupr": (
        "src/groupr.f90",
        "0b7b7237f897071552b81a59eb0c3bcccae36aa3dbc585a4d55f0e103e9f6d31",
    ),
    "license": (
        "LICENSE",
        "08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c",
    ),
}

DIAGNOSTIC_TABLES = [18, 19, 20]
HELD_OUT = {
    "H1": {"tables": [21, 22, 23], "family": "SPR-III EOI activities"},
    "H2": {"tables": [24, 25], "family": "ACRR EOI activities"},
    "H3": {"tables": [36], "family": "high-temperature Maxwellian SACS"},
}
PRODUCT_PATHS = ["Cargo.lock", "Cargo.toml", "crates", "python", "data", "examples"]
PRE_UNSEAL_ALLOWED_RESULTS = {
    "results/g0_p17_seal.json",
    "results/g1_p17_operators.json",
    "results/g2_p17_identical_data.json",
    "results/g3_p17_processing.json",
    "results/g4_p17_diagnostics.json",
    "results/p17_unseal_authorization.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def command(
    arguments: list[str | Path],
    *,
    timeout: float = 120.0,
    cwd: Path = ROOT,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def git_commit(path: Path) -> str | None:
    completed = command(["git", "-C", path, "rev-parse", "HEAD"])
    return completed.stdout.strip() if completed.returncode == 0 else None


def checked_file(path: Path, expected: str) -> dict[str, object]:
    actual = sha256(path) if path.is_file() else None
    return {
        "filename": path.name,
        "expected_sha256": expected,
        "actual_sha256": actual,
        "bytes": path.stat().st_size if path.is_file() else None,
        "pass": actual == expected,
    }


def archive_members(path: Path, expected: list[str]) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()
            corrupt = archive.testzip()
    except (OSError, zipfile.BadZipFile):
        members = []
        corrupt = "unreadable"
    normalized = [PurePosixPath(member) for member in members]
    safe = all(
        member
        and not member.startswith(("/", "\\"))
        and "\\" not in member
        and ".." not in pure.parts
        for member, pure in zip(members, normalized, strict=True)
    )
    return {
        "members": members,
        "expected_members": expected,
        "duplicates_absent": len(members) == len(set(members)),
        "safe_paths": safe,
        "first_corrupt_member": corrupt,
        "pass": members == expected
        and len(members) == len(set(members))
        and safe
        and corrupt is None,
    }


def pdf_pages(path: Path) -> int | None:
    completed = command(["pdfinfo", path])
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def protocol_and_source() -> dict[str, object]:
    protocol_actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    ledger_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P17_PROTOCOL.md"
    ledger = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    opening = command(["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"])
    release = command(["git", "rev-parse", "v1.0.1^{}"])
    changed = command(["git", "diff", "--name-only", OPENING_COMMIT, "HEAD", "--", *PRODUCT_PATHS])
    changed_paths = sorted(path for path in changed.stdout.splitlines() if path)
    return {
        "protocol": {
            "expected_sha256": PROTOCOL_SHA256,
            "actual_sha256": protocol_actual,
            "ledger_entry": ledger_line in ledger,
        },
        "opening_commit": OPENING_COMMIT,
        "opening_is_ancestor": opening.returncode == 0,
        "release_commit": RELEASE_COMMIT,
        "release_tag_commit": release.stdout.strip() if release.returncode == 0 else None,
        "production_paths_changed_since_opening": changed_paths,
        "pass": protocol_actual == PROTOCOL_SHA256
        and ledger_line in ledger
        and opening.returncode == 0
        and release.returncode == 0
        and release.stdout.strip() == RELEASE_COMMIT
        and changed.returncode == 0
        and not changed_paths,
    }


def irdff_inputs() -> dict[str, object]:
    rows: dict[str, Any] = {}
    for name, expected in IRDFF_INPUTS.items():
        path = IRDFF_DIR / expected["filename"]
        row = checked_file(path, expected["sha256"])
        row.update({"url": expected["url"], "expected_bytes": expected["bytes"]})
        row["size_matches"] = row["bytes"] == expected["bytes"]
        if "members" in expected:
            row["archive"] = archive_members(path, expected["members"])
        if "pdf_pages" in expected:
            row["expected_pdf_pages"] = expected["pdf_pages"]
            row["actual_pdf_pages"] = pdf_pages(path) if path.is_file() else None
            row["pdf_pages_match"] = row["actual_pdf_pages"] == expected["pdf_pages"]
        row["pass"] = bool(
            row["pass"]
            and row["size_matches"]
            and ("archive" not in row or row["archive"]["pass"])
            and ("pdf_pages_match" not in row or row["pdf_pages_match"])
        )
        rows[name] = row
    return {"files": rows, "pass": all(row["pass"] for row in rows.values())}


def processing_inputs() -> dict[str, object]:
    fendl = {
        name: checked_file(FENDL_DIR / filename, expected)
        for name, (filename, expected) in FENDL_INPUTS.items()
    }
    alara_files = {
        name: checked_file(ALARA_SOURCE / relative, expected)
        for name, (relative, expected) in ALARA_INPUTS.items()
    }
    njoy_sources = {
        name: checked_file(NJOY_SOURCE / relative, expected)
        for name, (relative, expected) in NJOY_SOURCES.items()
    }
    alara_version_run = command([ALARA_BIN, "-V"])
    temporary_root = ROOT / "target" / "p17-control-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    if NJOY_BIN.is_file():
        with tempfile.TemporaryDirectory(prefix="njoy-version-", dir=temporary_root) as directory:
            njoy_version_run = command(
                [NJOY_BIN], timeout=20.0, cwd=Path(directory), input_text="stop\n"
            )
    else:
        njoy_version_run = None
    actinv_version_run = command([ACTINV_BIN, "--version"]) if ACTINV_BIN.is_file() else None
    compiler_run = command([FORTRAN_COMPILER, "--version"]) if FORTRAN_COMPILER.is_file() else None
    cmake_environment = os.environ.copy()
    local_libraries = P17_TOOLCHAIN / "usr/lib/x86_64-linux-gnu"
    if local_libraries.is_dir():
        cmake_environment["LD_LIBRARY_PATH"] = str(local_libraries)
    cmake_run = (
        subprocess.run(
            [str(CMAKE), "--version"],
            cwd=ROOT,
            env=cmake_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20.0,
            check=False,
        )
        if CMAKE.is_file()
        else None
    )
    alara_commit = git_commit(ALARA_SOURCE)
    njoy_commit = git_commit(NJOY_SOURCE)
    versions = {
        "actinv": {
            "version": actinv_version_run.stdout.strip()
            if actinv_version_run and actinv_version_run.returncode == 0
            else None,
            "binary_sha256": sha256(ACTINV_BIN) if ACTINV_BIN.is_file() else None,
        },
        "alara": {
            "version": (alara_version_run.stdout + alara_version_run.stderr).strip()
            if alara_version_run.returncode == 0
            else None,
            "source_commit": alara_commit,
            "binary_sha256": sha256(ALARA_BIN) if ALARA_BIN.is_file() else None,
        },
        "njoy": {
            "banner_present": bool(
                njoy_version_run
                and "njoy" in (njoy_version_run.stdout + njoy_version_run.stderr).lower()
            ),
            "source_commit": njoy_commit,
            "binary_sha256": sha256(NJOY_BIN) if NJOY_BIN.is_file() else None,
            "compiler": compiler_run.stdout.splitlines()[0]
            if compiler_run and compiler_run.returncode == 0
            else None,
            "cmake": cmake_run.stdout.splitlines()[0]
            if cmake_run and cmake_run.returncode == 0
            else None,
        },
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "openmc": importlib.metadata.version("openmc"),
        "numpy": importlib.metadata.version("numpy"),
        "scipy": importlib.metadata.version("scipy"),
    }
    versions_pass = bool(
        versions["actinv"]["version"] == "actinv 1.0.1"
        and versions["alara"]["version"]
        and "ALARA 2.9.2" in versions["alara"]["version"]
        and alara_commit == ALARA_COMMIT
        and versions["njoy"]["banner_present"]
        and njoy_commit == NJOY_COMMIT
        and versions["njoy"]["compiler"] is not None
        and versions["njoy"]["cmake"] is not None
        and versions["openmc"] == "0.15.3"
        and versions["numpy"] == "2.5.2"
        and versions["scipy"] == "1.18.0"
    )
    return {
        "fendl": fendl,
        "alara_files": alara_files,
        "njoy_sources": njoy_sources,
        "versions": versions,
        "pass": all(row["pass"] for row in fendl.values())
        and all(row["pass"] for row in alara_files.values())
        and all(row["pass"] for row in njoy_sources.values())
        and versions_pass,
    }


def seal_partition() -> dict[str, object]:
    diagnostic = set(DIAGNOSTIC_TABLES)
    held_out_tables = {
        table
        for family in HELD_OUT.values()
        for table in family["tables"]
    }
    tracked = command(["git", "ls-files", "results"])
    p17_results = sorted(
        path for path in tracked.stdout.splitlines() if "p17" in Path(path).name.lower()
    )
    unexpected = sorted(set(p17_results) - PRE_UNSEAL_ALLOWED_RESULTS)
    return {
        "state": "sealed",
        "selection_basis": "primary-reference metadata and table captions only",
        "numeric_held_out_rows_inspected": False,
        "diagnostic_tables": sorted(diagnostic),
        "held_out_families": HELD_OUT,
        "partitions_disjoint": diagnostic.isdisjoint(held_out_tables),
        "tracked_p17_results": p17_results,
        "unexpected_pre_unseal_results": unexpected,
        "unseal_authorized": False,
        "pass": tracked.returncode == 0
        and diagnostic.isdisjoint(held_out_tables)
        and not unexpected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    source = protocol_and_source()
    irdff = irdff_inputs()
    processing = processing_inputs()
    seal = seal_partition()
    output = {
        "schema": "actinv-p17-seal-1",
        "source": source,
        "irdff": irdff,
        "processing_controls": processing,
        "partition": seal,
        "pass": bool(source["pass"] and irdff["pass"] and processing["pass"] and seal["pass"]),
    }
    encoded = json.dumps(output, indent=1, sort_keys=True) + "\n"
    if not arguments.no_write:
        RESULT.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
