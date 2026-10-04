#!/usr/bin/env python3
"""Portable P118 verdict over immutable predecessor evidence and fresh receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "controls"))
import check_p116 as p116
import check_p117 as p117
import check_p118 as p118

IMPLEMENTATION = ROOT / "results/p118_implementation_commit.json"
CI = ROOT / "results/p118_ci_runs.json"
VERDICT = ROOT / "results/p118_verdict.json"
ARTIFACTS = {"results/g0_p118_twin_waste.json": p118.G0,
             "results/g1_p118_twin_waste.json": p118.G1,
             "results/g2_p118_twin_waste.json": p118.G2,
             "results/g3_p118_quality.json": p118.G3}
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
        return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None


def _same_json(value: object, expected: object) -> bool:
    expected_raw = _canonical(expected)
    return expected_raw is not None and _canonical(value) == expected_raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def read(path: Path):
    try:
        relative = path.relative_to(ROOT).as_posix()
        return json.loads(p118._safe_file(relative).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _bound_sources(mapping: object) -> bool:
    if not isinstance(mapping, dict) or set(mapping) != set(p118.CONTROL_FILES):
        return False
    try:
        return all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                   and sha(p118._safe_file(path)) == digest for path, digest in mapping.items())
    except (OSError, ValueError, TypeError):
        return False


def _g0_ok(value: object, fresh: dict | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    expected = fresh
    if not isinstance(expected, dict):
        return False
    return (_canonical(value) == _canonical(expected) and value.get("schema") == "actinv-p118-twin-waste-g0-1"
            and value.get("phase") == "P118" and value.get("pass") is True
            and value.get("protocol_sha256") == p118.PROTOCOL_SHA256
            and value.get("protocol_registered") is True and value.get("seed_authorities_match") is True
            and value.get("historical_p116_verified") is True and value.get("historical_p117_verified") is True
            and value.get("predecessor_p117_fail_preserved") is True
            and value.get("initial_failure_matches") is True
            and type(value.get("repair_rounds")) is int and value["repair_rounds"] == 1
            and value.get("amendment_sha256") == p118.AMENDMENT_SHA256
            and value.get("amendment_registered") is True
            and type(value.get("inherited_rust_source_count")) is int
            and value.get("inherited_rust_source_count") == 100
            and isinstance(value.get("inherited_rust_sha256"), dict)
            and len(value["inherited_rust_sha256"]) == 100
            and value.get("source_population_matches_p116_commit") is True
            and _bound_sources(value.get("control_sha256")))


def _g1_expected(fresh: dict, equal: bool) -> dict:
    old = read(p118.P116_G1)
    return {
        "schema": "actinv-p118-twin-waste-g1-1", "phase": "P118",
        "protocol_sha256": p118.PROTOCOL_SHA256, "pass": bool(equal),
        "p116_g1_sha256": p118.P116_G1_SHA256, "p116_report_preserved": old,
        "fresh_p118_report": fresh, "exact_canonical_match": bool(equal),
        "request_count": fresh.get("request_count"),
        "component_target_count": fresh.get("component_target_count"),
        "independent_comparison_count": fresh.get("independent_comparison_count"),
        "mutations_rejected": fresh.get("mutations_rejected"),
        "refusal_controls": fresh.get("refusal_controls"),
        "repeat_byte_identical": fresh.get("repeat_byte_identical"),
        "failures": [] if equal else ["fresh P118 campaign differs from immutable P116 report"],
    }


def _g1_ok(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    old = read(p118.P116_G1)
    try:
        persisted = p118.G1.read_bytes()
        old_raw = p118.P116_G1.read_bytes()
    except OSError:
        return False
    expected = _g1_expected(old if isinstance(old, dict) else {}, True)
    return (_canonical(value) == persisted == _canonical(expected)
            and value.get("schema") == "actinv-p118-twin-waste-g1-1"
            and value.get("phase") == "P118" and value.get("pass") is True
            and value.get("protocol_sha256") == p118.PROTOCOL_SHA256
            and value.get("p116_report_preserved") == old
            and value.get("fresh_p118_report") == old
            and value.get("p116_g1_sha256") == p118.P116_G1_SHA256
            and value.get("exact_canonical_match") is True and value.get("failures") == []
            and type(value.get("request_count")) is int and value["request_count"] == 35
            and type(value.get("component_target_count")) is int and value["component_target_count"] == 138
            and type(value.get("independent_comparison_count")) is int
            and value["independent_comparison_count"] == 138
            and value.get("repeat_byte_identical") is True
            and p118._canonical(old) == old_raw)


def _g2_expected(g0: dict, g1: dict, fresh: dict) -> dict:
    return {"schema": "actinv-p118-twin-waste-g2-1", "phase": "P118",
            "protocol_sha256": p118.PROTOCOL_SHA256, "pass": True,
            "g0_exact_replay_equal": True, "g1_exact_replay_equal": True,
            "separate_output_paths_byte_identical": True,
            "p116_g1_sha256": p118.P116_G1_SHA256,
            "g0_result_sha256": sha(p118.G0), "g1_result_sha256": sha(p118.G1),
            "fresh_g1_report": fresh}


def _g2_ok(value: object, g0: dict, g1: dict) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        persisted = p118.G2.read_bytes()
    except OSError:
        return False
    fresh = read(p118.P116_G1)
    expected = _g2_expected(g0, g1, fresh if isinstance(fresh, dict) else {})
    return (_canonical(value) == persisted == _canonical(expected)
            and value.get("schema") == "actinv-p118-twin-waste-g2-1"
            and value.get("phase") == "P118" and value.get("pass") is True
            and value.get("protocol_sha256") == p118.PROTOCOL_SHA256
            and value.get("g0_exact_replay_equal") is True and value.get("g1_exact_replay_equal") is True
            and value.get("separate_output_paths_byte_identical") is True
            and g0.get("pass") is True and g1.get("pass") is True)


def _adopted_initial_ok(g3: dict, g0: dict) -> bool:
    entries = g3.get("p117_adopted_initial_gates")
    initial_g3 = p117._read(p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
    all_initial = initial_g3.get("fresh_gates", {}) if isinstance(initial_g3, dict) else None
    expected = ({name: all_initial[name] for name in sorted(p118.P117_ADOPTED_GATES)}
                if isinstance(all_initial, dict) and set(all_initial) == p117.INITIAL_GATES
                else None)
    return (isinstance(entries, dict) and set(entries) == p118.P117_ADOPTED_GATES
            and _same_json(entries, expected)
            and g3.get("p117_adopted_initial_gate_names") == sorted(p118.P117_ADOPTED_GATES)
            and all(p118._adopted_initial_entry_matches(name, entry, require_raw_target=False)
                    for name, entry in entries.items()))


def _g3_ok(value: object, g0: dict) -> bool:
    if (not isinstance(value, dict) or value.get("schema") != "actinv-p118-quality-1"
            or _canonical(value) is None):
        return False
    fresh = value.get("fresh_gates")
    if not isinstance(fresh, dict) or set(fresh) != p118.FRESH_GATES:
        return False
    for name, expected in fresh.items():
        try:
            receipt, live = p118._safe_receipt(name)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        if (not isinstance(expected, dict) or not isinstance(receipt, dict)
                or not isinstance(live, dict) or not _same_json(live, expected)):
            return False
    prior = read(p118.P116_G3)
    if not isinstance(prior, dict):
        return False
    adopted = value.get("p116_adopted")
    old_gates = prior.get("gates")
    handbook, handbook_ok = p118._handbook_snapshot()
    release = prior.get("release_build") if isinstance(prior.get("release_build"), dict) else {}
    expected_adoption = {
        "quality_sha256": p118.P116_G3_SHA256,
        "implementation_commit": p118.IMPLEMENTATION_COMMIT,
        "verdict_sha256": p118.P116_VERDICT_SHA256,
        "gates": old_gates,
        "gate_names": sorted(old_gates) if isinstance(old_gates, dict) else [],
        "successful_gate_count": len(old_gates) if isinstance(old_gates, dict) else 0,
        "workspace_tests": prior.get("workspace_tests"), "release_build": release,
        "production_rust_sha256": prior.get("production_rust_sha256"),
        "public_handbook_sha256": handbook, "quality_validated": True, "not_rerun": True,
    }
    history_evidence = g0.get("p117_history_evidence")
    return (value.get("phase") == "P118" and value.get("pass") is True
            and type(value.get("repair_rounds")) is int and value["repair_rounds"] == 1
            and value.get("amendment_sha256") == p118.AMENDMENT_SHA256
            and value.get("amendment_registered") is True
            and value.get("initial_failure_matches") is True
            and _same_json(value.get("initial_failure"), g0.get("initial_failure"))
            and value.get("historical_p117_verified") is True
            and value.get("historical_p116_verified") is True
            and _same_json(value.get("p117_terminal_history"), g0.get("p117_history"))
            and isinstance(history_evidence, dict)
            and value.get("p117_terminal_archive_sha256") == history_evidence.get("terminal_archive_files_sha256")
            and value.get("p117_initial_archive_sha256") == history_evidence.get("initial_archive_files_sha256")
            and _adopted_initial_ok(value, g0)
            and type(value.get("p117_consumed_repair_rounds")) is int
            and value.get("p117_consumed_repair_rounds") == 1
            and value.get("initial_failure_sha256") == g0.get("initial_failure_sha256")
            and isinstance(old_gates, dict) and len(old_gates) == 32
            and _same_json(value.get("adopted_p116_gates"), old_gates)
            and isinstance(adopted, dict) and _same_json(adopted, expected_adoption)
            and handbook_ok is True
            and value.get("public_handbook_sha256") == handbook
            and value.get("current_source_matches_p117_except_ci") is True
            and value.get("current_handbook_matches_p116") is True
            and value.get("source_sha256") == g0.get("control_sha256")
            and value.get("inherited_rust_sha256") == g0.get("inherited_rust_sha256")
            and value.get("p117_adopted_initial_gate_names") == sorted(p118.P117_ADOPTED_GATES)
            and value.get("fresh_gate_names") == sorted(p118.FRESH_GATES)
            and value.get("rerun_gate_names") == sorted(p117.RERUN_GATES)
            and value.get("p117_science_evidence_matches") is True
            and value.get("qualified_binary_matches_p116") is True
            and value.get("qualified_binary_sha256") == release.get("binary_sha256")
            and value.get("g0_sha256") == sha(p118.G0)
            and value.get("g1_sha256") == sha(p118.G1)
            and value.get("g2_sha256") == sha(p118.G2)
            and value.get("failures") == []
            and _same_json(value.get("resource_limits"), p117.RESOURCES))


def _source_commit_ok(g0: dict, implementation: dict | None) -> bool:
    if implementation is None:
        return _bound_sources(g0.get("control_sha256"))
    commit = implementation.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    try:
        controls = g0.get("control_sha256")
        old_rust = read(p118.P116_G3).get("production_rust_sha256")
        rust_paths = p116._rust_paths_at_commit(commit)
        current = p116._current_rust_source_hashes()
        return (isinstance(controls, dict) and set(controls) == set(p118.CONTROL_FILES)
                and isinstance(old_rust, dict) and len(old_rust) == 100
                and set(old_rust) == rust_paths and current == old_rust
                and all(_sha_bytes(p116._git_blob(commit, path)) == sha(p118._safe_file(path))
                        for path in controls)
                and all(_sha_bytes(p116._git_blob(commit, path)) == digest
                        for path, digest in old_rust.items()))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _implementation_ok(value: object, g0: dict, g1: dict, g2: dict, g3: dict) -> bool:
    if not isinstance(value, dict) or value.get("schema") != "actinv-p118-implementation-1":
        return False
    commit = value.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    try:
        for key, path in (("g0_sha256", p118.G0), ("g1_sha256", p118.G1),
                          ("g2_sha256", p118.G2), ("g3_sha256", p118.G3)):
            digest = sha(path)
            if value.get(key) != digest or _sha_bytes(p116._git_blob(commit, path.relative_to(ROOT).as_posix())) != digest:
                return False
    except (ImportError, OSError, ValueError, RuntimeError, KeyError):
        return False
    return _source_commit_ok(g0, value)


def _ci_state(implementation_ok: bool, implementation: dict | None, runs: object, exists: bool) -> tuple[bool, bool]:
    if not exists:
        return implementation_ok, False
    if (not implementation_ok or not isinstance(implementation, dict)
            or not isinstance(runs, list) or len(runs) != 6 or any(not isinstance(row, dict) for row in runs)):
        return False, False
    names = [row.get("workflowName") for row in runs]
    if any(not isinstance(name, str) for name in names):
        return False, False
    if len(set(names)) != 6 or set(names) != WORKFLOWS:
        return False, False
    ids = [row.get("databaseId") for row in runs]
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != 6:
        return False, False
    if any(row.get("url") != f"https://github.com/AvilaLabs/ACTINV/actions/runs/{row['databaseId']}" for row in runs):
        return False, False
    commit = implementation.get("commit_sha")
    passed = all(row.get("headSha") == commit and row.get("status") == "completed"
                 and row.get("conclusion") == "success" for row in runs)
    return passed, passed


def derive() -> dict:
    g0, g1, g2, g3 = (read(path) for path in ARTIFACTS.values())
    g0 = g0 if isinstance(g0, dict) else {}
    g1 = g1 if isinstance(g1, dict) else {}
    g2 = g2 if isinstance(g2, dict) else {}
    g3 = g3 if isinstance(g3, dict) else {}
    implementation = read(IMPLEMENTATION)
    runs = read(CI)
    impl_exists = IMPLEMENTATION.exists() or IMPLEMENTATION.is_symlink()
    ci_exists = CI.exists() or CI.is_symlink()
    impl_ok = (_implementation_ok(implementation, g0, g1, g2, g3) if isinstance(implementation, dict)
               else not impl_exists and _source_commit_ok(g0, None))
    try:
        fresh_g0 = p118._g0_base()
        historical = fresh_g0.get("p117_history_evidence", {})
        history_ok = (fresh_g0.get("pass") is True
                      and fresh_g0.get("historical_p117_verified") is True
                      and fresh_g0.get("historical_p116_verified") is True
                      and fresh_g0.get("predecessor_p117_fail_preserved") is True)
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        fresh_g0, historical, history_ok = None, {}, False
    checks = {
        "protocol": sha(p118.ROOT / p118.PROTOCOL) == p118.PROTOCOL_SHA256,
        "predecessor_history": history_ok,
        "G0": _g0_ok(g0, fresh_g0), "G1": _g1_ok(g1), "G2": _g2_ok(g2, g0, g1),
        "G3_local": _g3_ok(g3, g0),
        "source_commit": impl_ok,
    }
    ci_well_formed, ci_pass = _ci_state(impl_ok, implementation if isinstance(implementation, dict) else None,
                                        runs, ci_exists)
    checks["CI_evidence_consistent"] = ci_well_formed
    local = all(checks.values())
    return {"schema": "actinv-p118-verdict-1", "phase": "P118",
            "verdict": "P118-PASS" if local and ci_pass else "P118-LOCAL-PASS" if local else "P118-FAIL",
            "gates": {key: "PASS" if value else "FAIL" for key, value in checks.items()}
                     | {"G3_CI": "PASS" if ci_pass else "FAIL" if ci_exists else "PENDING"},
            "evidence_sha256": {name: sha(path) for name, path in ARTIFACTS.items()},
            "implementation_record_sha256": sha(IMPLEMENTATION), "ci_evidence_sha256": sha(CI),
            "implementation_commit": implementation.get("commit_sha") if isinstance(implementation, dict) else None,
            "repair_rounds": 1 if history_ok else None,
            "repair_amendment_sha256": p118.AMENDMENT_SHA256 if history_ok else None,
            "historical_p116_verified": history_ok,
            "historical_p117_verified": historical.get("pass") is True}


def _write_transition_allowed(prior: object, result: dict) -> bool:
    """Allow an identical replay or the exact, green-CI closure of local evidence."""
    prior_raw, result_raw = _canonical(prior), _canonical(result)
    if result_raw is not None and prior_raw == result_raw:
        return True
    gates = result.get("gates")
    required_gates = {"protocol", "predecessor_history", "G0", "G1", "G2",
                      "G3_local", "source_commit", "CI_evidence_consistent", "G3_CI"}
    if (result.get("schema") != "actinv-p118-verdict-1" or result.get("phase") != "P118"
            or result.get("verdict") != "P118-PASS" or not isinstance(gates, dict)
            or set(gates) != required_gates or any(value != "PASS" for value in gates.values())
            or type(result.get("repair_rounds")) is not int or result["repair_rounds"] != 1
            or not isinstance(result.get("implementation_commit"), str)
            or re.fullmatch(r"[0-9a-f]{40}", result["implementation_commit"]) is None
            or any(not isinstance(result.get(key), str)
                   or re.fullmatch(r"[0-9a-f]{64}", result[key]) is None
                   for key in ("implementation_record_sha256", "ci_evidence_sha256"))):
        return False
    expected_prior = {
        **result, "verdict": "P118-LOCAL-PASS",
        "gates": {**gates, "G3_CI": "PENDING"},
        "implementation_record_sha256": None, "ci_evidence_sha256": None,
        "implementation_commit": None,
    }
    return prior_raw is not None and prior_raw == _canonical(expected_prior)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    result = derive()
    raw = _canonical(result)
    if args.write:
        exists = os.path.lexists(VERDICT)
        prior = read(VERDICT) if exists else None
        if exists and (VERDICT.is_symlink() or not VERDICT.is_file()):
            return 1
        permitted = not exists or _write_transition_allowed(prior, result)
        if not permitted or raw is None:
            return 1
        VERDICT.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".p118-verdict-", dir=VERDICT.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, VERDICT)
            directory_fd = os.open(VERDICT.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        same = True
    else:
        same = read(VERDICT) is not None and VERDICT.read_bytes() == raw
    print(json.dumps({"verdict": result["verdict"], "gates": result["gates"],
                      "persisted_matches": same}, sort_keys=True, indent=2))
    return 0 if same and result["verdict"] in {"P118-LOCAL-PASS", "P118-PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
