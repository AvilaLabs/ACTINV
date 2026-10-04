#!/usr/bin/env python3
"""Preserve the initial P117 local-verdict evidence without modifying it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))

import check_p116 as p116  # noqa: E402
import check_p117  # noqa: E402
import run_p117_gate  # noqa: E402

ARCHIVE = ROOT / "results/failures/p117_initial"
OBSERVED_VERDICT = "target/p117-initial-verdict-observed.json"
VERDICT_LOG = "target/p117-initial-verdict.log"
ENTRY_POINT = ["python3", "controls/check_p117_verdict.py", "--write"]
GATE_STATES = {
    "protocol": "PASS",
    "historical_p116_ci_failure": "PASS",
    "G0": "PASS",
    "G1": "PASS",
    "G2": "PASS",
    "G3_local": "FAIL",
    "source_commit": "PASS",
    "CI_evidence_consistent": "PASS",
    "G3_CI": "PENDING",
}


class PreserveError(ValueError):
    """The requested source evidence is incomplete or has changed."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PreserveError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json(raw: bytes, label: str):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreserveError(f"invalid JSON in {label}: {error}") from error


def _relative(path: str) -> PurePosixPath:
    parsed = PurePosixPath(path)
    if (not path or parsed.is_absolute() or parsed.as_posix() != path
            or "\\" in path or any(part in ("", ".", "..") for part in parsed.parts)):
        raise PreserveError(f"unsafe archive path: {path!r}")
    return parsed


def _regular_source(relative: str) -> bytes:
    safe = _relative(relative)
    path = ROOT / Path(*safe.parts)
    cursor = ROOT
    if cursor.is_symlink():
        raise PreserveError("repository root is a symlink")
    for part in safe.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreserveError(f"source contains a symlink: {relative}")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreserveError(f"missing source {relative}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise PreserveError(f"source is not a regular file: {relative}")
    return path.read_bytes()


def _expected_files() -> list[str]:
    gate_names = sorted(check_p117.FRESH_GATES)
    if len(gate_names) != 19:
        raise PreserveError(f"expected 19 P117 fresh gates, found {len(gate_names)}")
    paths = []
    for name in gate_names:
        paths.extend((f"results/quality/p117/{name}.json",
                      f"results/quality/p117/{name}.log",
                      f"target/p117-{name}.log"))
    paths.extend((
        "results/quality/p117/quality_collection.log",
        "results/quality/p117/source/prepare_seed.py",
        "results/quality/p117/source/offline_install.py",
        "results/g0_p117_twin_waste.json",
        "results/g1_p117_twin_waste.json",
        "results/g2_p117_twin_waste.json",
        "results/g3_p117_quality.json",
        "results/p117_verdict.json",
        VERDICT_LOG,
        OBSERVED_VERDICT,
        check_p117.PROTOCOL,
        "results/p117_data_release.json",
    ))
    return paths


def _verify_source_seal(checkpoint: str, g0: dict) -> tuple[dict[str, str], dict[str, str]]:
    control_map = g0.get("control_sha256")
    expected_controls = set(check_p117.CONTROL_FILES)
    if (not isinstance(control_map, dict) or set(control_map) != expected_controls
            or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                   for digest in control_map.values())):
        raise PreserveError("G0 control source map is incomplete or malformed")

    current_controls = {}
    for relative, expected in sorted(control_map.items()):
        raw = _regular_source(relative)
        digest = _sha(raw)
        if digest != expected:
            raise PreserveError(f"current sealed control changed: {relative}")
        if _sha(p116._git_blob(checkpoint, relative)) != expected:
            raise PreserveError(f"checkpoint control blob differs from G0: {relative}")
        current_controls[relative] = digest

    rust_map = g0.get("inherited_rust_sha256")
    if (not isinstance(rust_map, dict) or len(rust_map) != 100
            or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                   for digest in rust_map.values())):
        raise PreserveError("G0 must contain exactly 100 valid Rust source hashes")
    rust_paths = p116._rust_paths_at_commit(checkpoint)
    current_rust = p116._current_rust_source_hashes()
    if set(rust_map) != rust_paths or current_rust != rust_map:
        raise PreserveError("current Rust population differs from sealed G0 or checkpoint")
    for relative, expected in sorted(rust_map.items()):
        if _sha(p116._git_blob(checkpoint, relative)) != expected:
            raise PreserveError(f"checkpoint Rust blob differs from G0: {relative}")
        if _sha(_regular_source(relative)) != expected:
            raise PreserveError(f"current Rust source differs from G0: {relative}")
    return current_controls, dict(sorted(rust_map.items()))


