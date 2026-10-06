#!/usr/bin/env python3
"""Verify immutable P118 FAIL evidence after replacing its live CI gates."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_p116 as p116
import check_p117_history as p117_history
import check_p118 as p118
import run_p118_gate

SCHEMA = "actinv-p118-history-verification-1"
CHECKPOINT = "6f3f964cf06ad4e969392b90effbe4f835d183a3"
DISCOVERY = "results/failures/p118_terminal/discovery.json"
DISCOVERY_SHA256 = "4d7f99aabec30439317c7de1bed2a70a26c63cb722c88d78a821038687d5de7a"
ARCHIVE = "results/failures/p118_terminal"
P119_PROTOCOL = "protocols/ACTINV-P119_PROTOCOL.md"
P119_PROTOCOL_SHA256 = "ba32fb5cd131297f4d7999b03531652c665638ebe2ccedd85b09a30fd7e52972"
P119_EDITS = {
    ".github/workflows/ci.yml",
    "controls/check_p117_history.py",
    "controls/test_p117_history.py",
}
HISTORY_GATE = "p117_history_regressions"
HISTORY_RECEIPT = f"results/quality/p118/{HISTORY_GATE}.json"
HISTORY_LOG = f"results/quality/p118/{HISTORY_GATE}.log"
HISTORY_RAW = f"target/p118-{HISTORY_GATE}.log"
HISTORY_OBS = "results/quality/p118/terminal_history_observed.json"
HISTORY_OUTER = "results/quality/p118/terminal_history_outer.log"
VERDICT_OBS = "results/quality/p118/terminal_verdict_observed.json"
VERDICT_LOG = "results/quality/p118/terminal_verdict.log"
VERDICT_RAW = "target/p118-terminal-verdict.log"
VERDICT_ENTRY_POINT = [
    "python3", "-c",
    ('import sys,json; sys.path[:0]=["scripts","controls"]; '
     'import run_p117_gate as r; resources,errors=r.inspect_resources(); '
     'errors+=r._resource_snapshot_errors(resources); '
     'print(json.dumps({"resources":resources,"errors":errors},sort_keys=True),flush=True); '
     'assert not errors,errors; import check_p118_verdict; '
     'raise SystemExit(check_p118_verdict.main(["--write"]))'),
]
FRESH_GATES = {
    "rust_fmt": (0, 1200, ["cargo", "fmt", "--all", "--", "--check"]),
    "p118_verdict_regressions": (0, 600, ["python3", "controls/test_p118_verdict.py"]),
    "recorder_regressions": (0, 600, ["python3", "scripts/test_run_p118_gate.py"]),
    HISTORY_GATE: (1, 600, ["python3", "controls/test_p117_history.py"]),
}
ABSENT = (
    "results/g0_p118_twin_waste.json", "results/g1_p118_twin_waste.json",
    "results/g2_p118_twin_waste.json", "results/g3_p118_quality.json",
    "results/p118_implementation_commit.json", "results/p118_ci_runs.json",
)

OLD_P118_CI_STEP = p117_history.NEW_P118_CI_STEP
NEW_P118_HISTORY_CI_STEP = b"""      - name: P118 terminal failure remains immutable
        env:
          ACTINV_BIN: target/release/actinv
        run: |
          python controls/test_p118_history.py
          python controls/check_p118_history.py
"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(relative: str) -> bytes:
    return p117_history._safe_file(relative).read_bytes()


def _json(raw: bytes):
    return p117_history._unique(raw)


def _workflow_transition_matches() -> bool:
    """Require the one registered P118 CI replacement against checkpoint Git."""
    try:
        previous = p116._git_blob(CHECKPOINT, ".github/workflows/ci.yml")
        current = _read(".github/workflows/ci.yml")
        return (previous.count(OLD_P118_CI_STEP) == 1
                and current == previous.replace(OLD_P118_CI_STEP, NEW_P118_HISTORY_CI_STEP, 1))
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


def _verify_protocol_registry() -> None:
    protocol_raw = _read(P119_PROTOCOL)
    if _sha(protocol_raw) != P119_PROTOCOL_SHA256:
        raise ValueError("P119 protocol digest differs")
    rows = _read("protocols/protocol_hash.txt").decode("utf-8").splitlines()
    expected = f"{P119_PROTOCOL_SHA256}  {P119_PROTOCOL}"
    if rows.count(expected) != 1:
        raise ValueError("P119 protocol is not registered exactly once")


