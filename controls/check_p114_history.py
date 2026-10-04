#!/usr/bin/env python3
"""Read-only historical verification of P114's terminal local failure."""
from __future__ import annotations

import hashlib
import ast
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "88e9251a6c7a61db2293ee9772457e4f51842253"
PROTOCOL_SHA256 = "01b5a99cf8855c57e1ecae12ab794d3d2fe2ce81930868043f0805289ec0bb8c"
AMENDMENT_SHA256 = "b320e9fe34dfa34c5bae88a10b7486f2618b41ac124790f5ac984e4332ab5c9e"
INITIAL_DISCOVERY = "results/failures/p114_initial/discovery.json"
INITIAL_DISCOVERY_SHA256 = "ac3f2638af88dedb56472ecbeda835efbf8c339316ece680867471043de87c7a"
INITIAL_PREFIX = "results/failures/p114_initial/"
FINAL_PREFIX = "results/failures/p114_after_amendment/"
TERMINAL_VERDICT_SHA256 = "3d9208cae1ac6522e4b2c3d8c21440ecb4bc481f02faa46fb88f4068151bd382"
G3_SHA256 = "053cc816a07e97bb9831cdcf84f96a1d5d8f2f263219b87d5a10f4fb99813fbb"
FAILURE_RECORD = ROOT / "results/p114_failure_commit.json"
PERSISTED_VERDICT = ROOT / "results/p114_verdict.json"
QUALITY = ROOT / "results/g3_p114_quality.json"
TEMP_ROOT = ROOT / "target/p115-p114-history-tmp"
EXPECTED_ARTIFACTS = {
    "results/p114_verdict.json": TERMINAL_VERDICT_SHA256,
    "results/g3_p114_quality.json": G3_SHA256,
    INITIAL_DISCOVERY: INITIAL_DISCOVERY_SHA256,
    "protocols/ACTINV-P114_PROTOCOL.md": PROTOCOL_SHA256,
    "protocols/ACTINV-P114_AMENDMENT_A.md": AMENDMENT_SHA256,
}
EXPECTED_RUST_SOURCES = 100
EXPECTED_GATES = {
    "gate_recorder_regressions": 0,
    "p113_history_regressions": 0,
    "p114_oracle_regressions": 0,
    "p114_seal_regressions": 1,
    "release_build": 0,
    "rust_fmt": 0,
    "workspace_check": 0,
    "workspace_clippy": 0,
    "workspace_tests": 0,
}
EXPECTED_NOT_RUN = [
    "full_p114_read_only_replay", "handbook_build", "handbook_chromium", "handbook_links",
    "historical_p107_replay", "historical_p108_replay", "historical_p109_replay",
    "historical_p110_replay", "historical_p111_replay", "historical_p112_replay",
    "historical_p113_replay",
    "p105_child_lifecycle_regressions", "p105_scientific_replay", "p107_scientific_replay",
    "p108_scientific_replay", "p110_scientific_replay", "p111_scientific_replay",
    "p112_scientific_replay", "p114_verdict_regressions",
]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a nonempty POSIX repository path")
    path = PurePosixPath(value)
    if (path.is_absolute() or "." in path.parts or ".." in path.parts
            or path.as_posix() != value):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path


def _regular_inside(relative: str) -> Path:
    safe = _safe_relative(relative)
    root = ROOT.resolve(strict=True)
    path = ROOT / safe
    path.resolve(strict=True).relative_to(root)
    cursor = ROOT
    for part in safe.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in evidence path: {relative}")
    if not path.is_file():
        raise ValueError(f"evidence is not a regular file: {relative}")
    return path


def _blob(commit: str, relative: str) -> bytes:
    from check_p107_history import _git_blob

    return _git_blob(commit, relative, root=ROOT)


def _exact_checkpoint_file(relative: str, expected: str) -> bytes:
    path = _regular_inside(relative)
    current = path.read_bytes()
    if _sha(current) != expected or _blob(CHECKPOINT, relative) != current:
        raise ValueError(f"checkpoint file bytes differ: {relative}")
    return current


