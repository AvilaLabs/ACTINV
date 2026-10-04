#!/usr/bin/env python3
"""Preserve terminal P118 failure without executing qualification or science."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "controls")]
import run_p117_gate
import check_p118 as p118

ARCHIVE = ROOT / "results/failures/p118_terminal"
EXPECTED_GATES = {
    "rust_fmt": (0, 1200),
    "recorder_regressions": (0, 600),
    "p118_verdict_regressions": (0, 600),
    "p117_history_regressions": (1, 600),
}
ABSENT = (
    "results/g0_p118_twin_waste.json",
    "results/g1_p118_twin_waste.json",
    "results/g2_p118_twin_waste.json",
    "results/g3_p118_quality.json",
    "results/p118_implementation_commit.json",
    "results/p118_ci_runs.json",
)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()

def safe_name(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or path.is_absolute()
            or ".." in path.parts or "." in path.parts or "\\" in name
            or path.as_posix() != name):
        raise ValueError(f"unsafe path: {name}")
    return path

def read(name):
    cursor = ROOT
    for part in safe_name(name).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink: {name}")
    if not cursor.is_file() or cursor.stat().st_size > 8 * 1024 * 1024:
        raise ValueError(f"missing or oversized text evidence: {name}")
    raw = cursor.read_bytes()
    raw.decode("utf-8")
    return raw

def document(name):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {name}")
            value[key] = item
        return value
    return json.loads(read(name), object_pairs_hook=unique)

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def git_blob(checkpoint, name):
    return p118.p116._git_blob(checkpoint, name)

def archive_map(prefix, expected):
    directory = ROOT / prefix
    actual = set()
    require(directory.is_dir() and not directory.is_symlink(), f"archive missing: {prefix}")
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "archive contains symlink")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
        else:
            require(path.is_dir(), "archive contains special file")
    require(actual == set(expected), f"archive population differs: {prefix}")
    for name, digest in expected.items():
        require(sha(read(f"{prefix}/{name}")) == digest, f"archive byte mismatch: {name}")
    return {f"{prefix}/{name}": digest for name, digest in sorted(expected.items())}

def main():
    require(len(sys.argv) == 2 and re.fullmatch(r"[0-9a-f]{40}", sys.argv[1]), "checkpoint SHA required")
    checkpoint = sys.argv[1]
    require(not ARCHIVE.exists() and not ARCHIVE.is_symlink(), "archive already exists")
    resources, errors = run_p117_gate.inspect_resources()
    errors += run_p117_gate._resource_snapshot_errors(resources)
    print(json.dumps({"resources": resources, "errors": errors}, sort_keys=True), flush=True)
    require(not errors, "enforced workstation limits unavailable")

    require(sha(read(p118.PROTOCOL)) == p118.PROTOCOL_SHA256, "protocol changed")
    require(sha(read(p118.AMENDMENT)) == p118.AMENDMENT_SHA256, "amendment changed")
    require(sha(read(p118.INITIAL_DISCOVERY)) == p118.INITIAL_DISCOVERY_SHA256, "initial discovery changed")
    initial = document(p118.INITIAL_DISCOVERY)
    initial_expected = dict(initial["preserved_files_sha256"])
    require(len(initial_expected) == 15, "initial archive count differs")
    initial_expected["discovery.json"] = p118.INITIAL_DISCOVERY_SHA256
    initial_map = archive_map("results/failures/p118_initial", initial_expected)

    terminal_path = p118.P117_TERMINAL_DISCOVERY
    require(sha(read(terminal_path)) == p118.P117_TERMINAL_DISCOVERY_SHA256, "P117 terminal discovery changed")
    terminal = document(terminal_path)
    terminal_expected = dict(terminal["preserved_files_sha256"])
    prior = terminal["original_initial_archive"]
    require(len(terminal_expected) == 28 and len(prior["files_sha256"]) == 70, "P117 archive counts differ")
    require(prior["discovery_sha256"] == p118.P117_INITIAL_DISCOVERY_SHA256, "P117 initial identity differs")
    terminal_expected.update({f"prior_initial_archive/{name}": digest for name, digest in prior["files_sha256"].items()})
    terminal_expected["prior_initial_archive/discovery.json"] = p118.P117_INITIAL_DISCOVERY_SHA256
    terminal_expected["discovery.json"] = p118.P117_TERMINAL_DISCOVERY_SHA256
    prior_map = archive_map("results/failures/p117_terminal", terminal_expected)
    require(len(prior_map) == 100 and len(initial_map) == 16, "complete prior archives differ")
    original = document("results/failures/p117_terminal/prior_initial_archive/discovery.json")
    require("file_count" not in original and len(original["preserved_files_sha256"]) == 70,
            "actual failed schema assumption not reproduced")
    require(original["preserved_files_sha256"] == prior["files_sha256"], "nested file map differs")
    require(b'original.get("file_count") != 70' in read("controls/check_p117_history.py"),
            "failed source changed")

    sources = {name: sha(read(name)) for name in p118.CONTROL_FILES}
    require(len(sources) == len(p118.CONTROL_FILES), "duplicate source paths")
    for name, digest in sources.items():
        require(sha(git_blob(checkpoint, name)) == digest, f"checkpoint source mismatch: {name}")
    rust = p118.p116._current_rust_source_hashes()
    require(len(rust) == 100, "Rust population differs")
    for name, digest in rust.items():
        require(sha(git_blob(checkpoint, name)) == digest
                and sha(git_blob(p118.IMPLEMENTATION_COMMIT, name)) == digest,
                f"Rust changed: {name}")
    handbook, current_ok = p118.p117._handbook_snapshot()
    require(current_ok and len(handbook) == 27, "public handbook changed")
    for name, digest in handbook.items():
        require(sha(git_blob(checkpoint, name)) == digest, f"checkpoint handbook mismatch: {name}")

    copies = {}
    for name in (*p118.P118_FILES, "protocols/protocol_hash.txt"):
        copies[name] = read(name)
    gates = {}
    for name, (exit_code, cap) in EXPECTED_GATES.items():
        receipt_name = f"results/quality/p118/{name}.json"
        log_name = f"results/quality/p118/{name}.log"
        raw_name = f"target/p118-{name}.log"
        receipt = document(receipt_name)
        require(receipt["schema"] == "actinv-roadmap-gate-receipt-1"
                and receipt["phase"] == "P118" and receipt["gate"] == name
                and type(receipt["child_exit_code"]) is int
                and receipt["child_exit_code"] == exit_code
                and receipt["status"] == ("completed" if exit_code == 0 else "child_failed")
                and receipt["error"] is None and receipt["cwd"] == ".",
                f"actual gate disposition differs: {name}")
        duration = receipt["elapsed_s"]
        require(type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= cap + 30
                and type(receipt["timeout_s"]) in (int, float) and receipt["timeout_s"] == cap,
                f"gate duration or cap differs: {name}")
        require(not run_p117_gate._resource_snapshot_errors(receipt["resources"]), f"gate resources differ: {name}")
        require(receipt["log_path"] == raw_name
                and read(raw_name) == read(log_name)
                and sha(read(raw_name)) == receipt["log_sha256"], f"gate log differs: {name}")
        for path in (receipt_name, log_name, raw_name):
            copies[path] = read(path)
        gates[name] = receipt
    require(b"Ran 14 tests" in copies["results/quality/p118/recorder_regressions.log"], "recorder count differs")
    require(b"Ran 9 tests" in copies["results/quality/p118/p118_verdict_regressions.log"], "verdict count differs")
    failure_log = copies["results/quality/p118/p117_history_regressions.log"]
    require(b"Ran 12 tests" in failure_log and b"FAILED (failures=1, errors=6)" in failure_log
            and b"nested original discovery mismatch" in failure_log, "failed regression summary differs")
    missing = sorted(p118.FRESH_GATES - set(EXPECTED_GATES))
    require(len(missing) == 7, "unrun gate count differs")
    for name in missing:
        for path in (f"results/quality/p118/{name}.json", f"results/quality/p118/{name}.log", f"target/p118-{name}.log"):
            require(not (ROOT / path).exists() and not (ROOT / path).is_symlink(), f"unrun evidence exists: {path}")
    for path in ABSENT:
        require(not (ROOT / path).exists() and not (ROOT / path).is_symlink(), f"absent artifact exists: {path}")

    history_obs = document("results/quality/p118/terminal_history_observed.json")
    history_report = dict(history_obs["returned_recorder_report"])
    observed_cap = history_report.pop("timeout_s")
    recorded_report = dict(gates["p117_history_regressions"])
    recorded_cap = recorded_report.pop("timeout_s")
    require(type(history_obs["exit_code"]) is int and history_obs["exit_code"] == 1
            and observed_cap == recorded_cap == 600 and canonical(history_report) == canonical(recorded_report)
            and history_obs["repair_rounds_consumed"] == 1, "history outer observation differs")
    history_outer = read(history_obs["outer_output_path"])
    require(history_outer.startswith((history_obs["scope_header"] + "\n").encode()), "history scope header differs")
    require(json.loads(history_outer.split(b"\n", 1)[1]) == history_obs["returned_recorder_report"], "history outer report differs")

    verdict_obs = document("results/quality/p118/terminal_verdict_observed.json")
    verdict_raw = read("results/p118_verdict.json")
    verdict = json.loads(verdict_raw)
    raw_verdict_log = read(verdict_obs["raw_output_path"])
    require(type(verdict_obs["exit_code"]) is int and verdict_obs["exit_code"] == 1
            and verdict_obs["verdict"] == verdict["verdict"] == "P118-FAIL"
            and sha(verdict_raw) == verdict_obs["verdict_sha256"]
            and sha(raw_verdict_log) == verdict_obs["output_sha256"]
            and raw_verdict_log == read(verdict_obs["durable_output_path"])
            and raw_verdict_log.startswith((verdict_obs["scope_header"] + "\n").encode()),
            "terminal verdict evidence differs")
    require(not run_p117_gate._resource_snapshot_errors(verdict_obs["resources"]), "terminal verdict resources differ")
    output_lines = raw_verdict_log.split(b"\n", 2)
    require(json.loads(output_lines[1]) == {"errors": [], "resources": verdict_obs["resources"]},
            "terminal verdict resource log differs")
    writer_report = json.loads(output_lines[2])
    require(writer_report == {"gates": verdict["gates"], "persisted_matches": True, "verdict": "P118-FAIL"},
            "terminal verdict output differs")
    require(all(value is None for value in verdict["evidence_sha256"].values())
            and verdict["implementation_commit"] is None and verdict["implementation_record_sha256"] is None
            and verdict["ci_evidence_sha256"] is None, "unexecuted evidence claimed")

    extra = (
        "results/quality/p118/initial_failure_observed.json",
        "results/quality/p118/initial_failure_outer.log",
        "results/quality/p118/terminal_history_observed.json",
        "results/quality/p118/terminal_history_outer.log",
        "results/quality/p118/terminal_verdict_observed.json",
        "results/quality/p118/terminal_verdict.log",
        "results/p118_verdict.json", "target/p118-terminal-verdict.log",
        "target/p118-reset-initial-gates.py", "target/p118-initial-reset.log",
        "docs/ROADMAP.md", "docs/history/sessions/P118.md",
        "docs/maintainers/CI_DATA_RECOVERY.md",
        "docs/maintainers/ROADMAP_OPEN_ITEMS-2026-10-03.md", "ledger.md",
    )
    for name in extra:
        copies[name] = read(name)
    for name, raw in copies.items():
        if not name.startswith("target/"):
            require(git_blob(checkpoint, name) == raw, f"retained checkpoint bytes differ: {name}")
    copies["preserve_terminal.py"] = Path(__file__).read_bytes()
    discovery = {
        "schema": "actinv-p118-terminal-preservation-1",
        "phase": "P118", "checkpoint_commit": checkpoint,
        "terminal_verdict": "P118-FAIL", "terminal_gates": verdict["gates"],
        "terminal_verdict_sha256": sha(verdict_raw),
        "repair_rounds_consumed": 1, "fresh_gate_observations": gates,
        "unrun_fresh_gates": missing, "absent_artifacts": list(ABSENT),
        "G0_status": "not_sealed", "G1_status": "not_run", "G2_status": "not_run", "G3_status": "not_run",
        "failed_regressions": {"tests": 12, "failures": 1, "errors": 6, "actual_child_exit_code": 1, "actual_outer_exit_code": 1},
        "failure_cause": "history checker expects absent file_count in pinned original discovery",
        "terminal_source_sha256": sources, "unchanged_rust_sha256": rust,
        "unchanged_public_handbook_sha256": handbook,
        "predecessor_terminal_archive_sha256": prior_map,
        "initial_failure_archive_sha256": initial_map,
        "preserved_files_sha256": {name: sha(raw) for name, raw in sorted(copies.items())},
        "source_verification": "complete current/checkpoint byte identity; no qualification re-execution",
        "owner_direction": "pause at documented local stopping point",
        "successor_phase": "not_registered", "pushed": False,
        "preservation_resources": resources,
    }
    staging = ARCHIVE.with_name(ARCHIVE.name + ".staging")
    require(not staging.exists() and not staging.is_symlink(), "staging archive already exists")
    staging.mkdir()
    for name, raw in copies.items():
        destination = staging.joinpath(*safe_name(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    raw_discovery = canonical(discovery)
    with (staging / "discovery.json").open("xb") as handle:
        handle.write(raw_discovery)
        handle.flush()
        os.fsync(handle.fileno())
    require(not ARCHIVE.exists(), "archive appeared during preservation")
    staging.rename(ARCHIVE)
    expected = dict(discovery["preserved_files_sha256"])
    expected["discovery.json"] = sha(raw_discovery)
    archive_map("results/failures/p118_terminal", expected)
    print(json.dumps({"checkpoint_commit": checkpoint, "terminal_verdict": "P118-FAIL",
                      "source_files": len(sources), "rust_files": len(rust), "handbook_files": len(handbook),
                      "preserved_files": len(copies), "archive_files": len(copies) + 1,
                      "discovery_sha256": sha(raw_discovery)}, sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
