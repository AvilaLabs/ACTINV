#!/usr/bin/env python3
"""Preserve the unsealed P118 recorder failure before its sole repair."""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "controls")]
import run_p118_gate as recorder
import check_p116 as p116

ARCHIVE = ROOT / "results/failures/p118_initial"
BASE_COMMIT = "b81e8c3365a5a08ed55e9f99c0c88f945709996d"
PROTOCOL_SHA256 = "81ceb65891eae00b9ec0ade95d32bb0e8bae7ec23ef56bafd670c92e4980df1b"
SOURCES = (
    "protocols/ACTINV-P118_PROTOCOL.md", "protocols/protocol_hash.txt",
    "scripts/run_p118_gate.py", "scripts/test_run_p118_gate.py",
    "scripts/run_p117_gate.py", "controls/p105_budget_control.py",
)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(relative):
    posix = PurePosixPath(relative)
    assert not posix.is_absolute() and ".." not in posix.parts and posix.as_posix() == relative
    path = ROOT / relative
    cursor = ROOT
    for part in posix.parts:
        cursor = cursor / part
        assert not cursor.is_symlink(), relative
    assert path.is_file(), relative
    return path.read_bytes()

def main():
    assert not os.path.lexists(ARCHIVE), "refuse existing archive"
    paths = list(SOURCES)
    for name in ("rust_fmt", "recorder_regressions"):
        paths += [f"results/quality/p118/{name}.json", f"results/quality/p118/{name}.log",
                  f"target/p118-{name}.log"]
    paths += ["results/quality/p118/initial_failure_outer.log",
              "results/quality/p118/initial_failure_observed.json"]
    files = {path: read(path) for path in paths}
    files["preserve_initial.py"] = read("target/p118-preserve-initial.py")
    assert sha(files[SOURCES[0]]) == PROTOCOL_SHA256
    assert f"{PROTOCOL_SHA256}  {SOURCES[0]}" in files[SOURCES[1]].decode().splitlines()
    helper_hashes = {}
    for path in ("scripts/run_p117_gate.py", "controls/p105_budget_control.py"):
        assert p116._git_blob(BASE_COMMIT, path) == files[path]
        helper_hashes[path] = sha(files[path])
    receipts = {}
    expected_argv = {"rust_fmt": ["cargo", "fmt", "--all", "--", "--check"],
                     "recorder_regressions": ["python3", "scripts/test_run_p118_gate.py"]}
    for name, code in (("rust_fmt", 0), ("recorder_regressions", 1)):
        receipt = json.loads(files[f"results/quality/p118/{name}.json"])
        assert receipt["schema"] == "actinv-roadmap-gate-receipt-1"
        assert receipt["phase"] == "P118" and receipt["gate"] == name
        assert receipt["argv"] == expected_argv[name] and receipt["cwd"] == "."
        assert type(receipt["child_exit_code"]) is int and receipt["child_exit_code"] == code
        assert receipt["status"] == ("completed" if code == 0 else "child_failed")
        assert receipt["error"] is None
        assert not recorder._resource_snapshot_errors(receipt["resources"])
        cap = 1200 if name == "rust_fmt" else 600
        assert type(receipt["timeout_s"]) in (int, float) and receipt["timeout_s"] == cap
        assert type(receipt["elapsed_s"]) in (int, float)
        assert math.isfinite(receipt["elapsed_s"]) and 0 <= receipt["elapsed_s"] <= cap + 15
        raw = files[f"target/p118-{name}.log"]
        assert raw == files[f"results/quality/p118/{name}.log"]
        assert receipt["log_path"] == f"target/p118-{name}.log"
        assert receipt["log_sha256"] == sha(raw)
        receipts[name] = receipt
    failed_log = files["target/p118-recorder_regressions.log"].decode()
    assert "Ran 14 tests" in failed_log and "FAILED (failures=1)" in failed_log
    assert "test_setup_resource_failure_is_recorded_without_launching_runner" in failed_log
    assert "AssertionError: 'memory.max' not found in" in failed_log
    assert "resource inspection cgroup limits do not match P118" in failed_log
    outer = files["results/quality/p118/initial_failure_outer.log"].decode()
    lines = outer.splitlines()
    opening = next(i for i,line in enumerate(lines) if line == "{")
    logged = json.loads("\n".join(lines[opening:]))
    observation = json.loads(files["results/quality/p118/initial_failure_observed.json"])
    assert observation["schema"] == "actinv-p118-initial-failure-observation-1"
    assert observation["phase"] == "P118" and observation["gate"] == "recorder_regressions"
    assert observation["status"] == "completed"
    assert type(observation["exit_code"]) is int and observation["exit_code"] == 1
    assert observation["entry_point"] == [
        "python3", "scripts/run_p118_gate.py", "--phase", "P118", "--name",
        "recorder_regressions", "--timeout-s", "600", "--log",
        "target/p118-recorder_regressions.log", "--receipt",
        "results/quality/p118/recorder_regressions.json", "--", "python3",
        "scripts/test_run_p118_gate.py",
    ]
    assert observation["execution_interface"] == "exec_command"
    assert type(observation["tool_wall_time_s"]) in (int, float)
    assert math.isfinite(observation["tool_wall_time_s"]) and observation["tool_wall_time_s"] > 0
    assert observation["returned_recorder_report"] == logged == receipts["recorder_regressions"]
    scope = logged["resources"]["cgroup_path"].rsplit("/", 1)[-1]
    assert lines[0].startswith(f"Running as unit: {scope}; invocation ID: ")
    for key in ("G0_status", "G1_status", "G2_status", "G3_status"):
        assert observation[key] == ("not_sealed" if key == "G0_status" else "not_run")
    for path in ("results/g0_p118_twin_waste.json", "results/g1_p118_twin_waste.json",
                 "results/g2_p118_twin_waste.json", "results/g3_p118_quality.json",
                 "results/p118_verdict.json", "results/p118_implementation_commit.json",
                 "results/p118_ci_runs.json"):
        assert not os.path.lexists(ROOT / path), path
    hashes = {path: sha(raw) for path,raw in sorted(files.items())}
    discovery = {
        "schema": "actinv-p118-initial-preservation-1", "base_commit": BASE_COMMIT,
        "protocol_sha256": PROTOCOL_SHA256, "repair_rounds_at_failure": 0,
        "G0_status": "not_sealed", "G1_status": "not_run", "G2_status": "not_run", "G3_status": "not_run",
        "failed_gate": "recorder_regressions", "actual_outer_exit_code": 1,
        "actual_child_exit_code": 1, "test_count": 14, "test_failure_count": 1,
        "failed_test": "test_setup_resource_failure_is_recorded_without_launching_runner",
        "successful_gates": ["rust_fmt"], "executed_source_sha256": {p: hashes[p] for p in SOURCES},
        "unchanged_helper_git_sha256": helper_hashes, "preserved_files_sha256": hashes,
        "observation_sha256": hashes["results/quality/p118/initial_failure_observed.json"],
        "outer_output_sha256": hashes["results/quality/p118/initial_failure_outer.log"],
    }
    raw_discovery = (json.dumps(discovery, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    cursor = ROOT
    for part in ARCHIVE.relative_to(ROOT).parts:
        cursor = cursor / part
        assert not cursor.is_symlink(), "archive symlink"
    ARCHIVE.mkdir(parents=True)
    for relative,raw in files.items():
        destination = ARCHIVE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    with (ARCHIVE / "discovery.json").open("xb") as stream:
        stream.write(raw_discovery)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"archive": ARCHIVE.relative_to(ROOT).as_posix(),
                      "preserved_files": len(files), "discovery_sha256": sha(raw_discovery),
                      "actual_outer_exit": 1, "actual_child_exit": 1}, sort_keys=True))

if __name__ == "__main__":
    main()
