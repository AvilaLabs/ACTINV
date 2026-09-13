#!/usr/bin/env python3
"""Independent checker for the P21 G1 grouping/selection/guard report.

Imports no ACTINV production or audit module. Re-derives every claim in
``results/g1_p21_grouping.json``:

- expected reuse is recounted from the recorded per-cell flux signatures
  (``cells - distinct signatures``), not trusted from the report;
- grouped/ungrouped byte-identity is re-verified from the recorded
  per-record SHA-256 lists, not the boolean;
- the normalized footers stored in the report must be equal, carry the
  asserted reuse counts, and contain no timing keys;
- the header-diff claim must name exactly ``spec_fingerprint_sha256``;
- field-selection records must contain exactly the named fields;
- the memory-guard message is reparsed: it must carry the named error and
  numeric peak/limit with peak > limit;
- the bogus-field rejection must name the offending field.

With ``--self-test`` it mutates copies of the report and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P21_PROTOCOL.md"
REPORT = ROOT / "results/g1_p21_grouping.json"
OUTPUT = ROOT / "results/g1_p21_check.json"

PROTOCOL_SHA256 = "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

TIMING_FIELDS = {"wall_time_s", "cells_per_s"}
FULL_RESULT_FIELDS = {
    "spec_title", "entry_point", "mode", "pruned_states", "total_states",
    "steps", "pathways", "pathway_closure", "ledger", "certificate",
}


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def check_report(report: dict, failures: list[str]) -> None:
    cells = report.get("cells")
    distinct = report.get("distinct_spectra")
    if not isinstance(cells, int) or cells < 4 or not isinstance(distinct, int) or not 1 < distinct < cells:
        failures.append("cells/distinct_spectra are not a sane repeated case")
        return
    signatures = report.get("cell_flux_signature_sha256")
    if (
        not isinstance(signatures, list)
        or len(signatures) != cells
        or not all(is_hex64(value) for value in signatures)
    ):
        failures.append("cell_flux_signature_sha256 is not a per-cell digest list")
        return
    expected_reuse = cells - len(set(signatures))
    if expected_reuse <= 0:
        failures.append("recorded signatures contain no repeats; case cannot exercise reuse")

    grouping = report.get("grouping") or {}
    for key in ("cell_record_sha256_grouped", "cell_record_sha256_ungrouped"):
        values = grouping.get(key)
        if (
            not isinstance(values, list)
            or len(values) != cells
            or not all(is_hex64(value) for value in values)
        ):
            failures.append(f"{key} is not a per-cell digest list")
            return
    if grouping["cell_record_sha256_grouped"] != grouping["cell_record_sha256_ungrouped"]:
        failures.append("grouped and ungrouped cell-record digests differ")
    if grouping.get("cell_records_byte_identical") is not True:
        failures.append("cell_records_byte_identical flag is not true")
    if grouping.get("expected_reuse") != expected_reuse:
        failures.append(
            f"reported expected_reuse {grouping.get('expected_reuse')} != recounted {expected_reuse}"
        )
    if grouping.get("cells_served_from_reuse_grouped") != expected_reuse:
        failures.append("grouped reuse count does not match the recounted expectation")
    if grouping.get("cells_served_from_reuse_ungrouped") != 0:
        failures.append("ungrouped run reports nonzero reuse")
    for key in ("footer_grouped_normalized", "footer_ungrouped_normalized"):
        footer = grouping.get(key)
        if not isinstance(footer, dict):
            failures.append(f"{key} missing")
            continue
        if TIMING_FIELDS & set(footer):
            failures.append(f"{key} still carries a timing field")
        if "cells_served_from_reuse" in footer:
            failures.append(f"{key} was not stripped of the reuse count")
    fg, fu = grouping.get("footer_grouped_normalized"), grouping.get("footer_ungrouped_normalized")
    if isinstance(fg, dict) and isinstance(fu, dict) and fg != fu:
        failures.append("normalized footers differ")
    if grouping.get("footer_identical_modulo_reuse_and_timing") is not True:
        failures.append("footer identity flag is not true")
    if grouping.get("header_diff_fields") != ["spec_fingerprint_sha256"]:
        failures.append("grouped/ungrouped headers differ in more than the fingerprint")

    selection = report.get("field_selection") or {}
    requested = selection.get("requested")
    if not isinstance(requested, list) or not requested:
        failures.append("field_selection lacks a requested field list")
        return
    if selection.get("result_key_sets") != [sorted(requested)]:
        failures.append("selected records do not carry exactly the requested fields")
    if selection.get("shared_fields_match_full_run") is not True:
        failures.append("selected fields do not match the full run")
    if selection.get("footer_closes") is not True:
        failures.append("selected-field run footer does not close")
    if selection.get("absent_option_full_record") is not True:
        failures.append("absent-option run does not emit the full result record")
    keysets = selection.get("result_key_sets") or [[]]
    if set(keysets[0]) - FULL_RESULT_FIELDS:
        failures.append("selected fields include a non-run-result field")

    validation = report.get("validation") or {}
    if validation.get("bogus_field_rejected") is not True or "not_a_field" not in (
        validation.get("bogus_field_message") or ""
    ):
        failures.append("bogus cell_result_fields entry was not rejected by name")

    guard = report.get("memory_guard") or {}
    message = guard.get("message") or ""
    match = re.search(
        r"memory_limit_bytes exceeded: peak RSS (\d+) bytes > limit (\d+) bytes",
        message,
    )
    if match is None:
        failures.append("memory guard did not emit the named error with both numbers")
    else:
        peak, limit = int(match.group(1)), int(match.group(2))
        if not peak > limit:
            failures.append("memory guard numbers do not show peak above limit")
        if limit != guard.get("limit_bytes"):
            failures.append("memory guard limit in the message differs from the report")
    if guard.get("no_output_left") is not True:
        failures.append("aborted run left an output file")
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
        failures.append("g1_p21_grouping.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p21-g1-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "reuse_count_flip": lambda r: r["grouping"].__setitem__(
            "cells_served_from_reuse_grouped", 0
        ),
        "digest_list_break": lambda r: r["grouping"]["cell_record_sha256_ungrouped"].__setitem__(
            0, "0" * 64
        ),
        "footer_diverge": lambda r: r["grouping"]["footer_ungrouped_normalized"].__setitem__(
            "cell_count", -1
        ),
        "header_diff_extra": lambda r: r["grouping"].__setitem__(
            "header_diff_fields", ["spec_fingerprint_sha256", "title"]
        ),
        "guard_message_weaken": lambda r: r["memory_guard"].__setitem__(
            "message", "out of memory"
        ),
        "signature_repeat_drop": lambda r: r.__setitem__(
            "cell_flux_signature_sha256", ["%064x" % i for i in range(r["cells"])]
        ),
        "pass_flag": lambda r: r.__setitem__("pass", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p21-g1-selftest-") as directory:
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
