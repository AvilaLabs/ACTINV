#!/usr/bin/env python3
"""Independent checker for the P19 opening gate.

Imports no ACTINV production, audit or scoring module. Verifies the frozen
protocol hash, the opening-commit ancestry, the prior verdict record, the
identity-battery baseline, the unresolved-resonance coverage inventory, the
oracle inventory, and the Core case compile evidence. With ``--self-test`` it
mutates a copy of the baseline and proves rejection.
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
PROTOCOL = ROOT / "protocols/ACTINV-P19_PROTOCOL.md"
BASELINE = ROOT / "results/g0_p19_identity_baseline.json"
LRU2 = ROOT / "results/p19_lru2_inventory.json"
ORACLE = ROOT / "results/p19_oracle_inventory.json"
CORE_PLAN = ROOT / "results/p19_core_plan.json"
OUTPUT = ROOT / "results/g0_p19_check.json"

PROTOCOL_SHA256 = "8ee3d561fec513b3038fcbabfc8b57a6cce25e36c992f2af311afb6740acce16"
OPENING_COMMIT = "d463d57db4bcd87715eaf0d2082d07a71ec36424"

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
    "verdict_p23.json": "P23-PASS",
}
REQUIRED_SURFACES = ("cli_cold", "cli_warm", "python", "mesh_cell")
TEST_SET = {"W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56"}


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
    if evidence.get("schema") != "actinv-p19-g0-baseline-1":
        failures.append("baseline schema is not actinv-p19-g0-baseline-1")
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
    # Re-derive the recorded identity claims from the stored hashes; a mutation
    # that keeps the flags while changing a hash is rejected here.
    derived_cold_warm = surfaces["cli_cold"] == surfaces["cli_warm"]
    derived_cli_python = surfaces["cli_cold"] == surfaces["python"]
    if derived_cold_warm is not True or evidence.get("cli_cold_eq_warm") is not True:
        failures.append("baseline does not record cold/warm CLI identity")
    if derived_cli_python is not True or evidence.get("cli_eq_python") is not True:
        failures.append("baseline does not record CLI/Python identity")


def check_inventories(failures: list[str]) -> None:
    if not LRU2.exists():
        failures.append("unresolved-resonance coverage inventory is missing")
    else:
        inventory = json.loads(LRU2.read_text(encoding="utf-8"))
        materials = inventory.get("materials")
        if not isinstance(materials, dict):
            failures.append("coverage inventory lacks a materials map")
        else:
            # Filenames zero-pad the mass number (n-Fe056); normalize digits.
            def norm(name: str) -> str:
                import re
                return re.sub(r"0+(\d)", r"\1", name).replace("-", "").lower()

            keys = {norm(k) for k in materials}
            for material in TEST_SET:
                if not any(norm(material) in key for key in keys):
                    failures.append(f"coverage inventory omits test-set material {material}")
        if not isinstance(inventory.get("with_unresolved_lru2"), int):
            failures.append("coverage inventory lacks with_unresolved_lru2")
    if not ORACLE.exists():
        failures.append("oracle inventory is missing")
    else:
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        if oracle.get("njoy_purr_mt152") is not True:
            failures.append("oracle inventory does not record a live MT=152 PURR probe")
        for field in ("njoy_sha256", "w186_pendf_sha256"):
            if not is_hex64(oracle.get(field)):
                failures.append(f"oracle inventory lacks a valid {field}")
        if not isinstance(oracle.get("sigma0_response_observed"), (int, float)):
            failures.append("oracle inventory lacks the observed dilution response")
    if not CORE_PLAN.exists():
        failures.append("Core case compile evidence is missing")
    else:
        plan = json.loads(CORE_PLAN.read_text(encoding="utf-8"))
        if plan.get("package_compiles") is not True:
            failures.append("Core case package did not compile under run --plan")


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
    check_inventories(failures)
    return {
        "schema": "actinv-p19-g0-check-1",
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
        "schema_rename": lambda v: v.__setitem__("schema", "actinv-p19-g0-baseline-0"),
        "spec_hash_drop": lambda v: v["inputs"].__setitem__("spec_sha256", "xyz"),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(baseline)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p19-g0-selftest-") as directory:
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