def _validate_record(record: object) -> dict[str, bytes]:
    if not isinstance(record, dict):
        raise ValueError("P114 failure record must be an object")
    exact = {
        "schema": "actinv-p114-failure-implementation-1",
        "commit_sha": CHECKPOINT,
        "verdict": "P114-FAIL",
        "ci_qualification": "none; terminal local seal-regression failure",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "terminal_verdict_sha256": TERMINAL_VERDICT_SHA256,
        "partial_g3_sha256": G3_SHA256,
        "initial_discovery_path": INITIAL_DISCOVERY,
        "initial_discovery_sha256": INITIAL_DISCOVERY_SHA256,
        "initial_archive_prefix": INITIAL_PREFIX,
        "initial_archive_file_count": 176,
        "final_archive_prefix": FINAL_PREFIX,
        "final_archive_file_count": 196,
        "final_archive_manifest": "results/g3_p114_quality.json#retained_after_amendment_sha256",
        "rust_source_count": EXPECTED_RUST_SOURCES,
        "executed_gate_receipt_count": 9,
        "successful_gate_count": 8,
        "failed_gate": "p114_seal_regressions",
        "failure_exit_code": 1,
        "first_failure_gate": "p114_oracle_regressions",
        "first_failure_exit_code": 1,
        "first_failure_receipt_path": INITIAL_PREFIX + "results/quality/p114/p114_oracle_regressions.json",
        "first_failure_receipt_sha256": "49fd05ba88bbfb1a1b5bbdfe09c23262b487a8bf6f3a81bc33379b2489ec26ab",
        "first_failure_log_path": INITIAL_PREFIX + "target/p114-p114_oracle_regressions.log",
        "first_failure_log_sha256": "c3e803d8d1aa8f0838086bbfae21f310abf00e308e3374e54b88bb8778f2c41f",
        "g0_sealed": False,
        "g1_executed": False,
        "g2_executed": False,
        "repair_rounds": 1,
        "implementation_record": None,
        "ci_record": None,
        "failure_receipt_path": FINAL_PREFIX + "results/quality/p114/p114_seal_regressions.json",
        "failure_receipt_sha256": "040de34fc70b307b2b68d1793c87b41ff8b7d5ca5cfa1577b1b49fa80a039ab2",
        "failure_log_path": FINAL_PREFIX + "target/p114-p114_seal_regressions.log",
        "failure_log_sha256": "dd4b3ebbf7a52f221b4b7ad253a18083241fd19071e738c415747955128914da",
        "first_failure_receipt_path": INITIAL_PREFIX + "results/quality/p114/p114_oracle_regressions.json",
        "first_failure_receipt_sha256": "49fd05ba88bbfb1a1b5bbdfe09c23262b487a8bf6f3a81bc33379b2489ec26ab",
        "first_failure_log_path": INITIAL_PREFIX + "target/p114-p114_oracle_regressions.log",
        "first_failure_log_sha256": "c3e803d8d1aa8f0838086bbfae21f310abf00e308e3374e54b88bb8778f2c41f",
    }
    for key, expected in exact.items():
        actual = record.get(key)
        if type(expected) is bool:
            valid = type(actual) is bool and actual is expected
        elif type(expected) is int:
            valid = type(actual) is int and actual == expected
        else:
            valid = actual == expected
        if not valid:
            raise ValueError(f"P114 failure record field differs: {key}")
    expected_fields = set(exact) | {"artifact_sha256"}
    if set(record) != expected_fields:
        raise ValueError("P114 failure record has missing or unrecognized fields")
    if record.get("artifact_sha256") != EXPECTED_ARTIFACTS:
        raise ValueError("P114 terminal artifact bindings differ")
    verified = {path: _exact_checkpoint_file(path, digest)
                for path, digest in EXPECTED_ARTIFACTS.items()}
    return verified


def _physical_files(prefix: str) -> set[str]:
    base = ROOT / _safe_relative(prefix.rstrip("/"))
    base.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    if base.is_symlink() or not base.is_dir():
        raise ValueError(f"archive root is not a regular directory: {prefix}")
    found: set[str] = set()
    for path in base.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in retained archive: {path}")
        if path.is_file():
            found.add(path.relative_to(ROOT).as_posix())
        elif not path.is_dir():
            raise ValueError(f"non-regular retained archive entry: {path}")
    return found


def _validate_archive(prefix: str, mapping: object, count: int,
                      extra_members: set[str] | None = None) -> dict[str, str]:
    if not isinstance(mapping, dict) or len(mapping) != count:
        raise ValueError(f"retained archive must bind exactly {count} files")
    expected_files = set(mapping) | (extra_members or set())
    if _physical_files(prefix) != expected_files:
        raise ValueError(f"retained archive physical population differs: {prefix}")
    safe_root = ROOT.resolve(strict=True)
    observed: dict[str, str] = {}
    for relative, expected in mapping.items():
        safe = _safe_relative(relative)
        if not safe.as_posix().startswith(prefix):
            raise ValueError(f"archive member outside retained prefix: {relative}")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"invalid archive hash: {relative}")
        path = _regular_inside(relative)
        path.resolve(strict=True).relative_to(safe_root)
        current = path.read_bytes()
        if _sha(current) != expected or _blob(CHECKPOINT, relative) != current:
            raise ValueError(f"retained checkpoint archive bytes differ: {relative}")
        observed[relative] = expected
    return observed


