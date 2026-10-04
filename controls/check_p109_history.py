#!/usr/bin/env python3
"""Read-only verification that the retained P109 checkpoint remains FAIL."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "495d6bf99248d3b99af43ca82894631c3e6b8c87"
PROTOCOL_SHA256 = "8ad8870d42d30f3f8c330021b15e7f4f570032deba1ee5b79f57b9411e40e7f9"
FAILURE_RECORD = ROOT / "results/p109_failure_commit.json"
PERSISTED_VERDICT = ROOT / "results/p109_verdict.json"
QUALITY = ROOT / "results/g3_p109_quality.json"
TEMP_ROOT = ROOT / "target/p110-history-tmp"
ARTIFACT_HASHES = {
    "results/g0_p109_composition_solve.json": "cad2351bf53e7f0165c6a899bcd8b97ba302d73effbb3e52bd220dcf598c1c09",
    "results/g1_p109_composition_solve.json": "a74fc950793785af5fa667eca793aca5934fc54ab366435004f88093b2451986",
    "results/g3_p109_quality.json": "d7b42c419704e21dcbe1fadfa3a3970a55b390851890e910598272571fcb1e36",
    "results/p109_verdict.json": "b6d370e3e560d0d7f780b0a7bb6344413fee87eb69eab865ac9e5ac58b449398",
}
EXPECTED_RUST_SOURCES = 97


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _registered(root: Path = ROOT) -> bool:
    protocol = root / "protocols/ACTINV-P110_PROTOCOL.md"
    registry = root / "protocols/protocol_hash.txt"
    expected = f"{PROTOCOL_SHA256}  protocols/ACTINV-P110_PROTOCOL.md"
    return protocol.is_file() and _sha(protocol.read_bytes()) == PROTOCOL_SHA256 and registry.is_file() and expected in registry.read_text(encoding="utf-8").splitlines()


def _git_blob(commit: str, path: str, *, root: Path = ROOT) -> bytes:
    """Read a committed blob using the reviewed P105 bounded Git runner."""
    from p105_budget_control import _run

    child = _run(["git", "show", f"{commit}:{path}"], cwd=root, timeout_s=120)
    if child.returncode != 0:
        raise ValueError(f"git show failed for {path}: {child.stderr[-1200:]}")
    return child.stdout.encode("utf-8")


def _validate_failure_record(record: dict, root: Path = ROOT, *, blob_reader=_git_blob) -> dict[str, bytes]:
    if record.get("schema") != "actinv-p109-failure-implementation-1":
        raise ValueError("failure checkpoint record schema differs")
    if record.get("commit_sha") != CHECKPOINT:
        raise ValueError("failure checkpoint commit differs from P110's frozen SHA")
    if record.get("verdict") != "P109-FAIL" or record.get("native_solver_evidence_obtained") is not False:
        raise ValueError("failure record does not describe the retained P109 terminal failure")
    if record.get("ci_qualification") != "none; local failed checkpoint":
        raise ValueError("failure record makes an unsupported CI qualification claim")
    declared = record.get("artifact_sha256")
    if not isinstance(declared, dict) or set(declared) != set(ARTIFACT_HASHES):
        raise ValueError("failure record artifact population differs or includes an invented artifact/G2")
    verified: dict[str, bytes] = {}
    for relative, frozen_sha in ARTIFACT_HASHES.items():
        if declared.get(relative) != frozen_sha:
            raise ValueError(f"failure record hash differs for {relative}")
        path = root / relative
        current = path.read_bytes()
        if _sha(current) != frozen_sha:
            raise ValueError(f"current retained artifact differs for {relative}")
        committed = blob_reader(CHECKPOINT, relative)
        if _sha(committed) != frozen_sha or committed != current:
            raise ValueError(f"checkpoint Git blob differs for {relative}")
        verified[relative] = current
    if (root / "results/g2_p109_composition_solve.json").exists():
        raise ValueError("P109 checkpoint has an unexpected G2 result")
    if not _registered(root):
        raise ValueError("P110 protocol registration/hash differs")
    return verified


def _source_hashes(quality: dict) -> dict[str, str]:
    from check_p107_history import _safe_rust_path

    values = quality.get("production_rust_sha256")
    if not isinstance(values, dict) or len(values) != EXPECTED_RUST_SOURCES:
        raise ValueError(f"P109 quality must record exactly {EXPECTED_RUST_SOURCES} Rust source blobs")
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

    source_hashes = _source_hashes(quality)
    def read_blob(commit_sha: str, source_path: str) -> bytes:
        return blob_reader(commit_sha, source_path, root=root)
    return _materialize_history(commit, source_hashes, directory, blob_reader=read_blob)


def _derive_with_snapshot(snapshot: Path) -> dict:
    """Use the original P109 verdict code, scoping only quality's source root."""
    module = importlib.import_module("check_p109_verdict")
    original_quality = module._quality
    original_root = module.ROOT

    def historical_quality(quality: dict, record: dict | None):
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


def verify() -> dict:
    """Verify the exact retained P109 FAIL without changing evidence or verdicts."""
    report: dict = {"pass": False, "checkpoint_commit": CHECKPOINT,
                    "expected_artifact_sha256": ARTIFACT_HASHES,
                    "historical_source_verification": False}
    try:
        record = _read_json(FAILURE_RECORD)
        retained = _validate_failure_record(record)
        quality = _read_json(QUALITY)
        source_hashes = _source_hashes(quality)
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="p109-", dir=TEMP_ROOT) as temp:
            snapshot = _materialize_sources(quality, Path(temp))
            derived = _derive_with_snapshot(snapshot)
        persisted = _read_json(PERSISTED_VERDICT)
        no_g2 = ("results/g2_p109_composition_solve.json" not in record.get("artifact_sha256", {})
                 and not (ROOT / "results/g2_p109_composition_solve.json").exists()
                 and derived.get("gates", {}).get("G2") == "FAIL")
        matched = derived == persisted and derived.get("verdict") == "P109-FAIL"
        historical_flag = derived.get("quality_checks", {}).get("historical_source_verification")
        report.update({"pass": bool(matched and no_g2 and historical_flag is False),
                       "artifact_blobs_verified": len(retained) == 4,
                       "rust_source_count": len(source_hashes),
                       "rust_sources_materialized": True,
                       "derived_verdict_matches_persisted": matched,
                       "derived_verdict": derived.get("verdict"),
                       "historical_source_verification": historical_flag,
                       "g2_absent": no_g2,
                       "persisted_verdict_sha256": _sha(PERSISTED_VERDICT.read_bytes())})
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        report["error"] = str(error)
    return report


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(0 if result.get("pass") is True else 1)
