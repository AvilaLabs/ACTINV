#!/usr/bin/env python3
"""Derive the P117 CI-cache recovery disposition from sealed evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "controls"))
import check_p116 as p116
import check_p116_history
import check_p116_verdict
import check_p112_verdict
import check_p117
import run_p117_gate

PROTOCOL_SHA256 = check_p117.PROTOCOL_SHA256
IMPLEMENTATION = ROOT / "results/p117_implementation_commit.json"
CI = ROOT / "results/p117_ci_runs.json"
VERDICT = ROOT / "results/p117_verdict.json"
ARTIFACTS = {
    "results/g0_p117_twin_waste.json": check_p117.G0,
    "results/g1_p117_twin_waste.json": check_p117.G1,
    "results/g2_p117_twin_waste.json": check_p117.G2,
    "results/g3_p117_quality.json": check_p117.G3,
}
WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
             "Build handbook", "fusion-isotope", "fns-iron"}


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def _canonical(value: object) -> bytes | None:
    try:
        return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, OverflowError):
        return None


def read(path: Path):
    try:
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink() or not path.is_file():
            return None
        check_p117._safe_file(relative)
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _bound_control_sources(mapping: object) -> bool:
    if not isinstance(mapping, dict) or set(mapping) != set(check_p117.CONTROL_FILES):
        return False
    try:
        for relative, expected in mapping.items():
            if not _digest(expected) or sha(check_p117._safe_file(relative)) != expected:
                return False
    except (OSError, ValueError, TypeError):
        return False
    return True


def _g0_ok(g0: object) -> bool:
    if not isinstance(g0, dict):
        return False
    try:
        fresh = check_p117._g0_base()
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False
    return (_canonical(g0) == _canonical(fresh)
        and g0.get("schema") == "actinv-p117-twin-waste-g0-1"
        and g0.get("phase") == "P117" and g0.get("pass") is True
        and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True
        and g0.get("seed_authorities_match") is True
        and g0.get("historical_p116_verified") is True
        and g0.get("predecessor_p116_fail_preserved") is True
        and g0.get("source_population_matches_p116_commit") is True
        and type(g0.get("inherited_rust_source_count")) is int
        and g0.get("inherited_rust_source_count") == 100
        and isinstance(g0.get("inherited_rust_sha256"), dict)
        and len(g0["inherited_rust_sha256"]) == 100
        and isinstance(g0.get("public_handbook_sha256"), dict)
        and bool(g0["public_handbook_sha256"])
        and g0.get("public_handbook_matches_p116") is True
        and g0.get("seed_file_count") == 13 and g0.get("seed_total_bytes") == 166789318
        and type(g0.get("repair_rounds")) is int and g0.get("repair_rounds") == 0
        and _bound_control_sources(g0.get("control_sha256")))


def _expected_g1() -> dict:
    old = read(check_p117.P116_G1)
    return check_p117._g1_report(old, True)


def _g1_ok(g1: object) -> bool:
    if not isinstance(g1, dict):
        return False
    try:
        old_raw = check_p117.P116_G1.read_bytes()
        expected = _expected_g1()
        persisted_raw = check_p117.G1.read_bytes()
    except (OSError, ValueError, TypeError):
        return False
    return (_canonical(g1) == check_p117.G1.read_bytes()
        and _canonical(expected) == persisted_raw
        and g1.get("schema") == "actinv-p117-twin-waste-g1-1"
        and g1.get("phase") == "P117" and g1.get("pass") is True
        and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and g1.get("p116_g1_sha256") == check_p117.P116_G1_SHA256
        and g1.get("p116_report_preserved") == read(check_p117.P116_G1)
        and g1.get("fresh_p117_report") == read(check_p117.P116_G1)
        and g1.get("exact_canonical_match") is True and g1.get("failures") == []
        and type(g1.get("request_count")) is int and g1["request_count"] == 35
        and type(g1.get("component_target_count")) is int and g1["component_target_count"] == 138
        and type(g1.get("independent_comparison_count")) is int and g1["independent_comparison_count"] == 138
        and g1.get("repeat_byte_identical") is True and _canonical(read(check_p117.P116_G1)) == old_raw)


def _g2_ok(g2: object, g0: dict, g1: dict) -> bool:
    if not isinstance(g2, dict):
        return False
    try:
        persisted_raw = check_p117.G2.read_bytes()
    except OSError:
        return False
    expected = {
        "schema": "actinv-p117-twin-waste-g2-1", "phase": "P117",
        "protocol_sha256": PROTOCOL_SHA256, "pass": True,
        "g0_exact_replay_equal": True, "g1_exact_replay_equal": True,
        "separate_output_paths_byte_identical": True,
        "p116_g1_sha256": check_p117.P116_G1_SHA256,
        "g0_result_sha256": sha(check_p117.G0), "g1_result_sha256": sha(check_p117.G1),
        "fresh_g1_report": read(check_p117.P116_G1),
    }
    return (_canonical(g2) == persisted_raw
            and _canonical(g2) == _canonical(expected)
            and g2.get("pass") is True and g2.get("g0_exact_replay_equal") is True
            and g2.get("g1_exact_replay_equal") is True
            and g2.get("separate_output_paths_byte_identical") is True
            and g2.get("g0_result_sha256") == sha(check_p117.G0)
            and g2.get("g1_result_sha256") == sha(check_p117.G1)
            and g0.get("pass") is True and g1.get("pass") is True)


def _safe_p117_receipt(name: str, entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    try:
        receipt_path = check_p117._safe_file(f"results/quality/p117/{name}.json")
        log_path = check_p117._safe_file(f"results/quality/p117/{name}.log")
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
        log = log_path.read_bytes()
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(receipt, dict):
        return False
    timeout = receipt.get("timeout_s")
    cap = 1200 if name in check_p117.QUALITY_GATES else 600
    return (entry.get("name") == name
        and entry.get("receipt_path") == f"results/quality/p117/{name}.json"
        and entry.get("receipt_sha256") == _sha_bytes(raw)
        and entry.get("log_path") == f"results/quality/p117/{name}.log"
        and entry.get("log_sha256") == _sha_bytes(log) == receipt.get("log_sha256")
        and entry.get("argv") == receipt.get("argv")
        and type(entry.get("exit_code")) is int and entry.get("exit_code") == 0
        and entry.get("resources") == receipt.get("resources")
        and receipt.get("schema") == "actinv-roadmap-gate-receipt-1"
        and receipt.get("phase") == "P117" and receipt.get("gate") == name
        and receipt.get("status") == "completed"
        and type(receipt.get("child_exit_code")) is int and receipt["child_exit_code"] == 0
        and receipt.get("cwd") == "." and receipt.get("log_path") == f"target/p117-{name}.log"
        and receipt.get("error") is None
        and isinstance(receipt.get("argv"), list) and bool(receipt["argv"])
        and all(isinstance(item, str) and item.strip() for item in receipt["argv"])
        and not run_p117_gate._has_placeholder(receipt["argv"])
        and isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
        and math.isfinite(float(timeout)) and 0 < timeout <= cap
        and _resources_ok(receipt.get("resources")))


def _resources_ok(resources: object) -> bool:
    if not isinstance(resources, dict):
        return False
    tmp = resources.get("tmpdir")
    cgroup_path = resources.get("cgroup_path")
    path_parts = cgroup_path.split("/") if isinstance(cgroup_path, str) else []
    return (resources.get("platform") == "linux"
        and isinstance(cgroup_path, str)
        and cgroup_path.startswith("/user.slice/")
        and cgroup_path.endswith(".scope")
        and not any(part in {".", ".."} for part in path_parts)
        and not any(not part for part in path_parts[1:])
        and resources.get("cgroup_limits") == {
            "memory.max": "6442450944", "memory.swap.max": "0",
            "pids.max": "128", "cpu.max": "200000 100000"}
        and resources.get("environment") == {
            "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
        and isinstance(tmp, dict) and tmp.get("path") == "target/preflight-tmp"
        and isinstance(tmp.get("mount_point"), str) and Path(tmp["mount_point"]).is_absolute()
        and isinstance(tmp.get("filesystem"), str) and bool(tmp["filesystem"].strip())
        and tmp["filesystem"].lower() not in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"})


def _handbook_from_commit(commit: str) -> dict[str, str] | None:
    try:
        paths = check_p112_verdict._handbook_paths(commit)
        return {path: _sha_bytes(p116._git_blob(commit, path)) for path in sorted(paths)}
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return None


def _g3_ok(g3: object, g0: dict, implementation_record: dict | None) -> bool:
    if not isinstance(g3, dict):
        return False
    old_g3 = read(check_p117.P116_G3)
    adopted = old_g3.get("gates") if isinstance(old_g3, dict) else None
    fresh = g3.get("fresh_gates")
    if not isinstance(adopted, dict) or len(adopted) != 32 or g3.get("adopted_p116_gates") != adopted:
        return False
    if not isinstance(fresh, dict) or set(fresh) != check_p117.FRESH_GATES:
        return False
    if any(not _safe_p117_receipt(name, entry) for name, entry in fresh.items()):
        return False
    g1 = read(check_p117.G1)
    if not isinstance(g1, dict) or g1.get("pass") is not True:
        return False
    release = old_g3.get("release_build") if isinstance(old_g3, dict) else None
    if not isinstance(release, dict):
        return False
    handbook = _handbook_from_commit(check_p117.IMPLEMENTATION_COMMIT)
    expected_adoption = {
        "quality_sha256": check_p117.P116_G3_SHA256,
        "implementation_commit": check_p117.IMPLEMENTATION_COMMIT,
        "verdict_sha256": check_p117.P116_VERDICT_SHA256,
        "gates": adopted, "gate_names": sorted(adopted), "successful_gate_count": 32,
        "workspace_tests": old_g3.get("workspace_tests"),
        "release_build": release,
        "production_rust_sha256": old_g3.get("production_rust_sha256"),
        "public_handbook_sha256": handbook,
        "quality_validated": True, "not_rerun": True,
    }
    source_map = g0.get("control_sha256")
    return (g3.get("schema") == "actinv-p117-quality-1" and g3.get("phase") == "P117"
        and g3.get("pass") is True and type(g3.get("repair_rounds")) is int and g3.get("repair_rounds") == 0
        and g3.get("resource_limits") == check_p117.RESOURCES
        and g3.get("p116_adopted") == expected_adoption
        and g3.get("fresh_gate_names") == sorted(fresh)
        and g3.get("p116_history_verified") is True
        and g3.get("current_rust_matches_p116") is True
        and g3.get("current_rust_sha256") == old_g3.get("production_rust_sha256")
        and g3.get("source_sha256") == source_map
        and g3.get("p117_science_evidence_matches") is True
        and g3.get("public_handbook_matches_p116") is True
        and g3.get("public_handbook_sha256") == g0.get("public_handbook_sha256")
        and g3.get("qualified_binary_matches_p116") is True
        and isinstance(release.get("binary_sha256"), str)
        and g3.get("qualified_binary_sha256") == release.get("binary_sha256")
        and g3.get("failures") == []
        and g3.get("g0_sha256") == sha(check_p117.G0)
        and g3.get("g1_sha256") == sha(check_p117.G1)
        and g3.get("g2_sha256") == sha(check_p117.G2)
        and g0.get("pass") is True)


def _source_commit_matches(g0: dict, record: dict | None) -> bool:
    if record is None:
        return _bound_control_sources(g0.get("control_sha256"))
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    controls = g0.get("control_sha256")
    if not isinstance(controls, dict) or not controls:
        return False
    try:
        rust_paths = p116._rust_paths_at_commit(commit)
        old_rust = read(check_p117.P116_G3).get("production_rust_sha256")
        current = p116._current_rust_source_hashes()
        return (len(rust_paths) == 100 and isinstance(old_rust, dict) and set(old_rust) == rust_paths
                and current == old_rust
                and all(_sha_bytes(p116._git_blob(commit, path)) == digest
                        for path, digest in old_rust.items())
                and all(_sha_bytes(p116._git_blob(commit, path)) == expected
                        for path, expected in controls.items()))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False


def _implementation_ok(record: object, g0: dict, g1: dict, g2: dict, g3: dict) -> bool:
    if not isinstance(record, dict) or record.get("schema") != "actinv-p117-implementation-1":
        return False
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    artifact_hashes = {
        "g0_sha256": check_p117.G0, "g1_sha256": check_p117.G1,
        "g2_sha256": check_p117.G2, "g3_sha256": check_p117.G3,
    }
    if not all(record.get(key) == sha(path) for key, path in artifact_hashes.items()):
        return False
    try:
        for key, path in artifact_hashes.items():
            relative = path.relative_to(ROOT).as_posix()
            if _sha_bytes(p116._git_blob(commit, relative)) != sha(path):
                return False
    except (OSError, ValueError, RuntimeError, KeyError, ImportError):
        return False
    return (record.get("g0_sha256") == sha(check_p117.G0)
        and record.get("g1_sha256") == sha(check_p117.G1)
        and record.get("g2_sha256") == sha(check_p117.G2)
        and record.get("g3_sha256") == sha(check_p117.G3)
        and _source_commit_matches(g0, record))


def _ci_state(record_ok: bool, record: dict | None, runs: object, exists: bool) -> tuple[bool, bool]:
    if not exists:
        return record_ok, False
    if not record_ok or not isinstance(record, dict) or not isinstance(runs, list) or len(runs) != 6:
        return False, False
    if any(not isinstance(row, dict) for row in runs):
        return False, False
    names = [row.get("workflowName") if isinstance(row, dict) else None for row in runs]
    if any(not isinstance(name, str) for name in names):
        return False, False
    if len(set(names)) != 6 or set(names) != WORKFLOWS:
        return False, False
    commit = record.get("commit_sha")
    run_ids = []
    for row in runs:
        run_id = row.get("databaseId")
        if (type(run_id) is not int or run_id <= 0
                or row.get("url") != f"https://github.com/AvilaLabs/ACTINV/actions/runs/{run_id}"):
            return False, False
        run_ids.append(run_id)
    if len(set(run_ids)) != 6:
        return False, False
    passed = all(row.get("headSha") == commit and row.get("status") == "completed"
                 and row.get("conclusion") == "success" for row in runs)
    return passed, passed


def derive() -> dict:
    g0, g1, g2, g3 = (read(path) for path in ARTIFACTS.values())
    g0 = g0 if isinstance(g0, dict) else {}
    g1 = g1 if isinstance(g1, dict) else {}
    g2 = g2 if isinstance(g2, dict) else {}
    g3 = g3 if isinstance(g3, dict) else {}
    record, runs = read(IMPLEMENTATION), read(CI)
    record_exists = IMPLEMENTATION.exists() or IMPLEMENTATION.is_symlink()
    ci_exists = CI.exists() or CI.is_symlink()
    record_ok = (not record_exists and _source_commit_matches(g0, None)) or (
        isinstance(record, dict) and _implementation_ok(record, g0, g1, g2, g3))
    checks = {
        "protocol": sha(ROOT / check_p117.PROTOCOL) == PROTOCOL_SHA256,
        "historical_p116_ci_failure": check_p116_history.verify().get("pass") is True,
        "G0": _g0_ok(g0), "G1": _g1_ok(g1), "G2": _g2_ok(g2, g0, g1),
        "G3_local": _g3_ok(g3, g0, record if isinstance(record, dict) else None),
        "source_commit": record_ok and _source_commit_matches(g0, record if isinstance(record, dict) else None),
    }
    ci_well_formed, ci_pass = _ci_state(record_ok, record if isinstance(record, dict) else None,
                                        runs, ci_exists)
    checks["CI_evidence_consistent"] = ci_well_formed
    local = all(checks.values())
    return {
        "schema": "actinv-p117-verdict-1", "phase": "P117",
        "verdict": "P117-PASS" if local and ci_pass else "P117-LOCAL-PASS" if local else "P117-FAIL",
        "gates": {name: "PASS" if passed else "FAIL" for name, passed in checks.items()}
            | {"G3_CI": "PASS" if ci_pass else "FAIL" if ci_exists else "PENDING"},
        "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
        "implementation_record_sha256": sha(IMPLEMENTATION), "ci_evidence_sha256": sha(CI),
        "implementation_commit": record.get("commit_sha") if isinstance(record, dict) else None,
        "p116_terminal_verdict_sha256": check_p117.P116_VERDICT_SHA256,
        "historical_source_verification": bool(record_exists and record_ok and checks["source_commit"] and ci_pass),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    result = derive()
    if args.write:
        VERDICT.write_bytes(_canonical(result) or b"")
        same = True
    else:
        same = read(VERDICT) == result
    print(json.dumps({"verdict": result["verdict"], "gates": result["gates"],
                      "persisted_matches": same}, sort_keys=True, indent=2))
    return 0 if same and result["verdict"] in {"P117-LOCAL-PASS", "P117-PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