def _validate_observed(observed: object) -> None:
    if not isinstance(observed, dict):
        raise PreserveError("observed verdict record must be an object")
    if observed.get("schema") != "actinv-p117-initial-verdict-observation-1":
        raise PreserveError("unexpected observed-verdict schema")
    if observed.get("entry_point") != ENTRY_POINT:
        raise PreserveError("observed logical verdict entry point differs from the required command")
    argv = observed.get("argv")
    if (not isinstance(argv, list) or len(argv) != 3 or argv[:2] != ["python3", "-c"]
            or not isinstance(argv[2], str)):
        raise PreserveError("observed argv must retain the actual python3 -c wrapper invocation")
    wrapper = argv[2]
    required_fragments = (
        "inspect_resources", "check_p117_verdict.main", "--write", "raise",
    )
    if any(fragment not in wrapper for fragment in required_fragments):
        raise PreserveError("actual wrapper lacks resource checks or the verdict entry point")
    compact = re.sub(r"\s+", "", wrapper)
    if not re.search(r"check_p117_verdict\.main\(\[(['\"])--write\1\]\)", compact):
        raise PreserveError("actual wrapper does not call the verdict write entry point")
    if observed.get("status") != "completed" or type(observed.get("exit_code")) is not int:
        raise PreserveError("observed verdict must retain a completed integer exit status")
    if observed["exit_code"] != 1:
        raise PreserveError("initial verdict command did not return the required actual exit 1")
    resources = observed.get("resources")
    if (not isinstance(resources, dict)
            or run_p117_gate._resource_snapshot_errors(resources)
            or observed.get("inspection_errors") != []):
        raise PreserveError("observed resource inspection is missing, invalid, or reported errors")


def _validate_verdict(verdict: object, g0: dict, g3: dict, source_map: dict[str, bytes]) -> None:
    if not isinstance(verdict, dict):
        raise PreserveError("initial verdict must be an object")
    gates = verdict.get("gates")
    if (verdict.get("schema") != "actinv-p117-verdict-1"
            or verdict.get("phase") != "P117"
            or verdict.get("verdict") != "P117-FAIL"
            or gates != GATE_STATES):
        raise PreserveError("initial verdict is not exactly G3-local FAIL with CI pending")
    if (verdict.get("historical_source_verification") is not False
            or verdict.get("p116_terminal_verdict_sha256") != check_p117.P116_VERDICT_SHA256):
        raise PreserveError("initial verdict history disposition differs from the expected state")
    artifacts = {
        "results/g0_p117_twin_waste.json": "results/g0_p117_twin_waste.json",
        "results/g1_p117_twin_waste.json": "results/g1_p117_twin_waste.json",
        "results/g2_p117_twin_waste.json": "results/g2_p117_twin_waste.json",
        "results/g3_p117_quality.json": "results/g3_p117_quality.json",
    }
    evidence_hashes = verdict.get("evidence_sha256")
    if (not isinstance(evidence_hashes, dict)
            or set(evidence_hashes) != set(artifacts)
            or any(evidence_hashes[key] != _sha(source_map[path]) for key, path in artifacts.items())):
        raise PreserveError("initial verdict does not bind the four archived G0-G3 artifacts")
    adoption = g3.get("p116_adopted")
    if (g3.get("pass") is not True or not isinstance(adoption, dict)
            or adoption.get("public_handbook_sha256") != g0.get("public_handbook_sha256")
            or "public_handbook_sha256" in g3):
        raise PreserveError("initial G3 does not retain the expected nested handbook mismatch")


