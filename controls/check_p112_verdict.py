#!/usr/bin/env python3
"""Derive P112 disposition from sealed source, science, and quality evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA256 = "ca19ccc59603ae97e84f5b2a50620a4ae3d87d8d6515a7b04b7d2af5d9cd56be"
CHECKPOINT = "ff7e42e9f106946e610297d46f9f2b8787813f7e"
VERDICT = ROOT / "results/p112_verdict.json"
CI = ROOT / "results/p112_ci_runs.json"
IMPLEMENTATION = ROOT / "results/p112_implementation_commit.json"
ARTIFACTS = {name: ROOT / name for name in (
    "results/g0_p112_intrusion_screen.json", "results/g1_p112_intrusion_screen.json",
    "results/g2_p112_intrusion_screen.json", "results/g3_p112_quality.json")}
OLD_QUALITY = ROOT / "results/g3_p111_quality.json"
OLD_VERDICT = ROOT / "results/p111_verdict.json"
P111_FAILURE_RECORD = ROOT / "results/p111_failure_commit.json"
P111_HISTORY_LOG = ROOT / "results/failures/p111_initial/historical_p108_invocation_after_repair.log"
P111_G3_SHA256 = "18caa3dced925f47ba5f3bbe2bdc2935a0d3db838b6137acd221126a80fb309e"
P111_VERDICT_SHA256 = "35c3b5eb4ef12fcad7e78f90a9103b476cdb51b29cb7974b7ec0a3ddae25579c"
P111_LOG_SHA256 = "b86b768a2a0deddb77d90f826dd02e55a13d1b418ef06756e084a8887a81ce7c"
P111_ARTIFACT_SHA256 = {
    "results/g0_p111_intrusion_screen.json": "a89640ce1c7517ad14ea8c047dade510a1645c2fd6eb49d8a5db0aee2b297940",
    "results/g1_p111_intrusion_screen.json": "f23fe8837def36420486e1007799f27d29cff488808c611dc28166b862262c23",
    "results/g2_p111_intrusion_screen.json": "de311b2981ec09f0eea8c05a19a84ac5bc83dd3f46e6596ce075de537837e1fd",
    "results/g3_p111_quality.json": P111_G3_SHA256,
    "results/p111_verdict.json": P111_VERDICT_SHA256,
}
PACK = ROOT / "data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json"
RESOURCES = {"memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0,
             "tasks_max": 128, "cpu_quota_percent": 200,
             "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial"}
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench",
                      "Build handbook", "fusion-isotope", "fns-iron"}
NEW_GATES = {
    "p112_source_regressions", "p112_seal_regressions", "p112_verdict_regressions",
    "historical_p111_regressions", "full_successor_control_read_only_replay",
    "pre_push_rust_fmt", "historical_p111_replay", "historical_p108_replay",
    "historical_p109_replay", "historical_p110_replay",
}
OLD_UNADOPTED = {"historical_p108_replay", "historical_p109_replay", "historical_p110_replay"}
REQUIRED_CONTROL_FILES = set(__import__("check_p112").CONTROL_FILES)


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


def _bound_files(mapping: object, required: set[str]) -> bool:
    if not isinstance(mapping, dict) or not required.issubset(mapping):
        return False
    for raw_path, expected in mapping.items():
        try:
            path = _safe_rel(raw_path)
        except ValueError:
            return False
        if not digest(expected) or sha(ROOT / path) != expected:
            return False
    return True


def _repair_policy(g0: dict) -> bool:
    rounds = g0.get("repair_rounds")
    return type(rounds) is int and rounds == 0 and not (ROOT / "protocols/ACTINV-P112_AMENDMENT_A.md").exists()


def _workspace_counts_match(workspace: object, inherited: object) -> bool:
    if not isinstance(workspace, dict) or not isinstance(inherited, dict):
        return False
    expected = {"passed": 474, "failed": 0, "ignored": 2}
    return all(type(workspace.get(name)) is int and workspace.get(name) == value
               and inherited.get(name) == value for name, value in expected.items())


def _implementation_record_ok(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if (record.get("schema") != "actinv-p112-implementation-1"
            or not isinstance(record.get("commit_sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}", record["commit_sha"]) is None
            or any(record.get(f"g{index}_sha256") != sha(path)
                   for index, path in enumerate(ARTIFACTS.values()))):
        return False
    try:
        return all(_sha_bytes(_git_blob(record["commit_sha"], path.relative_to(ROOT).as_posix())) == sha(path)
                   for path in ARTIFACTS.values())
    except (ValueError, RuntimeError, OSError):
        return False


def _ci_state(record_valid: bool, record: object, runs: object, ci_exists: bool) -> tuple[bool, bool]:
    if not ci_exists:
        return record_valid, False
    if not record_valid or not isinstance(record, dict) or not isinstance(runs, list) or not runs:
        return False, False
    names = {run.get("workflowName") for run in runs if isinstance(run, dict)}
    passed = (REQUIRED_WORKFLOWS.issubset(names)
              and all(isinstance(run, dict) and run.get("headSha") == record.get("commit_sha")
                      and run.get("status") == "completed" and run.get("conclusion") == "success"
                      for run in runs))
    return passed, passed


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a nonempty string")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts
            or "\\" in value or path.as_posix() != value):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path


def _git_blob(commit: str, path: str) -> bytes:
    from check_p107_history import _git_blob as read_blob
    return read_blob(commit, path, root=ROOT)


def _selected_handbook_path(path: str) -> bool:
    return (path.startswith("docs/guide/") or path == "book.toml"
            or path == "scripts/check_docs.py" or path == "web/docs-smoke.mjs"
            or path.startswith("web/package"))


def _handbook_paths(commit: str | None) -> set[str]:
    from p105_budget_control import _run
    command = (["git", "ls-tree", "-r", "--name-only", commit] if commit is not None
               else ["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    result = _run(command, cwd=ROOT, timeout_s=120)
    if result.returncode != 0:
        raise ValueError("could not enumerate checkpoint tree")
    paths = set()
    for raw in result.stdout.splitlines():
        path = _safe_rel(raw).as_posix()
        if _selected_handbook_path(path):
            paths.add(path)
    required = {"book.toml", "scripts/check_docs.py", "web/docs-smoke.mjs"}
    if not required.issubset(paths) or not any(path.startswith("docs/guide/") for path in paths):
        raise ValueError("checkpoint handbook population is incomplete")
    return paths


def _adoption(quality: dict, implementation_record: dict | None) -> tuple[bool, dict]:
    old = read(OLD_QUALITY)
    if not isinstance(old, dict) or sha(OLD_QUALITY) != P111_G3_SHA256:
        return False, {"error": "immutable P111 quality evidence is missing or changed"}
    old_gates = old.get("gates")
    if not isinstance(old_gates, dict):
        return False, {"error": "P111 gate map is malformed"}
    successful = {name: entry for name, entry in old_gates.items()
                  if isinstance(entry, dict) and type(entry.get("exit_code")) is int
                  and entry.get("exit_code") == 0}
    if len(successful) != 18:
        return False, {"error": "P111 does not contain exactly 18 successful observations"}
    for name, entry in successful.items():
        if (entry.get("name") != name or not digest(entry.get("log_sha256"))
                or not isinstance(entry.get("log"), str) or not entry["log"]):
            return False, {"error": f"P111 adopted observation {name} is malformed"}
    if successful.keys() & OLD_UNADOPTED:
        return False, {"error": "failed or unrun P111 history gate was adopted"}
    current_gates = quality.get("gates")
    if not isinstance(current_gates, dict):
        return False, {"error": "P112 gate map is malformed"}
    if set(current_gates) != set(successful) | NEW_GATES:
        return False, {"error": "P112 gate map has missing or unexpected gate records"}
    adopted_current = {name: current_gates.get(name) for name in successful}
    adopted_ok = adopted_current == successful
    new_ok = all(
        isinstance(current_gates.get(name), dict)
        and current_gates[name].get("name") == name
        and type(current_gates[name].get("exit_code")) is int
        and current_gates[name]["exit_code"] == 0
        and digest(current_gates[name].get("log_sha256"))
        and isinstance(current_gates[name].get("log"), str)
        and current_gates[name]["log"]
        for name in NEW_GATES
    )
    adoption = quality.get("adopted_quality")
    try:
        from check_p107_history import _safe_rust_path
        old_rust = old.get("production_rust_sha256")
        new_rust = quality.get("production_rust_sha256")
        if not isinstance(old_rust, dict) or len(old_rust) != 99 or new_rust != old_rust:
            raise ValueError("P112 Rust source map differs from the 99 P111 source hashes")
        for raw_path, expected in old_rust.items():
            safe = _safe_rust_path(raw_path).as_posix()
            if not digest(expected) or _sha_bytes(_git_blob(CHECKPOINT, safe)) != expected:
                raise ValueError(f"checkpoint Rust blob differs for {safe}")
        handbook_paths = _handbook_paths(CHECKPOINT)
        handbook = {path: _sha_bytes(_git_blob(CHECKPOINT, path))
                    for path in sorted(handbook_paths)}
        if not isinstance(adoption, dict):
            raise ValueError("adopted_quality metadata is missing")
        old_release = old.get("release_build")
        if not isinstance(old_release, dict):
            raise ValueError("P111 release-build record is malformed")
        expected_names = sorted(successful)
        if (adoption.get("checkpoint_commit") != CHECKPOINT
                or adoption.get("quality_sha256") != P111_G3_SHA256
                or adoption.get("gate_names") != expected_names
                or adoption.get("public_handbook_sha256") != handbook
                or adoption.get("release_binary_sha256") != old_release.get("binary_sha256")):
            raise ValueError("adoption metadata does not match immutable P111 evidence")
        if not digest(adoption.get("release_binary_sha256")):
            raise ValueError("adopted release binary hash is malformed")
        commit = implementation_record.get("commit_sha") if isinstance(implementation_record, dict) else None
        if commit is None:
            handbook_current_paths = _handbook_paths(None)
            handbook_current = (handbook_current_paths == handbook_paths
                                and all(sha(ROOT / path) == expected for path, expected in handbook.items()))
            rust_current = all(sha(ROOT / path) == expected for path, expected in old_rust.items())
        else:
            if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                raise ValueError("P112 implementation commit is malformed")
            handbook_current_paths = _handbook_paths(commit)
            handbook_current = (handbook_current_paths == handbook_paths
                                and all(_sha_bytes(_git_blob(commit, path)) == expected
                                        for path, expected in handbook.items()))
            rust_current = all(_sha_bytes(_git_blob(commit, path)) == expected
                               for path, expected in old_rust.items())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        return False, {"error": str(error), "adopted_gate_entries_match": adopted_ok,
                       "fresh_gate_entries_green": new_ok}
    passed = adopted_ok and new_ok and rust_current and handbook_current
    return passed, {"adopted_gate_entries_match": adopted_ok,
                    "fresh_gate_entries_green": new_ok,
                    "rust_hashes_match_checkpoint_and_worktree": rust_current,
                    "public_handbook_matches_checkpoint": handbook_current,
                    "historical_source_verification": bool(implementation_record is not None
                                                           and rust_current and handbook_current),
                    "adopted_gate_count": len(successful),
                    "fresh_gate_count": len(NEW_GATES),
                    "checkpoint_commit": CHECKPOINT,
                    "quality_sha256": P111_G3_SHA256,
                    "handbook_file_count": len(handbook),
                    "release_binary_sha256": adoption.get("release_binary_sha256")}


def derive() -> dict:
    import check_p112

    loaded = [read(path) for path in ARTIFACTS.values()]
    g0, g1, g2, g3 = (value if isinstance(value, dict) else {} for value in loaded)
    checks = {}
    checks["protocol"] = (sha(ROOT / "protocols/ACTINV-P112_PROTOCOL.md") == PROTOCOL_SHA256
                          and f"{PROTOCOL_SHA256}  protocols/ACTINV-P112_PROTOCOL.md"
                          in (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines())
    checks["repair_policy"] = _repair_policy(g0)
    prior_details = g0.get("prior_verdict_details", {})
    checks["prior_verdicts"] = (
        g0.get("historical_p108_verified") is True
        and g0.get("historical_p110_verified") is True
        and g0.get("historical_p111_verified") is True
        and g0.get("prior_verdicts_match") is True
        and isinstance(prior_details, dict)
        and isinstance(prior_details.get("P111"), dict)
        and prior_details["P111"].get("verdict") == "P111-FAIL")
    prior = g0.get("prior_verdict_sha256", {}) if isinstance(g0, dict) else {}
    required_prior = {f"results/{phase.lower()}_verdict.json" for phase in check_p112.SOURCE_IDS}
    checks["prior_verdict_hashes"] = (_bound_files(prior, required_prior)
                                     and all((read(ROOT / name) or {}).get("verdict") == wanted
                                             for phase, wanted in check_p112.PRIOR_VERDICTS.items()
                                             for name in [f"results/{phase.lower()}_verdict.json"])
                                     and all(sha(ROOT / name) == expected
                                             and prior.get("results/p111_verdict.json") == P111_VERDICT_SHA256
                                             for name, expected in P111_ARTIFACT_SHA256.items())
                                     and sha(P111_HISTORY_LOG) == P111_LOG_SHA256)
    control_hashes = g0.get("control_sha256", {}) if isinstance(g0, dict) else {}
    checks["source_controls_bound"] = _bound_files(control_hashes, REQUIRED_CONTROL_FILES)
    checks["G0"] = (
        g0.get("schema") == "actinv-p112-intrusion-screen-g0-1" and g0.get("phase") == "P112"
        and g0.get("pass") is True and g0.get("protocol_sha256") == PROTOCOL_SHA256
        and g0.get("protocol_registered") is True and g0.get("pack_mirror_identical") is True
        and g0.get("case_fixture_matches") is True and g0.get("historical_p108_verified") is True
        and g0.get("historical_p110_verified") is True and g0.get("historical_p111_verified") is True
        and isinstance(g0.get("source_review"), dict)
        and g0["source_review"].get("pass") is True
        and type(g0.get("source_rows_checked")) is int and g0.get("source_rows_checked") == 25
        and type(g0.get("column_checks")) is int and g0.get("column_checks") == 75
        and digest(g0.get("pack_sha256")) and g0.get("pack_sha256") == sha(PACK) == sha(MIRROR)
        and g0.get("case_count") == 61 and g0.get("target_count") == 62
        and checks["repair_policy"] and checks["prior_verdicts"] and checks["prior_verdict_hashes"]
        and checks["source_controls_bound"])
    g1_evidence = g1.get("case_evidence", []) if isinstance(g1, dict) else []
    evidence_ids = [item["id"] for item in g1_evidence
                    if isinstance(item, dict) and isinstance(item.get("id"), str)] \
        if isinstance(g1_evidence, list) else []
    mutations = g1.get("mutations_rejected", {}) if isinstance(g1, dict) else {}
    refusals = g1.get("refusal_controls", {}) if isinstance(g1, dict) else {}
    checks["G1"] = (
        g1.get("schema") == "actinv-p112-intrusion-screen-g1-1" and g1.get("phase") == "P112"
        and g1.get("pass") is True and g1.get("protocol_sha256") == PROTOCOL_SHA256
        and type(g1.get("case_count")) is int and g1.get("case_count") == 61
        and type(g1.get("target_count")) is int and g1.get("target_count") == 62
        and type(g1.get("independent_comparison_count")) is int and g1.get("independent_comparison_count") == 62
        and g1.get("repeat_byte_identical") is True
        and g1.get("failures") == [] and isinstance(g1_evidence, list) and len(g1_evidence) == 61
        and len(set(evidence_ids)) == 61
        and all(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
                and all(digest(item.get(field)) for field in (
                    "input_sha256", "output_sha256", "generated_waste_spec_sha256"))
                for item in g1_evidence)
        and isinstance(mutations, dict) and len(mutations) >= 20 and all(v is True for v in mutations.values())
        and isinstance(refusals, dict) and refusals.get("pass") is True
        and isinstance(refusals.get("checks"), dict) and len(refusals["checks"]) >= 30
        and all(v is True for v in refusals["checks"].values()))
    checks["G2"] = (
        g2.get("schema") == "actinv-p112-intrusion-screen-g2-1" and g2.get("phase") == "P112"
        and g2.get("pass") is True and g2.get("protocol_sha256") == PROTOCOL_SHA256
        and g2.get("g1_result_sha256") == sha(ROOT / "results/g1_p112_intrusion_screen.json")
        and g2.get("g0_exact_replay_equal") is True
        and g2.get("exact_replay_equal") is True and g2.get("separate_output_paths_byte_identical") is True)
    record = read(IMPLEMENTATION)
    quality_ok, quality_detail = _adoption(g3, record if isinstance(record, dict) else None)
    old_quality = read(OLD_QUALITY) or {}
    workspace = g3.get("workspace_tests", {}) if isinstance(g3, dict) else {}
    old_workspace = old_quality.get("workspace_tests", {})
    inspection = g3.get("resource_limits_inspection", {}) if isinstance(g3, dict) else {}
    checks["G3_local"] = (
        quality_ok and g3.get("schema") == "actinv-p112-quality-1"
        and g3.get("phase") == "P112" and g3.get("pass") is True
        and g3.get("resource_limits") == RESOURCES
        and isinstance(inspection, dict) and digest(inspection.get("log_sha256"))
        and type(g3.get("repair_rounds")) is int and g3.get("repair_rounds") == 0
        and _workspace_counts_match(workspace, old_workspace)
        and g3.get("release_build") == old_quality.get("release_build")
        and isinstance(g3.get("production_rust_sha256"), dict)
        and len(g3["production_rust_sha256"]) == 99)
    ci_runs = read(CI)
    record_ok = _implementation_record_ok(record) if IMPLEMENTATION.exists() else True
    ci_evidence_well_formed, ci_pass = _ci_state(record_ok, record, ci_runs, CI.exists())
    checks["G3_CI_evidence_consistent"] = ci_evidence_well_formed
    local = all(checks.values())
    verdict = "P112-PASS" if local and ci_pass else "P112-LOCAL-PASS" if local else "P112-FAIL"
    return {"schema": "actinv-p112-verdict-1", "phase": "P112", "verdict": verdict,
            "gates": {**{name: "PASS" if value else "FAIL" for name, value in checks.items()},
                      "G3_CI": "PASS" if ci_pass else "FAIL" if CI.exists() else "PENDING"},
            "quality_checks": quality_detail,
            "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
            "ci_evidence_sha256": sha(CI), "implementation_record_sha256": sha(IMPLEMENTATION),
            "implementation_commit": record.get("commit_sha") if isinstance(record, dict) else None,
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
    return 0 if same and result["verdict"] in ("P112-LOCAL-PASS", "P112-PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
