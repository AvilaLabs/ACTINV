#!/usr/bin/env python3
"""Independent checker for the P21 G4 documentation/evidence gate.

Imports no ACTINV production or audit module. Verifies:

- ``docs/SPEC.md`` documents all four new mesh-spec fields
  (``group_workloads``, ``cell_result_fields``, ``memory_limit_bytes``,
  ``resume``), the ``spec_fingerprint_sha256`` header field and the
  ``cells_served_from_reuse`` footer field;
- ``docs/METHOD.md`` describes the grouping, selection, memory guard and
  resume machinery and points at the executed record;
- ``README.md`` and ``examples/README.md`` surface the new fields and the
  executed 20,000-cell case;
- ``controls/cb1_mesh_performance.py`` emits a ``superseded_by`` pointer to
  the executed record; the committed ``results/cb1_mesh_performance.json``
  keeps its explicit not-executed warning (its sha256 is pinned by the
  sealed ``session_cb1.json`` and must not be regenerated), and
  ``docs/COMPETITIVE_BENCHMARK.md`` carries the supersession statement
  naming the executed record;
- ``results/g3_p21_executed.json`` exists, names 20,000 executed cells and
  records its field selection and hardware;
- ``results/g4_p21_surfaces.json`` exists and records every surface's
  normalized hash equal to the committed opening baseline (absent-option
  byte identity on the post-change build).

Emits ``results/g4_p21_check.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P21_PROTOCOL.md"
EXECUTED = ROOT / "results/g3_p21_executed.json"
CB1_SOURCE = ROOT / "controls/cb1_mesh_performance.py"
CB1_RESULT = ROOT / "results/cb1_mesh_performance.json"
OUTPUT = ROOT / "results/g4_p21_check.json"

PROTOCOL_SHA256 = "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

SPEC_FIELDS = (
    "group_workloads",
    "cell_result_fields",
    "memory_limit_bytes",
    "resume",
    "spec_fingerprint_sha256",
    "cells_served_from_reuse",
)
METHOD_TERMS = ("SHA-256", "cells_served_from_reuse", "cell_result_fields", "memory_limit_bytes", "resume")
README_TERMS = ("cells_served_from_reuse", "cell_result_fields", "memory_limit_bytes", "resume")


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

    spec_doc = (ROOT / "docs/SPEC.md").read_text(encoding="utf-8")
    for field in SPEC_FIELDS:
        if field not in spec_doc:
            failures.append(f"docs/SPEC.md does not document {field}")
    method = (ROOT / "docs/METHOD.md").read_text(encoding="utf-8")
    for term in METHOD_TERMS:
        if term not in method:
            failures.append(f"docs/METHOD.md does not describe {term}")
    if "g3_p21_executed.json" not in method:
        failures.append("docs/METHOD.md does not name the executed record")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for term in README_TERMS:
        if term not in readme:
            failures.append(f"README.md does not surface {term}")
    if "g3_p21_executed.json" not in readme:
        failures.append("README.md does not name the executed record")
    examples = (ROOT / "examples/README.md").read_text(encoding="utf-8")
    for term in ("group_workloads", "cell_result_fields", "memory_limit_bytes", "resume"):
        if term not in examples:
            failures.append(f"examples/README.md does not surface {term}")

    source = CB1_SOURCE.read_text(encoding="utf-8")
    if "superseded_by" not in source or "g3_p21_executed.json" not in source:
        failures.append("cb1 control does not record the executed-case supersede pointer")
    if not CB1_RESULT.exists():
        failures.append("results/cb1_mesh_performance.json is missing")
    else:
        cb1 = json.loads(CB1_RESULT.read_text(encoding="utf-8"))
        block = cb1.get("million_cell_linear_extrapolation_not_executed") or {}
        if "not executed" not in (block.get("warning") or ""):
            failures.append("CB1 extrapolation block lost its not-executed warning")
    # The committed record is sealed by session_cb1.json's evidence digests;
    # the supersession is carried by the committed benchmark report.
    benchmark = (ROOT / "docs/COMPETITIVE_BENCHMARK.md").read_text(encoding="utf-8")
    if "superseded by executed evidence" not in benchmark:
        failures.append("COMPETITIVE_BENCHMARK.md lacks the supersession statement")
    if "g3_p21_executed.json" not in benchmark:
        failures.append("COMPETITIVE_BENCHMARK.md does not name the executed record")

    if not EXECUTED.exists():
        failures.append("results/g3_p21_executed.json is missing")
    else:
        executed = json.loads(EXECUTED.read_text(encoding="utf-8"))
        case = executed.get("executed_case") or {}
        if case.get("cells") != 20_000:
            failures.append("executed record does not name 20,000 cells")
        if not isinstance(case.get("cell_result_fields"), list):
            failures.append("executed record lacks its field selection")
        if not (executed.get("hardware") or {}).get("kernel"):
            failures.append("executed record lacks the hardware record")

    surfaces = ROOT / "results/g4_p21_surfaces.json"
    if not surfaces.exists():
        failures.append("results/g4_p21_surfaces.json is missing")
    else:
        record = json.loads(surfaces.read_text(encoding="utf-8"))
        baseline = json.loads(
            (ROOT / "results/g0_p21_identity_baseline.json").read_text(encoding="utf-8")
        )
        expected = baseline.get("normalized_result_sha256") or {}
        observed = record.get("observed_sha256") or {}
        for name, value in expected.items():
            if observed.get(name) != value:
                failures.append(f"surface {name} hash differs from the opening baseline")
        if record.get("pass") is not True:
            failures.append("surface identity record does not record pass")

    return {
        "schema": "actinv-p21-g4-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
