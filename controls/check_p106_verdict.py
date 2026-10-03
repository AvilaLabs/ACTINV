#!/usr/bin/env python3
"""Derive the P106 disposition from retained local, quality, and CI evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    "g0_p106_bounds.json": ROOT / "results/g0_p106_bounds.json",
    "g1_p106_bounds.json": ROOT / "results/g1_p106_bounds.json",
    "g2_p106_bounds.json": ROOT / "results/g2_p106_bounds.json",
    "g3_p106_quality.json": ROOT / "results/g3_p106_quality.json",
}
CI_PATH = ROOT / "results/p106_ci_runs.json"
VERDICT_PATH = ROOT / "results/p106_verdict.json"
PROTOCOL_SHA256 = "163a7a265363583c42b8d27a28ec11664b87d393a0b2565fcc76ec3904d606ee"
AMENDMENT_SHA256 = "a9b8568fb5e7fde35b8325b525646fbc441e6989c4292fa6ef8735ce84c17f97"
REQUIRED_QUALITY_GATES = {
    "control_child_lifecycle", "full_control_read_only_replay", "g1_read_only_replay",
    "handbook_build", "handbook_chromium", "handbook_links",
    "rust_check_all_targets_features", "rust_clippy_all_targets_features", "rust_fmt",
    "rust_test_all_targets_features",
}
REQUIRED_CI_WORKFLOWS = {
    "controls", "desktop builds", "Build browser workbench", "Build handbook",
}
EXPECTED_RESOURCES = {
    "memory_max_bytes": 6_442_450_944,
    "memory_swap_max_bytes": 0,
    "tasks_max": 128,
    "cpu_quota_percent": 200,
    "disk_tmpdir": "target/preflight-tmp",
    "coordinator_jobs": "serial",
}


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> tuple[dict, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {}, str(error)
    return (value, None) if isinstance(value, dict) else ({}, "artifact root is not an object")


def _g1_ok(g1: dict) -> bool:
    mutations = g1.get("mutations_rejected")
    refusals = g1.get("refusal_controls")
    return (
        g1.get("pass") is True
        and g1.get("case_count") == 146
        and g1.get("target_count") == 150
        and g1.get("endpoint_count") == 300
        and g1.get("independent_comparison_count") == 146
        and g1.get("repeat_byte_identical") is True
        and isinstance(mutations, dict) and bool(mutations) and all(value is True for value in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusals.get("checks"), dict) and bool(refusals["checks"])
        and all(value is True for value in refusals["checks"].values())
        and g1.get("failures") == []
    )


def _quality_ok(quality: dict) -> tuple[bool, dict]:
    gates = quality.get("gates")
    resources = quality.get("resource_limits")
    gate_names_ok = isinstance(gates, dict) and REQUIRED_QUALITY_GATES.issubset(gates)
    gates_ok = gate_names_ok and all(
        isinstance(gate, dict) and gate.get("exit_code") == 0 for gate in gates.values()
    )
    resource_checks = {
        key: isinstance(resources, dict) and resources.get(key) == expected
        for key, expected in EXPECTED_RESOURCES.items()
    }
    tests = quality.get("workspace_tests")
    tests_ok = isinstance(tests, dict) and tests.get("failed") == 0 and tests.get("passed", 0) > 0
    result = {
        "required_gate_names_present": gate_names_ok,
        "all_recorded_gate_exit_codes_zero": gates_ok,
        "resource_limits": resource_checks,
        "workspace_tests_reported_green": tests_ok,
        "reported_gate_count": len(gates) if isinstance(gates, dict) else 0,
    }
    return quality.get("pass") is True and gate_names_ok and gates_ok and all(resource_checks.values()) and tests_ok, result


def _ci_result() -> tuple[bool, str, dict | None]:
    if not CI_PATH.is_file():
        return False, "PENDING", None
    try:
        runs = json.loads(CI_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, "FAIL", {"error": str(error)}
    if not isinstance(runs, list):
        return False, "FAIL", {"error": "CI evidence root must be an array"}
    names = {run.get("workflowName") for run in runs if isinstance(run, dict)}
    hashes = {run.get("headSha") for run in runs if isinstance(run, dict)}
    valid_records = all(
        isinstance(run, dict) and run.get("status") == "completed" and run.get("conclusion") == "success"
        for run in runs
    )
    passed = bool(runs) and REQUIRED_CI_WORKFLOWS.issubset(names) and len(hashes) == 1 and None not in hashes and valid_records
    return passed, "PASS" if passed else "FAIL", {
        "required_workflows": sorted(REQUIRED_CI_WORKFLOWS),
        "observed_workflows": sorted(name for name in names if isinstance(name, str)),
        "single_head_sha": len(hashes) == 1 and None not in hashes,
        "all_completed_success": valid_records,
        "pass": passed,
    }


def derive() -> dict:
    loaded = {name: _read(path) for name, path in ARTIFACTS.items()}
    g0, g1, g2, quality = (loaded[name][0] for name in ARTIFACTS)
    errors = {name: error for name, (_, error) in loaded.items() if error is not None}
    predecessor, predecessor_error = _read(ROOT / "results/p105_verdict.json")
    predecessor_ok = predecessor_error is None and predecessor.get("verdict") == "P105-PASS"
    if predecessor_error is not None:
        errors["p105_verdict.json"] = predecessor_error
    amendment_ok = (
        g0.get("amendment_sha256") == AMENDMENT_SHA256
        and g0.get("amendment_registered") is True
    )
    g0_ok = (
        g0.get("schema") == "actinv-p106-bounds-g0-seal-1"
        and g0.get("phase") == "P106"
        and g0.get("pass") is True
        and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True
        and amendment_ok
        and g0.get("case_count") == 146
        and g0.get("pack_mirror_identical") is True
    )
    replay_failure_path = ROOT / "results/g0_p106_replay_failure.json"
    replay_failure, replay_error = _read(replay_failure_path)
    if replay_failure_path.is_file():
        # A failed mandatory replay is terminal after Amendment A. The earlier
        # successful seal remains immutable and cannot override that failure.
        g0_ok = g0_ok and replay_error is None and replay_failure.get("pass") is True
    g1_ok = _g1_ok(g1)
    actual_g1_sha = _sha(ARTIFACTS["g1_p106_bounds.json"])
    g2_ok = (
        g2.get("schema") == "actinv-p106-bounds-g2-1"
        and g2.get("phase") == "P106"
        and g2.get("pass") is True
        and g2.get("protocol_sha256") == PROTOCOL_SHA256
        and g2.get("g1_result_sha256") == actual_g1_sha
        and g2.get("separate_output_paths_byte_identical") is True
    )
    quality_ok, quality_detail = _quality_ok(quality)
    local_pass = predecessor_ok and g0_ok and g1_ok and g2_ok and quality_ok
    ci_pass, ci_status, ci_detail = _ci_result()
    verdict = "P106-PASS" if local_pass and ci_pass else "P106-LOCAL-PASS" if local_pass and not CI_PATH.exists() else "P106-FAIL"
    evidence = {name: _sha(path) for name, path in ARTIFACTS.items()}
    if replay_failure_path.is_file():
        evidence["g0_p106_replay_failure.json"] = _sha(replay_failure_path)
    result = {
        "schema": "actinv-p106-verdict-1",
        "phase": "P106",
        "repair_rounds": 1,
        "verdict": verdict,
        "gates": {
            "G0": "PASS" if g0_ok else "FAIL",
            "G1": "PASS" if g1_ok else "FAIL",
            "G2": "PASS" if g2_ok else "FAIL",
            "G3_local": "PASS" if quality_ok else "FAIL",
            "G3_CI": ci_status,
        },
        "checks": {
            "predecessor_p105_terminal_pass": predecessor_ok,
            "p106_amendment_registered": amendment_ok,
            "quality": quality_detail,
            "ci": ci_detail,
            "artifact_read_errors": errors,
        },
        "evidence_sha256": {**evidence, "p105_verdict.json": _sha(ROOT / "results/p105_verdict.json")},
        "ci_evidence_sha256": _sha(CI_PATH),
        "predecessors": {"P105": "PASS" if predecessor_ok else "FAIL"},
        "qualification": "Conservative class supersets for declared whole-component rectangular activity bounds under the pinned 10 CFR Part 61.55 rule pack; no uncertainty propagation, upstream completeness qualification, or disposal-acceptance conclusion.",
    }
    return result


def main(write: bool) -> int:
    result = derive()
    if write:
        VERDICT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        same = True
    else:
        persisted, error = _read(VERDICT_PATH)
        same = error is None and persisted == result
    passed_verdict = result["verdict"] in ("P106-PASS", "P106-LOCAL-PASS")
    print(json.dumps({"verdict": result["verdict"], "gates": result["gates"],
                      "evidence_sha256": result["evidence_sha256"],
                      "persisted_matches": same}, indent=2, sort_keys=True))
    return 0 if passed_verdict and same else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="persist the derived P106 verdict")
    raise SystemExit(main(parser.parse_args().write))
