#!/usr/bin/env python3
"""Derive P111 from sealed source/control evidence and exact-commit CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA256 = "c2ffdb958682090803702c1de4adf670b3838b87db47eb8312356278199f231f"
VERDICT = ROOT / "results/p111_verdict.json"
CI = ROOT / "results/p111_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p111_implementation_commit.json"
PACK = ROOT / "data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
ARTIFACTS = {name: ROOT / name for name in (
    "results/g0_p111_intrusion_screen.json", "results/g1_p111_intrusion_screen.json",
    "results/g2_p111_intrusion_screen.json", "results/g3_p111_quality.json")}
REQUIRED_GATES = {
    "rust_fmt", "rust_check_all_targets_features", "rust_clippy_all_targets_features",
    "rust_test_all_targets_features", "release_build", "intrusion_oracle_regressions",
    "intrusion_seal_regressions", "control_child_lifecycle", "full_control_read_only_replay",
    "p105_scientific_replay", "p107_scientific_replay", "p108_scientific_replay",
    "p110_scientific_replay", "historical_p107_replay", "historical_p108_replay",
    "historical_p109_replay", "historical_p110_replay", "handbook_build",
    "handbook_links", "handbook_chromium",
}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
                      "Build handbook", "fusion-isotope", "fns-iron"}
RESOURCES = {"memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0,
             "tasks_max": 128, "cpu_quota_percent": 200,
             "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial"}
REQUIRED_SOURCES = {"crates/actinv-cli/src/waste.rs",
                    "crates/actinv-cli/src/waste_intrusion.rs",
                    "crates/actinv-cli/src/lib.rs", "crates/actinv-cli/src/command.rs",
                    "crates/actinv-core/src/waste.rs",
                    "crates/actinv-core/src/waste_intrusion.rs",
                    "crates/actinv-core/src/lib.rs"}
REQUIRED_CONTROLS = {"controls/check_p111.py", "controls/check_p111_verdict.py",
                     "controls/p111_intrusion_control.py", "controls/test_p111_oracle.py",
                     "controls/test_p111_seal.py", "controls/test_p111_verdict.py",
                     "controls/fixtures/p111/cases.json",
                     "controls/fixtures/p111/literal_rows.json",
                     "controls/fixtures/p111/source_excerpt_feb2026.txt",
                     "controls/fixtures/p111/source_excerpt_mar2025.txt",
                     "controls/fixtures/p111/source_reconciliation.json"}


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def count(value: object, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _bound_files(mapping: object, required: set[str]) -> bool:
    if not isinstance(mapping, dict) or not required.issubset(mapping):
        return False
    for name, expected in mapping.items():
        if not isinstance(name, str):
            return False
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or path.as_posix() != name
                or not digest(expected) or sha(ROOT / path) != expected):
            return False
    return True


def _quality(quality: dict, record: dict | None) -> tuple[bool, dict]:
    from check_p107_history import _git_blob, _materialize_history, _safe_rust_path

    gates = quality.get("gates", {})
    gates_ok = (isinstance(gates, dict) and REQUIRED_GATES.issubset(gates)
                and all(isinstance(gate, dict) and type(gate.get("exit_code")) is int
                        and gate["exit_code"] == 0 and digest(gate.get("log_sha256"))
                        and isinstance(gate.get("log"), str) and bool(gate["log"])
                        for gate in gates.values()))
    resources_ok = quality.get("resource_limits") == RESOURCES
    inspection = quality.get("resource_limits_inspection", {})
    resources_ok = (resources_ok and isinstance(inspection, dict)
                    and digest(inspection.get("log_sha256")))
    release = quality.get("release_build", {})
    binary_ok = (isinstance(release, dict) and type(release.get("exit_code")) is int
                 and release["exit_code"] == 0 and digest(release.get("binary_sha256"))
                 and digest(release.get("log_sha256")))
    tests = quality.get("workspace_tests", {})
    tests_ok = (isinstance(tests, dict) and type(tests.get("failed")) is int
                and tests["failed"] == 0 and count(tests.get("passed"), 1))
    hashes = quality.get("production_rust_sha256", {})
    source_ok = False
    historical = False
    detail = {}
    try:
        if not isinstance(hashes, dict) or not REQUIRED_SOURCES.issubset(hashes):
            raise ValueError("missing source hashes")
        for path, expected in hashes.items():
            _safe_rust_path(path)
            if not digest(expected):
                raise ValueError("malformed source hash")
        if record is not None:
            commit = record.get("commit_sha")
            if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                raise ValueError("malformed implementation commit")
            for path in ARTIFACTS.values():
                relative = path.relative_to(ROOT).as_posix()
                if hashlib.sha256(_git_blob(commit, relative)).hexdigest() != sha(path):
                    raise ValueError(f"{relative} differs from implementation tree")
            temporary = ROOT / "target/p111-verdict-history"
            temporary.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="p111-", dir=temporary) as temp:
                _materialize_history(commit, hashes, Path(temp))
            source_ok = True
            historical = True
        else:
            source_ok = all(sha(ROOT / path) == expected for path, expected in hashes.items())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        detail["error"] = str(error)
    detail.update({"required_gate_names_present": gates_ok,
                   "resource_limits_match": resources_ok,
                   "workspace_tests_reported_green": tests_ok,
                   "fresh_release_reported_green": binary_ok,
                   "source_hashes_match": source_ok,
                   "historical_source_verification": historical})
    passed = (quality.get("schema") == "actinv-p111-quality-1"
              and quality.get("phase") == "P111" and quality.get("pass") is True
              and gates_ok and resources_ok and tests_ok and binary_ok and source_ok)
    return passed, detail


def _repair(g0: dict, registry: list[str]) -> bool:
    rounds = g0.get("repair_rounds", 0)
    amendment = ROOT / "protocols/ACTINV-P111_AMENDMENT_A.md"
    if type(rounds) is not int or rounds not in (0, 1):
        return False
    if rounds == 0:
        return not amendment.exists()
    expected = g0.get("repair_amendment_sha256")
    evidence = g0.get("repair_evidence_sha256")
    return (digest(expected) and sha(amendment) == expected
            and f"{expected}  protocols/ACTINV-P111_AMENDMENT_A.md" in registry
            and isinstance(evidence, dict) and bool(evidence)
            and _bound_files(evidence, set()))


def derive() -> dict:
    loaded = [read(path) for path in ARTIFACTS.values()]
    g0, g1, g2, g3 = (value if isinstance(value, dict) else {} for value in loaded)
    record = read(IMPLEMENTATION)
    if not isinstance(record, dict):
        record = None
    registry_path = ROOT / "protocols/protocol_hash.txt"
    registry = registry_path.read_text().splitlines() if registry_path.is_file() else []
    checks = {"protocol": (
        sha(ROOT / "protocols/ACTINV-P111_PROTOCOL.md") == PROTOCOL_SHA256
        and f"{PROTOCOL_SHA256}  protocols/ACTINV-P111_PROTOCOL.md" in registry),
        "repair_policy": _repair(g0, registry)}
    cases = g0.get("case_count")
    targets = g0.get("target_count")
    prior = g0.get("prior_verdict_sha256", {})
    required_prior = {f"results/p{phase}_verdict.json" for phase in (105, 107, 108, 109, 110)}
    checks["prior_verdicts_preserved"] = _bound_files(prior, required_prior)
    checks["prior_dispositions"] = all(
        isinstance(value := read(ROOT / f"results/p{phase}_verdict.json"), dict)
        and value.get("verdict") == f"P{phase}-{'FAIL' if phase == 109 else 'PASS'}"
        for phase in (105, 107, 108, 109, 110))
    checks["G0"] = (
        g0.get("schema") == "actinv-p111-intrusion-screen-g0-1"
        and g0.get("phase") == "P111" and g0.get("pass") is True
        and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True and g0.get("pack_mirror_identical") is True
        and digest(g0.get("pack_sha256")) and g0["pack_sha256"] == sha(PACK) == sha(MIRROR)
        and g0.get("case_fixture_matches") is True and g0.get("historical_p110_verified") is True
        and g0.get("source_review", {}).get("pass") is True
        and count(g0.get("source_rows_checked"), 25) and g0["source_rows_checked"] == 25
        and count(g0.get("column_checks"), 75) and g0["column_checks"] == 75
        and count(cases, 40) and count(targets, 40)
        and _bound_files(g0.get("control_sha256"), REQUIRED_CONTROLS))
    evidence = g1.get("case_evidence", [])
    evidence_ok = (isinstance(evidence, list) and count(cases, 40) and len(evidence) == cases
                   and all(isinstance(case, dict) and isinstance(case.get("id"), str)
                           and bool(case["id"]) and all(digest(case.get(field)) for field in (
                               "input_sha256", "output_sha256", "generated_waste_spec_sha256"))
                           for case in evidence)
                   and len({case["id"] for case in evidence}) == cases)
    mutations = g1.get("mutations_rejected", {})
    refusals = g1.get("refusal_controls", {})
    checks["G1"] = (
        g1.get("schema") == "actinv-p111-intrusion-screen-g1-1"
        and g1.get("phase") == "P111" and g1.get("pass") is True
        and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and count(g1.get("case_count"), 40) and g1["case_count"] == cases
        and count(g1.get("target_count"), 40) and g1["target_count"] == targets
        and count(g1.get("independent_comparison_count"), 40)
        and g1.get("repeat_byte_identical") is True and g1.get("failures") == []
        and evidence_ok and isinstance(mutations, dict) and len(mutations) >= 20
        and all(value is True for value in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusals.get("checks"), dict) and len(refusals["checks"]) >= 30
        and all(value is True for value in refusals["checks"].values()))
    checks["G2"] = (
        g2.get("schema") == "actinv-p111-intrusion-screen-g2-1"
        and g2.get("phase") == "P111" and g2.get("pass") is True
        and g2.get("protocol_sha256") == PROTOCOL_SHA256
        and g2.get("g1_result_sha256") == sha(ROOT / "results/g1_p111_intrusion_screen.json")
        and g2.get("exact_replay_equal") is True
        and g2.get("separate_output_paths_byte_identical") is True)
    checks["G3_local"], quality_detail = _quality(g3, record)
    runs = read(CI)
    ci_pass = False
    if CI.exists() and record is not None and isinstance(runs, list) and runs:
        record_ok = (record.get("schema") == "actinv-p111-implementation-1"
                     and all(record.get(f"g{index}_sha256") == sha(path)
                             for index, path in enumerate(ARTIFACTS.values())))
        names = {run.get("workflowName") for run in runs if isinstance(run, dict)}
        ci_pass = (record_ok and REQUIRED_WORKFLOWS.issubset(names)
                   and all(isinstance(run, dict) and run.get("headSha") == record.get("commit_sha")
                           and run.get("status") == "completed" and run.get("conclusion") == "success"
                           for run in runs))
    local = all(checks.values())
    verdict = ("P111-PASS" if local and ci_pass else
               "P111-LOCAL-PASS" if local and not CI.exists() else "P111-FAIL")
    return {"schema": "actinv-p111-verdict-1", "phase": "P111", "verdict": verdict,
            "repair_rounds": g0.get("repair_rounds", 0),
            "gates": {**{name: "PASS" if value else "FAIL" for name, value in checks.items()},
                      "G3_CI": "PASS" if ci_pass else "FAIL" if CI.exists() else "PENDING"},
            "quality_checks": quality_detail,
            "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
            "ci_evidence_sha256": sha(CI), "implementation_record_sha256": sha(IMPLEMENTATION),
            "implementation_commit": record.get("commit_sha") if record else None,
            "qualification": "Nominal whole-container screening against literal February 2026 draft "
                             "Table 8-5 with explicit conditional presence interpretations and coverage "
                             "outcomes; no disposal acceptance, intrusion dose, legal compliance, "
                             "statistical uncertainty or physical inventory completeness qualification."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = derive()
    if args.write:
        VERDICT.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        same = True
    else:
        same = read(VERDICT) == result
    print(json.dumps({"verdict": result["verdict"], "gates": result["gates"],
                      "persisted_matches": same}, indent=2, sort_keys=True))
    return 0 if same and result["verdict"] in ("P111-LOCAL-PASS", "P111-PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
