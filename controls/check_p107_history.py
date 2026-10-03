#!/usr/bin/env python3
"""Verify P107 against the Rust source recorded by its implementation commit."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "27d5636ad5331a3dacd8130a6b5e3516fb661e9f"
QUALITY = ROOT / "results/g3_p107_quality.json"
TEMP_ROOT = ROOT / "target/p107-history-tmp"
REQUIRED_WORKFLOWS = {"controls", "desktop builds", "Build browser workbench", "Build handbook"}


def _sha_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _safe_rust_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("source path must be a nonempty string")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts
            or path.as_posix() != value or not value.startswith("crates/")
            or path.suffix != ".rs"):
        raise ValueError(f"unsafe or non-Rust source path: {value!r}")
    return path


def _validate_record(record: dict, g0_bytes: bytes, g3_bytes: bytes,
                     ci_runs: object) -> tuple[str, dict[str, str]]:
    if record.get("schema") != "actinv-p107-implementation-1":
        raise ValueError("implementation record schema differs")
    commit = record.get("commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("implementation commit SHA is malformed")
    if commit != EXPECTED_COMMIT:
        raise ValueError("implementation commit differs from frozen P107 commit")
    if record.get("g0_sha256") != _sha_bytes(g0_bytes):
        raise ValueError("implementation record does not bind current G0 evidence")
    if record.get("g3_sha256") != _sha_bytes(g3_bytes):
        raise ValueError("implementation record does not bind current G3 evidence")
    if not isinstance(ci_runs, list) or not ci_runs:
        raise ValueError("P107 CI evidence must be a nonempty array")
    names = set()
    for run in ci_runs:
        if not isinstance(run, dict):
            raise ValueError("P107 CI entry must be an object")
        names.add(run.get("workflowName"))
        if run.get("headSha") != commit:
            raise ValueError("P107 CI run is not bound to the implementation commit")
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            raise ValueError("P107 CI run is not completed successfully")
    if not REQUIRED_WORKFLOWS.issubset(names):
        raise ValueError("P107 CI evidence omits a required workflow")
    sources = _read_json(QUALITY).get("production_rust_sha256")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("P107 quality evidence has no recorded Rust source hashes")
    normalized = {}
    for raw_path, digest in sources.items():
        path = _safe_rust_path(raw_path)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid recorded Rust source hash for {raw_path}")
        normalized[path.as_posix()] = digest
    return commit, normalized


def _git_blob(commit: str, source_path: str, *, root: Path = ROOT) -> bytes:
    from p105_budget_control import _run

    child = _run(["git", "show", f"{commit}:{source_path}"], cwd=root, timeout_s=120)
    if child.returncode != 0:
        raise ValueError(f"git show failed for {source_path}: {child.stderr[-1200:]}")
    return child.stdout.encode("utf-8")


def _materialize_history(commit: str, source_hashes: dict[str, str], directory: Path,
                         *, blob_reader=_git_blob) -> Path:
    snapshot = directory / "source"
    snapshot.mkdir(parents=True, exist_ok=False)
    for source_path, expected_hash in sorted(source_hashes.items()):
        safe_path = _safe_rust_path(source_path)
        content = blob_reader(commit, safe_path.as_posix())
        if _sha_bytes(content) != expected_hash:
            raise ValueError(f"historical source hash mismatch for {source_path}")
        target = snapshot.joinpath(*safe_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return snapshot


def _derive_with_historical_sources(snapshot: Path) -> dict:
    # Keep the P107 verdict implementation and all evidence paths unchanged.
    # Only its quality checker resolves Rust source paths inside the snapshot.
    module = importlib.import_module("check_p107_verdict")
    original_quality = module._quality_ok

    def historical_quality(quality: dict):
        original_root = module.ROOT
        module.ROOT = snapshot
        try:
            return original_quality(quality)
        finally:
            module.ROOT = original_root

    module._quality_ok = historical_quality
    try:
        return module.derive()
    finally:
        module._quality_ok = original_quality


def verify() -> dict:
    record_path = ROOT / "results/p107_implementation_commit.json"
    quality_path = ROOT / "results/g3_p107_quality.json"
    g0_path = ROOT / "results/g0_p107_bounds.json"
    ci_path = ROOT / "results/p107_ci_runs.json"
    verdict_path = ROOT / "results/p107_verdict.json"
    record = _read_json(record_path)
    ci_runs = json.loads(ci_path.read_text(encoding="utf-8"))
    commit, source_hashes = _validate_record(record, g0_path.read_bytes(),
                                              quality_path.read_bytes(), ci_runs)
    persisted = _read_json(verdict_path)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p107-historical-", dir=TEMP_ROOT) as temp:
        snapshot = _materialize_history(commit, source_hashes, Path(temp))
        derived = _derive_with_historical_sources(snapshot)
    passed = derived == persisted and derived.get("verdict") == "P107-PASS"
    return {"pass": passed,
            "persisted_verdict_matches_historical_derivation": derived == persisted,
            "implementation_commit": commit,
            "source_file_count": len(source_hashes),
            "source_hashes_verified": True,
            "g0_sha256": _sha_bytes(g0_path.read_bytes()),
            "g3_sha256": _sha_bytes(quality_path.read_bytes()),
            "ci_sha256": _sha_file(ci_path)}


def main() -> int:
    try:
        report = verify()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        report = {"pass": False, "error": str(error)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
