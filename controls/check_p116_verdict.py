#!/usr/bin/env python3
"""Derive P116 disposition from its sealed scientific and durable quality evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P116_PROTOCOL.md"
PROTOCOL_SHA256 = "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa"
CHECKPOINT = "6db45f75b96fbcdfd2bfda0f6a603c10fbf88672"
VERDICT = ROOT / "results/p116_verdict.json"
CI = ROOT / "results/p116_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p116_implementation_commit.json"
ARTIFACTS = {
    "results/g0_p116_twin_waste.json": ROOT / "results/g0_p116_twin_waste.json",
    "results/g1_p116_twin_waste.json": ROOT / "results/g1_p116_twin_waste.json",
    "results/g2_p116_twin_waste.json": ROOT / "results/g2_p116_twin_waste.json",
    "results/g3_p116_quality.json": ROOT / "results/g3_p116_quality.json",
}
RESOURCES = {"memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0,
             "tasks_max": 128, "cpu_quota_percent": 200,
             "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial"}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
                      "Build handbook", "fusion-isotope", "fns-iron"}
QUALITY_GATES = {"rust_fmt", "workspace_check", "workspace_clippy", "workspace_tests",
                 "release_build", "handbook_build", "handbook_links", "handbook_chromium"}
REQUIRED_GATES = {
    "rust_fmt", "workspace_check", "workspace_clippy", "workspace_tests", "release_build",
    "p116_oracle_regressions", "p116_seal_regressions", "p116_verdict_regressions",
    "p113_history_regressions", "p114_history_regressions", "p115_history_regressions", "gate_recorder_regressions",
    "p105_child_lifecycle_regressions",
    "historical_p107_replay", "historical_p108_replay", "historical_p109_replay",
    "historical_p110_replay", "historical_p111_replay", "historical_p112_replay",
    "historical_p113_replay", "historical_p114_replay", "historical_p115_replay", "p105_scientific_replay", "p107_scientific_replay",
    "p108_scientific_replay", "p110_scientific_replay", "p111_scientific_replay",
    "p112_scientific_replay", "full_p116_read_only_replay", "handbook_build",
    "handbook_links", "handbook_chromium",
}
PRIOR_VERDICTS = {"P105": "P105-PASS", "P107": "P107-PASS", "P108": "P108-PASS",
                  "P109": "P109-FAIL", "P110": "P110-PASS", "P111": "P111-FAIL",
                  "P112": "P112-PASS", "P113": "P113-FAIL", "P114": "P114-FAIL",
                  "P115": "P115-FAIL"}
EXPECTED_G0_SCHEMA = "actinv-p116-twin-waste-g0-1"
EXPECTED_G1_SCHEMA = "actinv-p116-twin-waste-g1-1"
EXPECTED_G2_SCHEMA = "actinv-p116-twin-waste-g2-1"
EXPECTED_G3_SCHEMA = "actinv-p116-quality-1"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes | None:
    try:
        return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None


def sha(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a nonempty string")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts
            or "\\" in value or path.as_posix() != value):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path


def _bound_files(mapping: object, required: set[str]) -> bool:
    if not isinstance(mapping, dict) or not required.issubset(mapping):
        return False
    root = ROOT.resolve()
    for raw_path, expected in mapping.items():
        try:
            relative = _safe_rel(raw_path)
            path = _safe_regular_file(relative)
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            return False
        if not digest(expected) or sha(path) != expected:
            return False
    return True


def _safe_regular_file(relative: PurePosixPath) -> Path:
    path = ROOT / relative
    resolved = path.resolve(strict=True)
    resolved.relative_to(ROOT.resolve(strict=True))
    cursor = ROOT
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink in evidence path")
    if not path.is_file():
        raise ValueError("evidence path is not a regular file")
    return path


def _prior_ok(g0: dict) -> bool:
    details = g0.get("prior_verdict_details")
    hashes = g0.get("prior_verdict_sha256")
    if not isinstance(details, dict) or not isinstance(hashes, dict):
        return False
    for phase, verdict in PRIOR_VERDICTS.items():
        rel = f"results/{phase.lower()}_verdict.json"
        current = read(ROOT / rel)
        if (not isinstance(details.get(phase), dict) or _canonical(details[phase]) != _canonical(current)
                or details[phase].get("verdict") != verdict
                or not digest(hashes.get(rel)) or hashes[rel] != sha(ROOT / rel)):
            return False
    return True


def _history_ok(module_name: str) -> bool:
    try:
        module = __import__(module_name)
        result = module.verify()
        return isinstance(result, dict) and result.get("pass") is True
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _g0_ok(g0: dict) -> bool:
    try:
        import check_p116

        independently_derived = check_p116._g0_base(verify_current_sources=False)
        fixture = check_p116._fixture_check()
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False
    g0_bytes = _canonical(g0)
    expected_bytes = _canonical(independently_derived)
    return (g0_bytes is not None and expected_bytes is not None and g0_bytes == expected_bytes
        and g0.get("schema") == EXPECTED_G0_SCHEMA
        and g0.get("phase") == "P116" and g0.get("pass") is True
        and g0.get("protocol_sha256") == PROTOCOL_SHA256 and sha(PROTOCOL) == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True and g0.get("fixture_check") == fixture
        and fixture.get("pass") is True and fixture.get("fixture_sha256") ==
            "e6bcebc856c7faaa571455a45c81da49e988a34bfd1f7149bc67656d93d5f956"
        and type(g0.get("request_count")) is int and g0.get("request_count") == 35
        and type(g0.get("component_target_count")) is int and g0.get("component_target_count") == 138
        and g0.get("historical_p113_verified") is True and _history_ok("check_p113_history")
        and g0.get("historical_p114_verified") is True and _history_ok("check_p114_history")
        and g0.get("historical_p115_verified") is True and _history_ok("check_p115_history")
        and g0.get("p115_failure_record_matches") is True
        and g0.get("p115_failure_verdict_sha256") == "4e09b66d5b1c74cafecdc278d9d5ce786292007a7dea0ab6e8c8588f7255faac"
        and g0.get("p115_failure_g3_sha256") == "9f48cde1106ebc7174961aa594fbf57e5c772c3aeed3a06a133558a6946aa45d"
        and isinstance(g0.get("p115_initial_archive_sha256"), dict)
        and len(g0["p115_initial_archive_sha256"]) == 68
        and isinstance(g0.get("p115_amended_archive_sha256"), dict)
        and len(g0["p115_amended_archive_sha256"]) == 68
        and isinstance(g0.get("p115_amended_source_sha256"), dict)
        and len(g0["p115_amended_source_sha256"]) == 182
        and g0.get("p115_unexecuted_artifacts_absent") is True
        and g0.get("p114_failure_record_matches") is True
        and isinstance(g0.get("p114_initial_archive_sha256"), dict)
        and len(g0["p114_initial_archive_sha256"]) == 176
        and isinstance(g0.get("p114_final_archive_sha256"), dict)
        and len(g0["p114_final_archive_sha256"]) == 196
        and type(g0.get("repair_rounds")) is int and g0.get("repair_rounds") == 1
        and check_p116._repair_policy(g0, independently_derived.get("p116_repair_evidence"))
        and type(g0.get("inherited_rust_source_count")) is int
        and g0.get("inherited_rust_source_count") == 100
        and g0.get("inherited_rust_population_matches") is False
        and type(g0.get("production_rust_source_count")) is int
        and g0.get("production_rust_source_count") == 100
        and g0.get("production_rust_population_allowed") is True
        and _production_population_ok(g0.get("production_rust_sha256"),
                                      g0.get("inherited_rust_sha256"))
        and _prior_ok(g0))


def _g1_ok(g1: dict, g0: dict) -> bool:
    evidence = g1.get("request_evidence")
    if (not isinstance(evidence, list) or len(evidence) != 35
            or any(not isinstance(row, dict) for row in evidence)):
        return False
    ids = [row.get("id") for row in evidence]
    counts = [row.get("component_target_count") for row in evidence]
    if (any(not isinstance(item, str) or not item for item in ids)
            or any(not digest(row.get(key)) for row in evidence
                   for key in ("input_sha256", "output_sha256", "ordinary_waste_sha256"))
            or len(set(ids)) != 35 or any(type(count) is not int or count <= 0 for count in counts)):
        return False
    mutations = g1.get("mutations_rejected")
    refusals = g1.get("refusal_controls")
    refusal_checks = refusals.get("checks") if isinstance(refusals, dict) else None
    return (g1.get("schema") == EXPECTED_G1_SCHEMA and g1.get("phase") == "P116"
        and g1.get("pass") is True and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and type(g1.get("request_count")) is int and g1["request_count"] == 35
        and type(g1.get("component_target_count")) is int and g1["component_target_count"] == 138
        and type(g1.get("independent_comparison_count")) is int and g1["independent_comparison_count"] == 138
        and g1.get("failures") == [] and g1.get("repeat_byte_identical") is True
        and ids == g0.get("request_ids") and counts == g0.get("case_targets") and sum(counts) == 138
        and isinstance(mutations, dict) and len(mutations) >= 30 and all(value is True for value in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusal_checks, dict) and len(refusal_checks) >= 30
        and all(value is True for value in refusal_checks.values()))


def _g2_ok(g2: dict) -> bool:
    return (g2.get("schema") == EXPECTED_G2_SCHEMA and g2.get("phase") == "P116"
            and g2.get("pass") is True and g2.get("protocol_sha256") == PROTOCOL_SHA256
            and g2.get("g0_exact_replay_equal") is True and g2.get("exact_replay_equal") is True
            and g2.get("separate_output_paths_byte_identical") is True
            and g2.get("g0_result_sha256") == sha(ROOT / "results/g0_p116_twin_waste.json")
            and g2.get("g1_result_sha256") == sha(ROOT / "results/g1_p116_twin_waste.json"))


def _receipt_ok(gate_name: str, gate: object) -> bool:
    if not isinstance(gate, dict):
        return False
    receipt_path_value = gate.get("receipt_path")
    try:
        relative = _safe_rel(receipt_path_value)
        path = _safe_regular_file(relative)
    except (OSError, ValueError):
        return False
    if sha(path) != gate.get("receipt_sha256"):
        return False
    receipt = read(path)
    argv = gate.get("argv")
    resources = gate.get("resources")
    if not isinstance(receipt, dict) or not isinstance(argv, list) or not argv:
        return False
    if (receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != "P116" or receipt.get("gate") != gate_name
            or receipt.get("argv") != argv or receipt.get("status") != "completed"
            or type(receipt.get("child_exit_code")) is not int or receipt["child_exit_code"] != 0
            or type(gate.get("exit_code")) is not int or gate["exit_code"] != 0
            or gate["exit_code"] != receipt["child_exit_code"]
            or receipt.get("log_sha256") != gate.get("log_sha256")
            or not digest(gate.get("log_sha256")) or not isinstance(gate.get("log_path"), str)
            or not isinstance(resources, dict) or receipt.get("resources") != resources
            or receipt.get("error") is not None):
        return False
    if any(not isinstance(part, str) or not part or "undefined" in part.lower() or "none" == part.lower()
           for part in argv):
        return False
    try:
        if receipt.get("cwd") != ".":
            return False
        _safe_rel(receipt.get("log_path"))
        gate_log_rel = _safe_rel(gate.get("log_path"))
    except ValueError:
        return False
    try:
        log_path = _safe_regular_file(gate_log_rel)
    except (OSError, ValueError):
        return False
    expected_receipt_log = f"target/p116-{gate_name}.log"
    expected_gate_log = f"results/quality/p116/{gate_name}.log"
    if (sha(log_path) != gate.get("log_sha256")
            or receipt.get("log_path") != expected_receipt_log
            or receipt_path_value != f"results/quality/p116/{gate_name}.json"
            or gate.get("log_path") != expected_gate_log):
        return False
    timeout = receipt.get("timeout_s")
    max_timeout = 1200 if gate_name in QUALITY_GATES else 600
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < timeout <= max_timeout or not math.isfinite(float(timeout))):
        return False
    observed = resources.get("cgroup_limits")
    environment = resources.get("environment")
    tmpdir = resources.get("tmpdir")
    cgroup_path = resources.get("cgroup_path")
    return (resources.get("platform") == "linux"
        and isinstance(cgroup_path, str) and cgroup_path.startswith("/user.slice/")
        and cgroup_path.endswith(".scope")
        and isinstance(observed, dict)
        and observed.get("memory.max") == "6442450944"
        and observed.get("memory.swap.max") == "0"
        and observed.get("pids.max") == "128"
        and observed.get("cpu.max") == "200000 100000"
        and environment == {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1",
                            "RAYON_NUM_THREADS": "2"}
        and isinstance(tmpdir, dict) and tmpdir.get("path") == "target/preflight-tmp"
        and isinstance(tmpdir.get("mount_point"), str)
        and Path(tmpdir["mount_point"]).is_absolute()
        and isinstance(tmpdir.get("filesystem"), str)
        and tmpdir["filesystem"].lower() not in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"})


def _quality_ok(g3: dict, g0: dict) -> bool:
    gates = g3.get("gates")
    tests = g3.get("workspace_tests")
    release = g3.get("release_build")
    inspection = g3.get("resource_inspection")
    source_hashes = g3.get("production_rust_sha256")
    return (g3.get("schema") == EXPECTED_G3_SCHEMA and g3.get("phase") == "P116"
        and g3.get("pass") is True and type(g3.get("repair_rounds")) is int
        and g3.get("repair_rounds") == g0.get("repair_rounds")
        and type(g0.get("repair_rounds")) is int and g0.get("repair_rounds") == 1
        and g3.get("resource_limits") == RESOURCES
        and isinstance(gates, dict) and set(gates) == REQUIRED_GATES
        and all(isinstance(gate, dict) and gate.get("name") == name and _receipt_ok(name, gate)
                for name, gate in gates.items())
        and isinstance(tests, dict) and type(tests.get("passed")) is int and tests["passed"] > 474
        and type(tests.get("failed")) is int and tests["failed"] == 0
        and type(tests.get("ignored")) is int and tests["ignored"] == 2
        and isinstance(release, dict) and type(release.get("exit_code")) is int
        and release.get("exit_code") == 0 and digest(release.get("binary_sha256"))
        and digest(release.get("log_sha256"))
        and isinstance(gates.get("release_build"), dict)
        and release.get("log_sha256") == gates["release_build"].get("log_sha256")
        and _resource_inspection_ok(inspection)
        and isinstance(source_hashes, dict) and len(source_hashes) == 100
        and source_hashes == g0.get("production_rust_sha256")
        and _production_population_ok(source_hashes, g0.get("inherited_rust_sha256")))


def _resource_inspection_ok(inspection: object) -> bool:
    if not isinstance(inspection, dict) or type(inspection.get("exit_code")) is not int:
        return False
    expected_limits = {"memory.max": "6442450944", "memory.swap.max": "0",
                       "pids.max": "128", "cpu.max": "200000 100000"}
    expected_path = "results/quality/p116/resource_inspection.log"
    try:
        relative = _safe_rel(inspection.get("log_path"))
        path = _safe_regular_file(relative)
    except (OSError, ValueError):
        return False
    return (inspection.get("exit_code") == 0 and inspection.get("log_path") == expected_path
        and digest(inspection.get("log_sha256")) and sha(path) == inspection.get("log_sha256")
        and inspection.get("limits") == expected_limits)


def _production_population_ok(hashes: object, inherited: object) -> bool:
    """Amendment A allows exactly the twin parser change in the 100-file tree."""
    if (not isinstance(hashes, dict) or not isinstance(inherited, dict)
            or len(hashes) != 100 or set(hashes) != set(inherited)
            or not all(digest(value) for value in (*hashes.values(), *inherited.values()))):
        return False
    changed = {path for path in hashes if hashes[path] != inherited[path]}
    return changed == {"crates/actinv-cli/src/twin_waste.rs"}


def _source_commit_matches(g3: dict, record: dict | None) -> bool:
    hashes = g3.get("production_rust_sha256")
    if not isinstance(hashes, dict) or len(hashes) != 100:
        return False
    if record is None:
        try:
            from check_p116 import _current_rust_source_hashes, _checkpoint_source_hashes

            return (hashes == _current_rust_source_hashes()
                    and _production_population_ok(hashes, _checkpoint_source_hashes()))
        except (ImportError, OSError, ValueError, RuntimeError, KeyError):
            return False
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    try:
        from check_p116 import _rust_paths_at_commit, _git_blob, _checkpoint_source_hashes

        if (set(hashes) != _rust_paths_at_commit(commit)
                or set(hashes) != set(_checkpoint_source_hashes())):
            return False
        checkpoint_hashes = _checkpoint_source_hashes()
        return (_production_population_ok(hashes, checkpoint_hashes)
                and all(_sha_bytes(_git_blob(commit, path)) == expected
                        for path, expected in hashes.items()))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError):
        return False


def _ci_state(record_ok: bool, record: object, runs: object, exists: bool) -> tuple[bool, bool]:
    if not exists:
        return record_ok, False
    if not record_ok or not isinstance(record, dict) or not isinstance(runs, list) or len(runs) != 6:
        return False, False
    if any(not isinstance(run, dict) for run in runs):
        return False, False
    names = [run.get("workflowName") for run in runs]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != 6 or set(names) != REQUIRED_WORKFLOWS:
        return False, False
    commit = record.get("commit_sha")
    for run in runs:
        run_id = run.get("databaseId")
        if (type(run_id) is not int or run_id <= 0
                or run.get("url") != f"https://github.com/AvilaLabs/ACTINV/actions/runs/{run_id}"):
            return False, False
    passed = all(run.get("headSha") == commit and run.get("status") == "completed"
                 and run.get("conclusion") == "success" for run in runs)
    return passed, passed


def _implementation_record_ok(record: object) -> bool:
    if not isinstance(record, dict) or record.get("schema") != "actinv-p116-implementation-1":
        return False
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    try:
        from check_p116 import _git_blob

        return all(record.get(f"g{index}_sha256") == sha(path)
                   and _sha_bytes(_git_blob(commit, relative)) == sha(path)
                   for index, (relative, path) in enumerate(ARTIFACTS.items()))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError):
        return False


def derive() -> dict:
    g0, g1, g2, g3 = (read(path) for path in ARTIFACTS.values())
    g0 = g0 if isinstance(g0, dict) else {}
    g1 = g1 if isinstance(g1, dict) else {}
    g2 = g2 if isinstance(g2, dict) else {}
    g3 = g3 if isinstance(g3, dict) else {}
    checks = {"protocol": sha(PROTOCOL) == PROTOCOL_SHA256,
              "historical_p113_failure": _history_ok("check_p113_history"),
              "historical_p114_failure": _history_ok("check_p114_history"),
              "historical_p115_failure": _history_ok("check_p115_history"),
              "G0": _g0_ok(g0), "G1": _g1_ok(g1, g0), "G2": _g2_ok(g2),
              "G3_local": _quality_ok(g3, g0)}
    record = read(IMPLEMENTATION)
    if record is not None and not isinstance(record, dict):
        record = None
    record_exists = IMPLEMENTATION.exists() or IMPLEMENTATION.is_symlink()
    record_ok = not record_exists or _implementation_record_ok(record)
    checks["source_commit"] = record_ok and _source_commit_matches(g3, record if isinstance(record, dict) else None)
    runs = read(CI)
    ci_exists = CI.exists() or CI.is_symlink()
    ci_well_formed, ci_pass = _ci_state(record_ok, record, runs, ci_exists)
    checks["CI_evidence_consistent"] = ci_well_formed
    local = all(checks.values())
    result = {"schema": "actinv-p116-verdict-1", "phase": "P116",
        "verdict": "P116-PASS" if local and ci_pass else "P116-LOCAL-PASS" if local else "P116-FAIL",
        "gates": {name: "PASS" if okay else "FAIL" for name, okay in checks.items()} |
            {"G3_CI": "PASS" if ci_pass else "FAIL" if ci_exists else "PENDING"},
        "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
        "implementation_record_sha256": sha(IMPLEMENTATION), "ci_evidence_sha256": sha(CI),
        "implementation_commit": record.get("commit_sha") if isinstance(record, dict) else None,
        "historical_source_verification": bool(record_exists and record_ok and checks["source_commit"] and ci_pass)}
    return result


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
                      "persisted_matches": same}, sort_keys=True, indent=2))
    return 0 if same and result["verdict"] in ("P116-LOCAL-PASS", "P116-PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
