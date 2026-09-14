#!/usr/bin/env python3
"""Independent checker for the P24 G0 seal record.

Imports no ACTINV production, audit, parsing or scoring module and does
not import the seal control. Independently:

- rehashes the frozen P24 protocol, requires the ledger line, and
  requires the opening commit an ancestor of HEAD with the protocol
  unchanged since;
- rehashes every IRDFF-II authority input against the P17-pinned
  digests and re-verifies the P17 session's evidence pins verbatim;
- re-reads every prior verdict file including ``P17-FAIL``, ``P18-FAIL``,
  ``P18b-FAIL`` and ``P25-FAIL`` verbatim;
- re-derives the IRDFF-II incident-neutron target set from the
  pointwise archive's MF=1 headers and requires the candidate build
  record's target set to equal it exactly, with every built source
  matching the sealed P18b manifest;
- rehashes the v1.0.1 baseline and P24 candidate artifacts against the
  record;
- re-verifies the partition invariants: fresh tables disjoint from
  diagnostic/consumed/support, no committed parser map naming a fresh
  table, and no result/control file outside the pre-unseal allowlist
  added since the opening commit;
- requires the release boundary (workspace 1.1.0, passing P22 record,
  no ``v1.1*`` tag) and the record's own ``pass``.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g0_p24_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols" / "ACTINV-P24_PROTOCOL.md"
P17_PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
P17_AMENDMENT = ROOT / "protocols" / "ACTINV-P17_AMENDMENT_1.md"
P17_SESSION = ROOT / "results" / "session_p17.json"
P22_RC = ROOT / "results" / "g4_p22_release_candidate.json"
REPORT = ROOT / "results" / "g0_p24_seals.json"
BUILD = ROOT / "results" / "g0_p24_candidate_build.json"
OUTPUT = ROOT / "results" / "g0_p24_check.json"
CORPUS_MANIFEST = ROOT / "results" / "p18b_source_manifest.json.gz"
DATA_ROOT = Path(
    os.environ.get("ACTINV_P17_IRDFF", Path.home() / "nuclear-data" / "p17-irdff")
)
CORPUS_ROOT = Path(
    os.environ.get("ACTINV_TENDL", Path.home() / "nuclear-data" / "tendl-2025" / "files")
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
BASELINE_NPZ_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
BASELINE_INDEX_SHA256 = "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"
POINTWISE_SHA256 = "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db"

IRDFF_INPUTS = {
    "primary_reference": (
        "IRDFF-II_primary_1909.03336.pdf",
        "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    ),
    "benchmark_field_list": (
        "NeutronBenchmarkFields-IRDFF-II.pdf",
        "93926f4a9937ef1314ebbaa29a11a638ad9d1b3abad08596b0db57ee6bc9c304",
    ),
    "pointwise": (
        "IRDFF-II_ENDF.zip",
        "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
    ),
    "group_725": (
        "IRDFF-II_g725.zip",
        "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
    ),
    "spectra": (
        "IRDFF-II_sp_g.zip",
        "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
    ),
    "decay": (
        "IRDFF-II_dd_ENDF.zip",
        "397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7",
    ),
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

FRESH_TABLES = [26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 41, 42, 43, 44, 45, 46]
PRE_UNSEAL_ALLOWED_RESULTS = {
    "results/g0_p24_candidate_build.json",
    "results/g0_p24_seals.json",
    "results/g0_p24_check.json",
    "results/g1_p24_definitions.json",
    "results/g1_p24_check.json",
    "results/g2_p24_scorer_audit.json",
    "results/g2_p24_check.json",
    "results/g3_p24_diagnostic.json",
    "results/g3_p24_check.json",
}
PRE_UNSEAL_ALLOWED_CONTROLS = {
    "controls/g0_p24_candidate_build.py",
    "controls/g0_p24_seal.py",
    "controls/check_g0_p24.py",
    "controls/p24_definitions.py",
    "controls/g1_p24_definitions.py",
    "controls/check_g1_p24.py",
    "controls/p24_scorer.py",
    "controls/g2_p24_scorer_audit.py",
    "controls/check_g2_p24.py",
    "controls/g3_p24_diagnostic.py",
    "controls/check_g3_p24.py",
}
TABLE_MAP_PATTERN = re.compile(
    r"(?:TABLE_PAGES|EXPECTED_ROWS|TABLE_SPECTRUM_MAT|FIELD_SPECTRUM_MAT)"
    r"\s*(?::[^=]*)?=\s*\{([^}]*)\}",
    re.S,
)
TABLE_CALL_PATTERN = re.compile(r"table_text\((\d+)\)")

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


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def endf_float(field: str) -> float:
    # ENDF-6 exponents may be space-padded inside the field ("2.50550+ 4")
    return float(re.sub(r"([+-])\s*(\d+)$", r"e\1\2", field.strip()))


def irdff_isotopic_targets() -> set[int]:
    with zipfile.ZipFile(DATA_ROOT / "IRDFF-II_ENDF.zip") as archive:
        text = archive.read("IRDFF-II.endf").decode("ascii", "replace")
    header = re.compile(r"^(.{11})(.{11}).{44}.{0,4}?(\d{4}) 1451    1", re.M)
    targets: set[int] = set()
    for match in header.finditer(text):
        try:
            za = endf_float(match.group(1))
        except ValueError:
            continue
        if int(round(za)) % 1000 != 0:
            targets.add(int(round(za)))
    return targets


def check_report(
    report: dict, failures: list[str], build_record: dict | None
) -> None:
    if report.get("schema") != "actinv-p24-seals-1":
        failures.append("schema is not actinv-p24-seals-1")
    source = report.get("source") or {}
    protocol = source.get("protocol") or {}
    if protocol.get("actual_sha256") != PROTOCOL_SHA256:
        failures.append("record does not pin the frozen P24 protocol digest")
    if protocol.get("ledger_entry") is not True:
        failures.append("record lacks the protocol_hash.txt ledger line")
    if source.get("opening_commit") != OPENING_COMMIT:
        failures.append("record names a different opening commit")
    if source.get("opening_is_ancestor") is not True:
        failures.append("record does not descend from the opening commit")
    if source.get("production_paths_changed_since_opening") != []:
        failures.append("production paths changed since the opening commit")
    verdicts = (report.get("prior_verdicts") or {}).get("verdicts") or {}
    if set(verdicts) != set(EXPECTED_VERDICTS):
        failures.append("verdict map keys differ from the frozen list")
    else:
        for name, expected in EXPECTED_VERDICTS.items():
            entry = verdicts.get(name) or {}
            if entry.get("expected") != expected or entry.get("observed") != expected:
                failures.append(f"{name} verdict mismatch in record")
    lineage = report.get("p17_lineage") or {}
    if lineage.get("p17_protocol_sha256") != P17_PROTOCOL_SHA256:
        failures.append("P17 protocol digest mismatch")
    if lineage.get("p17_amendment_sha256") != P17_AMENDMENT_SHA256:
        failures.append("P17 Amendment 1 digest mismatch")
    seals = lineage.get("evidence_seals") or {}
    if set(seals) != set(P17_EVIDENCE):
        failures.append("P17 evidence seal map keys differ")
    elif not all(s.get("matches") is True for s in seals.values()):
        failures.append("a P17 evidence seal does not match its pinned digest")
    boundary = report.get("release_boundary") or {}
    if boundary.get("workspace_version") != "1.1.0":
        failures.append("release boundary workspace version is not 1.1.0")
    if boundary.get("p22_rc_passes") is not True:
        failures.append("release boundary lacks a passing P22 record")
    if boundary.get("v1_1_tags") not in ([], None):
        failures.append("release boundary records a v1.1* tag")
    if boundary.get("hold_stands") is not True:
        failures.append("release boundary does not hold")
    if boundary.get("p25_verdict") != "P25-FAIL":
        failures.append("P25 verdict is not preserved verbatim")
    partition = report.get("partition") or {}
    if partition.get("state") != "sealed" or partition.get("unseal_authorized") is not False:
        failures.append("partition is not recorded sealed")
    if partition.get("numeric_held_out_rows_inspected") is not False:
        failures.append("record admits pre-unseal numeric inspection")
    if partition.get("partitions_disjoint") is not True:
        failures.append("partition disjointness is not recorded")
    if partition.get("unexpected_results_since_opening") != []:
        failures.append("record admits unexpected results since opening")
    if partition.get("unexpected_controls_since_opening") != []:
        failures.append("record admits unexpected controls since opening")
    families = partition.get("fresh_families") or {}
    if sorted(t for f in families.values() for t in f["tables"]) != FRESH_TABLES:
        failures.append("fresh family table map differs from the frozen partition")
    scan = partition.get("parser_map_scan") or {}
    if scan.get("pass") is not True:
        failures.append("parser-map scan does not pass in record")
    artifact = report.get("artifacts") or {}
    if artifact.get("baseline_matches_v101") is not True:
        failures.append("v1.0.1 baseline artifact digests do not match")
    if artifact.get("baseline_npz_sha256") != BASELINE_NPZ_SHA256:
        failures.append("record baseline NPZ digest differs from the pinned v1.0.1")
    if artifact.get("baseline_index_sha256") != BASELINE_INDEX_SHA256:
        failures.append("record baseline index digest differs from the pinned v1.0.1")
    if artifact.get("candidate_build_record_pass") is not True:
        failures.append("candidate build record does not carry pass")
    build_artifact = (build_record or {}).get("artifact") or {}
    if artifact.get("candidate_npz_sha256") != build_artifact.get("npz_sha256"):
        failures.append("record candidate NPZ digest disagrees with the build record")
    if artifact.get("candidate_index_sha256") != build_artifact.get("index_sha256"):
        failures.append("record candidate index digest disagrees with the build record")
    if artifact.get("candidate_build_record_sha256") != (
        sha256(BUILD) if BUILD.is_file() else None
    ):
        failures.append("record build-record digest differs from the committed file")
    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def check_live(failures: list[str]) -> None:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("live protocol digest differs from frozen")
    if sha256(P17_PROTOCOL) != P17_PROTOCOL_SHA256:
        failures.append("live P17 protocol digest differs from frozen")
    if sha256(P17_AMENDMENT) != P17_AMENDMENT_SHA256:
        failures.append("live P17 amendment digest differs from frozen")
    ledger = (ROOT / "protocols" / "protocol_hash.txt").read_text(encoding="utf-8")
    if f"{PROTOCOL_SHA256}  protocols/ACTINV-P24_PROTOCOL.md" not in ledger.splitlines():
        failures.append("protocol_hash.txt lacks the P24 ledger line")
    if f"{P17_AMENDMENT_SHA256}  protocols/ACTINV-P17_AMENDMENT_1.md" not in ledger.splitlines():
        failures.append("protocol_hash.txt lacks the P17 Amendment 1 ledger line")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"], cwd=ROOT
    ).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")
    diff = subprocess.run(
        ["git", "diff", "--name-only", OPENING_COMMIT, "HEAD", "--", "protocols/"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if diff.stdout.strip():
        failures.append("a protocol file changed after the opening commit")
    irdff_mounted = DATA_ROOT.is_dir()
    for name, (filename, expected) in IRDFF_INPUTS.items():
        path = DATA_ROOT / filename
        if not path.is_file():
            if irdff_mounted:
                failures.append(f"IRDFF input {name} missing")
            continue
        if sha256(path) != expected:
            failures.append(f"IRDFF input {name} changed")
    session = json.loads(P17_SESSION.read_text(encoding="utf-8"))
    if session.get("verdict") != "P17-FAIL":
        failures.append("live P17 session verdict is not P17-FAIL")
    pinned = session.get("evidence_sha256") or {}
    for name, relative in P17_EVIDENCE.items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != pinned.get(name):
            failures.append(f"sealed P17 evidence {relative} no longer matches")
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text(encoding="utf-8")).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")
    rc = json.loads(P22_RC.read_text(encoding="utf-8"))
    if not (rc.get("pass") and (rc.get("decision") or {}).get("release_ready")):
        failures.append("P22 release-candidate record does not pass")
    tags = subprocess.run(
        ["git", "tag", "--list", "v1.1*"], cwd=ROOT, text=True, capture_output=True
    ).stdout.split()
    if tags:
        failures.append(f"v1.1* tags exist: {tags}")
    if BASELINE_NPZ.is_file() and sha256(BASELINE_NPZ) != BASELINE_NPZ_SHA256:
        failures.append("v1.0.1 baseline NPZ changed")
    if BASELINE_INDEX.is_file() and sha256(BASELINE_INDEX) != BASELINE_INDEX_SHA256:
        failures.append("v1.0.1 baseline index changed")

    # Independent candidate-build verification: the record must cover the
    # full isotopic set (re-derived from the archive when mounted) and every
    # built source must match the sealed manifest. On-disk artifact digests
    # are re-verified only where the build tree is present.
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    per_file = build.get("per_file") or []
    built_targets = {
        int(rec["target_za"]) for rec in per_file if rec.get("outcome") == "built"
    }
    failed = [rec for rec in per_file if rec.get("outcome") != "built"]
    if (DATA_ROOT / "IRDFF-II_ENDF.zip").is_file():
        if {int(rec["target_za"]) for rec in per_file} != irdff_isotopic_targets():
            failures.append("build record does not cover the full isotopic target set")
    if failed and any(rec.get("outcome") != "construction_failed" for rec in failed):
        failures.append("a failed construction lacks the construction_failed outcome")
    manifest = json.loads(gzip.open(CORPUS_MANIFEST, "rt").read())
    entries = {rec["name"]: rec for rec in manifest["corpora"]["neutron"]["files"]}
    corpus_mounted = (CORPUS_ROOT / "n-working").is_dir()
    for rec in per_file:
        expected = entries.get(rec.get("name"))
        if expected is None:
            failures.append(f"{rec.get('name')} is not in the sealed neutron corpus")
            continue
        if rec.get("outcome") == "built":
            if rec.get("source_sha256") != expected["source_sha256"]:
                failures.append(f"{rec['name']} digest disagrees with sealed manifest")
                continue
            if corpus_mounted:
                source = CORPUS_ROOT / "n-working" / rec["name"]
                if not source.is_file() or sha256(source) != expected["source_sha256"]:
                    failures.append(f"{rec['name']} no longer matches the sealed manifest")
    if CANDIDATE_NPZ.is_file() and build.get("artifact", {}).get("npz_sha256") != sha256(CANDIDATE_NPZ):
        failures.append("candidate NPZ on disk differs from the build record")
    if CANDIDATE_INDEX.is_file() and build.get("artifact", {}).get("index_sha256") != sha256(CANDIDATE_INDEX):
        failures.append("candidate index on disk differs from the build record")
    if set(build.get("artifact", {}).get("index_targets") or []) != built_targets:
        failures.append("candidate index targets differ from the built set")

    # Partition invariants, recomputed.
    fresh = set(FRESH_TABLES)
    consumed = {21, 22, 23, 24, 25, 36}
    open_tables = {18, 19, 20, 5, 16, 37, 38, 39, 40, 47}
    if fresh & (consumed | open_tables):
        failures.append("fresh partition intersects open/consumed tables")
    new_paths = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, text=True, capture_output=True,
    ).stdout.splitlines()
    for path in new_paths:
        if path.startswith("results/") and path not in PRE_UNSEAL_ALLOWED_RESULTS:
            failures.append(f"unexpected new result file {path}")
        if path.startswith("controls/") and path not in PRE_UNSEAL_ALLOWED_CONTROLS:
            failures.append(f"unexpected new control file {path}")
        if path.startswith(("crates/", "python/", "data/", "examples/")) or path in {
            "Cargo.toml",
            "Cargo.lock",
            "pyproject.toml",
        }:
            failures.append(f"production path changed since opening: {path}")
    tracked = subprocess.run(
        ["git", "ls-files", "controls"], cwd=ROOT, text=True, capture_output=True
    ).stdout.splitlines()
    for relative in tracked:
        if relative in PRE_UNSEAL_ALLOWED_CONTROLS:
            continue
        path = ROOT / relative
        if not path.is_file() or path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        named: set[int] = set()
        for block in TABLE_MAP_PATTERN.finditer(text):
            named.update(int(k) for k in re.findall(r"(\d+)\s*:", block.group(1)))
        named.update(int(k) for k in TABLE_CALL_PATTERN.findall(text))
        if named & fresh:
            failures.append(f"{relative} names fresh tables {sorted(named & fresh)}")


def run_checks() -> dict:
    failures: list[str] = []
    check_live(failures)
    build_record = json.loads(BUILD.read_text(encoding="utf-8")) if BUILD.is_file() else None
    if build_record is None:
        failures.append("results/g0_p24_candidate_build.json is missing")
    if not REPORT.exists():
        failures.append("results/g0_p24_seals.json is missing")
    else:
        check_report(
            json.loads(REPORT.read_text(encoding="utf-8")),
            failures,
            build_record,
        )
    return {
        "schema": "actinv-p24-g0-check-1",
        "protocol_sha256": sha256(PROTOCOL),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "verdict_flip": lambda r: r["prior_verdicts"]["verdicts"]["verdict_p25.json"].__setitem__(
            "observed", "P25-PASS"
        ),
        "seal_break": lambda r: r["p17_lineage"]["evidence_seals"]["g5_heldout"].__setitem__(
            "matches", False
        ),
        "boundary_tag": lambda r: r["release_boundary"].__setitem__(
            "v1_1_tags", ["v1.1.0"]
        ),
        "partition_leak": lambda r: r["partition"].__setitem__(
            "unexpected_results_since_opening", ["results/g4_p24_heldout.json"]
        ),
        "artifact_swap": lambda r: r["artifacts"].__setitem__(
            "candidate_npz_sha256", "0" * 64
        ),
        "pass_forge": lambda r: r.__setitem__("pass", True),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        if name == "pass_forge":
            candidate["prior_verdicts"]["verdicts"]["verdict_p17.json"]["observed"] = "P17-PASS"
        failures: list[str] = []
        build_record = json.loads(BUILD.read_text(encoding="utf-8"))
        check_report(candidate, failures, build_record)
        rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} report mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    record = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=1, sort_keys=True))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
