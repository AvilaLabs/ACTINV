#!/usr/bin/env python3
"""Independent checker for the P23 G1 feed/removal gate.

Imports no ACTINV production, audit or scoring module. Verifies the frozen
protocol hash, the opening-commit ancestry, the prior verdict record, and the
feed/removal battery evidence produced by controls/p23_feed_removal.py. With
``--self-test`` it mutates a copy of the evidence and proves rejection.
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
PROTOCOL = ROOT / "protocols/ACTINV-P23_PROTOCOL.md"
EVIDENCE = ROOT / "results/g1_p23_feed_removal.json"
OUTPUT = ROOT / "results/g1_p23_check.json"

PROTOCOL_SHA256 = "fa0df3411e7e2d1d8c5777810db03e76563d6dec1f695fb9219dc0ce7ee59dd5"
OPENING_COMMIT = "325f20704ead9bda1dd5eac3523a1d7b574c3537"

EXPECTED_CHECKS = {
    "feed_decaying_matches_analytic",
    "feed_stable_matches_analytic",
    "feed_daughter_matches_analytic",
    "dense_expm_agrees_feed",
    "removal_state_matches",
    "removal_sink_matches",
    "removal_daughter_matches",
    "removal_conserves_atoms",
    "element_removal_dense_agrees",
    "reservoir_exempt_ledgered",
    "exempt_sink_matches_dense",
    "irradiated_feed_conserves_fed_atoms",
    "feed_split_invariant",
    "empty_maps_byte_identical",
    "empty_maps_omit_sink_field",
    "feed_element_key_rejected",
    "feed_unknown_nuclide_rejected",
    "removal_absent_nuclide_rejected",
    "negative_removal_rejected",
    "nan_feed_rejected",
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
    "verdict_cb1.json": "CB1-COMPLETE",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def check_evidence(evidence: dict, failures: list[str]) -> None:
    if evidence.get("schema") != "actinv-p23-g1-feed-removal-1":
        failures.append("evidence schema is not actinv-p23-g1-feed-removal-1")
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        failures.append("evidence has no checks map")
        return
    if set(checks) != EXPECTED_CHECKS:
        missing = sorted(EXPECTED_CHECKS - set(checks))
        extra = sorted(set(checks) - EXPECTED_CHECKS)
        failures.append(f"check set differs; missing={missing} extra={extra}")
        return
    for name, value in checks.items():
        if value is not True:
            failures.append(f"check {name} is {value!r}, expected true")
    # Re-derive the recorded pass flag; a mutation that keeps the flag while
    # dropping a check is rejected here.
    if evidence.get("pass") != (set(checks) == EXPECTED_CHECKS and all(checks.values())):
        failures.append("recorded pass flag inconsistent with the check map")
    tolerance = evidence.get("tolerance")
    if not isinstance(tolerance, (int, float)) or tolerance > 1e-8:
        failures.append(f"comparison tolerance {tolerance!r} looser than 1e-8")
    details = evidence.get("details")
    if not isinstance(details, dict) or "case_feed" not in details or "case_removal" not in details:
        failures.append("evidence lacks the feed and removal detail blocks")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    head = git("rev-parse", "HEAD")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    if ancestor is False:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of {head}")
    protocol_diff = git("diff", f"{OPENING_COMMIT}..HEAD", "--", str(PROTOCOL.relative_to(ROOT)))
    if protocol_diff:
        failures.append("protocol changed after the opening commit")
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict record {name}")
            continue
        verdict = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        if verdict != expected:
            failures.append(f"{name} verdict {verdict!r} != expected {expected!r}")
    if not EVIDENCE.exists():
        failures.append("feed/removal battery evidence is missing")
    else:
        check_evidence(json.loads(EVIDENCE.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p23-g1-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "verdicts_checked": len(EXPECTED_VERDICTS),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    mutations = {
        "check_flip": lambda v: v["checks"].__setitem__("feed_decaying_matches_analytic", False),
        "check_drop": lambda v: v["checks"].pop("removal_sink_matches"),
        "pass_flag_flip": lambda v: (
            v["checks"].__setitem__("feed_stable_matches_analytic", False),
            v.__setitem__("pass", True),
        ),
        "schema_rename": lambda v: v.__setitem__("schema", "actinv-p23-g1-feed-removal-0"),
        "tolerance_loosen": lambda v: v.__setitem__("tolerance", 1e-3),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(evidence)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p23-g1-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_evidence(json.loads(planted.read_text(encoding="utf-8")), failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} evidence mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
