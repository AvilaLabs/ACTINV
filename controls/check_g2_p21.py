#!/usr/bin/env python3
"""Independent checker for the P21 G2 resume/checkpoint report.

Imports no ACTINV production or audit module. Re-derives every claim in
``results/g2_p21_resume.json``:

- resume identity is re-verified from the recorded per-line SHA-256 lists:
  every line digest except the footer line must be equal, and the stored
  normalized footers must be equal and free of timing fields;
- the reuse count preserved across the resume is re-checked against the
  recorded alternating-spectrum case (cells - 2);
- the complete-run no-op is re-checked from the stored summary and the
  bytes-unchanged flag;
- each rejection (fingerprint mismatch, corrupt mid-file record,
  out-of-order ordinal) must carry its named error;
- the fresh run must have produced a complete (cells + 2 records) output.

With ``--self-test`` it mutates copies of the report and proves rejection.
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
PROTOCOL = ROOT / "protocols/ACTINV-P21_PROTOCOL.md"
REPORT = ROOT / "results/g2_p21_resume.json"
OUTPUT = ROOT / "results/g2_p21_check.json"

PROTOCOL_SHA256 = "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

TIMING_FIELDS = {"wall_time_s", "cells_per_s"}


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def check_report(report: dict, failures: list[str]) -> None:
    cells = report.get("cells")
    prefix_cells = report.get("prefix_cells")
    if not isinstance(cells, int) or cells < 4 or not isinstance(prefix_cells, int) or not 0 < prefix_cells < cells:
        failures.append("cells/prefix_cells are not a sane resume case")
        return

    identity = report.get("resume_identity") or {}
    ref = identity.get("reference_line_sha256")
    res = identity.get("resumed_line_sha256")
    for name, values in (("reference_line_sha256", ref), ("resumed_line_sha256", res)):
        if (
            not isinstance(values, list)
            or len(values) != cells + 2
            or not all(is_hex64(value) for value in values)
        ):
            failures.append(f"{name} is not a (cells+2)-line digest list")
            return
    # Header + every cell record must be byte-identical; the footer line is
    # excluded because its timing fields legitimately differ.
    if ref[:-1] != res[:-1]:
        failures.append("resumed output line digests differ from the reference")
    if identity.get("header_byte_identical") is not True:
        failures.append("header_byte_identical flag is not true")
    if identity.get("cell_records_byte_identical") is not True:
        failures.append("cell_records_byte_identical flag is not true")
    for key in ("footer_reference_normalized", "footer_resumed_normalized"):
        footer = identity.get(key)
        if not isinstance(footer, dict):
            failures.append(f"{key} missing")
            continue
        if TIMING_FIELDS & set(footer):
            failures.append(f"{key} still carries a timing field")
        if footer.get("cell_count") != cells:
            failures.append(f"{key} reports the wrong cell count")
    fref, fres = identity.get("footer_reference_normalized"), identity.get(
        "footer_resumed_normalized"
    )
    if isinstance(fref, dict) and isinstance(fres, dict):
        if fref != fres:
            failures.append("normalized footers differ")
        if fref.get("cells_served_from_reuse") != cells - 2:
            failures.append(
                "normalized footers do not carry the expected reuse count "
                f"{cells - 2} for the alternating-spectra case"
            )
    if identity.get("footer_identical_modulo_timing") is not True:
        failures.append("footer_identical_modulo_timing flag is not true")
    if identity.get("reuse_count_preserved") is not True:
        failures.append("reuse_count_preserved flag is not true")

    noop = report.get("complete_run_noop") or {}
    summary = noop.get("summary") or {}
    if summary.get("cells") != cells or summary.get("cells_per_s") != 0.0:
        failures.append("complete-run resume did not return a done summary")
    if noop.get("bytes_unchanged") is not True:
        failures.append("complete-run resume rewrote the output file")

    rejections = report.get("rejections") or {}
    expected_phrases = {
        "fingerprint_mismatch": "fingerprint",
        "corrupt_mid_record": "corrupt record",
        "out_of_order_ordinal": "out of order",
    }
    for name, phrase in expected_phrases.items():
        entry = rejections.get(name) or {}
        if entry.get("named") is not True or phrase not in (entry.get("message") or ""):
            failures.append(f"rejection {name} lacks its named error")

    if report.get("fresh_run_atomic_replace") is not True:
        failures.append("fresh run did not atomically replace the partial output")
    if report.get("pass") is not True:
        failures.append("report does not record pass")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")
    if not REPORT.exists():
        failures.append("g2_p21_resume.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p21-g2-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "line_digest_break": lambda r: r["resume_identity"]["resumed_line_sha256"].__setitem__(
            2, "0" * 64
        ),
        "footer_diverge": lambda r: r["resume_identity"][
            "footer_resumed_normalized"
        ].__setitem__("cell_count", -1),
        "reuse_count_break": lambda r: r["resume_identity"][
            "footer_resumed_normalized"
        ].__setitem__("cells_served_from_reuse", 0),
        "noop_summary_break": lambda r: r["complete_run_noop"]["summary"].__setitem__(
            "cells_per_s", 9.9
        ),
        "rejection_message_weaken": lambda r: r["rejections"][
            "fingerprint_mismatch"
        ].__setitem__("message", "resume failed"),
        "fresh_replace_flip": lambda r: r.__setitem__("fresh_run_atomic_replace", False),
        "pass_flag": lambda r: r.__setitem__("pass", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p21-g2-selftest-") as directory:
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
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
