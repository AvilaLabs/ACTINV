#!/usr/bin/env python3
"""Independent checker for the P20 G2 defect-exclusion gate.

Imports no ACTINV production or audit module. Verifies the frozen protocol,
the G1 check record, and the committed defect report: the absent-section
byte-identity leg must reproduce the pinned G0 hash, each defect case must
record its named exclusion with a defect of the right sign, and the clean
full-corpus reference must show zero exclusions with bands identical to the
G0 reference. With ``--self-test`` it mutates a copy and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P20_PROTOCOL.md"
REPORT = ROOT / "results/g2_p20_defects.json"
G1_CHECK = ROOT / "results/g1_p20_check.json"
OUTPUT = ROOT / "results/g2_p20_check.json"

PROTOCOL_SHA256 = "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335"
OPENING_COMMIT = "2b7f87309d6bc3a87c33d76d9838d7c4aa33d59a"
EXPECTED_REASONS = {"asymmetric_block", "non_positive_semidefinite"}


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


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p20-g2-defects-1":
        failures.append("report schema is not actinv-p20-g2-defects-1")
    if report.get("protocol_sha256") != PROTOCOL_SHA256:
        failures.append("report does not bind the frozen protocol hash")
    if not is_hex64(report.get("binary_sha256")):
        failures.append("report lacks the executed binary hash")
    identity = report.get("absent_section_byte_identity") or {}
    if identity.get("byte_identical") is not True:
        failures.append("absent-section output is not byte-identical to the G0 baseline")
    if not is_hex64(identity.get("expected_sha256")):
        failures.append("byte-identity leg lacks the pinned expected hash")
    cases = report.get("defect_cases") or {}
    if set(cases) != EXPECTED_REASONS:
        failures.append(f"defect cases differ from {sorted(EXPECTED_REASONS)}")
    for reason, case in cases.items():
        block = case.get("excluded_block") or {}
        if block.get("reason") != reason:
            failures.append(f"{reason}: exclusion reason {block.get('reason')!r}")
        defect = block.get("measured_defect")
        if reason == "asymmetric_block":
            if not (isinstance(defect, (int, float)) and defect > 0.0):
                failures.append("asymmetric_block: measured defect not positive")
        elif not (isinstance(defect, (int, float)) and defect < 0.0):
            failures.append("non_positive_semidefinite: measured defect not negative")
        if block.get("mt") != block.get("mt1") or block.get("mt") is None:
            failures.append(f"{reason}: exclusion is not a self block")
        for field in ("spec_sha256", "sidecar_sha256", "result_sha256"):
            if not is_hex64(case.get(field)):
                failures.append(f"{reason}: missing {field}")
        if len(case.get("excluded_library_rows") or []) < 2:
            failures.append(f"{reason}: fewer than two parameters flagged excluded")
    clean = report.get("clean_corpus_reference") or {}
    if clean.get("status") != "compared":
        failures.append("clean-corpus reference was not compared")
    else:
        if clean.get("bands_identical") is not True:
            failures.append("clean-corpus bands differ from the G0 reference")
        if clean.get("no_exclusions") is not True:
            failures.append("clean corpus produced exclusions")
        for field in ("g0_result_sha256", "g2_result_sha256"):
            if not is_hex64(clean.get(field)):
                failures.append(f"clean reference lacks {field}")
    checks = report.get("checks") or {}
    if report.get("pass") is not True or not all(checks.values()):
        failures.append("report does not record a pass")


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
    protocol_diff = git(
        "diff", f"{OPENING_COMMIT}..HEAD", "--", str(PROTOCOL.relative_to(ROOT))
    )
    if protocol_diff:
        failures.append("protocol changed after the opening commit")
    if not G1_CHECK.exists():
        failures.append("missing G1 check record")
    elif json.loads(G1_CHECK.read_text(encoding="utf-8")).get("pass") is not True:
        failures.append("G1 check record does not pass")
    if not REPORT.exists():
        failures.append("defect report is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p20-g2-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "identity_flip": lambda v: v["absent_section_byte_identity"].__setitem__(
            "byte_identical", False
        ),
        "reason_swap": lambda v: v["defect_cases"]["asymmetric_block"][
            "excluded_block"
        ].__setitem__("reason", "non_positive_semidefinite"),
        "defect_sign_flip": lambda v: v["defect_cases"]["non_positive_semidefinite"][
            "excluded_block"
        ].__setitem__("measured_defect", 1.0),
        "exclusion_in_clean": lambda v: v["clean_corpus_reference"].__setitem__(
            "no_exclusions", False
        ),
        "pass_flag_flip": lambda v: v.__setitem__("pass", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p20-g2-selftest-") as directory:
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
        OUTPUT.write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