def _validate_initial_failure() -> tuple[dict[str, str], dict[str, Any]]:
    discovery_path = _regular_inside(INITIAL_DISCOVERY)
    if _sha(discovery_path.read_bytes()) != INITIAL_DISCOVERY_SHA256:
        raise ValueError("initial P114 discovery record changed")
    discovery = _read_json(discovery_path)
    if (not isinstance(discovery, dict)
            or discovery.get("schema") != "actinv-p114-initial-failure-1"
            or discovery.get("phase") != "P114"
            or discovery.get("gate") != "p114_oracle_regressions"
            or type(discovery.get("exit_code")) is not int or discovery["exit_code"] != 1
            or type(discovery.get("file_count")) is not int or discovery["file_count"] != 176
            or type(discovery.get("observed_test_count")) is not int or discovery["observed_test_count"] != 28
            or type(discovery.get("observed_error_count")) is not int or discovery["observed_error_count"] != 2
            or discovery.get("initial_successful_gate_names") != ["gate_recorder_regressions", "p113_history_regressions"]
            or discovery.get("g0_executed") is not False
            or discovery.get("g1_executed") is not False
            or discovery.get("g2_executed") is not False):
        raise ValueError("initial P114 failure disposition differs")
    archive = _validate_archive(
        INITIAL_PREFIX, discovery.get("files_sha256"), 176, extra_members={INITIAL_DISCOVERY}
    )
    receipt_path = INITIAL_PREFIX + "results/quality/p114/p114_oracle_regressions.json"
    log_path = INITIAL_PREFIX + "target/p114-p114_oracle_regressions.log"
    receipt_bytes = ROOT.joinpath(*_safe_relative(receipt_path).parts).read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    if (_sha(receipt_bytes) != discovery.get("failure_receipt_sha256")
            or receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != "P114" or receipt.get("gate") != "p114_oracle_regressions"
            or receipt.get("argv") != ["python3", "controls/test_p114_oracle.py"]
            or receipt.get("cwd") != "." or receipt.get("status") != "child_failed"
            or type(receipt.get("child_exit_code")) is not int or receipt["child_exit_code"] != 1
            or receipt.get("log_path") != "target/p114-p114_oracle_regressions.log"
            or receipt.get("log_sha256") != archive.get(log_path)):
        raise ValueError("initial P114 failed receipt or log differs")
    return archive, receipt


def _validate_gates(quality: dict[str, Any], final_archive: dict[str, str]) -> dict[str, Any]:
    gates = quality.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(EXPECTED_GATES):
        raise ValueError("P114 terminal G3 gate population differs")
    for name, exit_code in EXPECTED_GATES.items():
        gate = gates[name]
        if (not isinstance(gate, dict) or gate.get("name") != name
                or type(gate.get("exit_code")) is not int or gate["exit_code"] != exit_code
                or not isinstance(gate.get("argv"), list) or not gate["argv"]):
            raise ValueError(f"P114 gate observation differs: {name}")
        receipt_rel = gate.get("receipt_path")
        log_rel = gate.get("log_path")
        receipt_path = _safe_relative(receipt_rel)
        log_path = _safe_relative(log_rel)
        receipt_hash = gate.get("receipt_sha256")
        log_hash = gate.get("log_sha256")
        if final_archive.get(receipt_rel) != receipt_hash or final_archive.get(log_rel) != log_hash:
            raise ValueError(f"P114 gate receipt/log not bound by final archive: {name}")
        receipt = _read_json(ROOT / receipt_path)
        if (not isinstance(receipt, dict)
                or receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
                or receipt.get("phase") != "P114" or receipt.get("gate") != name
                or receipt.get("argv") != gate["argv"] or receipt.get("cwd") != "."
                or receipt.get("status") != ("completed" if exit_code == 0 else "child_failed")
                or type(receipt.get("child_exit_code")) is not int
                or receipt["child_exit_code"] != exit_code
                or receipt.get("log_sha256") != log_hash
                or not isinstance(receipt.get("log_path"), str)
                or Path(receipt["log_path"]).name != log_path.name):
            raise ValueError(f"P114 gate receipt content differs: {name}")
    failure = gates["p114_seal_regressions"]
    if (failure.get("receipt_path") != FINAL_PREFIX + "results/quality/p114/p114_seal_regressions.json"
            or failure.get("log_path") != FINAL_PREFIX + "target/p114-p114_seal_regressions.log"):
        raise ValueError("terminal P114 failure receipt/log path differs")
    return gates


