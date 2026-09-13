#!/usr/bin/env python3
"""Independent checker for the P25 G0 seal record.

Imports no ACTINV production, audit or scoring module. Independently:

- rehashes the frozen P25 protocol and confirms the opening commit is an
  ancestor of HEAD and the protocol is unchanged since;
- re-reads every prior verdict file and requires its verdict string to
  equal the frozen expectation (26 verdicts — P17-FAIL, P18-FAIL and
  P18b-FAIL asserted verbatim);
- rehashes the P18b session's pinned evidence digests against the result
  files on disk;
- independently rehashes the sealed four-corpus TENDL-2025 tree against
  the source manifest when the corpus is mounted (the sealed neutron
  population is the ``n-working`` tree — the official n-Pb208.tendl
  carries literal NaN tokens and the sealed corpus used the recorded
  repair; n-Pb208 appears in zero held-out rows);
- requires the release boundary: workspace version 1.1.0, a passing P22
  release-candidate record, and no ``v1.1*`` tag;
- requires the G0 record's own ``pass`` flag to be true.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g0_p25_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P25_PROTOCOL.md"
REPORT = ROOT / "results/g0_p25_seals.json"
OUTPUT = ROOT / "results/g0_p25_check.json"
CORPUS_MANIFEST = ROOT / "results/p18b_source_manifest.json.gz"
SESSION_P18B = ROOT / "results/session_p18b.json"
P22_RC = ROOT / "results/g4_p22_release_candidate.json"
CORPUS_ROOT = Path("/home/connoravila/nuclear-data/tendl-2025/files")

PROTOCOL_SHA256 = "ccd9bd98e513609532ed3a9871ce455be81828651e8ecf623d2a253a9f9fb552"
AMENDMENT_A_SHA256 = "214992c3709803b4991926251622731cf47071903fd255d51c03ceec17b483e8"
AMENDMENT_A = ROOT / "protocols/ACTINV-P25_AMENDMENT_A.md"
OPENING_COMMIT = "77dfaef8c1cb4067c8cc8e1b7c4a686123161ea7"

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
}

P18B_EVIDENCE = {
    "g0_check": "results/g0_p18b_check.json",
    "g1_check": "results/g1_p18b_check.json",
    "g2_check": "results/g2_p18b_check.json",
    "g3_check": "results/g3_p18b_check.json",
    "g4_diagnostics": "results/g4_p18b_diagnostics.json",
    "g5_heldout": "results/g5_p18b_heldout.json",
    "g5_run1_coverage_limited": "results/g5_p18b_heldout_run1_coverage_limited.json",
}

CORPUS_CODE = {
    "neutron": "n-working",
    "proton": "p",
    "deuteron": "d",
    "alpha": "a",
}


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


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p25-seals-1":
        failures.append("schema is not actinv-p25-seals-1")
    if report.get("amendment_a_sha256") != AMENDMENT_A_SHA256:
        failures.append("record does not pin the frozen Amendment A digest")
    verdicts = report.get("verdicts") or {}
    if set(verdicts) != set(EXPECTED_VERDICTS):
        failures.append("verdict map keys differ from the frozen list")
    else:
        for name, expected in EXPECTED_VERDICTS.items():
            entry = verdicts.get(name) or {}
            if entry.get("expected") != expected:
                failures.append(f"{name} records the wrong expected verdict")
            if entry.get("observed") != expected:
                failures.append(f"{name} observed verdict != {expected}")
    seals = report.get("p18b_evidence_seals") or {}
    if set(seals) != set(P18B_EVIDENCE):
        failures.append("seal map keys differ from the P18b evidence list")
    else:
        for name in P18B_EVIDENCE:
            if seals[name].get("matches") is not True:
                failures.append(f"P18b seal {name} does not match its pinned digest")
    corpus = report.get("corpus_seal") or {}
    counts = corpus.get("file_counts") or {}
    if counts != {p: 2850 for p in CORPUS_CODE}:
        failures.append("corpus file counts differ from the sealed 2850-per-projectile")
    if corpus.get("missing_count") != 0 or corpus.get("mismatched_count") != 0:
        failures.append("corpus seal reports missing or mismatched files")
    if corpus.get("pass") is not True:
        failures.append("corpus seal does not carry pass")
    boundary = report.get("release_boundary") or {}
    if boundary.get("workspace_version") != "1.1.0":
        failures.append("release boundary: workspace version is not 1.1.0")
    if boundary.get("p22_rc_passes") is not True:
        failures.append("release boundary: P22 RC record does not pass")
    if boundary.get("v1_1_tags") not in ([], None):
        failures.append("release boundary: a v1.1* tag exists")
    if boundary.get("hold_stands") is not True:
        failures.append("release boundary does not hold")
    session = report.get("p18b_session") or {}
    if session.get("verdict") != "P18b-FAIL":
        failures.append("P18b session verdict is not P18b-FAIL")
    candidate = report.get("candidate") or {}
    if not is_hex64(candidate.get("actinv_binary_sha256")) or not is_hex64(
        candidate.get("python_module_sha256")
    ):
        failures.append("candidate pin lacks binary/module digests")
    if not isinstance(candidate.get("cgroup"), dict):
        failures.append("candidate pin lacks the cgroup record")
    if report.get("head_descends_from_opening") is not True:
        failures.append("record does not descend from the opening commit")
    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def check_live(failures: list[str]) -> None:
    """Independent re-derivation against live files (not the record)."""
    session = json.loads(SESSION_P18B.read_text(encoding="utf-8"))
    if session.get("verdict") != "P18b-FAIL":
        failures.append("live P18b session verdict is not P18b-FAIL")
    pinned = session["evidence_sha256"]
    for name, rel in P18B_EVIDENCE.items():
        path = ROOT / rel
        if not path.exists() or sha256(path) != pinned.get(name):
            failures.append(f"sealed P18b evidence {rel} no longer matches")
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
        ["git", "tag", "--list", "v1.1*"], cwd=ROOT, text=True,
        capture_output=True, check=False,
    ).stdout.split()
    if tags:
        failures.append(f"v1.1* tags exist: {tags}")
    # Full independent corpus rehash — only where the corpus is mounted
    # (the frozen tree is local evidence, not shipped to CI).
    manifest = json.loads(gzip.open(CORPUS_MANIFEST, "rt").read())
    corpus = manifest["corpora"]
    if all((CORPUS_ROOT / d).is_dir() for d in CORPUS_CODE.values()):
        for projectile, entry in corpus.items():
            for rec in entry["files"]:
                path = CORPUS_ROOT / CORPUS_CODE[projectile] / rec["name"]
                if not path.exists():
                    failures.append(f"corpus file missing: {projectile}/{rec['name']}")
                    continue
                if sha256(path) != rec["source_sha256"]:
                    failures.append(
                        f"corpus digest mismatch: {projectile}/{rec['name']}"
                    )
    else:
        for projectile, entry in corpus.items():
            if len(entry["files"]) != 2850:
                failures.append(f"{projectile} manifest does not seal 2850 files")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    observed_amendment = sha256(AMENDMENT_A) if AMENDMENT_A.exists() else None
    if observed_amendment != AMENDMENT_A_SHA256:
        failures.append(
            f"Amendment A sha256 {observed_amendment} != frozen {AMENDMENT_A_SHA256}"
        )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")
    diff = subprocess.run(
        ["git", "diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P25_PROTOCOL.md"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if diff.stdout.strip():
        failures.append("protocol changed after the opening commit")
    check_live(failures)
    if not REPORT.exists():
        failures.append("results/g0_p25_seals.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p25-g0-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "verdict_flip": lambda r: r["verdicts"]["verdict_p18b.json"].__setitem__(
            "observed", "P18b-PASS"
        ),
        "seal_break": lambda r: r["p18b_evidence_seals"]["g5_heldout"].__setitem__(
            "matches", False
        ),
        "corpus_loss": lambda r: r["corpus_seal"].__setitem__("mismatched_count", 3),
        "boundary_tag": lambda r: r["release_boundary"].__setitem__(
            "v1_1_tags", ["v1.1.0"]
        ),
        "pass_forge": lambda r: r.__setitem__("pass", True),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        if name == "pass_forge":
            candidate["verdicts"]["verdict_p18b.json"]["observed"] = "P18b-PASS"
        with tempfile.TemporaryDirectory(prefix="actinv-p25-g0-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_report(json.loads(planted.read_text(encoding="utf-8")), failures)
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
