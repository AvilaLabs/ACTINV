#!/usr/bin/env python3
"""P118 source seal and exact replay for the unchanged P116 twin controls."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_p116 as p116
import check_p116_history
import check_p116_verdict
import p105_budget_control
import check_p112_verdict
import fetch_ci_seed
import check_p117 as p117
import check_p117_history
import run_p118_gate

PHASE = "P118"
PROTOCOL = "protocols/ACTINV-P118_PROTOCOL.md"
PROTOCOL_SHA256 = "81ceb65891eae00b9ec0ade95d32bb0e8bae7ec23ef56bafd670c92e4980df1b"
AMENDMENT = "protocols/ACTINV-P118_AMENDMENT_A.md"
AMENDMENT_SHA256 = "a3dd8e42248abdc73eb58e5c490e18a3f1a6223496f0b7c005dce225f1c3e6ac"
INITIAL_DISCOVERY = "results/failures/p118_initial/discovery.json"
INITIAL_DISCOVERY_SHA256 = "21a94cd256bbbb858d9cfc9ebb342e15f3ada0b6d8e73ea39bf9112ac837f080"
INITIAL_FAILURE_COMMIT = "b81e8c3365a5a08ed55e9f99c0c88f945709996d"
INITIAL_RECORDER_SHA256 = "0c90a5bda677c67a7af1f310fe0ec8ccc0e675fc6d8e2ae648d7687737adb252"
INITIAL_RECORDER_TEST_SHA256 = "8e8e96109cb2d8a6241e459d2ec1ce03623ae846717b2bb938f67762f43b9085"
INITIAL_ARCHIVE = ROOT / "results/failures/p118_initial"
IMPLEMENTATION_COMMIT = "2117f3b5df715ce832658f58250103789be845bb"
P116_G0_SHA256 = "7e08a5f94e2cce3d62229c1e1233938270420c27556abb1a81e3f304ebc4cf78"
P116_G1_SHA256 = "74f6eab80ee223c81b6f7391f3f4147d7fa38ffeac55485f3d2a05e8618a65b9"
P116_G2_SHA256 = "5e1cb32140fa7fe3000bf2d22e54b30af021d992f689d1a1a7ff1038b979fb6b"
P116_G3_SHA256 = "746876fc7c2d9da2432c5e1a816702a648f18c9ae920798c2639f9670082dd5b"
P116_VERDICT_SHA256 = "7d782e476b051cc2e7d9666e984511167f30d6e1e5ec73afc127d34ac9dac710"
P116_IMPLEMENTATION_SHA256 = "be2e96c12e5fd5356eb493bc14909a782f6f6cba2fb739645d264ae0f1b249fa"
P116_CI_SHA256 = "df939adb6bcb2a81bb9bca58df8638ea16ce0cdfcf94f89f1e07b3d302ff3c73"
P116_ARCHIVE_DISCOVERY_SHA256 = "c007355933dd2f685f6189714a96277e6448a9da0802c0c18db331f72d660039"
P117_TERMINAL_ARCHIVE = ROOT / "results/failures/p117_terminal"
P117_TERMINAL_DISCOVERY = "results/failures/p117_terminal/discovery.json"
P117_TERMINAL_DISCOVERY_SHA256 = "0c736be95e42413481378d38ebaea66424caa5caab1aa7712699e93f1866a26e"
P117_TERMINAL_CHECKPOINT = "b81e8c3365a5a08ed55e9f99c0c88f945709996d"
P117_INITIAL_DISCOVERY_SHA256 = "73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a"
P117_INITIAL_VERDICT_SHA256 = "871231b33029b8696e9ed2f0e260a1f8b44b01a49b08803fd9e7f866f16c96a8"
P117_TERMINAL_VERDICT_SHA256 = "11ea137a5362b5cc42c771137771cbad08b5cd7084939480138b80e656ee4e9b"
P117_DUPLICATE_OBSERVATION_SHA256 = "ce4ce3483ae7b1afb4a9a8947d83d1c9611c835d01b12e9fb4b042bf49e39b50"
P117_DUPLICATE_OUTPUT_SHA256 = "4f989719df72b3ef41050748bb96939364f4f1b683b7545d4ee5529f3dbe7dee"
MANIFEST = "scripts/ci_data_seed.json"
NOTICE = "docs/maintainers/CI_DATA_CACHE_NOTICE.md"
STAGE_SOURCE = "results/quality/p117/source/prepare_seed.py"
G0 = ROOT / "results/g0_p118_twin_waste.json"
G1 = ROOT / "results/g1_p118_twin_waste.json"
G2 = ROOT / "results/g2_p118_twin_waste.json"
G3 = ROOT / "results/g3_p118_quality.json"
P116_G0 = ROOT / "results/g0_p116_twin_waste.json"
P116_G1 = ROOT / "results/g1_p116_twin_waste.json"
P116_G2 = ROOT / "results/g2_p116_twin_waste.json"
P116_G3 = ROOT / "results/g3_p116_quality.json"
P116_VERDICT = ROOT / "results/p116_verdict.json"
P116_IMPLEMENTATION = ROOT / "results/p116_implementation_commit.json"
P116_CI = ROOT / "results/p116_ci_runs.json"
ACTINV = p116.ACTINV
FRESH_GATES = {
    "rust_fmt", "p118_regressions", "p118_verdict_regressions", "p117_history_regressions",
    "p117_history_replay", "recorder_regressions", "g0_seal", "g0_replay", "g1", "g2",
    "full_read_only_replay",
}
P117_ADOPTED_GATES = set(p117.ADOPTED_INITIAL_GATES)
QUALITY_GATES = {"rust_fmt", "seed_local_verify", "seed_offline_install", "seed_release_download",
                 "native_data_fetch", "fns_science", "fns_diagnosis"}
RESOURCES = {
    "memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0, "tasks_max": 128,
    "cpu_quota_percent": 200, "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial",
}

# P117 evidence is historical input. Its source population and terminal archive
# are independently checked by check_p117_history before any native replay.
P118_FILES = (
    PROTOCOL, AMENDMENT, INITIAL_DISCOVERY, ".github/workflows/ci.yml", "scripts/run_p118_gate.py",
    "scripts/test_run_p118_gate.py", "controls/check_p117_history.py",
    "controls/test_p117_history.py", "controls/check_p118.py",
    "controls/check_p118_verdict.py", "controls/test_p118.py",
    "controls/test_p118_verdict.py",
)


def _initial_failure_control_paths() -> tuple[str, ...]:
    try:
        raw = (ROOT / INITIAL_DISCOVERY).read_bytes()
        if hashlib.sha256(raw).hexdigest() != INITIAL_DISCOVERY_SHA256:
            return ()
        document = json.loads(raw.decode("utf-8"))
        files = document.get("preserved_files_sha256") if isinstance(document, dict) else None
        if not isinstance(files, dict) or len(files) != 15:
            return ()
        names = []
        for name in files:
            path = PurePosixPath(name) if isinstance(name, str) else PurePosixPath("/")
            if (path.is_absolute() or not name or ".." in path.parts or "." in path.parts
                    or "\\" in name or path.as_posix() != name):
                return ()
            names.append(f"results/failures/p118_initial/{name}")
        return tuple(sorted(names))
    except (OSError, ValueError, TypeError, UnicodeError):
        return ()


def _terminal_archive_control_paths() -> tuple[str, ...]:
    try:
        def names_from(relative: str, expected_sha: str, prefix: str) -> list[str]:
            raw = (ROOT / relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_sha:
                return []
            discovery = json.loads(raw.decode("utf-8"))
            files = discovery.get("preserved_files_sha256") if isinstance(discovery, dict) else None
            if not isinstance(files, dict):
                return []
            result = []
            for name in files:
                safe = PurePosixPath(name) if isinstance(name, str) else PurePosixPath("/")
                if (safe.is_absolute() or not name or ".." in safe.parts or "." in safe.parts
                        or "\\" in name or safe.as_posix() != name):
                    return []
                result.append(f"{prefix}{name}")
            return result
        top = names_from(P117_TERMINAL_DISCOVERY, P117_TERMINAL_DISCOVERY_SHA256,
                         "results/failures/p117_terminal/")
        nested = names_from("results/failures/p117_terminal/prior_initial_archive/discovery.json",
                            P117_INITIAL_DISCOVERY_SHA256,
                            "results/failures/p117_terminal/prior_initial_archive/")
        if len(top) != 28 or len(nested) != 70:
            return ()
        return tuple(sorted((P117_TERMINAL_DISCOVERY,
                             "results/failures/p117_terminal/prior_initial_archive/discovery.json",
                             *top, *nested)))
    except (OSError, ValueError, TypeError, UnicodeError):
        return ()


TERMINAL_ARCHIVE_FILES = _terminal_archive_control_paths()
INITIAL_FAILURE_FILES = _initial_failure_control_paths()
CONTROL_FILES = tuple(dict.fromkeys((*p117.CONTROL_FILES, *P118_FILES,
                                     *TERMINAL_ARCHIVE_FILES, *INITIAL_FAILURE_FILES)))


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a nonempty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value or path.as_posix() != value:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def _safe_file(relative: object) -> Path:
    safe = _safe_rel(relative)
    path = ROOT / safe
    path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    current = ROOT
    for part in safe.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink in sealed source path")
    if not path.is_file():
        raise ValueError("sealed path is not a regular file")
    return path


def _control_hashes() -> dict[str, str]:
    return {name: _sha(_safe_file(name)) or "" for name in CONTROL_FILES}


def _p116_current_identity() -> tuple[dict, dict, dict, dict, dict]:
    g0, g1, g2, g3 = map(_read, (P116_G0, P116_G1, P116_G2, P116_G3))
    verdict, implementation, ci = map(_read, (P116_VERDICT, P116_IMPLEMENTATION, P116_CI))
    objects = (g0, g1, g2, g3, verdict, implementation)
    if (any(not isinstance(item, dict) for item in objects)
            or not isinstance(ci, list) or len(ci) != 6):
        raise ValueError("P116 immutable evidence is missing or malformed")
    fixed = ((P116_G0, P116_G0_SHA256), (P116_G1, P116_G1_SHA256),
             (P116_G2, P116_G2_SHA256), (P116_G3, P116_G3_SHA256),
             (P116_VERDICT, P116_VERDICT_SHA256),
             (P116_IMPLEMENTATION, P116_IMPLEMENTATION_SHA256), (P116_CI, P116_CI_SHA256))
    if any(_sha(path) != digest for path, digest in fixed):
        raise ValueError("an immutable P116 artifact differs from its pinned identity")
    if (implementation.get("commit_sha") != IMPLEMENTATION_COMMIT
            or verdict.get("verdict") != "P116-FAIL"
            or verdict.get("implementation_commit") != IMPLEMENTATION_COMMIT
            or _sha(ROOT / "results/failures/p116_ci/discovery.json") != P116_ARCHIVE_DISCOVERY_SHA256):
        raise ValueError("P116 terminal implementation/failure identity changed")
    return g0, g1, g2, g3, verdict


def _source_population_ok(g0: dict, history_proof: dict | None = None) -> tuple[bool, dict[str, str], dict[str, str]]:
    try:
        hashes = _control_hashes()
        proof = history_proof if isinstance(history_proof, dict) else _repair_evidence()
        old_control = proof.get("terminal_source_sha256")
        if (not isinstance(old_control, dict) or len(old_control) != 193
                or any(hashes.get(k) != v for k, v in old_control.items()
                       if k != ".github/workflows/ci.yml")):
            return False, hashes, {}
        if set(hashes) != set(CONTROL_FILES):
            return False, hashes, {}
        current_rust = p116._current_rust_source_hashes()
        old_rust = proof.get("history", {}).get("inherited_rust_sha256")
        commit_paths = p116._rust_paths_at_commit(P117_TERMINAL_CHECKPOINT)
        if (not isinstance(old_rust, dict) or len(old_rust) != 100
                or set(current_rust) != set(old_rust) or set(current_rust) != commit_paths
                or current_rust != old_rust):
            return False, hashes, current_rust
        return True, hashes, current_rust
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False, {}, {}


def _handbook_snapshot() -> tuple[dict[str, str], bool]:
    try:
        paths = check_p112_verdict._handbook_paths(IMPLEMENTATION_COMMIT)
        hashes = {path: _sha_bytes(p116._git_blob(IMPLEMENTATION_COMMIT, path))
                  for path in sorted(paths)}
        current_paths = check_p112_verdict._handbook_paths(None)
        current_ok = (current_paths == paths and all(_sha(ROOT / path) == digest
                                                     for path, digest in hashes.items()))
        return hashes, current_ok
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        return {}, False


def _unique_json(raw: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


def _archive_resource_ok(resources: object) -> bool:
    if not isinstance(resources, dict):
        return False
    tmp = resources.get("tmpdir")
    cgroup = resources.get("cgroup_path")
    pieces = cgroup.split("/") if isinstance(cgroup, str) else []
    return (resources.get("platform") == "linux"
            and isinstance(cgroup, str) and cgroup.startswith("/user.slice/")
            and cgroup.endswith(".scope")
            and not any(piece in {".", ".."} for piece in pieces)
            and not any(not piece for piece in pieces[1:])
            and resources.get("cgroup_limits") == {
                "memory.max": "6442450944", "memory.swap.max": "0",
                "pids.max": "128", "cpu.max": "200000 100000"}
            and resources.get("environment") == {
                "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            and isinstance(tmp, dict) and tmp.get("path") == "target/preflight-tmp"
            and isinstance(tmp.get("mount_point"), str) and Path(tmp["mount_point"]).is_absolute()
            and isinstance(tmp.get("filesystem"), str) and bool(tmp["filesystem"].strip())
            and tmp["filesystem"].lower() not in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"})


def _initial_failure_evidence() -> dict:
    try:
        raw = _safe_file(INITIAL_DISCOVERY).read_bytes()
        if _sha_bytes(raw) != INITIAL_DISCOVERY_SHA256:
            raise ValueError("P118 initial discovery digest changed")
        discovery = _unique_json(raw)
        files = discovery.get("preserved_files_sha256")
        if not isinstance(files, dict) or len(files) != 15:
            raise ValueError("P118 initial failure archive must contain 15 retained files")
        safe = {_safe_rel(name).as_posix() for name in files}
        if len(safe) != len(files) or "discovery.json" in safe:
            raise ValueError("P118 initial archive has duplicate or reserved paths")
        physical = set()
        for path in INITIAL_ARCHIVE.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlink in P118 initial archive")
            if path.is_file():
                physical.add(path.relative_to(INITIAL_ARCHIVE).as_posix())
            elif not path.is_dir():
                raise ValueError("nonregular entry in P118 initial archive")
        if physical != safe | {"discovery.json"}:
            raise ValueError("P118 initial archive has missing or extra files")
        sealed: dict[str, str] = {}
        for relative, expected in files.items():
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise ValueError("invalid P118 initial archive hash")
            path = _safe_file(f"results/failures/p118_initial/{relative}")
            if _sha(path) != expected:
                raise ValueError("P118 initial archived bytes changed")
            sealed[f"results/failures/p118_initial/{relative}"] = expected
        executed = discovery.get("executed_source_sha256")
        expected_executed = {
            "controls/p105_budget_control.py": files["controls/p105_budget_control.py"],
            "protocols/ACTINV-P118_PROTOCOL.md": files["protocols/ACTINV-P118_PROTOCOL.md"],
            "protocols/protocol_hash.txt": files["protocols/protocol_hash.txt"],
            "scripts/run_p117_gate.py": files["scripts/run_p117_gate.py"],
            "scripts/run_p118_gate.py": files["scripts/run_p118_gate.py"],
            "scripts/test_run_p118_gate.py": files["scripts/test_run_p118_gate.py"],
        }
        if executed != expected_executed:
            raise ValueError("initial execution source map differs from the byte archive")
        if (executed.get("protocols/ACTINV-P118_PROTOCOL.md") != PROTOCOL_SHA256
                or executed.get("scripts/run_p118_gate.py") != INITIAL_RECORDER_SHA256
                or executed.get("scripts/test_run_p118_gate.py") != INITIAL_RECORDER_TEST_SHA256
                or executed.get("controls/p105_budget_control.py") != "b9b69dc424a37d45b41b165276884c3d032d32ea968e3fd0f4c85add653c41bb"
                or executed.get("scripts/run_p117_gate.py") != "ae1e8cbfa837a10266af00474c4f963daf685bf942322ff868fe6c0b9300be78"
                or executed.get("protocols/protocol_hash.txt") != "df33906bd4d77cfe5a781259b7d1fa91cd2a357c70de8e067a8b617e26b9667a"):
            raise ValueError("initial execution sources differ from pinned historical identities")
        for source_path, source_hash in executed.items():
            _safe_rel(source_path)
            if not isinstance(source_hash, str) or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
                raise ValueError("invalid original P118 source digest")
        observation = _unique_json(_safe_file(
            "results/failures/p118_initial/results/quality/p118/initial_failure_observed.json").read_bytes())
        failed = _unique_json(_safe_file(
            "results/failures/p118_initial/results/quality/p118/recorder_regressions.json").read_bytes())
        fmt = _unique_json(_safe_file(
            "results/failures/p118_initial/results/quality/p118/rust_fmt.json").read_bytes())
        failed_log = _safe_file("results/failures/p118_initial/target/p118-recorder_regressions.log").read_text()
        failed_copy = _safe_file("results/failures/p118_initial/results/quality/p118/recorder_regressions.log").read_bytes()
        fmt_raw = _safe_file("results/failures/p118_initial/target/p118-rust_fmt.log").read_bytes()
        fmt_copy = _safe_file("results/failures/p118_initial/results/quality/p118/rust_fmt.log").read_bytes()
        old_protocol_hash = _safe_file("results/failures/p118_initial/protocols/protocol_hash.txt").read_text()
        old_protocol_lines = old_protocol_hash.splitlines()
        current_registry = _safe_file("protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
        outer_raw = _safe_file(
            "results/failures/p118_initial/results/quality/p118/initial_failure_outer.log").read_bytes()
        outer_text = outer_raw.decode("utf-8")
        outer_lines = outer_text.splitlines()
        outer_receipt = _unique_json("\n".join(outer_lines[1:]).encode("utf-8")) if len(outer_lines) > 1 else None
        expected_argv = ["python3", "scripts/test_run_p118_gate.py"]
        expected_entry = ["python3", "scripts/run_p118_gate.py", "--phase", "P118", "--name",
                          "recorder_regressions", "--timeout-s", "600", "--log",
                          "target/p118-recorder_regressions.log", "--receipt",
                          "results/quality/p118/recorder_regressions.json", "--", *expected_argv]
        timeout = failed.get("timeout_s")
        elapsed = failed.get("elapsed_s")
        fmt_timeout = fmt.get("timeout_s")
        fmt_elapsed = fmt.get("elapsed_s")
        if (discovery.get("schema") != "actinv-p118-initial-preservation-1"
                or discovery.get("protocol_sha256") != PROTOCOL_SHA256
                or discovery.get("base_commit") != INITIAL_FAILURE_COMMIT
                or discovery.get("failed_gate") != "recorder_regressions"
                or type(discovery.get("repair_rounds_at_failure")) is not int
                or discovery.get("repair_rounds_at_failure") != 0
                or discovery.get("G0_status") != "not_sealed"
                or discovery.get("G1_status") != "not_run" or discovery.get("G2_status") != "not_run"
                or discovery.get("G3_status") != "not_run"
                or type(discovery.get("actual_child_exit_code")) is not int
                or discovery.get("actual_child_exit_code") != 1
                or type(discovery.get("actual_outer_exit_code")) is not int
                or discovery.get("actual_outer_exit_code") != 1
                or discovery.get("successful_gates") != ["rust_fmt"]
                or type(discovery.get("test_count")) is not int
                or discovery.get("test_count") != 14 or discovery.get("test_failure_count") != 1
                or type(discovery.get("test_failure_count")) is not int
                or observation.get("schema") != "actinv-p118-initial-failure-observation-1"
                or observation.get("phase") != "P118" or observation.get("gate") != "recorder_regressions"
                or observation.get("status") != "completed" or type(observation.get("exit_code")) is not int
                or observation.get("exit_code") != 1
                or observation.get("entry_point") != expected_entry
                or observation.get("execution_interface") != "exec_command"
                or observation.get("outer_output_path") != "results/quality/p118/initial_failure_outer.log"
                or not isinstance(observation.get("tool_wall_time_s"), (int, float))
                or isinstance(observation.get("tool_wall_time_s"), bool)
                or not math.isfinite(float(observation["tool_wall_time_s"]))
                or observation["tool_wall_time_s"] <= 0
                or observation.get("G0_status") != "not_sealed"
                or observation.get("G1_status") != "not_run" or observation.get("G2_status") != "not_run"
                or observation.get("G3_status") != "not_run"
                or observation.get("returned_recorder_report") != failed
                or observation.get("returned_recorder_report") != outer_receipt
                or not outer_lines[0].startswith("Running as unit: ")
                or outer_lines[0].count("invocation ID: ") != 1
                or failed.get("phase") != "P118" or failed.get("gate") != "recorder_regressions"
                or failed.get("status") != "child_failed" or type(failed.get("child_exit_code")) is not int
                or failed.get("child_exit_code") != 1
                or failed.get("cwd") != "." or failed.get("argv") != expected_argv
                or failed.get("schema") != "actinv-roadmap-gate-receipt-1"
                or failed.get("error") is not None
                or failed.get("log_path") != "target/p118-recorder_regressions.log"
                or type(timeout) not in (int, float) or isinstance(timeout, bool)
                or not math.isfinite(float(timeout)) or timeout != 600
                or type(elapsed) not in (int, float) or isinstance(elapsed, bool)
                or not math.isfinite(float(elapsed)) or elapsed < 0 or elapsed > float(timeout) + run_p118_gate.CLEANUP_HEADROOM_S
                or not _archive_resource_ok(failed.get("resources"))
                or run_p118_gate._resource_snapshot_errors(failed.get("resources"))
                or failed.get("log_sha256") != _sha_bytes(failed_copy)
                or failed_copy != _safe_file("results/failures/p118_initial/target/p118-recorder_regressions.log").read_bytes()
                or fmt.get("schema") != "actinv-roadmap-gate-receipt-1"
                or fmt.get("phase") != "P118" or fmt.get("gate") != "rust_fmt"
                or fmt.get("status") != "completed" or type(fmt.get("child_exit_code")) is not int
                or fmt.get("child_exit_code") != 0 or fmt.get("cwd") != "." or fmt.get("error") is not None
                or fmt.get("argv") != ["cargo", "fmt", "--all", "--", "--check"]
                or fmt.get("log_path") != "target/p118-rust_fmt.log"
                or type(fmt_timeout) not in (int, float) or isinstance(fmt_timeout, bool)
                or not math.isfinite(float(fmt_timeout)) or fmt_timeout != 1200
                or type(fmt_elapsed) not in (int, float) or isinstance(fmt_elapsed, bool)
                or not math.isfinite(float(fmt_elapsed)) or fmt_elapsed < 0
                or fmt_elapsed > float(fmt_timeout) + run_p118_gate.CLEANUP_HEADROOM_S
                or not _archive_resource_ok(fmt.get("resources"))
                or run_p118_gate._resource_snapshot_errors(fmt.get("resources"))
                or fmt.get("log_sha256") != _sha_bytes(fmt_copy)
                or fmt_copy != fmt_raw
                or "Ran 14 tests" not in failed_log or "FAILED (failures=1)" not in failed_log
                or "test_setup_resource_failure_is_recorded_without_launching_runner" not in failed_log
                or "AssertionError: 'memory.max' not found in" not in failed_log
                or "resource inspection cgroup limits do not match P118" not in failed_log
                or old_protocol_lines.count(f"{PROTOCOL_SHA256}  {PROTOCOL}") != 1
                or old_protocol_lines.count(f"{AMENDMENT_SHA256}  {AMENDMENT}") != 0
                or current_registry.count(f"{PROTOCOL_SHA256}  {PROTOCOL}") != 1
                or current_registry.count(f"{AMENDMENT_SHA256}  {AMENDMENT}") != 1
                or _sha(ROOT / AMENDMENT) != AMENDMENT_SHA256
                or _sha_bytes(outer_raw) != discovery.get("outer_output_sha256")
                or _sha_bytes(_safe_file(
                    "results/failures/p118_initial/results/quality/p118/initial_failure_observed.json").read_bytes())
                    != discovery.get("observation_sha256")):
            raise ValueError("P118 initial failure does not match Amendment A")
        scope = failed["resources"]["cgroup_path"].rsplit("/", 1)[-1]
        expected_scope_line = f"Running as unit: {scope}; invocation ID: "
        if (not outer_lines[0].startswith(expected_scope_line)
                or not outer_lines[0][len(expected_scope_line):].strip()
                or outer_lines[0].count("invocation ID: ") != 1
                or _unique_json("\n".join(outer_lines[1:]).encode("utf-8")) != failed):
            raise ValueError("initial outer recorder output does not match its scope and receipt")
        # These two dependency files must be exactly the already-reviewed base blobs.
        unchanged_helpers = discovery.get("unchanged_helper_git_sha256")
        if (not isinstance(unchanged_helpers, dict) or set(unchanged_helpers) != {
                "controls/p105_budget_control.py", "scripts/run_p117_gate.py"}):
            raise ValueError("initial helper Git map is incomplete")
        for path, expected in unchanged_helpers.items():
            if (expected != executed.get(path)
                    or _sha_bytes(p116._git_blob(INITIAL_FAILURE_COMMIT, path)) != expected):
                raise ValueError("initial P118 dependency differs from checkpoint Git")
        return {"pass": True, "discovery_sha256": INITIAL_DISCOVERY_SHA256,
                "preserved_files_sha256": sealed, "actual_child_exit_code": 1,
                "actual_outer_exit_code": 1, "failed_gate": "recorder_regressions",
                "failed_test": "test_setup_resource_failure_is_recorded_without_launching_runner",
                "test_count": 14, "test_failure_count": 1,
                "rust_fmt_exit_code": 0, "failure_receipt_sha256": files[
                    "results/quality/p118/recorder_regressions.json"],
                "failure_log_sha256": files["target/p118-recorder_regressions.log"],
                "outer_observation_sha256": files["results/quality/p118/initial_failure_observed.json"],
                "outer_log_sha256": files["results/quality/p118/initial_failure_outer.log"]}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError,
            ImportError, UnicodeError, json.JSONDecodeError):
        return {"pass": False, "preserved_files_sha256": {}}


def _repair_evidence() -> dict:
    """Verify immutable P117 terminal FAIL and the complete source transition."""
    try:
        initial_failure = _initial_failure_evidence()
        registry = _safe_file("protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
        amendment_registered = (registry.count(f"{AMENDMENT_SHA256}  {AMENDMENT}") == 1
                                and _sha(ROOT / AMENDMENT) == AMENDMENT_SHA256)
        if initial_failure.get("pass") is not True or not amendment_registered:
            raise ValueError("registered P118 repair evidence is incomplete")
        report = check_p117_history.verify()
        terminal_source = report.get("terminal_source_sha256")
        terminal_files = report.get("terminal_archive_files_sha256")
        initial_files = report.get("initial_archive_files_sha256")
        if (report.get("schema") != "actinv-p117-history-verification-1"
                or report.get("pass") is not True or report.get("phase") != "P117"
                or report.get("verdict") != "P117-FAIL"
                or report.get("checkpoint_commit") != P117_TERMINAL_CHECKPOINT
                or report.get("derived_verdict_matches_persisted") is not True
                or report.get("historical_p116_verified") is not True
                or type(report.get("control_source_count")) is not int
                or report.get("control_source_count") != 193
                or type(report.get("current_rust_source_count")) is not int
                or report.get("current_rust_source_count") != 100
                or report.get("current_source_matches_except_ci") is not True
                or not isinstance(terminal_source, dict) or len(terminal_source) != 193
                or not isinstance(terminal_files, dict) or len(terminal_files) != 100
                or not isinstance(initial_files, dict) or len(initial_files) != 71):
            raise ValueError("P117 terminal history summary is incomplete")
        if (_sha(ROOT / P117_TERMINAL_DISCOVERY) != P117_TERMINAL_DISCOVERY_SHA256
                or _sha(ROOT / "results/p117_verdict.json") != P117_TERMINAL_VERDICT_SHA256
                or report.get("terminal_archive_files_sha256") != terminal_files):
            raise ValueError("P117 terminal history artifact identity changed")
        p117_g0 = _read(P117_TERMINAL_ARCHIVE / "results/g0_p117_twin_waste.json")
        p117_g1 = _read(P117_TERMINAL_ARCHIVE / "results/g1_p117_twin_waste.json")
        terminal_verdict = _read(P117_TERMINAL_ARCHIVE / "results/p117_verdict.json")
        initial_g3 = p117._read(p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
        if (not isinstance(p117_g0, dict) or p117_g0.get("control_sha256") != terminal_source
                or p117_g0.get("inherited_rust_sha256") != report.get("inherited_rust_sha256")
                or not isinstance(p117_g1, dict) or p117_g1.get("fresh_p117_report") != _read(P116_G1)
                or not isinstance(terminal_verdict, dict) or terminal_verdict.get("verdict") != "P117-FAIL"
                or _sha(P117_TERMINAL_ARCHIVE / "results/p117_verdict.json") != P117_TERMINAL_VERDICT_SHA256
                or not isinstance(initial_g3, dict)
                or set(initial_g3.get("fresh_gates", {})) != p117.INITIAL_GATES):
            raise ValueError("P117 source seal or terminal report identity changed")
        discovery = _read(P117_TERMINAL_ARCHIVE / "discovery.json")
        if (not isinstance(discovery, dict)
                or discovery.get("schema") != "actinv-p117-terminal-preservation-1"
                or discovery.get("checkpoint_commit") != P117_TERMINAL_CHECKPOINT
                or discovery.get("terminal_verdict") != "P117-FAIL"
                or discovery.get("absent_artifacts") != ["results/g2_p117_twin_waste.json",
                                                           "results/g3_p117_quality.json"]):
            raise ValueError("P117 terminal archive metadata changed")
        # P117 source files remain at their terminal values, except the one CI
        # workflow transition required to replace live P117 gates with history.
        current_source = {path: _sha(_safe_file(path)) for path in terminal_source}
        if (set(current_source) != set(terminal_source)
                or any(value is None for value in current_source.values())
                or any(current_source[path] != digest for path, digest in terminal_source.items()
                       if path != ".github/workflows/ci.yml")):
            raise ValueError("P117 source transition changed beyond the registered CI workflow")
        rust = p116._current_rust_source_hashes()
        if (len(rust) != 100 or rust != report.get("inherited_rust_sha256")
                or set(rust) != p116._rust_paths_at_commit(P117_TERMINAL_CHECKPOINT)):
            raise ValueError("P117 Rust source population changed")
        for relative, expected in terminal_files.items():
            path = _safe_file(relative)
            if _sha(path) != expected:
                raise ValueError("P117 terminal archive file changed")
        for relative, expected in initial_files.items():
            path = _safe_file(relative)
            if _sha(path) != expected:
                raise ValueError("P117 initial archive file changed")
        return {"pass": True, "history": report, "initial_failure": initial_failure,
                "amendment_registered": True, "amendment_sha256": AMENDMENT_SHA256,
                "terminal_source_sha256": terminal_source,
                "terminal_archive_files_sha256": terminal_files,
                "initial_archive_files_sha256": initial_files,
                "p117_g0_sha256": _sha(P117_TERMINAL_ARCHIVE / "results/g0_p117_twin_waste.json"),
                "p117_g1_sha256": _sha(P117_TERMINAL_ARCHIVE / "results/g1_p117_twin_waste.json"),
                "terminal_verdict_sha256": P117_TERMINAL_VERDICT_SHA256}
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return {"pass": False, "history": {}, "initial_failure": {"pass": False}}


def _adopted_initial_entry_matches(name: str, expected: object, *, require_raw_target: bool) -> bool:
    """Verify one of P117's adopted successes, portable without ignored target files."""
    if name not in P117_ADOPTED_GATES or not isinstance(expected, dict):
        return False
    try:
        base = "results/failures/p117_terminal/prior_initial_archive/"
        archive_receipt = _safe_file(base + f"results/quality/p117/{name}.json").read_bytes()
        archive_log = _safe_file(base + f"results/quality/p117/{name}.log").read_bytes()
        archive_raw = _safe_file(base + f"target/p117-{name}.log").read_bytes()
        live_receipt = _safe_file(f"results/quality/p117/{name}.json").read_bytes()
        live_log = _safe_file(f"results/quality/p117/{name}.log").read_bytes()
        receipt = json.loads(live_receipt)
        if (not isinstance(receipt, dict) or archive_receipt != live_receipt
                or archive_log != archive_raw or archive_log != live_log
                or _sha_bytes(archive_receipt) != expected.get("receipt_sha256")
                or _sha_bytes(archive_log) != expected.get("log_sha256")):
            return False
        if require_raw_target:
            live_raw = _safe_file(f"target/p117-{name}.log").read_bytes()
            if live_raw != archive_raw:
                return False
        _archived = _safe_entry_from_p117_g3(name)
        return _archived == expected and receipt.get("child_exit_code") == 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _safe_entry_from_p117_g3(name: str) -> dict | None:
    try:
        g3 = p117._read(p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
        return g3.get("fresh_gates", {}).get(name) if isinstance(g3, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _prior_summary() -> dict:
    g0, g1, g2, g3, verdict = _p116_current_identity()
    return {
        "checkpoint_commit": IMPLEMENTATION_COMMIT,
        "g0_sha256": P116_G0_SHA256, "g1_sha256": P116_G1_SHA256,
        "g2_sha256": P116_G2_SHA256, "g3_sha256": P116_G3_SHA256,
        "verdict_sha256": P116_VERDICT_SHA256,
        "implementation_record_sha256": P116_IMPLEMENTATION_SHA256,
        "ci_record_sha256": P116_CI_SHA256,
        "ci_archive_discovery_sha256": P116_ARCHIVE_DISCOVERY_SHA256,
        "verdict": verdict["verdict"], "p116_g1_counts": {
            "requests": g1["request_count"], "component_targets": g1["component_target_count"],
            "comparisons": g1["independent_comparison_count"],
            "mutations": len(g1["mutations_rejected"]),
            "refusals": len(g1["refusal_controls"]["checks"]),
        },
        "p116_g3_gate_count": len(g3["gates"]),
    }


def _g0_base() -> dict:
    prior: dict = {}
    hashes: dict[str, str] = {}
    history = {"pass": False}
    registered = False
    authorities = False
    rust_ok = False
    rust_hashes: dict[str, str] = {}
    manifest: dict = {}
    predecessor_ok = False
    handbook_hashes: dict[str, str] = {}
    handbook_ok = False
    history_proof = _repair_evidence()
    try:
        prior = _prior_summary()
        registry = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
        registered = (f"{PROTOCOL_SHA256}  {PROTOCOL}" in registry
                      and _sha(ROOT / PROTOCOL) == PROTOCOL_SHA256)
        manifest = fetch_ci_seed.load_manifest()
        authorities = fetch_ci_seed.validate_authorities(manifest) is True
        history = history_proof.get("history", {})
        rust_ok, hashes, rust_hashes = _source_population_ok(_read(P116_G0), history_proof)
        handbook_hashes, handbook_ok = _handbook_snapshot()
        predecessor_ok = (history_proof.get("pass") is True
                          and history.get("derived_verdict_matches_persisted") is True
                          and history.get("verdict") == "P117-FAIL")
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        predecessor_ok = False
    p116_history_ok = history.get("historical_p116_verified") is True
    ok = (registered and authorities and rust_ok and handbook_ok and history_proof.get("pass") is True
          and p116_history_ok and predecessor_ok)
    report = {
        "schema": "actinv-p118-twin-waste-g0-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(ok),
        "protocol_registered": bool(registered), "seed_authorities_match": bool(authorities),
        "historical_p116_verified": bool(p116_history_ok),
        "historical_p117_verified": history_proof.get("pass") is True,
        "p117_history": history,
        "p117_history_evidence": {k: v for k, v in history_proof.items() if k != "history"},
        "predecessor_p117_fail_preserved": bool(predecessor_ok),
        "prior_p116": prior, "control_sha256": hashes,
        "source_population_matches_p116_commit": bool(rust_ok),
        "inherited_rust_source_count": len(rust_hashes),
        "inherited_rust_sha256": rust_hashes,
        "public_handbook_sha256": handbook_hashes,
        "public_handbook_matches_p116": bool(handbook_ok),
        "manifest_sha256": _sha(ROOT / MANIFEST), "seed_file_count": len(manifest.get("files", [])) if isinstance(manifest, dict) else 0,
        "seed_total_bytes": manifest.get("total_bytes") if isinstance(manifest, dict) else None,
        "repair_rounds": 1 if (history_proof.get("pass") is True
                                and history_proof.get("amendment_registered") is True) else None,
        "amendment_sha256": history_proof.get("amendment_sha256"),
        "amendment_registered": history_proof.get("amendment_registered") is True,
        "initial_failure_sha256": history_proof.get("initial_failure", {}).get("preserved_files_sha256", {}),
        "initial_failure_matches": history_proof.get("initial_failure", {}).get("pass") is True,
        "initial_failure": history_proof.get("initial_failure", {}),
        "p117_consumed_repair_rounds": 1,
        "terminal_p117_verdict_sha256": P117_TERMINAL_VERDICT_SHA256,
        "terminal_p117_discovery_sha256": P117_TERMINAL_DISCOVERY_SHA256,
    }
    return report


def _persist(path: Path, value: dict, no_write: bool) -> tuple[bool, bytes]:
    raw = _canonical(value)
    if no_write:
        return path.is_file() and path.read_bytes() == raw, raw
    if os.path.lexists(path):
        return path.is_file() and not path.is_symlink() and path.read_bytes() == raw, raw
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True, raw


def g0(*, seal: bool = False, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    report = _g0_base()
    if no_write:
        same = G0.is_file() and G0.read_bytes() == _canonical(report)
    else:
        same, _raw = _persist(G0, report, False)
    passed = report["pass"] and same
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if passed else 1), report


def _replay_old(output_name: str) -> dict:
    report = p116._run_g1(no_write=False, output_name=output_name)
    return report


def _campaign(output_name: str) -> tuple[dict, bool]:
    original = _read(P116_G1)
    fresh = _replay_old(output_name)
    equal = (isinstance(original, dict) and fresh == original
             and _canonical(fresh) == P116_G1.read_bytes())
    return fresh, bool(equal)


def _sealed_g0_present(expected: dict | None = None) -> bool:
    sealed = _read(G0)
    if not isinstance(sealed, dict) or sealed.get("pass") is not True:
        return False
    if expected is None:
        try:
            expected = _g0_base()
        except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            return False
    if _canonical(sealed) != _canonical(expected):
        return False
    try:
        current_hashes = _control_hashes()
        current_rust = p116._current_rust_source_hashes()
        handbook_hashes, handbook_ok = _handbook_snapshot()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False
    return (sealed.get("schema") == "actinv-p118-twin-waste-g0-1"
            and sealed.get("phase") == PHASE and sealed.get("protocol_sha256") == PROTOCOL_SHA256
            and sealed.get("control_sha256") == current_hashes
            and sealed.get("inherited_rust_sha256") == current_rust
            and sealed.get("historical_p117_verified") is True
            and sealed.get("historical_p116_verified") is True
            and type(sealed.get("repair_rounds")) is int and sealed.get("repair_rounds") == 1
            and sealed.get("amendment_sha256") == AMENDMENT_SHA256
            and sealed.get("initial_failure_matches") is True
            and handbook_ok is True
            and sealed.get("public_handbook_sha256") == handbook_hashes
            and sealed.get("source_population_matches_p116_commit") is True)


def _g1_report(fresh: dict, equal: bool) -> dict:
    original = _read(P116_G1)
    return {
        "schema": "actinv-p118-twin-waste-g1-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(equal),
        "p116_g1_sha256": P116_G1_SHA256, "p116_report_preserved": original,
        "fresh_p118_report": fresh, "exact_canonical_match": bool(equal),
        "request_count": fresh.get("request_count"),
        "component_target_count": fresh.get("component_target_count"),
        "independent_comparison_count": fresh.get("independent_comparison_count"),
        "mutations_rejected": fresh.get("mutations_rejected"),
        "refusal_controls": fresh.get("refusal_controls"),
        "repeat_byte_identical": fresh.get("repeat_byte_identical"),
        "failures": [] if equal else ["fresh P118 campaign differs from immutable P116 report"],
    }


def g1(*, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    try:
        expected_g0 = _g0_base()
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        expected_g0 = None
    sealed = _sealed_g0_present(expected_g0)
    fresh, equal = _campaign("p118-g1") if sealed else ({}, False)
    report = _g1_report(fresh, equal and sealed)
    if not sealed:
        report["failures"] = ["P118 G0 seal is missing or differs from current source identities"]
    if no_write:
        same = G1.is_file() and G1.read_bytes() == _canonical(report)
        report["pass"] = report["pass"] and same
    else:
        same, _ = _persist(G1, report, False)
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def g2(*, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    g0_code, g0_report = g0(no_write=True, quiet=True)
    sealed = _sealed_g0_present(g0_report)
    g0_code = 0 if g0_code == 0 and sealed else 1
    replay_fresh, replay_equal = _campaign("p118-g2") if g0_code == 0 else ({}, False)
    old = _read(P116_G1)
    expected_g1 = _g1_report(replay_fresh, replay_equal and sealed)
    g1_same = G1.is_file() and G1.read_bytes() == _canonical(expected_g1)
    equal = (g0_code == 0 and replay_equal and replay_fresh == old and g1_same)
    report = {
        "schema": "actinv-p118-twin-waste-g2-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(equal),
        "g0_exact_replay_equal": g0_code == 0, "g1_exact_replay_equal": replay_equal and g1_same,
        "separate_output_paths_byte_identical": replay_fresh.get("repeat_byte_identical") is True,
        "p116_g1_sha256": P116_G1_SHA256,
        "g0_result_sha256": _sha(G0), "g1_result_sha256": _sha(G1),
        "fresh_g1_report": replay_fresh,
    }
    if no_write:
        same = G2.is_file() and G2.read_bytes() == _canonical(report)
        report["pass"] = report["pass"] and same
    else:
        same, _ = _persist(G2, report, False)
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def _safe_receipt(name: str) -> tuple[dict | None, dict | None]:
    if name not in FRESH_GATES:
        return None, None
    receipt_rel = f"results/quality/p118/{name}.json"
    log_rel = f"results/quality/p118/{name}.log"
    receipt_path = _safe_file(receipt_rel)
    raw = receipt_path.read_bytes()
    receipt = _unique_json(raw)
    if not isinstance(receipt, dict):
        return None, None
    copied_log = _safe_file(log_rel).read_bytes()
    if _sha_bytes(copied_log) != receipt.get("log_sha256"):
        return None, None
    timeout = receipt.get("timeout_s")
    elapsed = receipt.get("elapsed_s")
    max_timeout = run_p118_gate.QUALITY_TIMEOUT_S if name == "rust_fmt" else run_p118_gate.SCIENCE_TIMEOUT_S
    resources = receipt.get("resources")
    argv = receipt.get("argv")
    if (receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != PHASE or receipt.get("gate") != name
            or receipt.get("status") != "completed" or type(receipt.get("child_exit_code")) is not int
            or receipt.get("child_exit_code") != 0 or receipt.get("cwd") != "."
            or receipt.get("log_path") != f"target/p118-{name}.log"
            or receipt.get("log_sha256") != _sha_bytes(copied_log)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or timeout <= 0 or timeout > max_timeout
            or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed)) or elapsed < 0
            or elapsed > float(timeout) + run_p118_gate.CLEANUP_HEADROOM_S
            or receipt.get("error") is not None or not isinstance(argv, list) or not argv
            or any(not isinstance(item, str) or not item.strip() for item in argv)
            or run_p118_gate._has_placeholder(argv)
            or not isinstance(resources, dict)):
        return None, None
    limits = resources.get("cgroup_limits")
    cgroup_path = resources.get("cgroup_path")
    path_parts = cgroup_path.split("/") if isinstance(cgroup_path, str) else []
    if (resources.get("platform") != "linux" or not isinstance(cgroup_path, str)
            or not cgroup_path.startswith("/user.slice/") or not cgroup_path.endswith(".scope")
            or any(part in {".", ".."} for part in path_parts)
            or any(not part for part in path_parts[1:])
            or limits != {"memory.max": "6442450944", "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
            or resources.get("environment") != {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            or not isinstance(resources.get("tmpdir"), dict)
            or resources["tmpdir"].get("path") != "target/preflight-tmp"
            or not isinstance(resources["tmpdir"].get("mount_point"), str)
            or not Path(resources["tmpdir"]["mount_point"]).is_absolute()
            or not isinstance(resources["tmpdir"].get("filesystem"), str)
            or not resources["tmpdir"]["filesystem"].strip()
            or resources["tmpdir"]["filesystem"].lower() in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"}):
        return None, None
    entry = {
        "name": name, "receipt_path": receipt_rel, "receipt_sha256": _sha_bytes(raw),
        "log_path": log_rel, "log_sha256": receipt["log_sha256"], "argv": receipt["argv"],
        "exit_code": receipt["child_exit_code"], "resources": resources,
    }
    return receipt, entry


def quality(*, no_write: bool = False) -> tuple[int, dict]:
    prior_g3 = _read(P116_G3)
    evidence = _repair_evidence()
    history = evidence.get("history", {})
    p117_initial_g3 = p117._read(p117.INITIAL_ARCHIVE / "results/g3_p117_quality.json")
    p117_adopted = (p117_initial_g3.get("fresh_gates", {})
                    if isinstance(p117_initial_g3, dict) else {})
    p116_adopted = prior_g3.get("gates") if isinstance(prior_g3, dict) else {}
    failures: list[str] = []
    adopted_initial: dict[str, dict] = {}
    fresh: dict[str, dict] = {}
    if set(p117_adopted) != p117.INITIAL_GATES:
        failures.append("P117 initial gate population is incomplete")
    for name in sorted(P117_ADOPTED_GATES):
        entry = p117_adopted.get(name) if isinstance(p117_adopted, dict) else None
        if not _adopted_initial_entry_matches(name, entry, require_raw_target=True):
            failures.append(f"P117 adopted receipt/log identity changed: {name}")
        elif isinstance(entry, dict):
            adopted_initial[name] = entry
    for name in sorted(FRESH_GATES):
        try:
            _receipt, entry = _safe_receipt(name)
            raw_log = _safe_file(f"target/p118-{name}.log").read_bytes()
            durable_log = _safe_file(f"results/quality/p118/{name}.log").read_bytes()
            if entry is None or raw_log != durable_log or _sha_bytes(raw_log) != entry["log_sha256"]:
                entry = None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            entry = None
        if entry is None:
            failures.append(f"invalid P118 receipt/log for {name}")
        else:
            fresh[name] = entry
    source_ok, source_hashes, current_rust = _source_population_ok({}, evidence)
    try:
        p116_quality_ok = (isinstance(prior_g3, dict)
                           and check_p116_verdict._quality_ok(prior_g3, _read(P116_G0))
                           and prior_g3.get("pass") is True and len(p116_adopted) == 32)
        release = prior_g3.get("release_build") if isinstance(prior_g3, dict) else {}
        handbook = check_p112_verdict._handbook_paths(IMPLEMENTATION_COMMIT)
        handbook_hashes = {path: _sha_bytes(p116._git_blob(IMPLEMENTATION_COMMIT, path))
                           for path in sorted(handbook)}
        handbook_ok = (set(check_p112_verdict._handbook_paths(None)) == handbook
                       and all(_sha(ROOT / path) == digest for path, digest in handbook_hashes.items()))
        g0_report, g1_report, g2_report = _read(G0), _read(G1), _read(G2)
        science_ok = (isinstance(g0_report, dict) and g0_report.get("pass") is True
                      and isinstance(g1_report, dict) and g1_report.get("pass") is True
                      and g1_report.get("fresh_p118_report") == _read(P116_G1)
                      and isinstance(g2_report, dict) and g2_report.get("pass") is True
                      and g2_report.get("g0_result_sha256") == _sha(G0)
                      and g2_report.get("g1_result_sha256") == _sha(G1))
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        p116_quality_ok = handbook_ok = science_ok = False
        release, handbook_hashes = {}, {}
    passed = bool(not failures and evidence.get("pass") is True and history.get("historical_p116_verified") is True
                  and source_ok and p116_quality_ok and handbook_ok and science_ok
                  and set(fresh) == FRESH_GATES and set(adopted_initial) == P117_ADOPTED_GATES
                  and isinstance(release, dict) and _sha(ACTINV) == release.get("binary_sha256"))
    initial_failure = evidence.get("initial_failure", {})
    report = {
        "schema": "actinv-p118-quality-1", "phase": PHASE, "pass": passed,
        "repair_rounds": 1 if evidence.get("pass") is True else None,
        "amendment_sha256": evidence.get("amendment_sha256"),
        "amendment_registered": evidence.get("amendment_registered") is True,
        "initial_failure_matches": initial_failure.get("pass") is True,
        "initial_failure_sha256": initial_failure.get("preserved_files_sha256", {}),
        "initial_failure": initial_failure,
        "p117_consumed_repair_rounds": 1,
        "historical_p117_verified": evidence.get("pass") is True,
        "historical_p116_verified": history.get("historical_p116_verified") is True,
        "p117_terminal_history": history,
        "p117_terminal_archive_sha256": evidence.get("terminal_archive_files_sha256", {}),
        "p117_initial_archive_sha256": evidence.get("initial_archive_files_sha256", {}),
        "p117_adopted_initial_gates": adopted_initial,
        "p117_adopted_initial_gate_names": sorted(adopted_initial),
        "rerun_gate_names": sorted(p117.RERUN_GATES),
        "p117_science_evidence_matches": evidence.get("pass") is True,
        "p116_adopted": {
            "quality_sha256": P116_G3_SHA256, "implementation_commit": IMPLEMENTATION_COMMIT,
            "verdict_sha256": P116_VERDICT_SHA256, "gates": p116_adopted,
            "gate_names": sorted(p116_adopted) if isinstance(p116_adopted, dict) else [],
            "successful_gate_count": len(p116_adopted) if isinstance(p116_adopted, dict) else 0,
            "workspace_tests": prior_g3.get("workspace_tests") if isinstance(prior_g3, dict) else None,
            "release_build": release, "production_rust_sha256": prior_g3.get("production_rust_sha256", {}) if isinstance(prior_g3, dict) else {},
            "public_handbook_sha256": handbook_hashes,
            "quality_validated": bool(p116_quality_ok), "not_rerun": True,
        },
        "adopted_p116_gates": p116_adopted,
        "fresh_gates": fresh, "fresh_gate_names": sorted(fresh),
        "source_sha256": source_hashes, "inherited_rust_sha256": current_rust,
        "public_handbook_sha256": handbook_hashes,
        "current_source_matches_p117_except_ci": bool(source_ok),
        "current_handbook_matches_p116": bool(handbook_ok),
        "qualified_binary_sha256": _sha(ACTINV),
        "qualified_binary_matches_p116": bool(isinstance(release, dict)
                                               and _sha(ACTINV) == release.get("binary_sha256")),
        "g0_sha256": _sha(G0), "g1_sha256": _sha(G1), "g2_sha256": _sha(G2),
        "resource_limits": RESOURCES, "failures": failures,
    }
    same, _ = _persist(G3, report, no_write)
    if no_write:
        report["pass"] = report["pass"] and same
    print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def _g0_replay() -> tuple[bool, dict]:
    with contextlib.redirect_stdout(io.StringIO()):
        code, report = g0(no_write=True, quiet=True)
    return code == 0 and _sealed_g0_present(report), report


def _g0_replay_ok() -> bool:
    return _g0_replay()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--g0-only", action="store_true")
    group.add_argument("--g1-only", action="store_true")
    group.add_argument("--g2-only", action="store_true")
    group.add_argument("--quality", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if args.quality and (args.g0_only or args.g1_only or args.g2_only):
        parser.error("--quality cannot be combined with a single-gate selector")
    if args.quality:
        return quality(no_write=args.no_write)[0]
    if args.g0_only:
        return g0(seal=not args.no_write, no_write=args.no_write)[0]
    if args.g1_only:
        return g1(no_write=args.no_write)[0]
    if args.g2_only:
        return g2(no_write=args.no_write)[0]
    if args.no_write:
        # The replay path performs all stable comparisons first; output is delayed
        # until after both G0 and the complete G1 campaign have been checked.
        g0_ok, g0_report = _g0_replay()
        if g0_ok:
            fresh, g1_ok = _campaign("p118-full-replay")
            g1_report = _g1_report(fresh, g1_ok and _sealed_g0_present(g0_report))
            g1_ok = (g1_ok and fresh == _read(P116_G1) and G1.is_file()
                     and G1.read_bytes() == _canonical(g1_report))
        else:
            fresh, g1_ok = {}, False
        expected_g2 = {
            "schema": "actinv-p118-twin-waste-g2-1", "phase": PHASE,
            "protocol_sha256": PROTOCOL_SHA256,
            "pass": bool(g0_ok and g1_ok),
            "g0_exact_replay_equal": bool(g0_ok),
            "g1_exact_replay_equal": bool(g1_ok),
            "separate_output_paths_byte_identical": fresh.get("repeat_byte_identical") is True,
            "p116_g1_sha256": P116_G1_SHA256,
            "g0_result_sha256": _sha(G0), "g1_result_sha256": _sha(G1),
            "fresh_g1_report": fresh,
        }
        g2_ok = G2.is_file() and G2.read_bytes() == _canonical(expected_g2)
        passed = g0_ok and g1_ok and g2_ok
        result = {"schema": "actinv-p118-full-replay-1", "phase": PHASE,
                  "pass": bool(passed), "g0_exact_replay_equal": bool(g0_ok),
                  "g1_exact_replay_equal": bool(g1_ok), "g2_exact_replay_equal": bool(g2_ok),
                  "p116_g1_sha256": P116_G1_SHA256,
                  "request_count": fresh.get("request_count"),
                  "component_target_count": fresh.get("component_target_count"),
                  "mutations_rejected": fresh.get("mutations_rejected"),
                  "refusal_controls": fresh.get("refusal_controls"),
                  "repeat_byte_identical": fresh.get("repeat_byte_identical")}
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if passed else 1
    code0, _ = g0(seal=True)
    if code0 != 0:
        return 1
    code1, _ = g1()
    if code1 != 0:
        return 1
    return g2()[0]


if __name__ == "__main__":
    raise SystemExit(main())