def _validate_quality(quality: object) -> tuple[dict[str, str], dict[str, Any]]:
    if (not isinstance(quality, dict) or quality.get("schema") != "actinv-p114-quality-1"
            or quality.get("phase") != "P114" or quality.get("pass") is not False
            or quality.get("ci_status") != "not qualified; terminal source-gate failure"
            or quality.get("g0_status") != "not_sealed"
            or quality.get("g1_status") != "not_run" or quality.get("g2_status") != "not_run"
            or type(quality.get("repair_rounds")) is not int or quality.get("repair_rounds") != 1):
        raise ValueError("P114 terminal G3 disposition differs")
    terminal = quality.get("terminal_failure")
    if (not isinstance(terminal, dict) or terminal.get("gate") != "p114_seal_regressions"
            or type(terminal.get("exit_code")) is not int or terminal.get("exit_code") != 1
            or type(terminal.get("observed_test_count")) is not int or terminal.get("observed_test_count") != 4
            or type(terminal.get("observed_failure_count")) is not int or terminal.get("observed_failure_count") != 3
            or terminal.get("cause") != "checker expects absent terminal_g0_sha256 field in immutable P113 failure record"
            or quality.get("not_run_gate_names") != EXPECTED_NOT_RUN):
        raise ValueError("P114 terminal failure record differs")
    gates = quality.get("gates")
    final_map = quality.get("retained_after_amendment_sha256")
    final_archive = _validate_archive(FINAL_PREFIX, final_map, 196)
    if _sha(QUALITY.read_bytes()) != G3_SHA256:
        raise ValueError("P114 G3 artifact SHA differs")
    if final_map != {path: digest for path, digest in final_archive.items()}:
        raise ValueError("P114 final archive map differs from disk")
    checked_gates = _validate_gates(quality, final_archive)
    tests = quality.get("workspace_tests")
    if (not isinstance(tests, dict) or type(tests.get("passed")) is not int
            or tests.get("passed") != 484 or type(tests.get("failed")) is not int
            or tests.get("failed") != 0 or type(tests.get("ignored")) is not int
            or tests.get("ignored") != 2):
        raise ValueError("P114 observed workspace test summary differs")
    return final_archive, checked_gates


def _rust_sources(quality: dict[str, Any], final_archive: dict[str, str]) -> dict[str, str]:
    values = quality.get("production_rust_sha256")
    if not isinstance(values, dict) or len(values) != EXPECTED_RUST_SOURCES:
        raise ValueError("P114 G3 must bind exactly 100 Rust source files")
    from check_p113_verdict import _rust_source_paths

    expected_paths = _rust_source_paths(CHECKPOINT)
    if set(values) != expected_paths:
        raise ValueError("P114 Rust source population differs from checkpoint")
    for relative, expected in values.items():
        path = _safe_relative(relative)
        if not path.as_posix().startswith("crates/") or not path.as_posix().endswith(".rs"):
            raise ValueError(f"unsafe P114 Rust source path: {relative}")
        if (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                or final_archive.get(FINAL_PREFIX + relative) != expected):
            raise ValueError(f"P114 Rust source is not bound by final archive: {relative}")
        if _sha(_blob(CHECKPOINT, relative)) != expected:
            raise ValueError(f"P114 checkpoint Rust blob differs: {relative}")
    return values


def _p114_control_files() -> tuple[str, ...]:
    """Read the frozen control population from the checkpoint source, never import it."""
    source = _blob(CHECKPOINT, "controls/check_p114.py").decode("utf-8")
    tree = ast.parse(source, filename="checkpoint:controls/check_p114.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CONTROL_FILES"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if (not isinstance(value, tuple) or not value
                    or any(not isinstance(item, str) for item in value)
                    or len(value) != len(set(value))):
                raise ValueError("checkpoint P114 CONTROL_FILES declaration is invalid")
            for item in value:
                _safe_relative(item)
            return value
    raise ValueError("checkpoint P114 CONTROL_FILES declaration is missing")


def _validate_current_control_sources(final_archive: dict[str, str]) -> int:
    for relative in _p114_control_files():
        archived = final_archive.get(FINAL_PREFIX + relative)
        checkpoint = _blob(CHECKPOINT, relative)
        current = _regular_inside(relative).read_bytes()
        digest = _sha(checkpoint)
        if archived != digest or current != checkpoint:
            raise ValueError(f"current P114 frozen control source differs: {relative}")
    return len(_p114_control_files())


def _materialize_rust(source_hashes: dict[str, str]) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="p114-rust-", dir=TEMP_ROOT)
    snapshot = Path(temporary.name)
    for relative, expected in source_hashes.items():
        raw = _blob(CHECKPOINT, relative)
        if _sha(raw) != expected:
            temporary.cleanup()
            raise ValueError(f"P114 historical Rust blob differs: {relative}")
        destination = snapshot / _safe_relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return snapshot, temporary


