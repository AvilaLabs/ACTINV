#!/usr/bin/env python3
"""Independent checker for the P21 opening gate.

Imports no ACTINV production, audit or scoring module. Verifies the frozen
protocol hash, the opening-commit ancestry, the prior verdict records, the
identity-battery baseline, and the baseline memory/throughput profile record.
With ``--self-test`` it mutates a copy of the baseline and proves rejection.
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
PROTOCOL = ROOT / "protocols/ACTINV-P21_PROTOCOL.md"
BASELINE = ROOT / "results/g0_p21_identity_baseline.json"
OUTPUT = ROOT / "results/g0_p21_check.json"

PROTOCOL_SHA256 = "d14710f456bd7940fef905aafd880a4bdb760674482bbf0fe92dea15bcc56414"
OPENING_COMMIT = "871c96505567bb0f16447eef816d3b2b966b7f62"

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
REQUIRED_SURFACES = ("cli_cold", "cli_warm", "python", "mesh_cell")
REQUIRED_PROFILE_SIZES = (256, 1000)


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


def check_baseline(evidence: dict, failures: list[str]) -> None:
    if evidence.get("schema") != "actinv-p21-g0-baseline-1":
        failures.append("baseline schema is not actinv-p21-g0-baseline-1")
    surfaces = evidence.get("normalized_result_sha256")
    if not isinstance(surfaces, dict) or set(surfaces) != set(REQUIRED_SURFACES):
        failures.append(f"baseline surfaces differ from {sorted(REQUIRED_SURFACES)}")
        return
    for name, value in surfaces.items():
        if not is_hex64(value):
            failures.append(f"surface {name} hash is not 64 lowercase hex")
    inputs = evidence.get("inputs")
    if not isinstance(inputs, dict) or not is_hex64(inputs.get("spec_sha256")):
        failures.append("baseline lacks a valid spec_sha256")
    if not is_hex64((inputs or {}).get("library_sha256")):
        failures.append("baseline lacks a valid library_sha256")
    if not is_hex64((inputs or {}).get("mesh_flux_sha256")):
        failures.append("baseline lacks a valid mesh_flux_sha256")
    if surfaces["cli_cold"] != surfaces["cli_warm"] or evidence.get("cli_cold_eq_warm") is not True:
        failures.append("baseline does not record cold/warm CLI identity")
    if surfaces["cli_cold"] != surfaces["python"] or evidence.get("cli_eq_python") is not True:
        failures.append("baseline does not record CLI/Python identity")

    profile = evidence.get("baseline_profile")
    if not isinstance(profile, dict):
        failures.append("baseline lacks the memory/throughput profile")
        return
    hardware = profile.get("hardware")
    if not isinstance(hardware, dict) or not hardware.get("kernel"):
        failures.append("profile lacks a hardware record")
    runs = profile.get("runs")
    if not isinstance(runs, list) or sorted(r["cells"] for r in runs) != sorted(
        REQUIRED_PROFILE_SIZES
    ):
        failures.append(f"profile lacks runs at {REQUIRED_PROFILE_SIZES}")
        return
    for run in runs:
        for field in (
            "wall_time_s", "cells_per_s", "peak_rss_bytes",
            "output_bytes", "canonical_flux_bytes",
        ):
            if not isinstance(run.get(field), (int, float)) or run[field] <= 0:
                failures.append(f"profile run {run.get('cells')} lacks {field}")
        if run.get("threads") != 2 or run.get("chunk_cells") != 64:
            failures.append("profile run parameters differ from the frozen 2-thread/64-chunk plan")


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
    if not BASELINE.exists():
        failures.append("identity battery baseline is missing")
    else:
        check_baseline(json.loads(BASELINE.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p21-g0-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "opening_commit": OPENING_COMMIT,
        "verdicts_checked": len(EXPECTED_VERDICTS),
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    mutations = {
        "surface_hash_flip": lambda v: v["normalized_result_sha256"].__setitem__(
            "cli_cold", "0" * 64
        ),
        "surface_drop": lambda v: v["normalized_result_sha256"].pop("python"),
        "equality_flag_flip": lambda v: v.__setitem__("cli_eq_python", False),
        "schema_rename": lambda v: v.__setitem__("schema", "actinv-p20-g0-baseline-1"),
        "profile_size_drop": lambda v: v["baseline_profile"].__setitem__(
            "runs", v["baseline_profile"]["runs"][:1]
        ),
        "profile_rss_zero": lambda v: v["baseline_profile"]["runs"][0].__setitem__(
            "peak_rss_bytes", 0
        ),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(baseline)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p21-g0-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_baseline(json.loads(planted.read_text(encoding="utf-8")), failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} baseline mutations rejected")


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
