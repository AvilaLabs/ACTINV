#!/usr/bin/env python3
"""Independent checker for the P21 G3 executed-scale report.

Imports no ACTINV production or audit module. Re-derives every gate in
``results/g3_p21_executed.json`` from the recorded per-leg measurements:

- the cell-count flatness bound is recomputed as max-min over the three
  recorded size legs and compared to the recorded 64 MB slack;
- the chunk-tracking delta is recomputed as RSS(256) - RSS(16) and compared
  to the recorded 32 MB bound;
- the executed 20,000-cell record is checked for completeness: real size,
  distinct spectra declared, pinned library/decay hashes, hardware/cgroup/
  version record, input and output digests, timing and throughput;
- the grouping leg's reuse count is re-derived from cells and distinct
  spectra and both rates must be recorded;
- no leg may report an unexecuted or extrapolated cell count.

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
REPORT = ROOT / "results/g3_p21_executed.json"
OUTPUT = ROOT / "results/g3_p21_check.json"

PROTOCOL_SHA256 = "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

EXECUTED_CELLS = 20_000
SIZE_CELLS = (1_000, 5_000, 20_000)
CHUNK_LEGS = (16, 256)
SIZE_SLACK = 64 << 20
CHUNK_DELTA = 32 << 20
GROUP_CELLS = 1_000
GROUP_DISTINCT = 25

LEG_FIELDS = (
    "cells",
    "threads",
    "chunk_cells",
    "wall_time_s",
    "cells_per_s",
    "peak_rss_bytes",
    "output_bytes",
    "output_sha256",
    "output_records",
    "canonical_flux_sha256",
)


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def is_hex40(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(char in "0123456789abcdef" for char in value)
    )


def check_leg(name: str, leg: dict, cells: int, failures: list[str]) -> None:
    if not isinstance(leg, dict):
        failures.append(f"leg {name} missing")
        return
    for field in LEG_FIELDS:
        if leg.get(field) is None:
            failures.append(f"leg {name} lacks {field}")
    if leg.get("cells") != cells:
        failures.append(f"leg {name} reports cells={leg.get('cells')} != {cells}")
    if leg.get("output_records") != cells + 2:
        failures.append(f"leg {name} output records != cells + 2")
    if not isinstance(leg.get("peak_rss_bytes"), int) or leg["peak_rss_bytes"] <= 0:
        failures.append(f"leg {name} lacks a positive peak_rss_bytes")
    if not is_hex64(leg.get("output_sha256")) or not is_hex64(
        leg.get("canonical_flux_sha256")
    ):
        failures.append(f"leg {name} lacks digest evidence")


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p21-g3-executed-1":
        failures.append("schema is not actinv-p21-g3-executed-1")

    legs = report.get("legs") or {}
    for cells in SIZE_CELLS:
        check_leg(f"size_{cells}", legs.get(f"size_{cells}"), cells, failures)
    for chunk in CHUNK_LEGS:
        check_leg(
            f"chunk_{chunk}",
            legs.get(f"chunk_{chunk}"),
            GROUP_CELLS,
            failures,
        )
    check_leg("group_on", legs.get("group_on"), GROUP_CELLS, failures)
    check_leg("group_off", legs.get("group_off"), GROUP_CELLS, failures)
    if failures:
        return

    size_rss = [legs[f"size_{cells}"]["peak_rss_bytes"] for cells in SIZE_CELLS]
    span = max(size_rss) - min(size_rss)
    gates = report.get("memory_gates") or {}
    if gates.get("cell_count_span_bytes") != span:
        failures.append("recorded cell-count RSS span does not recompute")
    if gates.get("cell_count_span_limit_bytes") != SIZE_SLACK:
        failures.append("recorded cell-count slack differs from the frozen 64 MB")
    if span > SIZE_SLACK or gates.get("cell_count_flat") is not True:
        failures.append("peak RSS grows with cell count beyond the 64 MB slack")
    chunk_delta = legs["chunk_256"]["peak_rss_bytes"] - legs["chunk_16"]["peak_rss_bytes"]
    if gates.get("chunk_delta_bytes") != chunk_delta:
        failures.append("recorded chunk delta does not recompute")
    if gates.get("chunk_delta_limit_bytes") != CHUNK_DELTA:
        failures.append("recorded chunk bound differs from the frozen 32 MB")
    if chunk_delta < CHUNK_DELTA or gates.get("chunk_tracked") is not True:
        failures.append("peak RSS does not track chunk_cells by the frozen 32 MB")
    if legs["chunk_16"]["chunk_cells"] != 16 or legs["chunk_256"]["chunk_cells"] != 256:
        failures.append("chunk legs ran the wrong chunk sizes")

    executed = report.get("executed_case") or {}
    if executed.get("cells") != EXECUTED_CELLS:
        failures.append("executed case does not name 20,000 cells")
    if executed.get("distinct_spectra") is not True:
        failures.append("executed case does not declare distinct spectra")
    if not is_hex64(executed.get("library_sha256")) or not is_hex64(
        executed.get("decay_sha256")
    ):
        failures.append("executed case lacks pinned input digests")
    if not isinstance(executed.get("cell_result_fields"), list):
        failures.append("executed case does not record its field selection")
    result = executed.get("result") or {}
    if result.get("cells") != EXECUTED_CELLS:
        failures.append("executed result does not carry 20,000 cells")

    grouping = report.get("grouping") or {}
    if grouping.get("cells") != GROUP_CELLS or grouping.get("distinct_spectra") != GROUP_DISTINCT:
        failures.append("grouping leg parameters differ from the frozen case")
    if grouping.get("reuse_grouped") != GROUP_CELLS - GROUP_DISTINCT or grouping.get(
        "reuse_expected"
    ) != GROUP_CELLS - GROUP_DISTINCT:
        failures.append("grouped reuse count does not recompute")
    if legs["group_on"].get("cells_served_from_reuse") != grouping.get("reuse_grouped"):
        failures.append("grouped leg and grouping section disagree on reuse")
    if legs.get("group_off", {}).get("cells_served_from_reuse") != 0:
        failures.append("ungrouped leg reports reuse")
    if not isinstance(grouping.get("rate_grouped"), (int, float)) or not isinstance(
        grouping.get("rate_ungrouped"), (int, float)
    ):
        failures.append("grouping rates are not recorded")

    hardware = report.get("hardware") or {}
    for field in ("kernel", "rustc", "platform"):
        if not hardware.get(field):
            failures.append(f"hardware record lacks {field}")
    if not isinstance(hardware.get("cgroup"), dict):
        failures.append("hardware record lacks the cgroup record")
    if not is_hex64(report.get("actinv_binary_sha256")):
        failures.append("report lacks the executed binary digest")
    if not is_hex40(report.get("head_commit")):
        failures.append("report lacks the head commit")
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
        failures.append("g3_p21_executed.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p21-g3-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "size_leg_missing": lambda r: r["legs"].pop("size_5000"),
        "rss_span_break": lambda r: r["legs"]["size_20000"].__setitem__(
            "peak_rss_bytes", r["legs"]["size_1000"]["peak_rss_bytes"] + (128 << 20)
        ),
        "chunk_delta_shrink": lambda r: r["legs"]["chunk_256"].__setitem__(
            "peak_rss_bytes", r["legs"]["chunk_16"]["peak_rss_bytes"] + (8 << 20)
        ),
        "executed_cells_shrink": lambda r: r["executed_case"].__setitem__("cells", 2000),
        "reuse_break": lambda r: r["legs"]["group_on"].__setitem__(
            "cells_served_from_reuse", 0
        ),
        "hardware_drop": lambda r: r["hardware"].pop("kernel"),
        "pass_flag": lambda r: r.__setitem__("pass", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p21-g3-selftest-") as directory:
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
