#!/usr/bin/env python3
"""P24-G0: opening, authority and fresh-partition seal.

Verifies, without importing any ACTINV production, audit or scoring
module:

- the frozen P24 protocol digest, its ``protocol_hash.txt`` ledger line,
  and that HEAD descends from the opening commit with no production-path
  change since;
- every prior verdict file exists and carries its expected verdict
  string (26 verdicts — ``P17-FAIL``, ``P18-FAIL``, ``P18b-FAIL`` and
  ``P25-FAIL`` asserted verbatim; P24 never rewrites them);
- the P17 lineage: protocol and Amendment 1 digests, and every evidence
  digest the P17 session pinned still matching the files on disk;
- the complete IRDFF-II source corpus (primary PDF, benchmark-field
  list, and the groupwise/spectrum/pointwise/decay archives) still
  hashing to the P17-pinned digests;
- the v1.0.1 baseline and P25-repaired candidate neutron artifacts
  (built by ``g0_p24_candidate_build.py`` over the IRDFF-II target set)
  with their SHA-256 identities;
- the fresh-partition seal: the family/table/page/MAT map, disjointness
  from diagnostic and consumed partitions, and that no committed parser
  configuration names a fresh table and no result file outside the
  pre-unseal allowlist has appeared since the opening commit;
- the release boundary: workspace version 1.1.0, passing P22
  release-candidate record, no ``v1.1*`` tag — the hold stands.

Writes ``results/g0_p24_seals.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p24_seals.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P24_PROTOCOL.md"
P17_PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
P17_AMENDMENT = ROOT / "protocols" / "ACTINV-P17_AMENDMENT_1.md"
P17_SESSION = ROOT / "results" / "session_p17.json"
P17_VERDICT = ROOT / "results" / "verdict_p17.json"
P25_VERDICT = ROOT / "results" / "verdict_p25.json"
P22_RC = ROOT / "results" / "g4_p22_release_candidate.json"
CANDIDATE_BUILD = ROOT / "results" / "g0_p24_candidate_build.json"
ACTINV = ROOT / "target" / "release" / "actinv"

DATA_ROOT = Path(
    os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff")
)
BASELINE_NPZ = Path(
    os.environ.get(
        "ACTINV_LIBRARY",
        Path.home() / "nuclear-data" / "tendl-2025" / "builds" / "full" / "neutron.n.p10.npz",
    )
)
BASELINE_INDEX = BASELINE_NPZ.with_name(BASELINE_NPZ.stem + "_index.json")
CANDIDATE_NPZ = ROOT / "target" / "p24-g0" / "candidate-neutron.npz"
CANDIDATE_INDEX = ROOT / "target" / "p24-g0" / "candidate-neutron_index.json"

PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
P17_PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
P17_AMENDMENT_SHA256 = "e78c84d9f80c9bc6b7f0e79050206991515d283f43deeabd32f42c325f53581e"
OPENING_COMMIT = "122a1d360895368f37d7ccf00ffc8b6cabbbc5dc"
RELEASE_COMMIT = "0332779401363d2f39722efe7a0b7218afcfb270"
BASELINE_NPZ_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
BASELINE_INDEX_SHA256 = "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"

PRODUCT_PATHS = ["Cargo.lock", "Cargo.toml", "crates", "python", "data", "examples"]

IRDFF_INPUTS: dict[str, dict[str, object]] = {
    "primary_reference": {
        "filename": "IRDFF-II_primary_1909.03336.pdf",
        "sha256": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    },
    "benchmark_field_list": {
        "filename": "NeutronBenchmarkFields-IRDFF-II.pdf",
        "sha256": "93926f4a9937ef1314ebbaa29a11a638ad9d1b3abad08596b0db57ee6bc9c304",
    },
    "pointwise": {
        "filename": "IRDFF-II_ENDF.zip",
        "sha256": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
        "members": ["IRDFF-II.endf"],
    },
    "group_725": {
        "filename": "IRDFF-II_g725.zip",
        "sha256": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
        "members": ["IRDFF-II.g725"],
    },
    "spectra": {
        "filename": "IRDFF-II_sp_g.zip",
        "sha256": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
        "members": ["IRDFF-II_sp.g"],
    },
    "decay": {
        "filename": "IRDFF-II_dd_ENDF.zip",
        "sha256": "397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7",
        "members": ["IRDFF-II_dd.endf"],
    },
}

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p25.json": "P25-FAIL",
}

P17_EVIDENCE = {
    "g0_seal": "results/g0_p17_seal.json",
    "g1_operators": "results/g1_p17_operators.json",
    "g2_identical_data": "results/g2_p17_identical_data.json",
    "g3_processing": "results/g3_p17_processing.json",
    "g4_diagnostics": "results/g4_p17_diagnostics.json",
    "g4_check": "results/g4_p17_check.json",
    "unseal_authorization": "results/p17_unseal_authorization.json",
    "unseal_check": "results/p17_unseal_check.json",
    "g5_heldout": "results/g5_p17_heldout.json",
    "g5_check": "results/g5_p17_check.json",
    "cause_ledger_open": "results/p17_cause_ledger.json",
    "cause_ledger_heldout": "results/p17_cause_ledger_g5.json",
}

DIAGNOSTIC_TABLES = [18, 19, 20]
CONSUMED_P17_TABLES = [21, 22, 23, 24, 25, 36]
OPEN_SUPPORT_TABLES = [5, 16, 37, 38, 39, 40, 47]
FRESH_FAMILIES: dict[str, dict[str, object]] = {
    "F-MOLBR1": {"tables": [26], "pages": [79], "mats": [9020], "observable": "spectral_index"},
    "F-TRIGA": {"tables": [27], "pages": [81], "mats": [9041, 9042, 9043, 9044], "observable": "filtered_unfiltered_ratio"},
    "F-HMF001": {"tables": [28], "pages": [82], "mats": [9101], "observable": "spectral_index"},
    "F-HMF028": {"tables": [29], "pages": [83], "mats": [9102], "observable": "spectral_index"},
    "F-IMF007": {"tables": [30], "pages": [84], "mats": [9103], "observable": "spectral_index"},
    "F-PMF": {"tables": [31], "pages": [85], "mats": [9104, 9106, 9107], "observable": "spectral_index"},
    "F-FMR001": {"tables": [32], "pages": [85], "mats": [9110], "observable": "spectral_index"},
    "F-LEGACY": {"tables": [33], "pages": [87], "mats": [9004, 9005, 9007], "observable": "sacs_or_si_ratio"},
    "F-THERMAL-XS": {"tables": [34], "pages": [88], "mats": [], "observable": "sigma0"},
    "F-THERMAL-I0": {"tables": [35], "pages": [89], "mats": [], "observable": "resonance_integral"},
    "F-ACRR-LB44": {"tables": [41], "pages": [96], "mats": [9013], "observable": "eoi_spectral_index"},
    "F-ACRR-PLG": {"tables": [42], "pages": [97], "mats": [9012], "observable": "eoi_spectral_index"},
    "F-ACRR-CDPOLY": {"tables": [43], "pages": [98], "mats": [9011], "observable": "eoi_spectral_index"},
    "F-ACRR-FREC2": {"tables": [44], "pages": [99], "mats": [9015], "observable": "eoi_spectral_index"},
    "F-BEDN": {"tables": [45, 46], "pages": [100, 100], "mats": [9408, 9409], "observable": "production_rate"},
}
FRESH_TABLES = sorted({t for f in FRESH_FAMILIES.values() for t in f["tables"]})

PRE_UNSEAL_ALLOWED_RESULTS = {
    "results/g0_p24_candidate_build.json",
    "results/g0_p24_seals.json",
    "results/g0_p24_check.json",
}
PRE_UNSEAL_ALLOWED_CONTROLS = {
    "controls/g0_p24_candidate_build.py",
    "controls/g0_p24_seal.py",
    "controls/check_g0_p24.py",
}
TABLE_MAP_PATTERN = re.compile(
    r"(?:TABLE_PAGES|EXPECTED_ROWS|TABLE_SPECTRUM_MAT|FIELD_SPECTRUM_MAT)"
    r"\s*(?::[^=]*)?=\s*\{([^}]*)\}",
    re.S,
)
TABLE_CALL_PATTERN = re.compile(r"table_text\((\d+)\)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command(arguments: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def protocol_and_source() -> dict[str, object]:
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    ledger_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P24_PROTOCOL.md"
    ledger = (ROOT / "protocols" / "protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    opening = command(["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"])
    protocol_diff = command(
        ["git", "diff", "--name-only", OPENING_COMMIT, "HEAD", "--", "protocols/"]
    )
    changed_protocols = sorted(
        path for path in protocol_diff.stdout.splitlines() if path
    )
    release = command(["git", "rev-parse", "v1.0.1^{}"])
    changed = command(
        ["git", "diff", "--name-only", OPENING_COMMIT, "HEAD", "--", *PRODUCT_PATHS]
    )
    changed_paths = sorted(path for path in changed.stdout.splitlines() if path)
    return {
        "protocol": {
            "expected_sha256": PROTOCOL_SHA256,
            "actual_sha256": actual,
            "ledger_entry": ledger_line in ledger,
        },
        "opening_commit": OPENING_COMMIT,
        "opening_is_ancestor": opening.returncode == 0,
        "protocol_paths_changed_since_opening": changed_protocols,
        "release_commit": RELEASE_COMMIT,
        "release_tag_commit": release.stdout.strip() if release.returncode == 0 else None,
        "production_paths_changed_since_opening": changed_paths,
        "pass": actual == PROTOCOL_SHA256
        and ledger_line in ledger
        and opening.returncode == 0
        and not changed_protocols
        and release.returncode == 0
        and release.stdout.strip() == RELEASE_COMMIT
        and changed.returncode == 0
        and not changed_paths,
    }


def irdff_inputs() -> dict[str, object]:
    rows: dict[str, object] = {}
    for name, expected in IRDFF_INPUTS.items():
        path = DATA_ROOT / expected["filename"]
        actual = sha256(path) if path.is_file() else None
        row: dict[str, object] = {
            "expected_sha256": expected["sha256"],
            "actual_sha256": actual,
            "pass": actual == expected["sha256"],
        }
        if "members" in expected and path.is_file():
            try:
                with zipfile.ZipFile(path) as archive:
                    members = archive.namelist()
                    corrupt = archive.testzip()
            except (OSError, zipfile.BadZipFile):
                members = []
                corrupt = "unreadable"
            safe = all(
                member
                and not member.startswith(("/", "\\"))
                and "\\" not in member
                and ".." not in PurePosixPath(member).parts
                for member in members
            )
            row["archive"] = {
                "members": members,
                "first_corrupt_member": corrupt,
                "safe_paths": safe,
                "pass": members == expected["members"] and safe and corrupt is None,
            }
            row["pass"] = bool(row["pass"] and row["archive"]["pass"])
        rows[name] = row
    return {"files": rows, "pass": all(row["pass"] for row in rows.values())}


def p17_lineage() -> dict[str, object]:
    session = json.loads(P17_SESSION.read_text(encoding="utf-8"))
    pinned = session.get("evidence_sha256", {})
    seals: dict[str, object] = {}
    for name, relative in P17_EVIDENCE.items():
        path = ROOT / relative
        actual = sha256(path) if path.is_file() else None
        seals[name] = {
            "file": relative,
            "expected_sha256": pinned.get(name),
            "observed_sha256": actual,
            "matches": actual is not None and actual == pinned.get(name),
        }
    protocol_actual = sha256(P17_PROTOCOL)
    amendment_actual = sha256(P17_AMENDMENT)
    return {
        "p17_protocol_sha256": protocol_actual,
        "p17_amendment_sha256": amendment_actual,
        "evidence_seals": seals,
        "session_verdict": session.get("verdict"),
        "pass": protocol_actual == P17_PROTOCOL_SHA256
        and amendment_actual == P17_AMENDMENT_SHA256
        and session.get("verdict") == "P17-FAIL"
        and all(row["matches"] for row in seals.values()),
    }


def prior_verdicts() -> dict[str, object]:
    verdicts: dict[str, object] = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        observed = None
        if path.exists():
            observed = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        verdicts[name] = {"expected": expected, "observed": observed}
    return {
        "verdicts": verdicts,
        "pass": all(v["observed"] == v["expected"] for v in verdicts.values()),
    }


def release_boundary() -> dict[str, object]:
    rc = json.loads(P22_RC.read_text(encoding="utf-8"))
    rc_ok = bool(rc.get("pass")) and bool((rc.get("decision") or {}).get("release_ready"))
    tags = [
        tag
        for tag in command(["git", "tag", "--list", "v1.1*"]).stdout.splitlines()
        if tag
    ]
    workspace = re.search(
        r'version\s*=\s*"([^"]+)"', (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    ).group(1)
    p25 = json.loads(P25_VERDICT.read_text(encoding="utf-8"))
    return {
        "workspace_version": workspace,
        "p22_rc_passes": rc_ok,
        "v1_1_tags": tags,
        "p25_verdict": p25.get("verdict"),
        "hold_stands": workspace == "1.1.0" and rc_ok and not tags and p25.get("verdict") == "P25-FAIL",
    }


def parser_map_scan() -> dict[str, object]:
    tracked = command(["git", "ls-files", "controls"]).stdout.splitlines()
    offenders: list[str] = []
    fresh = set(FRESH_TABLES)
    for relative in tracked:
        if relative in PRE_UNSEAL_ALLOWED_CONTROLS:
            continue
        path = ROOT / relative
        if not path.is_file() or path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        named: set[int] = set()
        for block in TABLE_MAP_PATTERN.finditer(text):
            named.update(int(key) for key in re.findall(r"(\d+)\s*:", block.group(1)))
        named.update(int(key) for key in TABLE_CALL_PATTERN.findall(text))
        leaked = sorted(named & fresh)
        if leaked:
            offenders.append(f"{relative}: fresh tables {leaked}")
    return {
        "fresh_tables": FRESH_TABLES,
        "offenders": offenders,
        "pass": not offenders,
    }


def partition_seal() -> dict[str, object]:
    diagnostic = set(DIAGNOSTIC_TABLES)
    consumed = set(CONSUMED_P17_TABLES)
    support = set(OPEN_SUPPORT_TABLES)
    fresh = set(FRESH_TABLES)
    new_results = command(
        ["git", "diff", "--name-only", "--diff-filter=AM", OPENING_COMMIT, "HEAD", "--", "results/"]
    )
    new_controls = command(
        ["git", "diff", "--name-only", "--diff-filter=AM", OPENING_COMMIT, "HEAD", "--", "controls/"]
    )
    unexpected_results = sorted(
        path for path in new_results.stdout.splitlines()
        if path and path not in PRE_UNSEAL_ALLOWED_RESULTS
    )
    unexpected_controls = sorted(
        path for path in new_controls.stdout.splitlines()
        if path and path not in PRE_UNSEAL_ALLOWED_CONTROLS
    )
    scan = parser_map_scan()
    return {
        "state": "sealed",
        "selection_basis": "primary-reference table of contents and captions only",
        "numeric_held_out_rows_inspected": False,
        "diagnostic_tables": sorted(diagnostic),
        "consumed_p17_tables": sorted(consumed),
        "open_support_tables": sorted(support),
        "fresh_families": FRESH_FAMILIES,
        "fresh_tables": FRESH_TABLES,
        "partitions_disjoint": fresh.isdisjoint(diagnostic | consumed | support),
        "unexpected_results_since_opening": unexpected_results,
        "unexpected_controls_since_opening": unexpected_controls,
        "parser_map_scan": scan,
        "unseal_authorized": False,
        "pass": new_results.returncode == 0
        and new_controls.returncode == 0
        and fresh.isdisjoint(diagnostic | consumed | support)
        and not unexpected_results
        and not unexpected_controls
        and scan["pass"],
    }


def artifacts() -> dict[str, object]:
    baseline_npz = sha256(BASELINE_NPZ) if BASELINE_NPZ.is_file() else None
    baseline_index = sha256(BASELINE_INDEX) if BASELINE_INDEX.is_file() else None
    candidate_npz = sha256(CANDIDATE_NPZ) if CANDIDATE_NPZ.is_file() else None
    candidate_index = sha256(CANDIDATE_INDEX) if CANDIDATE_INDEX.is_file() else None
    build = json.loads(CANDIDATE_BUILD.read_text(encoding="utf-8")) if CANDIDATE_BUILD.is_file() else None
    build_ok = bool(
        build
        and build.get("pass") is True
        and (build.get("artifact") or {}).get("npz_sha256") == candidate_npz
        and (build.get("artifact") or {}).get("index_sha256") == candidate_index
    )
    version = command([str(ACTINV), "--version"], timeout=60.0)
    return {
        "actinv_binary_sha256": sha256(ACTINV) if ACTINV.is_file() else None,
        "actinv_version": version.stdout.strip() if version.returncode == 0 else None,
        "baseline_npz_sha256": baseline_npz,
        "baseline_index_sha256": baseline_index,
        "baseline_matches_v101": baseline_npz == BASELINE_NPZ_SHA256
        and baseline_index == BASELINE_INDEX_SHA256,
        "candidate_npz_sha256": candidate_npz,
        "candidate_index_sha256": candidate_index,
        "candidate_build_record_sha256": sha256(CANDIDATE_BUILD)
        if CANDIDATE_BUILD.is_file()
        else None,
        "candidate_build_record_pass": build_ok,
        "candidate_counts": (build or {}).get("counts"),
        "pass": bool(
            baseline_npz == BASELINE_NPZ_SHA256
            and baseline_index == BASELINE_INDEX_SHA256
            and candidate_npz
            and candidate_index
            and build_ok
            and version.returncode == 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    source = protocol_and_source()
    irdff = irdff_inputs()
    lineage = p17_lineage()
    verdicts = prior_verdicts()
    boundary = release_boundary()
    partition = partition_seal()
    artifact = artifacts()
    output = {
        "schema": "actinv-p24-seals-1",
        "source": source,
        "irdff": irdff,
        "p17_lineage": lineage,
        "prior_verdicts": verdicts,
        "release_boundary": boundary,
        "partition": partition,
        "artifacts": artifact,
        "pass": bool(
            source["pass"]
            and irdff["pass"]
            and lineage["pass"]
            and verdicts["pass"]
            and boundary["hold_stands"]
            and partition["pass"]
            and artifact["pass"]
        ),
    }
    encoded = json.dumps(output, indent=1, sort_keys=True) + "\n"
    if not arguments.no_write:
        RESULT.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
