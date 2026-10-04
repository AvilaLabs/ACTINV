#!/usr/bin/env python3
"""Derive P113 disposition from sealed twin-waste and quality evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA256 = "0495157f3e8308d6a28475b94363e24b932f462c10f901518fa1c809a68ebbfa"
PROTOCOL = ROOT / "protocols/ACTINV-P113_PROTOCOL.md"
VERDICT = ROOT / "results/p113_verdict.json"
CI = ROOT / "results/p113_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p113_implementation_commit.json"
ARTIFACTS = {
    "results/g0_p113_twin_waste.json": ROOT / "results/g0_p113_twin_waste.json",
    "results/g1_p113_twin_waste.json": ROOT / "results/g1_p113_twin_waste.json",
    "results/g2_p113_twin_waste.json": ROOT / "results/g2_p113_twin_waste.json",
    "results/g3_p113_quality.json": ROOT / "results/g3_p113_quality.json",
}
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
RESOURCES = {"memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0,
             "tasks_max": 128, "cpu_quota_percent": 200,
             "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial"}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
                      "Build handbook", "fusion-isotope", "fns-iron"}
REQUIRED_GATES = {
    "rust_fmt", "workspace_check", "workspace_clippy", "workspace_tests", "release_build",
    "p113_oracle_regressions", "p113_seal_regressions", "p113_verdict_regressions",
    "p105_child_lifecycle_regressions", "historical_p107_replay", "historical_p108_replay",
    "historical_p109_replay", "historical_p110_replay", "historical_p111_replay",
    "historical_p112_replay", "p105_scientific_replay", "p107_scientific_replay",
    "p108_scientific_replay", "p110_scientific_replay", "p111_scientific_replay",
    "p112_scientific_replay", "full_p113_read_only_replay", "handbook_build",
    "handbook_links", "handbook_chromium",
}
SOURCE_PHASES = ("P105", "P107", "P108", "P109", "P110", "P111", "P112")
PRIOR_VERDICTS = {"P105": "P105-PASS", "P107": "P107-PASS", "P108": "P108-PASS",
                  "P109": "P109-FAIL", "P110": "P110-PASS", "P111": "P111-FAIL",
                  "P112": "P112-PASS"}


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
            path = ROOT / relative
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            return False
        if not digest(expected) or sha(path) != expected:
            return False
    return True


def _git_blob(commit: str, path: str) -> bytes:
    from check_p107_history import _git_blob as read_blob
    return read_blob(commit, path, root=ROOT)


def _rust_source_paths(commit: str | None) -> set[str]:
    from p105_budget_control import _run

    command = (["git", "ls-tree", "-r", "--full-tree", commit, "--", "crates"] if commit is not None
               else ["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    result = _run(command, cwd=ROOT, timeout_s=120)
    if result.returncode != 0:
        raise ValueError("cannot enumerate Rust source population")
    paths = set()
    for raw in result.stdout.splitlines():
        if commit is None:
            path = _safe_rel(raw).as_posix()
            if path.startswith("crates/") and path.endswith(".rs"):
                physical = ROOT / path
                if physical.is_symlink() or not physical.is_file():
                    raise ValueError(f"Rust source is not a regular file: {path}")
                physical.resolve(strict=True).relative_to(ROOT.resolve())
                paths.add(path)
        else:
            try:
                metadata, raw_path = raw.split("\t", 1)
                mode, kind, _object_id = metadata.split()
                path = _safe_rel(raw_path).as_posix()
            except (ValueError, TypeError) as error:
                raise ValueError("malformed Git tree entry") from error
            if path.startswith("crates/") and path.endswith(".rs"):
                if kind != "blob" or mode not in {"100644", "100755"}:
                    raise ValueError(f"Rust source is not a regular Git blob: {path}")
                paths.add(path)
    if not paths:
        raise ValueError("Rust source population is empty")
    return paths


def _implementation_record_ok(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    commit = record.get("commit_sha")
    if (record.get("schema") != "actinv-p113-implementation-1"
            or not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or any(record.get(f"g{index}_sha256") != sha(path)
                   for index, path in enumerate(ARTIFACTS.values()))):
        return False
    try:
        return all(_sha_bytes(_git_blob(commit, path.relative_to(ROOT).as_posix())) == sha(path)
                   for path in ARTIFACTS.values())
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


def _ci_state(record_valid: bool, record: object, runs: object,
              ci_exists: bool) -> tuple[bool, bool]:
    """Return (evidence_well_formed, all_required_workflows_green).

    No CI file means pending and is compatible with local qualification. A
    present but malformed or incomplete CI file is a failed evidence state.
    """
    if not ci_exists:
        return record_valid, False
    if not record_valid or not isinstance(record, dict) or not isinstance(runs, list) or not runs:
        return False, False
    if any(not isinstance(run, dict) for run in runs):
        return False, False
    names_list = [run.get("workflowName") for run in runs]
    if (len(runs) != len(REQUIRED_WORKFLOWS)
            or any(not isinstance(name, str) for name in names_list)
            or len(set(names_list)) != len(REQUIRED_WORKFLOWS)):
        return False, False
    for run in runs:
        database_id = run.get("databaseId")
        url = run.get("url")
        if (type(database_id) is not int or database_id <= 0
                or not isinstance(url, str)
                or url != f"https://github.com/AvilaLabs/ACTINV/actions/runs/{database_id}"):
            return False, False
    commit = record.get("commit_sha")
    names = set(names_list)
    passed = (REQUIRED_WORKFLOWS.issubset(names)
              and all(run.get("headSha") == commit and run.get("status") == "completed"
                      and run.get("conclusion") == "success" for run in runs))
    return passed, passed


def _strict_gate_map(g3: dict) -> bool:
    gates = g3.get("gates")
    if not isinstance(gates, dict) or not REQUIRED_GATES.issubset(gates):
        return False
    for name, gate in gates.items():
        if (not isinstance(gate, dict) or gate.get("name") != name
                or type(gate.get("exit_code")) is not int or gate["exit_code"] != 0
                or not digest(gate.get("log_sha256"))
                or not isinstance(gate.get("log"), str) or not gate["log"]):
            return False
        try:
            _safe_rel(gate["log"])
        except ValueError:
            return False
    return True


def _repair_policy(g0: dict) -> bool:
    rounds = g0.get("repair_rounds")
    amendment = ROOT / "protocols/ACTINV-P113_AMENDMENT_A.md"
    registry_path = ROOT / "protocols/protocol_hash.txt"
    registry = registry_path.read_text(encoding="utf-8").splitlines() if registry_path.is_file() else []
    if type(rounds) is not int or rounds not in (0, 1):
        return False
    if rounds == 0:
        return not amendment.exists()
    amendment_sha = g0.get("repair_amendment_sha256")
    failures = g0.get("repair_evidence_sha256")
    return (digest(amendment_sha) and sha(amendment) == amendment_sha
            and f"{amendment_sha}  protocols/ACTINV-P113_AMENDMENT_A.md" in registry
            and isinstance(failures, dict) and bool(failures)
            and _bound_files(failures, set()))


def _repair_metadata_ok(g0: dict) -> bool:
    """Re-derive Amendment A's exact retained-evidence binding for round one."""
    if type(g0.get("repair_rounds")) is not int or g0["repair_rounds"] != 1:
        return False
    try:
        import check_p113

        retained, matches = check_p113._repair_evidence()
        return (
            matches is True
            and g0.get("repair_amendment_sha256") == check_p113.AMENDMENT_SHA256
            and sha(check_p113.AMENDMENT) == check_p113.AMENDMENT_SHA256
            and g0.get("repair_amendment_registered") is True
            and check_p113._amendment_registered() is True
            and g0.get("repair_discovery_sha256") == check_p113.DISCOVERY_SHA256
            and sha(check_p113.DISCOVERY) == check_p113.DISCOVERY_SHA256
            and g0.get("repair_evidence_sha256") == retained
            and g0.get("repair_evidence_matches") is True
            and g0.get("initial_g0_sha256") == check_p113.INITIAL_G0_SHA256
            and g0.get("original_fixture_sha256") == check_p113.FIXTURE_SHA256
            and sha(check_p113.FIXTURE) == check_p113.FIXTURE_SHA256
        )
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _g0_ok(g0: dict) -> bool:
    prior_details = g0.get("prior_verdict_details")
    prior_hashes = g0.get("prior_verdict_sha256")
    prior_ok = (isinstance(prior_details, dict) and isinstance(prior_hashes, dict)
                and all(isinstance(prior_details.get(phase), dict)
                        and prior_details[phase].get("verdict") == verdict
                        and prior_details[phase] == read(
                            ROOT / f"results/{phase.lower()}_verdict.json")
                        and digest(prior_hashes.get(f"results/{phase.lower()}_verdict.json"))
                        and sha(ROOT / f"results/{phase.lower()}_verdict.json")
                        == prior_hashes[f"results/{phase.lower()}_verdict.json"]
                        for phase, verdict in PRIOR_VERDICTS.items()))
    if not prior_ok:
        return False
    try:
        from check_p113 import _fixture_check

        fixture_check = _fixture_check()
    except (ImportError, OSError, ValueError, TypeError, KeyError, RuntimeError):
        return False
    if (not fixture_check.get("pass") or g0.get("fixture_check") != fixture_check
            or g0.get("request_ids") != fixture_check.get("request_ids")
            or g0.get("case_targets") != fixture_check.get("request_component_target_counts")
            or g0.get("request_count") != fixture_check.get("request_count")
            or g0.get("component_target_count") != fixture_check.get("component_target_count")):
        return False
    try:
        protocol_registered = (sha(PROTOCOL) == PROTOCOL_SHA256
                               and f"{PROTOCOL_SHA256}  protocols/ACTINV-P113_PROTOCOL.md"
                               in (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines())
    except OSError:
        protocol_registered = False
    control_hashes = g0.get("control_sha256")
    required_controls = set(__import__("check_p113").CONTROL_FILES)
    return (
        g0.get("schema") == "actinv-p113-twin-waste-g0-1"
        and g0.get("phase") == "P113" and g0.get("pass") is True
        and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True and protocol_registered
        and g0.get("p105_verified") is True and g0.get("p112_verified") is True
        and g0.get("prior_verdicts_match") is True and prior_ok
        and g0.get("pack_mirror_identical") is True
        and digest(g0.get("pack_sha256")) and g0.get("pack_sha256") == sha(PACK) == sha(MIRROR)
        and g0.get("fixture_population_matches") is True
        and type(g0.get("request_count")) is int and g0["request_count"] >= 24
        and isinstance(g0.get("request_ids"), list)
        and len(g0["request_ids"]) == g0.get("request_count")
        and all(isinstance(item, str) and item for item in g0["request_ids"])
        and len(set(g0["request_ids"])) == len(g0["request_ids"])
        and isinstance(g0.get("case_targets"), list)
        and len(g0["case_targets"]) == g0.get("request_count")
        and all(type(value) is int and value > 0 for value in g0["case_targets"])
        and sum(g0["case_targets"]) == g0.get("component_target_count")
        and type(g0.get("component_target_count")) is int and g0["component_target_count"] >= 32
        and g0.get("p105_source_vector_seals_match") is True
        and _repair_policy(g0)
        and _repair_metadata_ok(g0)
        and _bound_files(control_hashes, required_controls))


def _historical_p112_ok() -> bool:
    """Re-derive P112 and bind its terminal PASS to its implementation and CI."""
    try:
        import check_p112_verdict

        derived = check_p112_verdict.derive()
        persisted = read(ROOT / "results/p112_verdict.json")
        record = read(ROOT / "results/p112_implementation_commit.json")
        runs = read(ROOT / "results/p112_ci_runs.json")
        artifacts = {
            "results/g0_p112_intrusion_screen.json": ROOT / "results/g0_p112_intrusion_screen.json",
            "results/g1_p112_intrusion_screen.json": ROOT / "results/g1_p112_intrusion_screen.json",
            "results/g2_p112_intrusion_screen.json": ROOT / "results/g2_p112_intrusion_screen.json",
            "results/g3_p112_quality.json": ROOT / "results/g3_p112_quality.json",
        }
        if (not isinstance(derived, dict) or not isinstance(persisted, dict)
                or derived != persisted or derived.get("verdict") != "P112-PASS"
                or not isinstance(record, dict) or record.get("schema") != "actinv-p112-implementation-1"
                or not isinstance(runs, list) or not runs):
            return False
        commit = record.get("commit_sha")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            return False
        if any(record.get(f"g{index}_sha256") != sha(path)
               or _sha_bytes(_git_blob(commit, path.relative_to(ROOT).as_posix())) != sha(path)
               for index, path in enumerate(artifacts.values())):
            return False
        return _ci_state(True, record, runs, True) == (True, True)
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _g1_ok(g1: dict, g0: dict) -> bool:
    evidence = g1.get("request_evidence")
    ids = [item.get("id") for item in evidence if isinstance(item, dict)] if isinstance(evidence, list) else []
    expected_ids = g0.get("request_ids")
    valid_ids = (isinstance(expected_ids, list) and all(isinstance(item, str) and item for item in expected_ids)
                 and isinstance(evidence, list) and all(isinstance(item, dict) for item in evidence)
                 and ids == expected_ids and len(set(ids)) == len(ids))
    mutations = g1.get("mutations_rejected")
    refusals = g1.get("refusal_controls")
    return (
        g1.get("schema") == "actinv-p113-twin-waste-g1-1"
        and g1.get("phase") == "P113" and g1.get("pass") is True
        and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and type(g1.get("request_count")) is int and g1.get("request_count") == g0.get("request_count")
        and type(g1.get("component_target_count")) is int
        and g1.get("component_target_count") == g0.get("component_target_count")
        and type(g1.get("independent_comparison_count")) is int
        and g1.get("independent_comparison_count") == g1.get("component_target_count")
        and g1.get("failures") == [] and g1.get("repeat_byte_identical") is True
        and valid_ids and isinstance(evidence, list) and len(evidence) == g1.get("request_count")
             and all(isinstance(item, dict) and isinstance(item.get("id"), str)
             and item["id"] and all(digest(item.get(field)) for field in
             ("input_sha256", "output_sha256", "ordinary_waste_sha256"))
             and type(item.get("component_target_count")) is int
             and item["component_target_count"] > 0
             for item in evidence)
        and [item["component_target_count"] for item in evidence] == g0.get("case_targets")
        and sum(item["component_target_count"] for item in evidence)
            == g1.get("component_target_count")
        and isinstance(mutations, dict) and len(mutations) >= 30 and all(value is True for value in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusals.get("checks"), dict) and len(refusals["checks"]) >= 30
        and all(value is True for value in refusals["checks"].values()))


def _g2_ok(g2: dict) -> bool:
    return (g2.get("schema") == "actinv-p113-twin-waste-g2-1"
            and g2.get("phase") == "P113" and g2.get("pass") is True
            and g2.get("protocol_sha256") == PROTOCOL_SHA256
            and g2.get("g0_exact_replay_equal") is True and g2.get("exact_replay_equal") is True
            and g2.get("separate_output_paths_byte_identical") is True
            and g2.get("g0_result_sha256") == sha(ROOT / "results/g0_p113_twin_waste.json")
            and g2.get("g1_result_sha256") == sha(ROOT / "results/g1_p113_twin_waste.json"))


def _g3_local_ok(g3: dict, g0: dict) -> bool:
    tests = g3.get("workspace_tests")
    inspection = g3.get("resource_inspection")
    release = g3.get("release_build")
    rust_hashes = g3.get("production_rust_sha256")
    return (
        g3.get("schema") == "actinv-p113-quality-1" and g3.get("phase") == "P113"
        and g3.get("pass") is True and g3.get("resource_limits") == RESOURCES
        and type(g3.get("repair_rounds")) is int
        and g3.get("repair_rounds") == g0.get("repair_rounds")
        and _strict_gate_map(g3)
        and isinstance(inspection, dict) and type(inspection.get("exit_code")) is int
        and inspection["exit_code"] == 0 and digest(inspection.get("log_sha256"))
        and isinstance(tests, dict) and type(tests.get("passed")) is int and tests["passed"] > 474
        and type(tests.get("failed")) is int and tests["failed"] == 0
        and type(tests.get("ignored")) is int and tests["ignored"] == 2
        and isinstance(release, dict) and type(release.get("exit_code")) is int
        and release["exit_code"] == 0 and digest(release.get("binary_sha256"))
        and digest(release.get("log_sha256"))
        and isinstance(g3.get("gates"), dict)
        and release.get("log_sha256") == g3["gates"].get("release_build", {}).get("log_sha256")
        and isinstance(rust_hashes, dict) and bool(rust_hashes)
        and all(isinstance(path, str) and digest(value) for path, value in rust_hashes.items()))


def _source_commit_matches(g3: dict, record: dict | None) -> bool:
    hashes = g3.get("production_rust_sha256")
    if not isinstance(hashes, dict) or not hashes:
        return False
    if record is None:
        try:
            if set(hashes) != _rust_source_paths(None):
                return False
        except (OSError, ValueError, RuntimeError):
            return False
        for raw_path, expected in hashes.items():
            try:
                path = _safe_rel(raw_path)
                (ROOT / path).resolve(strict=True).relative_to(ROOT.resolve())
            except (OSError, ValueError):
                return False
            if not digest(expected) or sha(ROOT / path) != expected:
                return False
        return True
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    try:
        if set(hashes) != _rust_source_paths(commit):
            return False
        return all(digest(expected) and _sha_bytes(_git_blob(commit, _safe_rel(path).as_posix())) == expected
                   for path, expected in hashes.items())
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


def derive() -> dict:
    g0 = read(ARTIFACTS["results/g0_p113_twin_waste.json"])
    g1 = read(ARTIFACTS["results/g1_p113_twin_waste.json"])
    g2 = read(ARTIFACTS["results/g2_p113_twin_waste.json"])
    g3 = read(ARTIFACTS["results/g3_p113_quality.json"])
    g0 = g0 if isinstance(g0, dict) else {}
    g1 = g1 if isinstance(g1, dict) else {}
    g2 = g2 if isinstance(g2, dict) else {}
    g3 = g3 if isinstance(g3, dict) else {}
    checks = {"protocol": sha(PROTOCOL) == PROTOCOL_SHA256,
              "historical_p112": _historical_p112_ok(),
              "G0": _g0_ok(g0), "G1": _g1_ok(g1, g0), "G2": _g2_ok(g2),
              "G3_local": _g3_local_ok(g3, g0)}
    record = read(IMPLEMENTATION)
    if record is not None and not isinstance(record, dict):
        record = None
    record_exists = IMPLEMENTATION.exists()
    record_ok = not record_exists or _implementation_record_ok(record)
    source_commit_ok = record_ok and _source_commit_matches(g3, record)
    checks["source_commit"] = source_commit_ok
    runs = read(CI)
    ci_well_formed, ci_pass = _ci_state(record_ok, record, runs, CI.exists())
    checks["CI_evidence_consistent"] = ci_well_formed
    local = all(checks.values())
    verdict = "P113-PASS" if local and ci_pass else "P113-LOCAL-PASS" if local else "P113-FAIL"
    return {"schema": "actinv-p113-verdict-1", "phase": "P113", "verdict": verdict,
            "gates": {name: "PASS" if okay else "FAIL" for name, okay in checks.items()} |
                     {"G3_CI": "PASS" if ci_pass else "FAIL" if CI.exists() else "PENDING"},
            "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
            "implementation_record_sha256": sha(IMPLEMENTATION), "ci_evidence_sha256": sha(CI),
            "implementation_commit": record.get("commit_sha") if isinstance(record, dict) else None,
            "historical_source_verification": bool(record_exists and record_ok
                                                   and source_commit_ok and ci_pass)}


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
    return 0 if same and result["verdict"] in ("P113-LOCAL-PASS", "P113-PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