def _verify_terminal_archive(discovery: dict) -> dict[str, str]:
    if (discovery.get("schema") != "actinv-p118-terminal-preservation-1"
            or discovery.get("phase") != "P118"
            or discovery.get("checkpoint_commit") != CHECKPOINT
            or discovery.get("terminal_verdict") != "P118-FAIL"
            or discovery.get("pushed") is not False
            or discovery.get("successor_phase") != "not_registered"
            or type(discovery.get("repair_rounds_consumed")) is not int
            or discovery.get("repair_rounds_consumed") != 1
            or discovery.get("terminal_gates") != {
                "CI_evidence_consistent": "FAIL", "G0": "FAIL", "G1": "FAIL",
                "G2": "FAIL", "G3_CI": "PENDING", "G3_local": "FAIL",
                "predecessor_history": "FAIL", "protocol": "PASS", "source_commit": "FAIL"}
            or discovery.get("absent_artifacts") != list(ABSENT)):
        raise ValueError("P118 terminal failure disposition differs")
    direct = discovery.get("preserved_files_sha256")
    if not isinstance(direct, dict) or len(direct) != 41 or "discovery.json" in direct:
        raise ValueError("P118 terminal archive map must contain 41 files")
    expected = dict(direct)
    expected["discovery.json"] = DISCOVERY_SHA256
    mapped = p117_history._archive_map(ARCHIVE, expected)
    if len(mapped) != 42:
        raise ValueError("P118 terminal archive physical population differs")
    return mapped


def _verify_source_maps(discovery: dict) -> tuple[dict, dict, dict]:
    sources = discovery.get("terminal_source_sha256")
    rust = discovery.get("unchanged_rust_sha256")
    handbook = discovery.get("unchanged_public_handbook_sha256")
    if (not isinstance(sources, dict) or len(sources) != 319
            or set(sources) != set(p118.CONTROL_FILES)
            or not isinstance(rust, dict) or len(rust) != 100
            or not isinstance(handbook, dict) or len(handbook) != 27
            or not P119_EDITS.issubset(sources)):
        raise ValueError("P118 checkpoint source populations differ")
    for path, digest in sources.items():
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid P118 checkpoint source digest: {path}")
        if _sha(p116._git_blob(CHECKPOINT, path)) != digest:
            raise ValueError(f"P118 checkpoint Git source differs: {path}")
        if path in P119_EDITS:
            archived = f"{ARCHIVE}/{path}"
            if discovery["preserved_files_sha256"].get(path) != digest or _sha(_read(archived)) != digest:
                raise ValueError(f"P118 prior source bytes not preserved: {path}")
        elif _sha(_read(path)) != digest:
            raise ValueError(f"current source differs from P118 checkpoint: {path}")
    if (set(rust) != p116._rust_paths_at_commit(CHECKPOINT)
            or p116._current_rust_source_hashes() != rust):
        raise ValueError("P118 Rust source population differs")
    for path, digest in rust.items():
        if _sha(p116._git_blob(CHECKPOINT, path)) != digest or _sha(_read(path)) != digest:
            raise ValueError(f"P118 Rust source differs: {path}")
    for path, digest in handbook.items():
        if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or _sha(p116._git_blob(CHECKPOINT, path)) != digest
                or _sha(_read(path)) != digest):
            raise ValueError(f"P118 public handbook input differs: {path}")
    if not _workflow_transition_matches():
        raise ValueError("P119 CI workflow transition differs")
    return sources, rust, handbook


