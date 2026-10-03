#!/usr/bin/env python3
"""Derive the P107 disposition from sealed local, quality, and CI evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    "g0_p107_bounds.json": ROOT / "results/g0_p107_bounds.json",
    "g1_p107_bounds.json": ROOT / "results/g1_p107_bounds.json",
    "g2_p107_bounds.json": ROOT / "results/g2_p107_bounds.json",
    "g3_p107_quality.json": ROOT / "results/g3_p107_quality.json",
}
CI_PATH = ROOT / "results/p107_ci_runs.json"
IMPLEMENTATION_PATH = ROOT / "results/p107_implementation_commit.json"
VERDICT_PATH = ROOT / "results/p107_verdict.json"
PROTOCOL_SHA256 = "8fe3c19a4c662092c1fb3ed0021857c8bb9337e265f76cd7903ea4f537ffe42b"
AMENDMENT_SHA256 = "cfb4e6e723489d0041192cf257a05fd7a3cda16dbe7208e31a85a350ea2cd09e"
P106_PROTOCOL_SHA256 = "163a7a265363583c42b8d27a28ec11664b87d393a0b2565fcc76ec3904d606ee"
P106_AMENDMENT_SHA256 = "a9b8568fb5e7fde35b8325b525646fbc441e6989c4292fa6ef8735ce84c17f97"
REQUIRED_QUALITY_GATES = {
    "bounds_population_regressions", "seal_identity_regressions",
    "control_child_lifecycle", "full_control_read_only_replay", "g1_read_only_replay",
    "handbook_build", "handbook_chromium", "handbook_links",
    "rust_check_all_targets_features", "rust_clippy_all_targets_features", "rust_fmt",
    "rust_test_all_targets_features",
}
REQUIRED_CI_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench", "Build handbook"}
EXPECTED_RESOURCES = {
    "memory_max_bytes": 6_442_450_944, "memory_swap_max_bytes": 0,
    "tasks_max": 128, "cpu_quota_percent": 200,
    "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial",
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
    mutations, refusals = g1.get("mutations_rejected"), g1.get("refusal_controls")
    return (
        g1.get("schema") == "actinv-p107-bounds-g1-1" and g1.get("pass") is True
        and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and g1.get("case_count") == 146 and g1.get("target_count") == 150
        and g1.get("endpoint_count") == 300 and g1.get("independent_comparison_count") == 146
        and g1.get("repeat_byte_identical") is True
        and isinstance(mutations, dict) and bool(mutations) and all(x is True for x in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusals.get("checks"), dict) and bool(refusals["checks"])
        and all(x is True for x in refusals["checks"].values()) and g1.get("failures") == []
    )


def _quality_ok(quality: dict) -> tuple[bool, dict]:
    gates, resources = quality.get("gates"), quality.get("resource_limits")
    names_ok = isinstance(gates, dict) and REQUIRED_QUALITY_GATES.issubset(gates)
    gates_ok = names_ok and all(isinstance(g, dict) and g.get("exit_code") == 0 for g in gates.values())
    resource_checks = {key: isinstance(resources, dict) and resources.get(key) == value
                       for key, value in EXPECTED_RESOURCES.items()}
    tests = quality.get("workspace_tests")
    tests_ok = isinstance(tests, dict) and tests.get("failed") == 0 and tests.get("passed", 0) > 0
    rust_sources = quality.get("production_rust_sha256")
    source_hash_checks = {}
    if isinstance(rust_sources, dict) and rust_sources:
        for relative, expected_hash in rust_sources.items():
            path = (ROOT / relative).resolve() if isinstance(relative, str) else ROOT / "__invalid__"
            inside_root = path == ROOT or ROOT in path.parents
            source_hash_checks[relative] = bool(
                isinstance(relative, str) and inside_root and path.is_file()
                and isinstance(expected_hash, str) and _sha(path) == expected_hash
            )
    rust_sources_ok = bool(source_hash_checks) and all(source_hash_checks.values())
    detail = {"required_gate_names_present": names_ok, "all_recorded_gate_exit_codes_zero": gates_ok,
              "resource_limits": resource_checks, "workspace_tests_reported_green": tests_ok,
              "reported_gate_count": len(gates) if isinstance(gates, dict) else 0,
              "production_rust_source_hashes": source_hash_checks,
              "production_rust_source_hashes_match": rust_sources_ok}
    return (quality.get("phase") == "P107" and quality.get("pass") is True and names_ok and gates_ok
            and all(resource_checks.values()) and tests_ok and rust_sources_ok), detail


def _ci_result() -> tuple[bool, str, dict | None]:
    if not CI_PATH.is_file():
        return False, "PENDING", None
    try:
        runs = json.loads(CI_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, "FAIL", {"error": str(error)}
    if not isinstance(runs, list):
        return False, "FAIL", {"error": "CI evidence root must be an array"}
    implementation, implementation_error = _read(IMPLEMENTATION_PATH)
    commit_sha = implementation.get("commit_sha")
    commit_format_ok = (isinstance(commit_sha, str) and len(commit_sha) == 40
                        and all(character in "0123456789abcdef" for character in commit_sha))
    implementation_ok = (
        implementation_error is None
        and implementation.get("schema") == "actinv-p107-implementation-1"
        and commit_format_ok
        and implementation.get("g0_sha256") == _sha(ARTIFACTS["g0_p107_bounds.json"])
        and implementation.get("g3_sha256") == _sha(ARTIFACTS["g3_p107_quality.json"])
    )
    names = {x.get("workflowName") for x in runs if isinstance(x, dict)}
    heads = {x.get("headSha") for x in runs if isinstance(x, dict)}
    records_ok = all(isinstance(x, dict) and x.get("status") == "completed" and x.get("conclusion") == "success"
                     for x in runs)
    sha_bound = implementation_ok and heads == {commit_sha}
    passed = (bool(runs) and REQUIRED_CI_WORKFLOWS.issubset(names) and len(heads) == 1
              and None not in heads and records_ok and sha_bound)
    return passed, "PASS" if passed else "FAIL", {
        "required_workflows": sorted(REQUIRED_CI_WORKFLOWS),
        "observed_workflows": sorted(x for x in names if isinstance(x, str)),
        "single_head_sha": len(heads) == 1 and None not in heads,
        "implementation_record_valid": implementation_ok,
        "implementation_commit_sha": commit_sha if commit_format_ok else None,
        "ci_head_sha_matches_implementation": sha_bound,
        "all_completed_success": records_ok, "pass": passed,
    }


def derive() -> dict:
    loaded = {name: _read(path) for name, path in ARTIFACTS.items()}
    g0, g1, g2, quality = (loaded[name][0] for name in ARTIFACTS)
    errors = {name: error for name, (_, error) in loaded.items() if error is not None}
    p105, p105_error = _read(ROOT / "results/p105_verdict.json")
    p106, p106_error = _read(ROOT / "results/p106_verdict.json")
    if p105_error:
        errors["p105_verdict.json"] = p105_error
    if p106_error:
        errors["p106_verdict.json"] = p106_error
    p105_ok = p105_error is None and p105.get("verdict") == "P105-PASS"
    p106_ok = p106_error is None and p106.get("verdict") == "P106-FAIL" and p106.get("phase") == "P106"
    amendment_ok = g0.get("amendment_sha256") == AMENDMENT_SHA256 and g0.get("amendment_registered") is True
    g0_ok = (
        g0.get("schema") == "actinv-p107-bounds-g0-seal-1" and g0.get("phase") == "P107"
        and g0.get("pass") is True and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True and amendment_ok
        and g0.get("inherited_p106_protocol_sha256") == P106_PROTOCOL_SHA256
        and g0.get("inherited_p106_protocol_registered") is True
        and g0.get("inherited_p106_amendment_sha256") == P106_AMENDMENT_SHA256
        and g0.get("inherited_p106_amendment_registered") is True
        and g0.get("inherited_p106_terminal_failure") is True
        and g0.get("inherited_p105_verdict") == "P105-PASS"
        and g0.get("case_count") == 146 and g0.get("pack_mirror_identical") is True
        and g0.get("seal_json_roundtrip_regression") is True
        and g0.get("frozen_case_fixture_sha256_matches") is True
        and g0.get("inherited_control_hashes_unchanged") is True
        and g0.get("expected_labels", {}).get("pass") is True
    )
    g1_ok = _g1_ok(g1)
    g1_path = ARTIFACTS["g1_p107_bounds.json"]
    g2_ok = (
        g2.get("schema") == "actinv-p107-bounds-g2-1" and g2.get("phase") == "P107"
        and g2.get("pass") is True and g2.get("protocol_sha256") == PROTOCOL_SHA256
        and g2.get("g1_result_sha256") == _sha(g1_path)
        and g2.get("separate_output_paths_byte_identical") is True
    )
    quality_ok, quality_detail = _quality_ok(quality)
    local_pass = p105_ok and p106_ok and g0_ok and g1_ok and g2_ok and quality_ok
    ci_pass, ci_status, ci_detail = _ci_result()
    verdict = "P107-PASS" if local_pass and ci_pass else (
        "P107-LOCAL-PASS" if local_pass and not CI_PATH.exists() else "P107-FAIL"
    )
    evidence = {name: _sha(path) for name, path in ARTIFACTS.items()}
    predecessor_hashes = {
        "p105_verdict.json": _sha(ROOT / "results/p105_verdict.json"),
        "p106_verdict.json": _sha(ROOT / "results/p106_verdict.json"),
        "g0_p106_bounds.json": _sha(ROOT / "results/g0_p106_bounds.json"),
        "g0_p106_replay_failure.json": _sha(ROOT / "results/g0_p106_replay_failure.json"),
    }
    implementation, implementation_error = _read(IMPLEMENTATION_PATH)
    result = {
        "schema": "actinv-p107-verdict-1", "phase": "P107", "repair_rounds": 1,
        "verdict": verdict,
        "gates": {"G0": "PASS" if g0_ok else "FAIL", "G1": "PASS" if g1_ok else "FAIL",
                  "G2": "PASS" if g2_ok else "FAIL", "G3_local": "PASS" if quality_ok else "FAIL",
                  "G3_CI": ci_status},
        "checks": {"predecessor_p105_terminal_pass": p105_ok, "predecessor_p106_terminal_fail": p106_ok,
                   "p107_amendment_registered": amendment_ok, "quality": quality_detail,
                   "ci": ci_detail, "artifact_read_errors": errors},
        "evidence_sha256": {**evidence, **predecessor_hashes},
        "ci_evidence_sha256": _sha(CI_PATH),
        "predecessors": {"P105": "PASS" if p105_ok else "FAIL", "P106": "FAIL" if p106_ok else "UNVERIFIED"},
        "qualification": "Conservative class supersets for declared whole-component rectangular activity bounds under the pinned 10 CFR Part 61.55 rule pack; no uncertainty propagation, upstream completeness qualification, or disposal-acceptance conclusion.",
    }
    if IMPLEMENTATION_PATH.is_file():
        result["implementation_record_sha256"] = _sha(IMPLEMENTATION_PATH)
        result["implementation_commit"] = implementation.get("commit_sha") if implementation_error is None else None
    return result


def main(write: bool) -> int:
    result = derive()
    if write:
        VERDICT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        same = True
    else:
        persisted, error = _read(VERDICT_PATH)
        same = error is None and persisted == result
    passed = result["verdict"] in ("P107-PASS", "P107-LOCAL-PASS")
    print(json.dumps({"verdict": result["verdict"], "gates": result["gates"],
                      "evidence_sha256": result["evidence_sha256"], "persisted_matches": same},
                     indent=2, sort_keys=True))
    return 0 if passed and same else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="persist the derived P107 verdict")
    raise SystemExit(main(parser.parse_args().write))