def _validate_g3_receipts(g3: dict, source_map: dict[str, bytes]) -> None:
    fresh = g3.get("fresh_gates")
    if not isinstance(fresh, dict) or set(fresh) != check_p117.FRESH_GATES:
        raise PreserveError("G3 does not contain all 19 fresh gate receipts")
    for name, entry in fresh.items():
        receipt_rel = f"results/quality/p117/{name}.json"
        copied_log_rel = f"results/quality/p117/{name}.log"
        target_log_rel = f"target/p117-{name}.log"
        if (not isinstance(entry, dict)
                or entry.get("receipt_path") != receipt_rel
                or entry.get("receipt_sha256") != _sha(source_map[receipt_rel])
                or entry.get("log_path") != copied_log_rel
                or entry.get("log_sha256") != _sha(source_map[copied_log_rel])
                or source_map[target_log_rel] != source_map[copied_log_rel]
                or type(entry.get("exit_code")) is not int or entry.get("exit_code") != 0):
            raise PreserveError(f"G3 receipt/log identity mismatch for gate {name}")
        receipt = _json(source_map[receipt_rel], receipt_rel)
        if (not isinstance(receipt, dict)
                or receipt.get("phase") != "P117" or receipt.get("gate") != name
                or receipt.get("status") != "completed"
                or type(receipt.get("child_exit_code")) is not int
                or receipt.get("child_exit_code") != 0
                or receipt.get("log_path") != target_log_rel
                or receipt.get("log_sha256") != entry.get("log_sha256")):
            raise PreserveError(f"receipt does not support its passing G3 entry: {name}")


def _ensure_new_archive_path() -> None:
    root = ROOT.resolve(strict=True)
    destination = ARCHIVE
    destination.relative_to(root)
    cursor = root
    for part in destination.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreserveError("archive destination has a symlink component")
    if os.path.lexists(destination):
        raise PreserveError("refusing to overwrite an existing P117 preservation archive")


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or os.path.lexists(path):
        raise PreserveError(f"refusing to overwrite archive entry: {path}")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def preserve(checkpoint: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", checkpoint):
        raise PreserveError("checkpoint must be a full lowercase 40-character Git SHA")

    sources = _expected_files()
    # Include the preservation utility itself as source evidence.
    source_map = {relative: _regular_source(relative) for relative in sources}
    source_map["preserve_initial.py"] = _regular_source("target/p117-preserve-initial.py")

    observed = _json(source_map[OBSERVED_VERDICT], OBSERVED_VERDICT)
    _validate_observed(observed)
    g0 = _json(source_map["results/g0_p117_twin_waste.json"], "P117 G0")
    g3 = _json(source_map["results/g3_p117_quality.json"], "P117 G3")
    verdict = _json(source_map["results/p117_verdict.json"], "initial P117 verdict")
    if not isinstance(g0, dict) or g0.get("pass") is not True:
        raise PreserveError("sealed P117 G0 is not passing")
    if not isinstance(g3, dict):
        raise PreserveError("P117 G3 quality record is not an object")
    for relative in ("results/g1_p117_twin_waste.json", "results/g2_p117_twin_waste.json"):
        result = _json(source_map[relative], relative)
        if not isinstance(result, dict) or result.get("pass") is not True:
            raise PreserveError(f"initial science artifact is not passing: {relative}")
    _validate_g3_receipts(g3, source_map)
    _validate_verdict(verdict, g0, g3, source_map)
    control_hashes, rust_hashes = _verify_source_seal(checkpoint, g0)

    hashes = {relative: _sha(raw) for relative, raw in sorted(source_map.items())}
    discovery = {
        "schema": "actinv-p117-initial-preservation-1",
        "checkpoint_commit": checkpoint,
        "initial_verdict": verdict["verdict"],
        "verdict_gates": dict(verdict["gates"]),
        "observed_exit": {"entry_point": observed["entry_point"], "argv": observed["argv"],
                          "status": observed["status"], "exit_code": observed["exit_code"],
                          "resources": observed["resources"],
                          "inspection_errors": observed["inspection_errors"],
                          "raw_log_sha256": observed.get("raw_log_sha256")},
        "sealed_g0_control_sha256": control_hashes,
        "sealed_g0_rust_sha256": rust_hashes,
        "preserved_files_sha256": hashes,
    }
    discovery_raw = (json.dumps(discovery, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()

    _ensure_new_archive_path()
    ARCHIVE.mkdir(parents=True)
    for relative, raw in source_map.items():
        _write_new(ARCHIVE / Path(*_relative(relative).parts), raw)
    _write_new(ARCHIVE / "discovery.json", discovery_raw)
    return {"archive": ARCHIVE.relative_to(ROOT).as_posix(), "files": len(source_map),
            "initial_verdict": verdict["verdict"], "exit_code": observed["exit_code"],
            "archive_sha256": _sha(discovery_raw)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args(argv)
    try:
        result = preserve(args.checkpoint)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as error:
        print(f"P117 initial preservation refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
