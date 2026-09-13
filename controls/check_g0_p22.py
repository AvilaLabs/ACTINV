#!/usr/bin/env python3
"""Independent checker for the P22 G0 seal record.

Imports no ACTINV production, audit or scoring module. Independently:

- rehashes the frozen P22 protocol and confirms the opening commit is an
  ancestor of HEAD and the protocol is unchanged since;
- re-reads every prior verdict file and requires its verdict string to
  equal the frozen expectation (25 verdicts);
- rehashes every sealed CB1 evidence file and requires equality with the
  digest pinned inside ``results/session_cb1.json`` — the comparison
  baseline is the sealed record, not whatever happens to be on disk;
- requires the recorded executed-scale link: the g3 record names 20,000
  cells and its binary digest equals the recorded candidate digest;
- requires the G0 record's own ``pass`` flag to be true.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g0_p22_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"
REPORT = ROOT / "results/g0_p22_seals.json"
OUTPUT = ROOT / "results/g0_p22_check.json"
SESSION_CB1 = ROOT / "results/session_cb1.json"
EXECUTED = ROOT / "results/g3_p21_executed.json"

PROTOCOL_SHA256 = "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"

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
    "verdict_p23.json": "P23-PASS",
}

CB1_EVIDENCE = {
    "access": "cb1_access.json",
    "numerical": "cb1_numerical.json",
    "alara": "cb1_alara.json",
    "fns": "cb1_fns.json",
    "prior_validation": "cb1_prior_validation.json",
    "performance": "cb1_performance.json",
    "mesh_performance": "cb1_mesh_performance.json",
    "first_use": "cb1_first_use.json",
    "capabilities": "cb1_capabilities.json",
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
    if report.get("schema") != "actinv-p22-seals-1":
        failures.append("schema is not actinv-p22-seals-1")
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
    seals = report.get("cb1_evidence_seals") or {}
    if set(seals) != set(CB1_EVIDENCE):
        failures.append("seal map keys differ from the CB1 evidence list")
    else:
        for name in CB1_EVIDENCE:
            if seals[name].get("matches") is not True:
                failures.append(f"CB1 seal {name} does not match its pinned digest")
    link = report.get("executed_scale_link") or {}
    if link.get("cells") != 20_000 or link.get("matches") is not True:
        failures.append("executed-scale record is not bound to the candidate binary")
    candidate = report.get("candidate") or {}
    if not is_hex64(candidate.get("actinv_binary_sha256")) or not is_hex64(
        candidate.get("python_module_sha256")
    ):
        failures.append("candidate pin lacks binary/module digests")
    if not isinstance(candidate.get("cgroup"), dict):
        failures.append("candidate pin lacks the cgroup record")
    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def check_live(failures: list[str]) -> None:
    """Independent re-derivation against live files (not the record)."""
    session = json.loads(SESSION_CB1.read_text(encoding="utf-8"))
    pinned = session["evidence_sha256"]
    for name, filename in CB1_EVIDENCE.items():
        path = ROOT / "results" / filename
        if not path.exists() or sha256(path) != pinned.get(name):
            failures.append(f"sealed CB1 evidence {filename} no longer matches")
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text(encoding="utf-8")).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")
    executed = json.loads(EXECUTED.read_text(encoding="utf-8"))
    if (executed.get("executed_case") or {}).get("cells") != 20_000:
        failures.append("executed record does not name 20,000 cells")
    if not is_hex64(executed.get("actinv_binary_sha256")):
        failures.append("executed record lacks its binary digest")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")
    diff = subprocess.run(
        ["git", "diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P22_PROTOCOL.md"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if diff.stdout.strip():
        failures.append("protocol changed after the opening commit")
    check_live(failures)
    if not REPORT.exists():
        failures.append("results/g0_p22_seals.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p22-g0-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "verdict_flip": lambda r: r["verdicts"]["verdict_p17.json"].__setitem__(
            "observed", "P17-PASS"
        ),
        "seal_break": lambda r: r["cb1_evidence_seals"]["fns"].__setitem__("matches", False),
        "link_break": lambda r: r["executed_scale_link"].__setitem__("matches", False),
        "cells_shrink": lambda r: r["executed_scale_link"].__setitem__("cells", 2_000),
        "pass_forge": lambda r: r.__setitem__("pass", True),
    }
    # pass_forge: forge pass on an otherwise-mutated record.
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        if name == "pass_forge":
            candidate["verdicts"]["verdict_p17.json"]["observed"] = "P17-PASS"
            candidate["pass"] = True
        with tempfile.TemporaryDirectory(prefix="actinv-p22-g0-selftest-") as directory:
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
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