def _derive_terminal(source_hashes: dict[str, str], snapshot: Path) -> dict[str, Any]:
    import check_p114_verdict as verdict

    original = verdict._source_commit_matches

    def historical_source_match(g3: dict[str, Any], record: dict[str, Any] | None) -> bool:
        if record is not None or g3.get("production_rust_sha256") != source_hashes:
            return False
        for relative, expected in source_hashes.items():
            path = snapshot / _safe_relative(relative)
            if path.is_symlink() or not path.is_file() or _sha(path.read_bytes()) != expected:
                return False
        return True

    verdict._source_commit_matches = historical_source_match
    try:
        return verdict.derive()
    finally:
        verdict._source_commit_matches = original


def _assert_no_unearned_records() -> None:
    for relative in (
        "results/g0_p114_twin_waste.json", "results/g1_p114_twin_waste.json",
        "results/g2_p114_twin_waste.json", "results/p114_implementation_commit.json",
        "results/p114_ci_runs.json",
    ):
        path = ROOT / relative
        if path.exists() or path.is_symlink():
            raise ValueError(f"P114 terminal failure must not gain later evidence: {relative}")
        try:
            _blob(CHECKPOINT, relative)
        except (OSError, ValueError, RuntimeError):
            continue
        raise ValueError(f"P114 checkpoint unexpectedly contains {relative}")


def verify() -> dict[str, Any]:
    report: dict[str, Any] = {"pass": False, "checkpoint_commit": CHECKPOINT,
                              "verdict": "P114-FAIL", "historical_source_verification": False}
    temporary = None
    try:
        record_path = _regular_inside("results/p114_failure_commit.json")
        if _read_json(PERSISTED_VERDICT) is None or _read_json(QUALITY) is None:
            raise ValueError("P114 terminal artifacts are missing")
        record = _read_json(record_path)
        _validate_record(record)
        quality = _read_json(QUALITY)
        initial_archive, first_receipt = _validate_initial_failure()
        final_archive, gates = _validate_quality(quality)
        if len(initial_archive) != 176 or len(final_archive) != 196 or len(gates) != 9:
            raise ValueError("P114 retained evidence counts differ")
        if sum(1 for gate in gates.values() if gate.get("exit_code") == 0) != 8:
            raise ValueError("P114 successful gate count differs")
        _assert_no_unearned_records()
        sources = _rust_sources(quality, final_archive)
        frozen_controls = _validate_current_control_sources(final_archive)
        snapshot, temporary = _materialize_rust(sources)
        derived = _derive_terminal(sources, snapshot)
        persisted = _read_json(PERSISTED_VERDICT)
        matches = (derived == persisted and derived.get("verdict") == "P114-FAIL"
                   and derived.get("historical_source_verification") is False)
        if not matches:
            raise ValueError("derived historical P114 verdict does not match persisted FAIL")
        checks = derived.get("gates", {})
        report.update({
            "pass": bool(matches and checks.get("G0") == "FAIL" and checks.get("G1") == "FAIL"
                          and checks.get("G2") == "FAIL" and checks.get("G3_local") == "FAIL"
                          and checks.get("G3_CI") == "PENDING"),
            "failure_record_verified": True,
            "initial_archive_files_verified": len(initial_archive),
            "final_amended_archive_files_verified": len(final_archive),
            "rust_files_verified_against_checkpoint": len(sources),
            "frozen_p114_control_files_verified": frozen_controls,
            "executed_gate_receipts_verified": len(gates),
            "successful_gate_receipts_verified": 8,
            "initial_oracle_failure_receipt_verified": first_receipt.get("child_exit_code") == 1,
            "terminal_seal_failure_exit_1_verified": gates["p114_seal_regressions"].get("exit_code") == 1,
            "g0_unsealed_g1_g2_unrun": (quality.get("g0_status") == "not_sealed"
                                         and quality.get("g1_status") == "not_run"
                                         and quality.get("g2_status") == "not_run"),
            "derived_verdict_matches_persisted": matches,
            "derived_verdict": derived.get("verdict"),
            "historical_source_verification": False,
        })
        if report["pass"] is not True:
            report["pass"] = False
            report["error"] = "P114 terminal FAIL gates or execution disposition differ"
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        report["error"] = str(error)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return report


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(0 if result.get("pass") is True else 1)
