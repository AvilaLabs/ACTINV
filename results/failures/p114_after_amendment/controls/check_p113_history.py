#!/usr/bin/env python3
"""Read-only verification of P113's terminal local failure checkpoint."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "81db5c03eeb876a88b6ddd7a2d2b8975308178e5"
PROTOCOL_SHA256 = "0495157f3e8308d6a28475b94363e24b932f462c10f901518fa1c809a68ebbfa"
AMENDMENT_SHA256 = "6a0191ccc3500ebaeb3165a2b630d0c857d18335ce8fe70dcf07d9d7445ac46c"
INITIAL_G0_SHA256 = "61d8c6faaf7eb93d8f3a4d9e369e8710eee45b93f946d405f54c216dd730a037"
DISCOVERY_SHA256 = "0f099f92637eea2e3346f02be0d588f9f6d96c95cdd41aae0d7050b1fbd36c4f"
FIXTURE_SHA256 = "e6bcebc856c7faaa571455a45c81da49e988a34bfd1f7149bc67656d93d5f956"
TERMINAL_VERDICT_SHA256 = "ae000348b4e6c58eb217d1ce9c5e9946fcf7b440562835855f724f6a1650e461"
G3_SHA256 = "529fb6b0f5c1542165300240a2d59cd2cd6ad9fa84c11d4f0748d48b72da2b01"
INVOCATION_PATH = "results/failures/p113_after_amendment/invocation.json"
INVOCATION_SHA256 = "c21f659d7c190aede161d7107ae6e0889e6442d69b3d802f7b12850eb42dcc71"
FAILURE_LOG_PATH = "results/failures/p113_after_amendment/target/p113-workspace-tests-final-confirmed.scope.log"
FAILURE_LOG_SHA256 = "b80894b20594fb39e9f074e1494e08880d96682595ec733912dac24df2190ac2"
FAILURE_RECORD = ROOT / "results/p113_failure_commit.json"
PERSISTED_VERDICT = ROOT / "results/p113_verdict.json"
QUALITY = ROOT / "results/g3_p113_quality.json"
TEMP_ROOT = ROOT / "target/p114-p113-history-tmp"
EXPECTED_ARTIFACTS = {
    "results/p113_verdict.json": TERMINAL_VERDICT_SHA256,
    "results/g0_p113_twin_waste.json": INITIAL_G0_SHA256,
    "results/g3_p113_quality.json": G3_SHA256,
    INVOCATION_PATH: INVOCATION_SHA256,
    FAILURE_LOG_PATH: FAILURE_LOG_SHA256,
    "results/failures/p113_initial_seal/discovery.json": DISCOVERY_SHA256,
    "controls/fixtures/p113/cases.json": FIXTURE_SHA256,
    "protocols/ACTINV-P113_PROTOCOL.md": PROTOCOL_SHA256,
    "protocols/ACTINV-P113_AMENDMENT_A.md": AMENDMENT_SHA256,
}
EXPECTED_RUST_SOURCES = 100
TERMINAL_NOT_RUN = (
    "release_build", "p113_oracle_regressions", "p113_seal_regressions",
    "p113_verdict_regressions",
    "p105_scientific_replay", "p107_scientific_replay", "p108_scientific_replay",
    "p110_scientific_replay", "p111_scientific_replay", "p112_scientific_replay",
    "handbook_build", "handbook_links", "handbook_chromium",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_repo_path(value: object) -> Path:
    from check_p113_verdict import _safe_rel

    return _safe_rel(value)


def _blob(commit: str, relative: str) -> bytes:
    from check_p107_history import _git_blob

    return _git_blob(commit, relative, root=ROOT)


def _regular_inside_repo(full: Path) -> bool:
    root = ROOT.resolve(strict=True)
    try:
        full.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return False
    try:
        relative = full.relative_to(ROOT)
    except ValueError:
        return False
    cursor = ROOT
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return False
    return full.is_file()


def _exact_git_file(relative: str, expected_sha256: str) -> bytes:
    path = _safe_repo_path(relative)
    full = ROOT / path
    if not _regular_inside_repo(full):
        raise ValueError(f"retained evidence is not a regular file: {relative}")
    current = full.read_bytes()
    if _sha(current) != expected_sha256 or _blob(CHECKPOINT, relative) != current:
        raise ValueError(f"checkpoint bytes differ for {relative}")
    return current


def _validate_record(record: object) -> dict[str, bytes]:
    if not isinstance(record, dict):
        raise ValueError("P113 failure checkpoint record must be an object")
    exact = {
        "schema": "actinv-p113-failure-implementation-1",
        "commit_sha": CHECKPOINT,
        "verdict": "P113-FAIL",
        "ci_qualification": "none; terminal local invocation failure",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "terminal_verdict_sha256": TERMINAL_VERDICT_SHA256,
        "partial_g3_sha256": G3_SHA256,
        "initial_g0_sha256": INITIAL_G0_SHA256,
        "initial_discovery_sha256": DISCOVERY_SHA256,
        "fixture_sha256": FIXTURE_SHA256,
        "failure_log_path": FAILURE_LOG_PATH,
        "failure_log_sha256": FAILURE_LOG_SHA256,
        "invocation_path": INVOCATION_PATH,
        "invocation_sha256": INVOCATION_SHA256,
        "failed_gate": "workspace_tests_confirmation_invocation",
        "failure_exit_code": 127,
        "amended_g0_executed": False,
        "g1_executed": False,
        "g2_executed": False,
        "implementation_record": None,
        "ci_record": None,
        "initial_archive_file_count": 59,
        "amended_retained_file_count": 109,
        "rust_source_count": EXPECTED_RUST_SOURCES,
        "retained_initial_g0_artifact_sha256": INITIAL_G0_SHA256,
        "terminal_g1_sha256": None,
        "terminal_g2_sha256": None,
    }
    for key, expected in exact.items():
        if type(expected) is bool:
            valid = type(record.get(key)) is bool and record.get(key) is expected
        elif type(expected) is int:
            valid = type(record.get(key)) is int and record.get(key) == expected
        else:
            valid = record.get(key) == expected
        if not valid:
            raise ValueError(f"failure checkpoint field differs: {key}")
    expected_fields = set(exact) | {"artifact_sha256"}
    if set(record) != expected_fields:
        raise ValueError("failure checkpoint record has missing or unrecognized fields")
    if record.get("artifact_sha256") != EXPECTED_ARTIFACTS:
        raise ValueError("failure checkpoint artifact population or hashes differ")
    verified = {relative: _exact_git_file(relative, expected)
                for relative, expected in EXPECTED_ARTIFACTS.items()}
    return verified


def _validate_terminal_controls() -> int:
    import check_p113

    for relative in check_p113.CONTROL_FILES:
        path = _safe_repo_path(relative)
        full = ROOT / path
        if not _regular_inside_repo(full):
            raise ValueError(f"P113 control source is not a regular file: {relative}")
        current = full.read_bytes()
        if _blob(CHECKPOINT, path.as_posix()) != current:
            raise ValueError(f"P113 sealed control source changed after checkpoint: {relative}")
    return len(check_p113.CONTROL_FILES)


def _validate_invocation(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("terminal invocation must be an object")
    if (value.get("schema") != "actinv-p113-terminal-invocation-1"
            or value.get("phase") != "P113"
            or value.get("gate") != "workspace_tests_confirmation_invocation"
            or type(value.get("observed_exit_code")) is not int
            or value.get("observed_exit_code") != 127
            or value.get("cgroup_started") is not False
            or value.get("cargo_started") is not False
            or value.get("amended_g0_executed") is not False
            or value.get("g1_executed") is not False
            or value.get("g2_executed") is not False
            or value.get("log") != FAILURE_LOG_PATH):
        raise ValueError("terminal invocation disposition differs")
    command = value.get("command")
    if not isinstance(command, str) or not command.startswith("undefined timeout "):
        raise ValueError("failed invocation command was not the retained undefined-prefix call")
    return value


def _validate_initial_seal() -> tuple[int, bool]:
    import check_p113

    retained, matches = check_p113._repair_evidence()
    if matches is not True or len(retained) != 59:
        raise ValueError("initial P113 G0 archive/discovery no longer matches")
    for relative, expected in retained.items():
        path = _safe_repo_path(relative)
        current = (ROOT / path).read_bytes()
        if _sha(current) != expected or _blob(CHECKPOINT, relative) != current:
            raise ValueError(f"initial seal archive checkpoint bytes differ: {relative}")
    return len(retained), True


def _source_map(quality: dict[str, Any]) -> dict[str, str]:
    values = quality.get("production_rust_sha256")
    if not isinstance(values, dict) or len(values) != EXPECTED_RUST_SOURCES:
        raise ValueError(f"terminal quality must bind exactly {EXPECTED_RUST_SOURCES} Rust files")
    result: dict[str, str] = {}
    for raw_path, digest in values.items():
        path = _safe_repo_path(raw_path)
        if not path.as_posix().startswith("crates/") or not path.as_posix().endswith(".rs"):
            raise ValueError(f"unexpected Rust source path: {raw_path!r}")
        if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or path.as_posix() in result):
            raise ValueError(f"invalid Rust source binding: {raw_path!r}")
        result[path.as_posix()] = digest
    from check_p113_verdict import _rust_source_paths

    if set(result) != _rust_source_paths(CHECKPOINT):
        raise ValueError("terminal quality does not cover the exact checkpoint Rust population")
    return result


def _materialize_rust(source_hashes: dict[str, str]) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="p113-rust-", dir=TEMP_ROOT)
    # The caller cleans this directory after verdict derivation.
    snapshot = Path(temporary.name)
    for relative, expected in source_hashes.items():
        raw = _blob(CHECKPOINT, relative)
        if _sha(raw) != expected:
            raise ValueError(f"terminal Rust Git blob differs from G3: {relative}")
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return snapshot, temporary


def _validate_amended_snapshot(quality: dict[str, Any]) -> int:
    retained = quality.get("retained_after_amendment_sha256")
    if not isinstance(retained, dict) or len(retained) != 109:
        raise ValueError("terminal G3 must retain its complete 109-file amended source snapshot")
    for raw_path, expected in retained.items():
        path = _safe_repo_path(raw_path)
        if (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                or not path.as_posix().startswith("results/failures/p113_after_amendment/")):
            raise ValueError(f"invalid amended-source snapshot binding: {raw_path!r}")
        full = ROOT / path
        if not _regular_inside_repo(full):
            raise ValueError(f"amended-source snapshot entry is not a regular file: {raw_path}")
        content = full.read_bytes()
        if _sha(content) != expected or _blob(CHECKPOINT, path.as_posix()) != content:
            raise ValueError(f"terminal amended-source Git bytes differ: {raw_path}")
    return len(retained)


def _assert_no_unearned_records() -> None:
    for relative in ("results/p113_implementation_commit.json", "results/p113_ci_runs.json",
                     "results/g1_p113_twin_waste.json", "results/g2_p113_twin_waste.json"):
        if (ROOT / relative).exists() or (ROOT / relative).is_symlink():
            raise ValueError(f"P113 terminal failure must not gain later evidence: {relative}")
        try:
            _blob(CHECKPOINT, relative)
        except (OSError, ValueError, RuntimeError):
            continue
        raise ValueError(f"terminal checkpoint unexpectedly contains {relative}")


def _derive_terminal(source_hashes: dict[str, str], snapshot: Path) -> dict[str, Any]:
    import check_p113_verdict as verdict

    original = verdict._source_commit_matches

    def historical_source_match(g3: dict[str, Any], record: dict[str, Any] | None) -> bool:
        if record is not None or g3.get("production_rust_sha256") != source_hashes:
            return False
        for relative, expected in source_hashes.items():
            path = snapshot / relative
            if not path.is_file() or path.is_symlink() or _sha(path.read_bytes()) != expected:
                return False
        return True

    verdict._source_commit_matches = historical_source_match
    try:
        return verdict.derive()
    finally:
        verdict._source_commit_matches = original


def verify() -> dict[str, Any]:
    report: dict[str, Any] = {"pass": False, "checkpoint_commit": CHECKPOINT,
                              "verdict": "P113-FAIL", "historical_source_verification": False}
    temporary = None
    try:
        if not _regular_inside_repo(FAILURE_RECORD):
            raise ValueError("P113 failure checkpoint record is not a regular repository file")
        record = _read_json(FAILURE_RECORD)
        retained = _validate_record(record)
        invocation = _validate_invocation(retained[INVOCATION_PATH])
        archived_count, initial_seal_ok = _validate_initial_seal()
        quality = _read_json(QUALITY)
        persisted = _read_json(PERSISTED_VERDICT)
        if not isinstance(quality, dict) or not isinstance(persisted, dict):
            raise ValueError("terminal quality or verdict artifact is missing")
        _assert_no_unearned_records()
        amended_count = _validate_amended_snapshot(quality)
        if (persisted.get("verdict") != "P113-FAIL"
                or persisted.get("historical_source_verification") is not False
                or persisted.get("implementation_commit") is not None
                or persisted.get("implementation_record_sha256") is not None
                or persisted.get("ci_evidence_sha256") is not None
                or quality.get("amended_g0_status") != "not_run"
                or quality.get("g1_status") != "not_run"
                or quality.get("g2_status") != "not_run"):
            raise ValueError("P113 persisted disposition contains unsupported qualification")
        if type(quality.get("repair_rounds")) is not int or quality["repair_rounds"] != 1:
            raise ValueError("P113 terminal record must preserve its one consumed repair round")
        if (quality.get("amended_g0_status") != "not_run"
                or quality.get("g1_status") != "not_run"
                or quality.get("g2_status") != "not_run"):
            raise ValueError("amended G0/G1/G2 were claimed as executed")
        for name in TERMINAL_NOT_RUN:
            gate = quality.get("gates", {}).get(name)
            if (not isinstance(gate, dict) or gate.get("status") != "not_run_after_terminal_failure"
                    or gate.get("exit_code") is not None):
                raise ValueError(f"terminal P113 gate was claimed or misreported: {name}")
        control_count = _validate_terminal_controls()
        source_hashes = _source_map(quality)
        snapshot, temporary = _materialize_rust(source_hashes)
        derived = _derive_terminal(source_hashes, snapshot)
        matches = derived == persisted and derived.get("verdict") == "P113-FAIL"
        checks = derived.get("gates", {})
        amended_not_run = (all(checks.get(key) == "FAIL" for key in ("G0", "G1", "G2"))
                           and checks.get("G3_local") == "FAIL"
                           and checks.get("G3_CI") == "PENDING")
        report.update({
            "pass": bool(matches and amended_not_run and initial_seal_ok and invocation["observed_exit_code"] == 127),
            "failure_artifacts_verified": len(retained) == len(EXPECTED_ARTIFACTS),
            "initial_g0_archive_files_verified": archived_count,
            "amended_source_archive_files_verified": amended_count,
            "terminal_control_sources_verified": control_count,
            "historical_rust_files_materialized": len(source_hashes),
            "terminal_invocation_exit_127_no_children": True,
            "amended_g0_g1_g2_not_run": amended_not_run,
            "derived_verdict_matches_persisted": matches,
            "derived_verdict": derived.get("verdict"),
            "historical_source_verification": False,
        })
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
