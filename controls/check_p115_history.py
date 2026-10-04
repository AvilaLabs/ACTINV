#!/usr/bin/env python3
"""Read-only verification of the terminal P115 local failure."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "6db45f75b96fbcdfd2bfda0f6a603c10fbf88672"
ORIGINAL = "887b28d94f32342ea2e3b3410dbb0f0e2aaf2a14"
PROTOCOL_SHA = "266dd88898b2e64b77c517d18a221576bf9d1c6fda107ccdf62c45e0321f95ba"
AMENDMENT_SHA = "d2c6543d4a420e50b8697538411a163542e16d4fcb3fd3d2ff61b75af86170b3"
DISCOVERY = "results/failures/p115_initial/discovery.json"
DISCOVERY_SHA = "22f03d9115f7c2ef5b4c98d19d9fe1cce85e62c38747036f1e130e4c711632db"
INITIAL_PREFIX = "results/failures/p115_initial/"
FINAL_PREFIX = "results/failures/p115_after_amendment/"
G3_PATH = "results/g3_p115_quality.json"
G3_SHA = "9f48cde1106ebc7174961aa594fbf57e5c772c3aeed3a06a133558a6946aa45d"
VERDICT_PATH = "results/p115_verdict.json"
VERDICT_SHA = "4e09b66d5b1c74cafecdc278d9d5ce786292007a7dea0ab6e8c8588f7255faac"
INITIAL_FAILURE_LOG_SHA = "51a89379d6fde40f857f9f5b499d79f571b220976ac667ffb420bbf7bcc4945c"
FINAL_FAILURE_LOG_SHA = "b5f4ed19ca056a563cda1b3fe5f11a542c9e6edc788d8524766c543aaa870c6c"
TIMEOUTS = {
    "rust_fmt": 1200.0, "workspace_check": 1200.0, "workspace_clippy": 1200.0,
    "workspace_tests": 1200.0, "release_build": 1200.0,
    "handbook_build": 120.0, "handbook_links": 120.0, "handbook_chromium": 150.0,
}
TEMP_ROOT = ROOT / "target/p116-p115-history-tmp"
FAILURE_RECORD = "results/p115_failure_commit.json"
EXPECTED_ARTIFACTS = {
    "protocols/ACTINV-P115_PROTOCOL.md": PROTOCOL_SHA,
    "protocols/ACTINV-P115_AMENDMENT_A.md": AMENDMENT_SHA,
    DISCOVERY: DISCOVERY_SHA,
    G3_PATH: G3_SHA,
    VERDICT_PATH: VERDICT_SHA,
    INITIAL_PREFIX + "results/quality/p115/p115_verdict_regressions.json":
        "b7656176ff945c7e503b5747424b979438b47c8aceb388911263073f8f588e54",
    INITIAL_PREFIX + "target/p115-p115_verdict_regressions.log": INITIAL_FAILURE_LOG_SHA,
    FINAL_PREFIX + "results/quality/p115/p115_verdict_regressions.json":
        "16d3ea00fc0504369e5cb875c918b4b7f3a4f924a909a1b27dc2f4274f223449",
    FINAL_PREFIX + "target/p115-p115_verdict_regressions.log": FINAL_FAILURE_LOG_SHA,
}
EXPECTED_GATES = {
    "gate_recorder_regressions", "handbook_build", "handbook_chromium", "handbook_links",
    "historical_p107_replay", "historical_p108_replay", "historical_p109_replay",
    "historical_p110_replay", "historical_p111_replay", "historical_p112_replay",
    "historical_p113_replay", "historical_p114_replay", "p105_child_lifecycle_regressions",
    "p113_history_regressions", "p114_history_regressions", "p115_oracle_regressions",
    "p115_verdict_regressions", "release_build", "rust_fmt", "workspace_check",
    "workspace_clippy", "workspace_tests",
}
SOURCE_METADATA = {
    ".github/workflows/ci.yml", "docs/ROADMAP.md", "ledger.md",
    "docs/history/sessions/P115.md", "protocols/protocol_hash.txt",
}
_GIT_READER_VERIFIED = False


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a nonempty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"unsafe path: {value!r}")
    return path


def _regular(relative: str) -> Path:
    safe = _safe_rel(relative)
    root = ROOT.resolve(strict=True)
    path = ROOT.joinpath(*safe.parts)
    path.resolve(strict=True).relative_to(root)
    cursor = ROOT
    for part in safe.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in evidence path: {relative}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {relative}")
    return path


def _json(relative: str) -> Any:
    return json.loads(_regular(relative).read_text(encoding="utf-8"))


def _blob(commit: str, relative: str) -> bytes:
    global _GIT_READER_VERIFIED
    if commit not in {ORIGINAL, CHECKPOINT}:
        raise ValueError("unexpected P115 source commit")
    if not _GIT_READER_VERIFIED:
        quality_raw = _regular(G3_PATH).read_bytes()
        if _sha(quality_raw) != G3_SHA:
            raise ValueError("pinned G3 source map is unavailable to bootstrap Git reader")
        quality = json.loads(quality_raw.decode("utf-8"))
        source_map = quality.get("source_at_failure_sha256")
        if not isinstance(source_map, dict):
            raise ValueError("pinned G3 source map is malformed")
        source = "controls/p105_budget_control.py"
        raw = _regular(source).read_bytes()
        if source_map.get(source) != _sha(raw):
            raise ValueError(f"bounded Git reader dependency is not checkpoint-pinned: {source}")
        _GIT_READER_VERIFIED = True
    sys.path.insert(0, str(ROOT / "controls"))
    try:
        sys.modules.pop("p105_budget_control", None)
        from p105_budget_control import _run
        child = _run(["git", "show", f"{commit}:{relative}"], cwd=ROOT, timeout_s=120)
        if child.returncode != 0:
            raise ValueError(f"git show failed for {relative}: {child.stderr[-1200:]}")
        return child.stdout.encode("utf-8")
    finally:
        if sys.path[0] == str(ROOT / "controls"):
            sys.path.pop(0)


def _validate_artifacts_and_record() -> dict[str, Any]:
    record = _json(FAILURE_RECORD)
    if not isinstance(record, dict):
        raise ValueError("failure record must be an object")
    expected: dict[str, object] = {
        "schema": "actinv-p115-failure-implementation-1",
        "phase": "P115", "verdict": "P115-FAIL",
        "ci_qualification": "none; local failed checkpoint",
        "commit_sha": CHECKPOINT, "original_checkpoint_commit": ORIGINAL,
        "protocol_sha256": PROTOCOL_SHA,
        "protocol_pinned_by_p116_sha256": "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa",
        "amendment_sha256": AMENDMENT_SHA,
        "discovery_path": DISCOVERY, "discovery_sha256": DISCOVERY_SHA,
        "terminal_g3_path": G3_PATH, "terminal_g3_sha256": G3_SHA,
        "terminal_verdict_path": VERDICT_PATH, "terminal_verdict_sha256": VERDICT_SHA,
        "initial_source_count": 180, "initial_rust_source_count": 100,
        "initial_retained_file_count": 68, "initial_archive_physical_file_count": 69,
        "terminal_source_count": 182, "terminal_rust_source_count": 100,
        "terminal_retained_file_count": 68,
        "initial_gate_count": 22, "terminal_gate_count": 22,
        "successful_gate_count_each_round": 21,
        "failed_gate": "p115_verdict_regressions",
        "initial_failure_exit_code": 1, "terminal_failure_exit_code": 1,
        "initial_failure_log_sha256": INITIAL_FAILURE_LOG_SHA,
        "terminal_failure_log_sha256": FINAL_FAILURE_LOG_SHA,
        "checker_error": {
            "exception_type": "NameError", "name": "historical_p114",
            "message": "name 'historical_p114' is not defined",
            "failure_projection": "unsealed_G0_forced_false_only",
            "original_derivation_completed": False,
        },
        "g0_status": "not_sealed", "g1_status": "not_run", "g2_status": "not_run",
        "repair_rounds": 1, "implementation_record_present": False,
        "ci_record_present": False,
    }
    for key, value in expected.items():
        actual = record.get(key)
        if type(value) is bool:
            valid = type(actual) is bool and actual is value
        elif type(value) is int:
            valid = type(actual) is int and actual == value
        else:
            valid = actual == value
        if not valid:
            raise ValueError(f"failure record field differs: {key}")
    checker_error = record.get("checker_error")
    if (not isinstance(checker_error, dict)
            or type(checker_error.get("original_derivation_completed")) is not bool
            or checker_error.get("original_derivation_completed") is not False):
        raise ValueError("failure record checker error typing differs")
    if set(record) != set(expected) | {"artifact_sha256"}:
        raise ValueError("failure record fields differ")
    if record.get("artifact_sha256") != EXPECTED_ARTIFACTS:
        raise ValueError("failure record artifact map differs")
    for relative, digest in EXPECTED_ARTIFACTS.items():
        raw = _regular(relative).read_bytes()
        if _sha(raw) != digest or _blob(CHECKPOINT, relative) != raw:
            raise ValueError(f"pinned artifact differs from checkpoint: {relative}")
    registry = _regular("protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    if (f"{PROTOCOL_SHA}  protocols/ACTINV-P115_PROTOCOL.md" not in registry
            or f"{AMENDMENT_SHA}  protocols/ACTINV-P115_AMENDMENT_A.md" not in registry):
        raise ValueError("P115 protocol/amendment registration is missing")
    return record


def _validate_map(mapping: object, *, commit: str, prefix: str,
                  count: int, extra: set[str] | None = None) -> dict[str, str]:
    if not isinstance(mapping, dict) or len(mapping) != count:
        raise ValueError(f"{prefix} hash map must contain exactly {count} entries")
    base = ROOT / prefix.rstrip("/")
    base.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    if base.is_symlink() or not base.is_dir():
        raise ValueError("retained archive directory is invalid")
    physical: set[str] = set()
    for path in base.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in retained archive")
        if path.is_file():
            physical.add(path.relative_to(ROOT).as_posix())
        elif not path.is_dir():
            raise ValueError("non-regular retained archive entry")
    expected_physical = set(mapping) | (extra or set())
    if physical != expected_physical:
        raise ValueError(f"physical archive members differ: {prefix}")
    checked: dict[str, str] = {}
    for rel, digest in mapping.items():
        safe = _safe_rel(rel)
        if not safe.as_posix().startswith(prefix) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid archived member: {rel!r}")
        raw = _regular(rel).read_bytes()
        if _sha(raw) != digest or _blob(commit, rel) != raw:
            raise ValueError(f"archive differs from checkpoint: {rel}")
        checked[rel] = digest
    return checked


def _validate_receipt(gate: dict[str, Any], *, archive: dict[str, str],
                      failure: bool) -> dict[str, Any]:
    name = gate.get("name")
    prefix = INITIAL_PREFIX if gate.get("round") == "initial" else FINAL_PREFIX
    rel_receipt = f"{prefix}results/quality/p115/{name}.json"
    rel_log = f"{prefix}target/p115-{name}.log"
    if gate.get("receipt_path") != rel_receipt or gate.get("log_path") != rel_log:
        raise ValueError(f"G3 receipt/log path differs: {name}")
    receipt_raw = _regular(rel_receipt).read_bytes()
    receipt = json.loads(receipt_raw.decode("utf-8"))
    exit_code = 1 if failure else 0
    argv = gate.get("argv")
    if (archive.get(rel_receipt) != gate.get("receipt_sha256")
            or _sha(receipt_raw) != gate.get("receipt_sha256")
            or archive.get(rel_log) != gate.get("log_sha256")
            or receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != "P115" or receipt.get("gate") != name
            or not isinstance(argv, list) or receipt.get("argv") != argv
            or receipt.get("cwd") != "."
            or receipt.get("status") != ("child_failed" if failure else "completed")
            or type(receipt.get("child_exit_code")) is not int
            or receipt.get("child_exit_code") != exit_code
            or receipt.get("log_path") != PurePosixPath(rel_log).relative_to(prefix).as_posix()
            or receipt.get("log_sha256") != gate.get("log_sha256")
            or type(receipt.get("timeout_s")) not in {int, float}
            or isinstance(receipt.get("timeout_s"), bool)
            or receipt.get("timeout_s") != TIMEOUTS.get(name, 600.0)):
        raise ValueError(f"receipt contents differ: {name}")
    expected_log = INITIAL_FAILURE_LOG_SHA if prefix == INITIAL_PREFIX else FINAL_FAILURE_LOG_SHA
    if failure and gate.get("log_sha256") != expected_log:
        raise ValueError("failed gate log digest differs")
    resources = receipt.get("resources")
    if (not isinstance(resources, dict)
            or resources.get("cgroup_limits") != {
                "cpu.max": "200000 100000", "memory.max": "6442450944",
                "memory.swap.max": "0", "pids.max": "128"}
            or resources.get("environment") != {
                "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            or resources.get("platform") != "linux"
            or not isinstance(resources.get("cgroup_path"), str)
            or not resources.get("cgroup_path")
            or resources.get("tmpdir") != {
                "filesystem": "ext4", "mount_point": "/", "path": "target/preflight-tmp"}):
        raise ValueError(f"receipt resource caps differ: {name}")
    return receipt


def _validate_round_receipts(archive: dict[str, str], gates: dict[str, Any],
                             *, round_name: str) -> None:
    prefix = INITIAL_PREFIX if round_name == "initial" else FINAL_PREFIX
    for name in sorted(EXPECTED_GATES):
        exit_code = 1 if name == "p115_verdict_regressions" else 0
        rel_receipt = f"{prefix}results/quality/p115/{name}.json"
        rel_log = f"{prefix}target/p115-{name}.log"
        gate = gates.get(name)
        if not isinstance(gate, dict):
            raise ValueError(f"missing archived P115 gate metadata: {name}")
        synthetic = {
            "name": name, "round": round_name,
            "receipt_path": rel_receipt, "log_path": rel_log,
            "receipt_sha256": archive.get(rel_receipt),
            "log_sha256": archive.get(rel_log), "argv": gate.get("argv"),
        }
        if not isinstance(synthetic["argv"], list) or not synthetic["argv"]:
            raise ValueError(f"missing expected argv for P115 gate: {name}")
        _validate_receipt(synthetic, archive=archive, failure=exit_code == 1)


def _load_control_files(checkpoint: str) -> tuple[str, ...]:
    source = _blob(checkpoint, "controls/check_p115.py").decode("utf-8")
    tree = ast.parse(source, filename="checkpoint:controls/check_p115.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CONTROL_FILES" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, tuple) or not value or len(value) != len(set(value)):
                raise ValueError("frozen P115 CONTROL_FILES is malformed")
            for path in value:
                _safe_rel(path)
            return value
    raise ValueError("frozen P115 CONTROL_FILES is absent")


def _verify_current_python_controls() -> None:
    terminal_controls = _load_control_files(CHECKPOINT)
    for relative in terminal_controls:
        if not (relative.startswith("controls/") or relative.startswith("scripts/")) or not relative.endswith(".py"):
            continue
        if _regular(relative).read_bytes() != _blob(CHECKPOINT, relative):
            raise ValueError(f"current historical Python source differs: {relative}")


def _source_population(checkpoint: str) -> set[str]:
    _verify_current_python_controls()
    rust_paths = _rust_source_paths(checkpoint)
    return set(_load_control_files(checkpoint)) | rust_paths | SOURCE_METADATA


def _rust_source_paths(checkpoint: str) -> set[str]:
    sys.path.insert(0, str(ROOT / "controls"))
    try:
        sys.modules.pop("p105_budget_control", None)
        from p105_budget_control import _run
        result = _run(["git", "ls-tree", "-r", "--full-tree", checkpoint, "--", "crates"],
                      cwd=ROOT, timeout_s=120)
        if result.returncode != 0:
            raise ValueError("cannot enumerate checkpoint Rust source population")
    finally:
        if sys.path[0] == str(ROOT / "controls"):
            sys.path.pop(0)
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        try:
            _entry, relative = line.split("\t", maxsplit=1)
        except ValueError as error:
            raise ValueError("malformed Git tree entry") from error
        safe = _safe_rel(relative)
        if safe.as_posix().startswith("crates/") and safe.suffix == ".rs":
            paths.add(safe.as_posix())
    if len(paths) != 100:
        raise ValueError(f"checkpoint Rust source count differs: {len(paths)}")
    return paths


def _validate_sources(quality: dict[str, Any], terminal: dict[str, str]) -> dict[str, str]:
    sources = quality.get("source_at_failure_sha256")
    if not isinstance(sources, dict) or len(sources) != 182:
        raise ValueError("terminal source map must contain exactly 182 entries")
    rust = quality.get("production_rust_sha256")
    if not isinstance(rust, dict) or len(rust) != 100:
        raise ValueError("terminal Rust source map must contain exactly 100 entries")
    _verify_current_python_controls()
    expected_rust = _rust_source_paths(CHECKPOINT)
    if set(rust) != expected_rust:
        raise ValueError("terminal Rust source population differs from Git tree")
    for relative, digest in sources.items():
        _safe_rel(relative)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid terminal source digest: {relative}")
        raw = _blob(CHECKPOINT, relative)
        if _sha(raw) != digest:
            raise ValueError(f"terminal source differs from checkpoint: {relative}")
    if set(sources) != _source_population(CHECKPOINT):
        raise ValueError("terminal P115 source population differs")
    for relative, digest in rust.items():
        if sources.get(relative) != digest:
            raise ValueError(f"Rust source not bound by terminal sources: {relative}")
    # Import only after verifying the exact source population used by the old verdict.
    for relative in _load_control_files(CHECKPOINT):
        if relative.startswith(("controls/", "scripts/")) and relative.endswith(".py"):
            digest = sources.get(relative)
            if digest is None or digest != _sha(_blob(CHECKPOINT, relative)):
                raise ValueError(f"P115 control source hash differs: {relative}")
    return rust


def _derive_failure(rust: dict[str, str], snapshot: Path) -> dict[str, Any]:
    controls = str(ROOT / "controls")
    if controls not in sys.path:
        sys.path.insert(0, controls)
    for name in ("check_p115", "check_p115_verdict"):
        sys.modules.pop(name, None)
    verdict = importlib.import_module("check_p115_verdict")
    observed_error: NameError | None = None
    try:
        verdict.derive()
    except NameError as error:
        if error.name != "historical_p114" or str(error) != "name 'historical_p114' is not defined":
            raise ValueError("original P115 derivation raised a different NameError") from error
        observed_error = error
    else:
        raise ValueError("original P115 derivation unexpectedly completed")
    if observed_error is None:
        raise ValueError("original P115 NameError was not retained")

    original_g0 = verdict._g0_ok
    original_source = verdict._source_commit_matches

    def fail_unsealed_g0(g0: dict) -> bool:
        return False

    def historical_source_match(g3: dict, record: dict | None) -> bool:
        if record is not None or g3.get("production_rust_sha256") != rust:
            return False
        for relative, digest in rust.items():
            path = snapshot / _safe_rel(relative)
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != digest:
                return False
        return True

    verdict._g0_ok = fail_unsealed_g0
    verdict._source_commit_matches = historical_source_match
    try:
        projected = verdict.derive()
        projected["checker_error"] = {
            "exception_type": "NameError", "name": observed_error.name,
            "message": str(observed_error),
            "failure_projection": "unsealed_G0_forced_false_only",
            "original_derivation_completed": False,
        }
        return projected
    finally:
        verdict._g0_ok = original_g0
        verdict._source_commit_matches = original_source


def _validate_quality() -> tuple[dict[str, str], dict[str, Any]]:
    quality = _json(G3_PATH)
    terminal_failure = quality.get("terminal_failure") if isinstance(quality, dict) else None
    if (not isinstance(terminal_failure, dict)
            or any(type(terminal_failure.get(key)) is not int
                   for key in ("exit_code", "observed_error_count", "observed_test_count"))
            or not isinstance(quality, dict) or quality.get("schema") != "actinv-p115-quality-1"
            or quality.get("phase") != "P115" or quality.get("pass") is not False
            or quality.get("g0_status") != "not_sealed"
            or quality.get("g1_status") != "not_run" or quality.get("g2_status") != "not_run"
            or quality.get("ci_status") != "not qualified; terminal source-gate failure"
            or type(quality.get("repair_rounds")) is not int or quality["repair_rounds"] != 1
            or terminal_failure != {
                "cause": "undefined historical_p114 in G0 construction and final predicate",
                "exit_code": 1, "gate": "p115_verdict_regressions",
                "observed_error_count": 1, "observed_test_count": 8}):
        raise ValueError("terminal P115 G3 disposition differs")
    test_counts = quality.get("workspace_tests")
    if (not isinstance(test_counts, dict)
            or any(type(test_counts.get(key)) is not int for key in ("passed", "failed", "ignored"))
            or test_counts != {"passed": 484, "failed": 0, "ignored": 2}):
        raise ValueError("terminal P115 workspace test counts differ")
    if _sha(_regular(G3_PATH).read_bytes()) != G3_SHA:
        raise ValueError("terminal P115 G3 digest differs")
    gates = quality.get("gates")
    if not isinstance(gates, dict) or set(gates) != EXPECTED_GATES:
        raise ValueError("terminal P115 gate set differs")
    final_map = quality.get("retained_after_amendment_sha256")
    terminal = _validate_map(final_map, commit=CHECKPOINT, prefix=FINAL_PREFIX, count=68)
    if terminal != final_map:
        raise ValueError("terminal archive map differs")
    for name, gate in gates.items():
        if not isinstance(gate, dict) or gate.get("name") != name:
            raise ValueError(f"terminal G3 gate malformed: {name}")
        exit_code = 1 if name == "p115_verdict_regressions" else 0
        if type(gate.get("exit_code")) is not int or gate["exit_code"] != exit_code:
            raise ValueError(f"terminal G3 exit differs: {name}")
        gate = dict(gate, round="terminal")
        _validate_receipt(gate, archive=terminal, failure=exit_code == 1)
    if quality.get("source_at_failure_sha256") is None:
        raise ValueError("terminal source map is missing")
    if _sha(_regular(VERDICT_PATH).read_bytes()) != VERDICT_SHA:
        raise ValueError("terminal P115 verdict digest differs")
    verdict = _json(VERDICT_PATH)
    return terminal, {"quality": quality, "verdict": verdict}


def _validate_initial() -> tuple[dict[str, str], dict[str, Any]]:
    discovery = _json(DISCOVERY)
    if (not isinstance(discovery, dict) or _sha(_regular(DISCOVERY).read_bytes()) != DISCOVERY_SHA
            or discovery.get("schema") != "actinv-p115-initial-failure-1"
            or discovery.get("phase") != "P115" or discovery.get("gate") != "p115_verdict_regressions"
            or type(discovery.get("exit_code")) is not int or discovery["exit_code"] != 1
            or type(discovery.get("source_file_count")) is not int or discovery["source_file_count"] != 180
            or type(discovery.get("retained_file_count")) is not int or discovery["retained_file_count"] != 68
            or type(discovery.get("rust_source_file_count")) is not int or discovery["rust_source_file_count"] != 100
            or type(discovery.get("failed_gate_count")) is not int or discovery["failed_gate_count"] != 1
            or type(discovery.get("observed_test_count")) is not int or discovery["observed_test_count"] != 8
            or type(discovery.get("observed_error_count")) is not int or discovery["observed_error_count"] != 1
            or type(discovery.get("g0_sealed")) is not bool or discovery["g0_sealed"]
            or type(discovery.get("g1_executed")) is not bool or discovery["g1_executed"]
            or type(discovery.get("g2_executed")) is not bool or discovery["g2_executed"]):
        raise ValueError("initial P115 discovery disposition differs")
    initial = _validate_map(discovery.get("files_sha256"), commit=ORIGINAL,
                            prefix=INITIAL_PREFIX, count=68, extra={DISCOVERY})
    sources = discovery.get("source_sha256")
    if not isinstance(sources, dict) or len(sources) != 180:
        raise ValueError("initial P115 source map must contain 180 entries")
    if set(sources) != _source_population(ORIGINAL):
        raise ValueError("initial P115 source population differs")
    for relative, digest in sources.items():
        _safe_rel(relative)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid initial source digest: {relative}")
        raw = _blob(ORIGINAL, relative)
        if _sha(raw) != digest:
            raise ValueError(f"initial source differs from original checkpoint: {relative}")
    exits = discovery.get("observed_gate_exit_codes")
    if (not isinstance(exits, dict) or set(exits) != EXPECTED_GATES
            or any(type(code) is not int or code != (1 if name == "p115_verdict_regressions" else 0)
                   for name, code in exits.items())):
        raise ValueError("initial P115 gate exit population differs")
    failure = {"name": "p115_verdict_regressions", "round": "initial",
               "receipt_path": INITIAL_PREFIX + "results/quality/p115/p115_verdict_regressions.json",
               "log_path": INITIAL_PREFIX + "target/p115-p115_verdict_regressions.log",
               "receipt_sha256": initial[INITIAL_PREFIX + "results/quality/p115/p115_verdict_regressions.json"],
               "log_sha256": initial[INITIAL_PREFIX + "target/p115-p115_verdict_regressions.log"],
               "argv": ["python3", "controls/test_p115_verdict.py"]}
    receipt = _validate_receipt(failure, archive=initial, failure=True)
    return initial, {"receipt": receipt, "discovery": discovery}


def _snapshot(rust: dict[str, str]) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="p115-history-", dir=TEMP_ROOT)
    root = Path(temporary.name)
    for relative, digest in rust.items():
        safe = _safe_rel(relative)
        raw = _blob(CHECKPOINT, safe.as_posix())
        if _sha(raw) != digest:
            temporary.cleanup()
            raise ValueError(f"Rust checkpoint blob differs: {relative}")
        target = root.joinpath(*safe.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return root, temporary


def _no_unearned() -> None:
    for relative in ("results/g0_p115_twin_waste.json", "results/g1_p115_twin_waste.json",
                     "results/g2_p115_twin_waste.json", "results/p115_implementation_commit.json",
                     "results/p115_ci_runs.json"):
        if (ROOT / relative).exists() or (ROOT / relative).is_symlink():
            raise ValueError(f"P115 failure has unearned evidence: {relative}")
        try:
            _blob(CHECKPOINT, relative)
        except (OSError, ValueError, RuntimeError):
            continue
        raise ValueError(f"P115 checkpoint contains unearned evidence: {relative}")


def verify() -> dict[str, Any]:
    report: dict[str, Any] = {"schema": "actinv-p115-history-verification-1", "pass": False,
                              "verdict": "P115-FAIL", "checkpoint_commit": CHECKPOINT}
    temporary = None
    try:
        record = _validate_artifacts_and_record()
        initial, initial_evidence = _validate_initial()
        terminal, artifacts = _validate_quality()
        quality = artifacts["quality"]
        _validate_round_receipts(initial, quality["gates"], round_name="initial")
        if len(initial) != 68 or len(terminal) != 68:
            raise ValueError("P115 archive counts differ")
        rust = _validate_sources(quality, terminal)
        _no_unearned()
        snapshot, temporary = _snapshot(rust)
        persisted = artifacts["verdict"]
        controls = str(ROOT / "controls")
        if controls not in sys.path:
            sys.path.insert(0, controls)
        for name in ("check_p115", "check_p115_verdict"):
            sys.modules.pop(name, None)
        derived = _derive_failure(rust, snapshot)
        matches = (derived == persisted and derived.get("verdict") == "P115-FAIL"
                   and derived.get("checker_error") == record["checker_error"])
        report.update({
            "failure_record_verified": True,
            "initial_source_files_verified": 180,
            "initial_retained_files_verified": len(initial),
            "terminal_source_files_verified": len(quality["source_at_failure_sha256"]),
            "terminal_rust_files_verified": len(rust),
            "terminal_retained_files_verified": len(terminal),
            "initial_gate_receipts_verified": 22,
            "terminal_gate_receipts_verified": 22,
            "historical_name_error_reproduced": True,
            "fail_only_projection_matches": matches,
            "original_derivation_completed": False,
            "terminal_verdict": derived.get("verdict"),
            "historical_source_verification": False,
            "unearned_records_absent": True,
            "pass": bool(matches and quality.get("g0_status") == "not_sealed"
                         and quality.get("g1_status") == "not_run"
                         and quality.get("g2_status") == "not_run"
                         and derived.get("gates", {}).get("G3_local") == "FAIL"
                         and derived.get("gates", {}).get("G3_CI") == "PENDING"),
        })
        if report["pass"] is not True:
            report["pass"] = False
            report["error"] = "derived P115 failure disposition differs"
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError,
            json.JSONDecodeError, SyntaxError, ImportError, NameError) as error:
        report["error"] = str(error)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return report


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(0 if result.get("pass") is True else 1)