def _verify_history_episode(discovery: dict, archive_files: dict[str, str]) -> dict:
    row = discovery.get("fresh_gate_observations", {}).get(HISTORY_GATE)
    receipt_raw = _read(f"{ARCHIVE}/{HISTORY_RECEIPT}")
    receipt = _json(receipt_raw)
    log_raw = _read(f"{ARCHIVE}/{HISTORY_LOG}")
    obs_raw = _read(f"{ARCHIVE}/{HISTORY_OBS}")
    obs = _json(obs_raw)
    outer_raw = _read(f"{ARCHIVE}/{HISTORY_OUTER}")
    outer_lines = outer_raw.decode("utf-8").splitlines()
    if (not isinstance(row, dict) or row != receipt
            or type(receipt.get("child_exit_code")) is not int
            or receipt.get("child_exit_code") != 1
            or _read(HISTORY_RECEIPT) != _read(f"{ARCHIVE}/{HISTORY_RECEIPT}")
            or _read(HISTORY_LOG) != log_raw):
        raise ValueError("P118 history gate descriptor differs from its complete receipt")
    if (discovery.get("failed_regressions") != {
            "actual_child_exit_code": 1, "actual_outer_exit_code": 1,
            "errors": 6, "failures": 1, "tests": 12}
            or "Ran 12 tests" not in log_raw.decode("utf-8")
            or "FAILED (failures=1, errors=6)" not in log_raw.decode("utf-8")
            or "ValueError: nested original discovery mismatch" not in log_raw.decode("utf-8")):
        raise ValueError("P118 history regression failure details differ")
    resources = receipt["resources"]
    scope = resources["cgroup_path"].rsplit("/", 1)[-1]
    if (obs.get("schema") != "actinv-p118-terminal-history-observation-1"
            or obs.get("phase") != "P118" or obs.get("gate") != HISTORY_GATE
            or obs.get("status") != "completed" or type(obs.get("exit_code")) is not int
            or obs.get("exit_code") != 1 or obs.get("execution_interface") != "exec_command"
            or obs.get("entry_point") != ["python3", "scripts/run_p118_gate.py", "--phase", "P118",
                "--name", HISTORY_GATE, "--timeout-s", "600", "--log", HISTORY_RAW,
                "--receipt", HISTORY_RECEIPT, "--", "python3", "controls/test_p117_history.py"]
            or obs.get("returned_recorder_report") != receipt
            or not outer_lines
            or obs.get("scope_header") != outer_lines[0]
            or obs.get("G0_status") != "not_sealed" or obs.get("G1_status") != "not_run"
            or obs.get("G2_status") != "not_run" or obs.get("G3_status") != "not_run"
            or obs.get("repair_rounds_consumed") != 1
            or not outer_lines[0].startswith(f"Running as unit: {scope}; invocation ID: ")
            or not outer_lines[0].split("invocation ID: ", 1)[1].strip()
            or _json("\n".join(outer_lines[1:]).encode("utf-8")) != receipt
            or archive_files.get(f"{ARCHIVE}/{HISTORY_OBS}") != _sha(obs_raw)
            or archive_files.get(f"{ARCHIVE}/{HISTORY_OUTER}") != _sha(outer_raw)):
        raise ValueError("P118 history outer invocation does not match its actual failure")
    if _read(HISTORY_OBS) != obs_raw or _read(HISTORY_OUTER) != outer_raw:
        raise ValueError("live P118 history observation differs from its archive")
    return {"actual_child_exit_code": 1, "actual_outer_exit_code": 1,
            "tests": 12, "failures": 1, "errors": 6,
            "receipt_sha256": _sha(receipt_raw), "log_sha256": _sha(log_raw),
            "observation_sha256": _sha(obs_raw), "outer_log_sha256": _sha(outer_raw)}


def _verify_fresh_gates(discovery: dict, archive_files: dict[str, str]) -> dict[str, dict]:
    """Check every P118 fresh receipt against its archived raw and durable logs."""
    observations = discovery.get("fresh_gate_observations")
    if not isinstance(observations, dict) or set(observations) != set(FRESH_GATES):
        raise ValueError("P118 fresh gate population differs")
    verified = {}
    for name, (exit_code, timeout_limit, argv) in FRESH_GATES.items():
        receipt_rel = f"results/quality/p118/{name}.json"
        durable_rel = f"results/quality/p118/{name}.log"
        raw_rel = f"target/p118-{name}.log"
        receipt_raw = _read(f"{ARCHIVE}/{receipt_rel}")
        receipt = _json(receipt_raw)
        durable = _read(f"{ARCHIVE}/{durable_rel}")
        raw = _read(f"{ARCHIVE}/{raw_rel}")
        row = observations[name]
        timeout = receipt.get("timeout_s")
        elapsed = receipt.get("elapsed_s")
        expected_status = "completed" if exit_code == 0 else "child_failed"
        if (not isinstance(row, dict) or row != receipt
                or row.get("child_exit_code") != exit_code
                or type(row.get("child_exit_code")) is not int
                or receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
                or receipt.get("phase") != "P118" or receipt.get("gate") != name
                or receipt.get("status") != expected_status
                or type(receipt.get("child_exit_code")) is not int
                or receipt.get("child_exit_code") != exit_code
                or receipt.get("error") is not None or receipt.get("cwd") != "."
                or receipt.get("argv") != argv or receipt.get("log_path") != raw_rel
                or type(timeout) not in (int, float) or isinstance(timeout, bool)
                or not math.isfinite(float(timeout)) or timeout != timeout_limit
                or type(elapsed) not in (int, float) or isinstance(elapsed, bool)
                or not math.isfinite(float(elapsed)) or elapsed < 0
                or elapsed > timeout + run_p118_gate.CLEANUP_HEADROOM_S
                or not p117_history._resource_ok(receipt.get("resources"))
                or receipt.get("log_sha256") != _sha(durable) or durable != raw
                or row.get("log_sha256") != _sha(raw)
                or archive_files.get(f"{ARCHIVE}/{receipt_rel}") != _sha(receipt_raw)
                or archive_files.get(f"{ARCHIVE}/{durable_rel}") != _sha(durable)
                or archive_files.get(f"{ARCHIVE}/{raw_rel}") != _sha(raw)
                or _read(receipt_rel) != receipt_raw or _read(durable_rel) != durable):
            raise ValueError(f"invalid P118 fresh receipt/log/resource evidence: {name}")
        verified[name] = {"exit_code": exit_code, "receipt_sha256": _sha(receipt_raw),
                          "log_sha256": _sha(durable)}
    return verified


