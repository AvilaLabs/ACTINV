#!/usr/bin/env python3
"""P25b G3 independent checker for the repair proposal and floor freeze.

Recomputes the proposal arithmetic from the G1 census and G2 traces
(floor = pre-repair ok; projected = floor + bound_limited_timeout
rows only), verifies Amendment 1 exists and freezes the same floors
and repair rows, verifies no production code changed since the G1
commit, and rejects planted mutations of the proposal record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
RECORD = RESULTS / "g3_p25b_proposal.json"
CENSUS = RESULTS / "g1_p25b_census.json"
TRACES = RESULTS / "g2_p25b_traces.json"
AMENDMENT = ROOT / "protocols" / "ACTINV-P25b_AMENDMENT_1.md"
G1_COMMIT = "e8f54a1"

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_record(record: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p25b-g3-proposal-1":
        local.append("schema")
    if record.get("g1_census_sha256") != sha256(CENSUS):
        local.append("g1 census hash")
    if record.get("g2_traces_sha256") != sha256(TRACES):
        local.append("g2 traces hash")
    if record.get("revised_bound_s") != 600.0:
        local.append("revised bound")

    census = json.loads(CENSUS.read_text())
    traces = json.loads(TRACES.read_text())
    timeout_targets = {
        (t["corpus"], t["target"])
        for t in traces["traces"]
        if t["adjudication"].startswith("bound_limited_timeout")
    }
    for corpus, prop in record.get("proposals", {}).items():
        hist = census["histograms"].get(corpus, {})
        if prop.get("pre_repair_ok") != hist.get("ok"):
            local.append(f"{corpus} pre_repair_ok")
        if prop.get("target_count") != census["target_count"]:
            local.append(f"{corpus} target_count")
        if prop.get("coverage_floor") != hist.get("ok"):
            local.append(f"{corpus} floor != pre_repair_ok")
        rows = prop.get("repairable_rows", [])
        if prop.get("projected_post_repair_ok") != hist.get("ok", 0) + len(rows):
            local.append(f"{corpus} projection arithmetic")
        for tgt in rows:
            if (corpus, tgt) not in timeout_targets:
                local.append(f"{corpus} {tgt} not a bound_limited_timeout row")
        for t in traces["traces"]:
            if (t["corpus"] == corpus
                    and t["g1_class"] != "not_in_target_set"
                    and not t["adjudication"].startswith("bound_limited_timeout")
                    and not any(d["target"] == t["target"]
                                for d in prop.get("non_repaired", []))):
                local.append(f"{corpus} {t['target']} neither repaired nor "
                             f"declared non-repaired")

    amend = AMENDMENT.read_text() if AMENDMENT.is_file() else ""
    for corpus, prop in record.get("proposals", {}).items():
        floor_str = f"{prop['coverage_floor']} / {prop['target_count']}"
        if floor_str not in amend:
            local.append(f"amendment missing floor {floor_str}")
    for row in record.get("repair_classes", {}) \
                      .get("R3_bound_revision", {}) \
                      .get("rows", {}).get("tendl_2023", []):
        if row not in amend:
            local.append(f"amendment missing repair row {row}")
    if "600" not in amend:
        local.append("amendment missing revised bound")
    return local


def main() -> int:
    record = json.loads(RECORD.read_text())
    failures.extend(check_record(record))

    dirty = subprocess.run(
        ["git", "diff", "--name-only", f"{G1_COMMIT}..HEAD", "--", "crates/"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        failures.append(f"production code changed since G1 commit: {dirty}")
    unstaged = subprocess.run(
        ["git", "status", "--porcelain", "--", "crates/"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if unstaged:
        failures.append(f"uncommitted production changes: {unstaged}")

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r["proposals"]["eaf_2010"].update({"coverage_floor": 1}),
        lambda r: r["proposals"]["tendl_2023"]
                  .update({"projected_post_repair_ok": 47}),
        lambda r: r["proposals"]["fendl_32c"]["repairable_rows"]
                  .append("Fe-56"),
        lambda r: r.update({"revised_bound_s": 90.0}),
        lambda r: r.update({"g1_census_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_record(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25b-g3-check-1",
        "pass": not failures,
        "failures": failures,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    (RESULTS / "g3_p25b_check.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
