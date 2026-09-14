#!/usr/bin/env python3
"""P25b G0 independent checker.

Imports no production module and no seal module.  Rehashes the
protocol, re-verifies the opening-commit ancestry, independently
rebuilds all three corpus manifests and format-marker census
histograms, rehashes the pinned P24/P25 evidence, re-verifies every
prior verdict verbatim, re-checks the release hold live, and rejects
planted mutations of the seal record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g0_p25b_seals.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P25b_PROTOCOL.md"
PROTOCOL_SHA256 = "3eec3c26db02e4aae0081818df5d1c2effee52f299e7a36e155d4437b08e71be"
OPENING_COMMIT = "f45c830693357a54e2d6b0db8d9fdfd2b3cb0f47"

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": (ND / "tendl-2023" / "files", "*.dat"),
    "fendl_32c": (ND / "fendl-3.2c" / "endf", "*.endf"),
    "eaf_2010": (ND / "eaf-2010" / "files", "*.dat"),
}

VERDICTS = {
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
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
    "verdict_p26.json": "P26-FAIL",
}

PRIOR_EVIDENCE = [
    RESULTS / "g1_p25_census.json",
    RESULTS / "g2_p25_traces.json",
    RESULTS / "g4_p25_repairs.json",
    RESULTS / "g5_p25_acceptance.json",
    RESULTS / "g1_p18b_decimal_oracle.json",
    RESULTS / "g4_p24_fresh.json",
    RESULTS / "g4_p24_row_ledger.json",
    RESULTS / "p24_cause_ledger.json",
    REPO / "docs" / "P25_TENDL2025_DEFECT_REPORT.md",
    REPO / "docs" / "COMPETITIVE_BENCHMARK.md",
]

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff(path: Path) -> str:
    """Independent reimplementation of the seal's marker census."""
    try:
        text = path.read_bytes()
    except OSError:
        return "unreadable"
    if b"EAF-2010" in text or b"EAF-20100" in text:
        return "eaf_marked"
    if b"TENDL-" in text:
        return "tendl_marked"
    has_endf_tail = False
    for raw in text.split(b"\n"):
        line = raw.rstrip(b"\r")
        if len(line) < 75:
            continue
        mat, mf, mt = line[66:70].strip(), line[70:72], line[72:75].strip()
        if mat.isdigit() and mt.isdigit() and mf in (b" 1", b" 2", b" 3",
                                                   b" 4", b" 5", b" 6"):
            has_endf_tail = True
            if mf in (b" 2", b" 6"):
                return "endf_mf2_or_mf6"
    if has_endf_tail:
        return "endf_no_detectable_marker"
    return "format_unsupported"


def rebuild(corpus: str) -> tuple[dict, dict]:
    root, pattern = CORPORA[corpus]
    entries, census = {}, {}
    for path in sorted(root.rglob(pattern)):
        rel = path.relative_to(root).as_posix()
        entries[rel] = sha256(path)
        cls = sniff(path)
        census[cls] = census.get(cls, 0) + 1
    return entries, census


def manifest_entries(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        h, rel = line.split("  ", 1)
        out[rel] = h
    return out


def check_report(record: dict) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p25b-g0-seal-1":
        local.append("schema")
    if record.get("protocol_sha256") != PROTOCOL_SHA256:
        local.append("protocol hash field")
    if record.get("opening_commit") != OPENING_COMMIT:
        local.append("opening commit field")
    if not record.get("pass"):
        local.append("seal pass is false")
    for name, want in VERDICTS.items():
        if record.get("prior_verdicts", {}).get(name) != want:
            local.append(f"recorded {name} != {want}")
    for path in PRIOR_EVIDENCE:
        want = record.get("prior_evidence_sha256", {}).get(path.name)
        if want != sha256(path):
            local.append(f"evidence hash {path.name}")
    for corpus, blk in record.get("candidates", {}).items():
        man = RESULTS / blk.get("manifest", "")
        if not man.is_file() or sha256(man) != blk.get("manifest_sha256"):
            local.append(f"{corpus} manifest hash")
        elif blk.get("file_count") != len(manifest_entries(man)):
            local.append(f"{corpus} file count")
        census = blk.get("format_sniff_census", {})
        if sum(census.values()) != blk.get("file_count"):
            local.append(f"{corpus} census total != file_count")
    for corpus, demo in record.get("demonstrations", {}).items():
        if demo.get("exit") != 0:
            local.append(f"{corpus} demo exit {demo.get('exit')}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(demo.get("npz_sha256") or "")):
            local.append(f"{corpus} demo npz hash")
        if not (isinstance(demo.get("seconds"), (int, float))
                and demo["seconds"] > 0):
            local.append(f"{corpus} demo seconds")
    hold = record.get("release_hold", {})
    if not hold.get("hold_standing"):
        local.append("release hold not standing")
    if hold.get("v11_tags"):
        local.append("v1.1 tags recorded")
    if not hold.get("changelog_unreleased") or hold.get("changelog_published"):
        local.append("changelog fields")
    if not record.get("prior_verdicts_ok"):
        local.append("prior_verdicts_ok false")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=REPO).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")

    record = json.loads(RECORD.read_text())

    for name, want in VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
        elif json.loads(path.read_text()).get("verdict") != want:
            failures.append(f"{name} verdict mismatch")

    tags = subprocess.run(["git", "tag", "-l", "v1.1*"], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()
    if tags:
        failures.append("v1.1* tag exists")

    for corpus in CORPORA:
        live_entries, live_census = rebuild(corpus)
        blk = record.get("candidates", {}).get(corpus, {})
        man = RESULTS / blk.get("manifest", "")
        if man.is_file():
            recorded = manifest_entries(man)
            if recorded != live_entries:
                failures.append(f"{corpus} manifest diverged")
        if blk.get("format_sniff_census") != live_census:
            failures.append(
                f"{corpus} census: recorded {blk.get('format_sniff_census')} "
                f"!= live {live_census}")

    failures.extend(check_report(record))

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["prior_verdicts"].update({"verdict_p25.json": "P25-PASS"}),
        lambda r: r["release_hold"].update({"hold_standing": False}),
        lambda r: r["demonstrations"]["tendl_2023"].update({"exit": 1}),
        lambda r: r.update({"protocol_sha256": "0" * 64}),
        lambda r: r["candidates"]["eaf_2010"]["format_sniff_census"]
                  .update({"eaf_marked": 1}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_report(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25b-g0-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g0_p25b_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