def _verify_terminal_verdict(discovery: dict, archive_files: dict[str, str]) -> dict:
    rel = "results/p118_verdict.json"
    verdict_raw = _read(f"{ARCHIVE}/{rel}")
    verdict = _json(verdict_raw)
    if (_sha(verdict_raw) != discovery.get("terminal_verdict_sha256")
            or verdict.get("schema") != "actinv-p118-verdict-1"
            or verdict.get("phase") != "P118" or verdict.get("verdict") != "P118-FAIL"
            or verdict.get("gates") != discovery.get("terminal_gates")
            or archive_files.get(f"{ARCHIVE}/{rel}") != _sha(verdict_raw)
            or _read(rel) != verdict_raw):
        raise ValueError("P118 terminal verdict identity differs")
    obs_raw = _read(f"{ARCHIVE}/{VERDICT_OBS}")
    obs = _json(obs_raw)
    durable = _read(f"{ARCHIVE}/{VERDICT_LOG}")
    raw = _read(f"{ARCHIVE}/{VERDICT_RAW}")
    lines = durable.decode("utf-8").splitlines()
    wall = obs.get("tool_wall_time_s")
    scope_completion = obs.get("scope_completion")
    resources = obs.get("resources")
    scope = resources.get("cgroup_path", "").rsplit("/", 1)[-1] if isinstance(resources, dict) else ""
    if (obs.get("schema") != "actinv-p118-terminal-verdict-observation-1"
            or obs.get("phase") != "P118" or obs.get("status") != "completed"
            or type(obs.get("exit_code")) is not int or obs.get("exit_code") != 1
            or obs.get("verdict") != "P118-FAIL" or obs.get("persisted_matches") is not True
            or obs.get("verdict_sha256") != _sha(verdict_raw)
            or obs.get("raw_output_path") != VERDICT_RAW or obs.get("durable_output_path") != VERDICT_LOG
            or obs.get("verdict_path") != "results/p118_verdict.json"
            or obs.get("entry_point") != VERDICT_ENTRY_POINT
            or obs.get("timeout_s") != 1200 or obs.get("termination_grace_s") != 30
            or type(wall) not in (int, float) or isinstance(wall, bool)
            or not math.isfinite(float(wall)) or wall <= 0
            or wall > 1200 + run_p118_gate.CLEANUP_HEADROOM_S
            or not p117_history._resource_ok(resources)
            or obs.get("output_sha256") != _sha(raw) or raw != durable
            or not lines or not lines[0].startswith("Running as unit: ")
            or not lines[0].startswith(f"Running as unit: {scope}; invocation ID: ")
            or not lines[0].split("invocation ID: ", 1)[1].strip()
            or obs.get("scope_header") != lines[0]
            or not isinstance(scope_completion, dict)
            or scope_completion.get("ActiveState") != "inactive"
            or scope_completion.get("SubState") != "dead"
            or scope_completion.get("Result") != "success"
            or obs.get("G0_status") != "not_sealed" or obs.get("G1_status") != "not_run"
            or obs.get("G2_status") != "not_run" or obs.get("G3_status") != "not_run"
            or obs.get("repair_rounds_consumed") != 1
            or obs.get("successor_phase") != "not_registered"
            or len(lines) < 4
            or _json(lines[1].encode("utf-8")) != {"errors": [], "resources": resources}
            or _json("\n".join(lines[2:]).encode("utf-8")) != {
                "gates": discovery["terminal_gates"], "persisted_matches": True,
                "verdict": "P118-FAIL"}
            or archive_files.get(f"{ARCHIVE}/{VERDICT_OBS}") != _sha(obs_raw)
            or archive_files.get(f"{ARCHIVE}/{VERDICT_LOG}") != _sha(durable)
            or archive_files.get(f"{ARCHIVE}/{VERDICT_RAW}") != _sha(raw)
            or _read(VERDICT_OBS) != obs_raw or _read(VERDICT_LOG) != durable):
        raise ValueError("P118 terminal verdict actual exit or output differs")
    return {"verdict": "P118-FAIL", "verdict_sha256": _sha(verdict_raw),
            "observation_sha256": _sha(obs_raw), "log_sha256": _sha(durable),
            "actual_exit_code": 1}


