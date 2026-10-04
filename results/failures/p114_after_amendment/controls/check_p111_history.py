#!/usr/bin/env python3
"""Read-only verification of the retained terminal P111 local failure."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "ff7e42e9f106946e610297d46f9f2b8787813f7e"
FAILURE_RECORD = ROOT / "results/p111_failure_commit.json"
PERSISTED_VERDICT = ROOT / "results/p111_verdict.json"
QUALITY = ROOT / "results/g3_p111_quality.json"
FAILURE_LOG = "results/failures/p111_initial/historical_p108_invocation_after_repair.log"
FAILURE_LOG_SHA256 = "b86b768a2a0deddb77d90f826dd02e55a13d1b418ef06756e084a8887a81ce7c"
TEMP_ROOT = ROOT / "target/p112-p111-history-tmp"
ARTIFACT_HASHES = {
    "results/g0_p111_intrusion_screen.json": "a89640ce1c7517ad14ea8c047dade510a1645c2fd6eb49d8a5db0aee2b297940",
    "results/g1_p111_intrusion_screen.json": "f23fe8837def36420486e1007799f27d29cff488808c611dc28166b862262c23",
    "results/g2_p111_intrusion_screen.json": "de311b2981ec09f0eea8c05a19a84ac5bc83dd3f46e6596ce075de537837e1fd",
    "results/g3_p111_quality.json": "18caa3dced925f47ba5f3bbe2bdc2935a0d3db838b6137acd221126a80fb309e",
    "results/p111_verdict.json": "35c3b5eb4ef12fcad7e78f90a9103b476cdb51b29cb7974b7ec0a3ddae25579c",
}
EXPECTED_RUST_SOURCES = 99


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _git_blob(commit: str, source_path: str, *, root: Path = ROOT) -> bytes:
    """Read one historical blob through P105's bounded, reaping Git runner."""
    from p105_budget_control import _run

    child = _run(["git", "show", f"{commit}:{source_path}"], cwd=root, timeout_s=120)
    if child.returncode != 0:
        raise ValueError(f"git show failed for {source_path}: {child.stderr[-1200:]}")
    return child.stdout.encode("utf-8")


def _validate_failure_record(record: dict, root: Path = ROOT, *, blob_reader=_git_blob) -> dict[str, bytes]:
    if record.get("schema") != "actinv-p111-failure-implementation-1":
        raise ValueError("failure checkpoint record schema differs")
    if record.get("commit_sha") != CHECKPOINT:
        raise ValueError("failure checkpoint commit differs from frozen P111 SHA")
    if record.get("verdict") != "P111-FAIL" or record.get("scientific_screen_evidence_obtained") is not True:
        raise ValueError("failure record does not preserve P111's failure and completed screen evidence")
    if record.get("ci_qualification") != "none; local failed checkpoint":
        raise ValueError("failure record makes an unsupported CI qualification claim")
    if record.get("failed_gate") != "historical_p108_replay" or record.get("failure_exit_code") != 2:
        raise ValueError("failure record does not bind the terminal exit-2 gate")
    if record.get("failure_log_path") != FAILURE_LOG or record.get("failure_log_sha256") != FAILURE_LOG_SHA256:
        raise ValueError("retained late-invocation failure binding differs")
    log_path = root / FAILURE_LOG
    log_bytes = log_path.read_bytes()
    if _sha(log_bytes) != FAILURE_LOG_SHA256:
        raise ValueError("retained late-invocation failure log changed")
    committed_log = blob_reader(CHECKPOINT, FAILURE_LOG)
    if committed_log != log_bytes or _sha(committed_log) != FAILURE_LOG_SHA256:
        raise ValueError("checkpoint Git blob differs for retained failure log")
    declared = record.get("artifact_sha256")
    if not isinstance(declared, dict) or set(declared) != set(ARTIFACT_HASHES):
        raise ValueError("failure record artifact population differs")
    verified: dict[str, bytes] = {}
    for relative, expected in ARTIFACT_HASHES.items():
        if declared.get(relative) != expected:
            raise ValueError(f"failure record hash differs for {relative}")
        current = (root / relative).read_bytes()
        if _sha(current) != expected:
            raise ValueError(f"current retained artifact changed: {relative}")
        committed = blob_reader(CHECKPOINT, relative)
        if committed != current or _sha(committed) != expected:
            raise ValueError(f"checkpoint Git blob differs for {relative}")
        verified[relative] = current
    if (root / "results/p111_implementation_commit.json").exists():
        raise ValueError("terminal P111 failure must not invent an implementation/CI record")
    if (root / "results/p111_ci_runs.json").exists():
        raise ValueError("terminal P111 failure must not invent CI qualification evidence")
    return verified


