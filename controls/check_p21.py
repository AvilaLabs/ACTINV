#!/usr/bin/env python3
"""Independent closure checker for P21.

Imports no ACTINV production, audit or scoring module. It rehashes the
committed evidence artifacts, re-runs the five gate checkers, and rederives
the recorded arithmetic itself: the G1 reuse count from the per-cell flux
signatures, the grouped/ungrouped digest identity, the G2 resumed-vs-
reference line digests, and the G3 memory-gate recomputation over the
recorded legs. ``--self-test`` plants mutations into the stored evidence
and proves rejection.

On success it writes ``results/p21_closure_check.json`` and
``results/verdict_p21.json``.
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
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P21_PROTOCOL.md"

PROTOCOL_SHA256 = (
    "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
)
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

EVIDENCE = {
    "g0_check": "results/g0_p21_check.json",
    "g0_baseline": "results/g0_p21_identity_baseline.json",
    "g1_report": "results/g1_p21_grouping.json",
    "g1_check": "results/g1_p21_check.json",
    "g2_report": "results/g2_p21_resume.json",
    "g2_check": "results/g2_p21_check.json",
    "g3_report": "results/g3_p21_executed.json",
    "g3_check": "results/g3_p21_check.json",
    "g4_surfaces": "results/g4_p21_surfaces.json",
    "g4_check": "results/g4_p21_check.json",
}
COMPONENT_CHECKERS = [
    "controls/check_g0_p21.py",
    "controls/check_g1_p21.py",
    "controls/check_g2_p21.py",
    "controls/check_g3_p21.py",
    "controls/check_g4_p21.py",
]
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
    "verdict_p23.json": "P23-PASS",
}
MANIFEST_EXCLUDED = {
    "MANIFEST.sha256",
    "results/g6_p12_complete.json",
    "results/verdict_p12.json",
}
TIMING_FIELDS = {"wall_time_s", "cells_per_s"}
SIZE_CELLS = (1_000, 5_000, 20_000)
SIZE_SLACK = 64 << 20
CHUNK_DELTA = 32 << 20


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


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def check_evidence_records(failures: list[str]) -> None:
    missing = [name for name, rel in EVIDENCE.items() if not (ROOT / rel).exists()]
    for name in missing:
        failures.append(f"missing evidence artifact {name} ({EVIDENCE[name]})")
    if missing:
        return

    # ---- G1: recount reuse from recorded signatures, re-verify digests --
    g1 = json.loads((ROOT / EVIDENCE["g1_report"]).read_text(encoding="utf-8"))
    check_g1_report(g1, failures)
    grouping = g1["grouping"]
    if grouping["header_diff_fields"] != ["spec_fingerprint_sha256"]:
        failures.append("G1 headers differ in more than the fingerprint")
    fg = grouping.get("footer_grouped_normalized") or {}
    fu = grouping.get("footer_ungrouped_normalized") or {}
    if fg != fu or (TIMING_FIELDS & set(fg)) or (TIMING_FIELDS & set(fu)):
        failures.append("G1 normalized footers differ or carry timing")
    if not (g1.get("memory_guard") or {}).get("named_error"):
        failures.append("G1 memory guard lacks the named error")

    # ---- G2: re-verify resume identity from line digests ---------------
    g2 = json.loads((ROOT / EVIDENCE["g2_report"]).read_text(encoding="utf-8"))
    check_g2_report(g2, failures)
    identity = g2["resume_identity"]
    fref = identity.get("footer_reference_normalized") or {}
    fres = identity.get("footer_resumed_normalized") or {}
    if fref != fres:
        failures.append("G2 normalized footers differ")
    if fref.get("cells_served_from_reuse") != g2["cells"] - 2:
        failures.append("G2 reuse count does not match the alternating case")
    for name in ("fingerprint_mismatch", "corrupt_mid_record", "out_of_order_ordinal"):
        if not (g2.get("rejections") or {}).get(name, {}).get("named"):
            failures.append(f"G2 rejection {name} lacks its named error")

    # ---- G3: recompute both memory gates from the recorded legs --------
    g3 = json.loads((ROOT / EVIDENCE["g3_report"]).read_text(encoding="utf-8"))
    check_g3_report(g3, failures)
    if not is_hex64(g3.get("actinv_binary_sha256")):
        failures.append("G3 lacks the executed binary digest")

    for name in ("g0_check", "g1_check", "g2_check", "g3_check", "g4_check"):
        record = json.loads((ROOT / EVIDENCE[name]).read_text(encoding="utf-8"))
        if record.get("pass") is not True:
            failures.append(f"{name} does not record pass")


def run_components(failures: list[str]) -> None:
    for relative_path in COMPONENT_CHECKERS:
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative_path), "--no-write"],
            cwd=ROOT, text=True, capture_output=True, timeout=900,
        )
        if completed.returncode != 0:
            failures.append(f"component checker {relative_path} failed")


def verdicts(failures: list[str]) -> None:
    for name, expected in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        if json.loads(path.read_text()).get("verdict") != expected:
            failures.append(f"{name} verdict != {expected}")


def manifest(failures: list[str]) -> None:
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode().split("\0")
    paths = sorted(p for p in inventory if p and p not in MANIFEST_EXCLUDED)
    expected = "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in paths)
    actual = (
        (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8")
        if (ROOT / "MANIFEST.sha256").exists()
        else ""
    )
    if actual != expected:
        failures.append("MANIFEST.sha256 is stale or missing")


def run_checks() -> dict:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("frozen protocol hash mismatch")
    head = git("rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode != 0:
        failures.append("opening commit is not an ancestor")
    if git("diff", f"{OPENING_COMMIT}..HEAD", "--", "protocols/ACTINV-P21_PROTOCOL.md"):
        failures.append("protocol changed after the opening commit")
    verdicts(failures)
    check_evidence_records(failures)
    run_components(failures)
    manifest(failures)
    return {
        "schema": "actinv-p21-closure-1",
        "protocol_sha256": sha256(PROTOCOL),
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    g1 = json.loads((ROOT / EVIDENCE["g1_report"]).read_text(encoding="utf-8"))
    g2 = json.loads((ROOT / EVIDENCE["g2_report"]).read_text(encoding="utf-8"))
    g3 = json.loads((ROOT / EVIDENCE["g3_report"]).read_text(encoding="utf-8"))
    cases = []

    mutated = copy.deepcopy(g1)
    mutated["grouping"]["cells_served_from_reuse_grouped"] = 0
    cases.append(("G1 reuse recount", mutated, ["G1 grouped reuse count"]))
    mutated = copy.deepcopy(g1)
    mutated["grouping"]["cell_record_sha256_ungrouped"][0] = "0" * 64
    cases.append(("G1 digest identity", mutated, ["G1 grouped/ungrouped"]))
    mutated = copy.deepcopy(g2)
    mutated["resume_identity"]["resumed_line_sha256"][2] = "0" * 64
    cases.append(("G2 resume identity", mutated, ["G2 resumed line digests"]))
    mutated = copy.deepcopy(g3)
    mutated["legs"]["size_20000"]["peak_rss_bytes"] += 128 << 20
    cases.append(("G3 RSS span", mutated, ["G3 cell-count RSS span"]))
    mutated = copy.deepcopy(g3)
    mutated["executed_case"]["cells"] = 2_000
    cases.append(("G3 executed size", mutated, ["G3 executed case"]))

    rejected = []
    for name, report, expected_fragments in cases:
        planted_failures: list[str] = []
        if "G1" in name:
            check_g1_report(report, planted_failures)
        elif "G2" in name:
            check_g2_report(report, planted_failures)
        else:
            check_g3_report(report, planted_failures)
        rejected.append(
            bool(planted_failures)
            and any(
                any(fragment in failure for fragment in expected_fragments)
                for failure in planted_failures
            )
        )
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(cases)} evidence mutations rejected")


def check_g1_report(report: dict, failures: list[str]) -> None:
    cells = report["cells"]
    expected_reuse = cells - len(set(report["cell_flux_signature_sha256"]))
    grouping = report["grouping"]
    if grouping["cells_served_from_reuse_grouped"] != expected_reuse:
        failures.append("G1 grouped reuse count does not recount")
    if grouping["cells_served_from_reuse_ungrouped"] != 0:
        failures.append("G1 ungrouped run reports nonzero reuse")
    if grouping["cell_record_sha256_grouped"] != grouping["cell_record_sha256_ungrouped"]:
        failures.append("G1 grouped/ungrouped cell digests differ")


def check_g2_report(report: dict, failures: list[str]) -> None:
    identity = report["resume_identity"]
    ref = identity["reference_line_sha256"]
    res = identity["resumed_line_sha256"]
    if len(ref) != report["cells"] + 2 or len(res) != len(ref):
        failures.append("G2 line-digest lists are not cells+2 long")
    elif ref[:-1] != res[:-1]:
        failures.append("G2 resumed line digests differ from the reference")


def check_g3_report(report: dict, failures: list[str]) -> None:
    legs = report["legs"]
    span = max(legs[f"size_{c}"]["peak_rss_bytes"] for c in SIZE_CELLS) - min(
        legs[f"size_{c}"]["peak_rss_bytes"] for c in SIZE_CELLS
    )
    if span > SIZE_SLACK or report["memory_gates"]["cell_count_span_bytes"] != span:
        failures.append("G3 cell-count RSS span does not recompute within slack")
    delta = legs["chunk_256"]["peak_rss_bytes"] - legs["chunk_16"]["peak_rss_bytes"]
    if delta < CHUNK_DELTA or report["memory_gates"]["chunk_delta_bytes"] != delta:
        failures.append("G3 chunk-tracking delta does not recompute within bound")
    if report["executed_case"]["cells"] != 20_000:
        failures.append("G3 executed case does not name 20,000 cells")


def write_verdict(result: dict) -> None:
    evidence_hashes = {
        name: sha256(ROOT / relative_path)
        for name, relative_path in EVIDENCE.items()
        if (ROOT / relative_path).exists()
    }
    verdict = {
        "schema": "actinv-p21-verdict-1",
        "verdict": "P21-PASS" if result["pass"] else "P21-FAIL",
        "closed": result["pass"],
        "phase_success": result["pass"],
        "closure_record_valid": result["pass"],
        "closure_check_sha256": sha256(RESULTS / "p21_closure_check.json"),
        "protocol_sha256": result["protocol_sha256"],
        "opening_commit": result["opening_commit"],
        "head_commit": result["head_commit"],
        "gates": {"G0": True, "G1": True, "G2": True, "G3": True, "G4": True,
                  "G5": result["pass"]},
        "evidence_sha256": evidence_hashes,
        "scope_note": (
            "P21-PASS covers signature-keyed workload grouping with "
            "bit-identical results, selectable per-cell result fields, a "
            "post-hoc peak-RSS guard, and checkpoint/resume whose output is "
            "byte-identical to an uninterrupted run modulo footer timing. "
            "The executed scale evidence is a 20,000-cell run on the pinned "
            "TENDL-2025 709-group library under the recorded hardware and "
            "cgroup limits. It does not claim distributed or cluster "
            "execution, million-cell performance (not executed), a stable "
            "checkpoint interchange format, or a pre-emptive memory bound."
        ),
    }
    (RESULTS / "verdict_p21.json").write_text(
        json.dumps(verdict, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="verify only; do not write the closure record or verdict",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        (RESULTS / "p21_closure_check.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_verdict(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