def verify() -> dict:
    """Verify P118's recorded terminal failure without asserting qualification."""
    try:
        discovery_raw = _read(DISCOVERY)
        if _sha(discovery_raw) != DISCOVERY_SHA256:
            raise ValueError("P118 terminal discovery digest changed")
        discovery = _json(discovery_raw)
        archive = _verify_terminal_archive(discovery)
        sources, rust, handbook = _verify_source_maps(discovery)
        _verify_protocol_registry()
        initial = p118._initial_failure_evidence()
        if initial.get("pass") is not True:
            raise ValueError("P118 initial failure archive verification failed")
        initial_map = initial.get("preserved_files_sha256")
        expected_initial = discovery.get("initial_failure_archive_sha256")
        discovery_member = p118.INITIAL_DISCOVERY
        complete_initial_map = (dict(initial_map) if isinstance(initial_map, dict) else {})
        complete_initial_map[discovery_member] = p118.INITIAL_DISCOVERY_SHA256
        if (not isinstance(initial_map, dict) or len(initial_map) != 15
                or discovery_member in initial_map or not isinstance(expected_initial, dict)
                or len(expected_initial) != 16
                or expected_initial.get(discovery_member) != p118.INITIAL_DISCOVERY_SHA256
                or len(complete_initial_map) != 16 or complete_initial_map != expected_initial):
            raise ValueError("P118 initial archive map differs from terminal descriptor")
        predecessor_discovery, predecessor_files, _predecessor_initial = p117_history._terminal_archive()
        expected_predecessor = discovery.get("predecessor_terminal_archive_sha256")
        if (predecessor_discovery.get("terminal_verdict") != "P117-FAIL"
                or predecessor_discovery.get("checkpoint_commit") != p117_history.CHECKPOINT
                or predecessor_files != expected_predecessor or len(predecessor_files) != 100):
            raise ValueError("P117 predecessor archive identity or population differs")
        fresh_gates = _verify_fresh_gates(discovery, archive)
        history = _verify_history_episode(discovery, archive)
        verdict = _verify_terminal_verdict(discovery, archive)
        _verify_absent_artifacts()
        return {
            "schema": SCHEMA, "pass": True, "phase": "P118", "verdict": "P118-FAIL",
            "checkpoint_commit": CHECKPOINT, "terminal_discovery_sha256": DISCOVERY_SHA256,
            "terminal_archive_file_count": len(archive), "initial_archive_file_count": 16,
            "predecessor_archive_file_count": len(predecessor_files),
            "terminal_source_file_count": len(sources), "rust_source_file_count": len(rust),
            "public_handbook_file_count": len(handbook), "current_source_matches_except_p119_edits": True,
            "initial_failure": initial,
            "p117_predecessor": {"checkpoint_commit": p117_history.CHECKPOINT,
                                  "terminal_verdict": "P117-FAIL",
                                  "archive_file_count": len(predecessor_files)},
            "fresh_gates": fresh_gates, "history_failure": history, "terminal_verdict": verdict,
            "unrun_artifacts_absent": list(ABSENT),
        }
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError,
            AttributeError, ImportError, UnicodeError, json.JSONDecodeError) as exc:
        return {"schema": SCHEMA, "pass": False, "phase": "P118", "verdict": "P118-FAIL",
                "checkpoint_commit": CHECKPOINT, "error": f"{type(exc).__name__}: {exc}"}


def _verify_absent_artifacts() -> None:
    for relative in ABSENT:
        candidate = ROOT / relative
        if candidate.exists() or candidate.is_symlink():
            raise ValueError(f"P118 unrun artifact must remain absent: {relative}")


def main() -> int:
    report = verify()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