def _source_hashes(quality: dict) -> dict[str, str]:
    from check_p107_history import _safe_rust_path

    values = quality.get("production_rust_sha256")
    if not isinstance(values, dict) or len(values) != EXPECTED_RUST_SOURCES:
        raise ValueError(f"P111 quality must record exactly {EXPECTED_RUST_SOURCES} Rust source blobs")
    result = {}
    for raw_path, digest in values.items():
        path = _safe_rust_path(raw_path)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid recorded Rust source hash for {raw_path}")
        result[path.as_posix()] = digest
    return result


def _materialize_sources(quality: dict, directory: Path, *, commit: str = CHECKPOINT,
                         root: Path = ROOT, blob_reader=_git_blob) -> Path:
    from check_p107_history import _materialize_history

    hashes = _source_hashes(quality)

    def read_blob(commit_sha: str, source_path: str) -> bytes:
        return blob_reader(commit_sha, source_path, root=root)

    return _materialize_history(commit, hashes, directory, blob_reader=read_blob)


def _derive_with_snapshot(snapshot: Path) -> dict:
    """Run the unchanged P111 verdict derivation against its recorded Rust tree."""
    module = importlib.import_module("check_p111_verdict")
    original_quality = module._quality
    original_root = module.ROOT

    def historical_quality(quality: dict, _record: dict | None):
        saved_root = module.ROOT
        module.ROOT = snapshot
        try:
            return original_quality(quality, record=None)
        finally:
            module.ROOT = saved_root

    module._quality = historical_quality
    try:
        return module.derive()
    finally:
        module._quality = original_quality
        module.ROOT = original_root


def _failure_disposition(quality: dict, derived: dict) -> bool:
    gates = derived.get("gates", {})
    failure_gate = quality.get("gates", {}).get("historical_p108_replay", {})
    terminal = quality.get("terminal_failure_after_repair", {})
    later_gates = [quality.get("gates", {}).get(name, {})
                   for name in ("historical_p109_replay", "historical_p110_replay")]
    return (
        type(quality.get("repair_rounds")) is int
        and quality.get("repair_rounds") == 1
        and type(failure_gate.get("exit_code")) is int
        and failure_gate.get("exit_code") == 2
        and failure_gate.get("log_sha256") == FAILURE_LOG_SHA256
        and type(terminal.get("exit_code")) is int
        and terminal.get("exit_code") == 2
        and terminal.get("retained_log_sha256") == FAILURE_LOG_SHA256
        and terminal.get("reason") == "coordinator invoked nonexistent controls/check_p108_history.py after the registered repair"
        and all(gates.get(name) == "PASS" for name in ("G0", "G1", "G2"))
        and gates.get("G3_local") == "FAIL"
        and gates.get("G3_CI") == "PENDING"
        and all(gate.get("status") == "not_run_after_terminal_failure" and gate.get("exit_code") is None
                for gate in later_gates)
    )


def verify() -> dict:
    report: dict = {"pass": False, "checkpoint_commit": CHECKPOINT,
                    "expected_artifact_sha256": ARTIFACT_HASHES,
                    "historical_source_verification": False}
    try:
        record = _read_json(FAILURE_RECORD)
        retained = _validate_failure_record(record)
        quality = _read_json(QUALITY)
        source_hashes = _source_hashes(quality)
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="p111-", dir=TEMP_ROOT) as temporary:
            snapshot = _materialize_sources(quality, Path(temporary))
            derived = _derive_with_snapshot(snapshot)
        persisted = _read_json(PERSISTED_VERDICT)
        quality_checks = derived.get("quality_checks", {})
        disposition = _failure_disposition(quality, derived)
        matched = derived == persisted and derived.get("verdict") == "P111-FAIL"
        historical = quality_checks.get("historical_source_verification")
        report.update({"pass": bool(matched and disposition and historical is False),
                       "artifact_blobs_verified": len(retained) == 5,
                       "rust_source_count": len(source_hashes),
                       "rust_sources_materialized": True,
                       "derived_verdict_matches_persisted": matched,
                       "derived_verdict": derived.get("verdict"),
                       "g0_g1_g2_pass_g3_local_fail": disposition,
                       "historical_source_verification": historical,
                       "failure_log_exit_2_and_bound": disposition,
                       "persisted_verdict_sha256": _sha(PERSISTED_VERDICT.read_bytes())})
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        report["error"] = str(error)
    return report


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(0 if result.get("pass") is True else 1)
