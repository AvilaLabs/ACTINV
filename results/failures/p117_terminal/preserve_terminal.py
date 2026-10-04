#!/usr/bin/env python3
"""Preserve the P117 terminal-failure evidence without changing its sources."""
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
sys.path.insert(0, str(ROOT / "scripts"))

import check_p116 as p116  # noqa: E402
import check_p117  # noqa: E402
import run_p117_gate  # noqa: E402

ARCHIVE = ROOT / "results/failures/p117_terminal"
INITIAL_ARCHIVE = ROOT / "results/failures/p117_initial"
INITIAL_DISCOVERY_SHA256 = "73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a"
ORIGINAL_CHECKPOINT = "24929f477ab56ed026eabee4260e39572c226428"
GATE_NAMES = ("p117_regressions", "p117_verdict_regressions", "g0_seal", "g0_replay")
AMENDED_SOURCES = (
    "controls/check_p117.py", "controls/check_p117_verdict.py",
    "controls/test_p117.py", "controls/test_p117_verdict.py",
)
TERMINAL_RAW_LOG = "target/p117-terminal-verdict-after-amendment.log"
TERMINAL_COPY_LOG = "results/quality/p117/terminal_verdict_after_amendment.log"
TERMINAL_OBSERVATION = "results/quality/p117/terminal_verdict_after_amendment_observed.json"
DUPLICATE_LOG = "results/quality/p117/duplicate_g0_replay.log"
DUPLICATE_OBSERVATION = "results/quality/p117/duplicate_g0_replay_observed.json"
ABSENT_ARTIFACTS = (
    "results/g2_p117_twin_waste.json",
    "results/g3_p117_quality.json",
)
TERMINAL_GATE_MAP = {
    "CI_evidence_consistent": "PASS", "G0": "PASS", "G1": "PASS", "G2": "FAIL",
    "G3_CI": "PENDING", "G3_local": "FAIL", "historical_p116_ci_failure": "PASS",
    "protocol": "PASS", "source_commit": "PASS",
}


class PreserveError(ValueError):
    """The requested evidence is incomplete, changed, or internally inconsistent."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreserveError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(raw: bytes, label: str):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreserveError(f"invalid JSON in {label}: {error}") from error


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value or "\\" in value
            or any(part in ("", ".", "..") for part in path.parts)):
        raise PreserveError(f"unsafe relative archive path: {value!r}")
    return path


def _read_regular(relative: str, root: Path = ROOT) -> bytes:
    safe = _relative(relative)
    physical = root / Path(*safe.parts)
    cursor = root
    if cursor.is_symlink():
        raise PreserveError(f"source root is a symlink: {root}")
    for part in safe.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreserveError(f"source has a symlink component: {relative}")
    try:
        metadata = physical.lstat()
    except OSError as error:
        raise PreserveError(f"missing source {relative}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise PreserveError(f"source is not a regular file: {relative}")
    return physical.read_bytes()


def _current_paths() -> list[str]:
    paths = [check_p117.PROTOCOL, "protocols/ACTINV-P117_AMENDMENT_A.md",
             "results/g0_p117_twin_waste.json", "results/g1_p117_twin_waste.json",
             "results/p117_verdict.json", "results/p117_data_release.json",
             TERMINAL_RAW_LOG, TERMINAL_COPY_LOG, TERMINAL_OBSERVATION,
             DUPLICATE_LOG, DUPLICATE_OBSERVATION]
    paths.extend(AMENDED_SOURCES)
    for name in GATE_NAMES:
        paths.extend((f"results/quality/p117/{name}.json",
                      f"results/quality/p117/{name}.log",
                      f"target/p117-{name}.log"))
    return paths


def _validate_initial_archive() -> tuple[dict, dict[str, str]]:
    discovery_raw = _read_regular("discovery.json", INITIAL_ARCHIVE)
    if _sha(discovery_raw) != INITIAL_DISCOVERY_SHA256:
        raise PreserveError("immutable initial P117 discovery identity changed")
    discovery = _json(discovery_raw, "initial P117 archive discovery")
    if not isinstance(discovery, dict) or discovery.get("checkpoint_commit") != ORIGINAL_CHECKPOINT:
        raise PreserveError("initial P117 archive checkpoint identity changed")
    file_map = discovery.get("preserved_files_sha256")
    if not isinstance(file_map, dict) or len(file_map) != 70:
        raise PreserveError("initial P117 archive must bind exactly 70 preserved files")
    actual = set()
    for directory, child_directories, filenames in os.walk(INITIAL_ARCHIVE, followlinks=False):
        base = Path(directory)
        for child in child_directories:
            candidate = base / child
            if candidate.is_symlink() or not candidate.is_dir():
                raise PreserveError(f"unsafe directory in initial archive: {candidate}")
        for filename in filenames:
            candidate = base / filename
            relative = candidate.relative_to(INITIAL_ARCHIVE).as_posix()
            raw = _read_regular(relative, INITIAL_ARCHIVE)
            if relative == "discovery.json":
                continue
            expected = file_map.get(relative)
            if not isinstance(expected, str) or _sha(raw) != expected:
                raise PreserveError(f"initial archive file identity changed: {relative}")
            actual.add(relative)
    if actual != set(file_map):
        raise PreserveError("initial P117 archive is missing or has extra files")
    if discovery.get("initial_verdict") != "P117-FAIL":
        raise PreserveError("initial P117 archive no longer records its original FAIL")
    return discovery, dict(sorted(file_map.items()))


def _verify_checkpoint(checkpoint: str, g0: dict) -> tuple[dict[str, str], dict[str, str]]:
    if not re.fullmatch(r"[0-9a-f]{40}", checkpoint):
        raise PreserveError("checkpoint must be a full lowercase 40-character Git SHA")
    controls = g0.get("control_sha256")
    if not isinstance(controls, dict) or set(controls) != set(check_p117.CONTROL_FILES):
        raise PreserveError("amended G0 must bind the complete current control-source population")
    if len(controls) != 193:
        raise PreserveError(f"expected 193 sealed control sources, found {len(controls)}")
    control_hashes = {}
    for relative, digest in sorted(controls.items()):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PreserveError(f"malformed G0 source hash for {relative}")
        if _sha(_read_regular(relative)) != digest:
            raise PreserveError(f"current amended source differs from G0: {relative}")
        if _sha(p116._git_blob(checkpoint, relative)) != digest:
            raise PreserveError(f"checkpoint amended source differs from G0: {relative}")
        control_hashes[relative] = digest

    rust_map = g0.get("inherited_rust_sha256")
    rust_paths = p116._rust_paths_at_commit(checkpoint)
    current_rust = p116._current_rust_source_hashes()
    if (not isinstance(rust_map, dict) or len(rust_map) != 100
            or set(rust_map) != rust_paths or current_rust != rust_map):
        raise PreserveError("the 100-file Rust population differs from amended G0/checkpoint")
    for relative, digest in sorted(rust_map.items()):
        if (_sha(_read_regular(relative)) != digest
                or _sha(p116._git_blob(checkpoint, relative)) != digest):
            raise PreserveError(f"Rust source differs from amended G0/checkpoint: {relative}")
    return control_hashes, dict(sorted(rust_map.items()))


def _validate_duplicate(raw_log: bytes, observation: object) -> None:
    if not isinstance(observation, dict):
        raise PreserveError("duplicate invocation observation must be an object")
    report = observation.get("returned_recorder_report")
    argv = observation.get("argv")
    expected_argv = [
        "python3", "scripts/run_p117_gate.py", "--phase", "P117", "--name", "g0_replay",
        "--timeout-s", "600", "--log", "target/p117-g0_replay.log", "--receipt",
        "results/quality/p117/g0_replay.json", "--", "python3", "controls/check_p117.py",
        "--g0-only", "--no-write",
    ]
    if (observation.get("schema") != "actinv-p117-post-amendment-recorder-observation-1"
            or observation.get("phase") != "P117" or observation.get("gate") != "g0_replay"
            or observation.get("status") != "completed"
            or type(observation.get("observed_exit_code")) is not int
            or observation.get("observed_exit_code") != 1
            or observation.get("resource_inspection_errors") != []
            or argv != expected_argv
            or observation.get("execution_interface") != "exec_command/write_stdin"
            or type(observation.get("exec_session_id")) is not int
            or not isinstance(report, dict)
            or type(report.get("child_exit_code")) is not int or report["child_exit_code"] != 0
            or report.get("status") != "setup_failed"
            or not isinstance(report.get("error"), str)
            or "FileExistsError" not in report["error"]
            or "p117-g0_replay.log" not in report["error"]
            or observation.get("terminal_output_path") != DUPLICATE_LOG
            or "Duplicate recorder invocation" not in observation.get("interpretation", "")):
        raise PreserveError("duplicate invocation evidence does not prove the expected recorder collision")
    if not raw_log or b"FileExistsError" not in raw_log:
        raise PreserveError("duplicate raw output does not retain the actual file-exists refusal")
    if b"p117-g0_replay.log" not in raw_log or b"g0_replay.json" not in raw_log:
        raise PreserveError("duplicate raw output does not retain both no-overwrite errors")
    report = observation["returned_recorder_report"]
    if run_p117_gate._resource_snapshot_errors(report.get("resources")):
        raise PreserveError("duplicate recorder report does not retain valid inspected resources")
    if observation.get("scope") != report["resources"].get("cgroup_path", "").rsplit("/", 1)[-1]:
        raise PreserveError("duplicate observation scope differs from its inspected cgroup")
    logged = _last_pretty_json(raw_log, "duplicate recorder output")
    if logged != report:
        raise PreserveError("duplicate raw pretty report differs from the observed recorder report")


def _last_pretty_json(raw: bytes, label: str):
    lines = raw.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == b"{"]
    if not starts:
        raise PreserveError(f"no standalone pretty-JSON object in {label}")
    return _json(b"\n".join(lines[starts[-1]:]), label)


def _resource_line(raw: bytes, label: str) -> dict:
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(b'{"errors"'):
            value = _json(stripped, label)
            if not isinstance(value, dict) or value.get("errors") != []:
                raise PreserveError(f"resource inspection failed in {label}")
            resources = value.get("resources")
            if not isinstance(resources, dict) or run_p117_gate._resource_snapshot_errors(resources):
                raise PreserveError(f"invalid bounded resource snapshot in {label}")
            return resources
    raise PreserveError(f"missing compact resource inspection JSON in {label}")


def _validate_terminal_verdict(verdict: object, g0: dict, g1: dict,
                               g2_present: bool, g3_present: bool,
                               source_map: dict[str, bytes]) -> None:
    if not isinstance(verdict, dict):
        raise PreserveError("terminal P117 verdict must be an object")
    gates = verdict.get("gates")
    if (verdict.get("schema") != "actinv-p117-verdict-1"
            or verdict.get("phase") != "P117"
            or verdict.get("verdict") != "P117-FAIL"
            or gates != TERMINAL_GATE_MAP
            or verdict.get("historical_source_verification") is not False
            or verdict.get("p116_terminal_verdict_sha256") != check_p117.P116_VERDICT_SHA256
            or verdict.get("implementation_commit") is not None
            or g0.get("pass") is not True or g1.get("pass") is not True
            or g2_present or g3_present):
        raise PreserveError("terminal result is not the preserved post-collision P117 FAIL state")
    evidence = verdict.get("evidence_sha256")
    if not isinstance(evidence, dict) or set(evidence) != {
            "results/g0_p117_twin_waste.json", "results/g1_p117_twin_waste.json",
            "results/g2_p117_twin_waste.json", "results/g3_p117_quality.json"}:
        raise PreserveError("terminal verdict evidence identity map is malformed")
    if evidence.get("results/g0_p117_twin_waste.json") != _sha(source_map["results/g0_p117_twin_waste.json"]):
        raise PreserveError("terminal verdict does not bind amended G0")
    if evidence.get("results/g1_p117_twin_waste.json") != _sha(source_map["results/g1_p117_twin_waste.json"]):
        raise PreserveError("terminal verdict does not bind retained G1")
    if (evidence.get("results/g2_p117_twin_waste.json") is not None
            or evidence.get("results/g3_p117_quality.json") is not None):
        raise PreserveError("terminal verdict fabricates evidence for absent G2/G3 artifacts")
    logged = _last_pretty_json(source_map[TERMINAL_RAW_LOG], "terminal verdict command output")
    if (not isinstance(logged, dict) or logged.get("verdict") != verdict.get("verdict")
            or logged.get("gates") != gates or logged.get("persisted_matches") is not True):
        raise PreserveError("terminal verdict artifact does not match the actual command output")
    inspected = _resource_line(source_map[TERMINAL_RAW_LOG], "terminal verdict resource line")
    observation = _json(source_map[TERMINAL_OBSERVATION], "terminal verdict observation")
    if observation.get("resources") != inspected:
        raise PreserveError("terminal verdict inspected resources differ from the observation")


def _validate_gate(name: str, source_map: dict[str, bytes]) -> dict:
    receipt_rel = f"results/quality/p117/{name}.json"
    copied_log_rel = f"results/quality/p117/{name}.log"
    raw_log_rel = f"target/p117-{name}.log"
    receipt = _json(source_map[receipt_rel], receipt_rel)
    if not isinstance(receipt, dict):
        raise PreserveError(f"receipt is not an object: {name}")
    if (receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != "P117" or receipt.get("gate") != name
            or receipt.get("status") != "completed"
            or type(receipt.get("child_exit_code")) is not int or receipt["child_exit_code"] != 0
            or receipt.get("log_path") != raw_log_rel
            or run_p117_gate._resource_snapshot_errors(receipt.get("resources"))
            or _sha(source_map[copied_log_rel]) != receipt.get("log_sha256")
            or source_map[copied_log_rel] != source_map[raw_log_rel]):
        raise PreserveError(f"amended gate receipt and raw/copied log do not agree: {name}")
    return {"receipt_sha256": _sha(source_map[receipt_rel]),
            "raw_log_sha256": _sha(source_map[raw_log_rel]),
            "copied_log_sha256": _sha(source_map[copied_log_rel]),
            "child_exit_code": receipt["child_exit_code"]}


def _ensure_destination() -> None:
    root = ROOT.resolve(strict=True)
    ARCHIVE.relative_to(root)
    cursor = root
    for part in ARCHIVE.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreserveError("terminal archive destination has a symlink component")
    if os.path.lexists(ARCHIVE):
        raise PreserveError("refusing to overwrite an existing P117 terminal archive")


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or os.path.lexists(path):
        raise PreserveError(f"refusing to overwrite terminal archive entry: {path}")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def preserve(checkpoint: str) -> dict:
    initial_discovery, initial_hashes = _validate_initial_archive()
    relative_paths = _current_paths()
    source_map = {relative: _read_regular(relative) for relative in relative_paths}
    source_map["preserve_terminal.py"] = _read_regular("target/p117-preserve-terminal.py")
    g0 = _json(source_map["results/g0_p117_twin_waste.json"], "amended G0")
    g1 = _json(source_map["results/g1_p117_twin_waste.json"], "retained G1")
    terminal = _json(source_map["results/p117_verdict.json"], "terminal verdict")
    g2_present = any((ROOT / relative).exists() or (ROOT / relative).is_symlink()
                     for relative in ABSENT_ARTIFACTS[:1])
    g3_present = any((ROOT / relative).exists() or (ROOT / relative).is_symlink()
                     for relative in ABSENT_ARTIFACTS[1:])
    if not isinstance(g0, dict) or not isinstance(g1, dict):
        raise PreserveError("amended G0 or retained G1 is malformed")
    if _sha(source_map["results/g1_p117_twin_waste.json"]) != initial_hashes.get(
            "results/g1_p117_twin_waste.json"):
        raise PreserveError("retained G1 differs from the immutable initial archive")
    if g2_present or g3_present:
        raise PreserveError("G2/G3 must remain absent for this terminal failure archive")
    control_hashes, rust_hashes = _verify_checkpoint(checkpoint, g0)
    for relative, raw in source_map.items():
        if relative.startswith("target/") or relative == "preserve_terminal.py":
            continue
        if _sha(p116._git_blob(checkpoint, relative)) != _sha(raw):
            raise PreserveError(f"checkpoint artifact differs from retained bytes: {relative}")

    duplicate_observation = _json(source_map[DUPLICATE_OBSERVATION], DUPLICATE_OBSERVATION)
    _validate_duplicate(source_map[DUPLICATE_LOG], duplicate_observation)
    terminal_observation = _json(source_map[TERMINAL_OBSERVATION], TERMINAL_OBSERVATION)
    # The terminal command uses the same inspected wrapper and completed integer
    # exit record as the original observation; require its observed fail status.
    if (not isinstance(terminal_observation, dict)
            or terminal_observation.get("schema") != "actinv-p117-terminal-verdict-observation-1"
            or terminal_observation.get("status") != "completed"
            or type(terminal_observation.get("exit_code")) is not int
            or terminal_observation.get("exit_code") != 1
            or terminal_observation.get("entry_point") != [
                "python3", "controls/check_p117_verdict.py", "--write"]
            or not isinstance(terminal_observation.get("argv"), list)
            or len(terminal_observation["argv"]) != 3
            or terminal_observation["argv"][:2] != ["python3", "-c"]
            or not isinstance(terminal_observation["argv"][2], str)
            or "inspect_resources" not in terminal_observation["argv"][2]
            or "_resource_snapshot_errors" not in terminal_observation["argv"][2]
            or "assert not errors" not in terminal_observation["argv"][2]
            or "check_p117_verdict.main" not in terminal_observation["argv"][2]
            or "--write" not in terminal_observation["argv"][2]
            or "raise" not in terminal_observation["argv"][2]
            or not isinstance(terminal_observation.get("resources"), dict)
            or run_p117_gate._resource_snapshot_errors(terminal_observation["resources"])
            or terminal_observation.get("inspection_errors") != []
            or terminal_observation.get("raw_log_sha256") != _sha(source_map[TERMINAL_RAW_LOG])):
        raise PreserveError("terminal verdict observation lacks an actual bounded integer exit 1")
    _validate_terminal_verdict(terminal, g0, g1, g2_present, g3_present, source_map)

    gate_records = {name: _validate_gate(name, source_map) for name in GATE_NAMES}
    # The duplicate attempt is extra evidence, not one of the four successful
    # amended gate receipts.
    if source_map[TERMINAL_COPY_LOG] != source_map[TERMINAL_RAW_LOG]:
        raise PreserveError("terminal verdict raw and copied logs differ")
    if _resource_line(source_map[TERMINAL_RAW_LOG], "terminal verdict resource line") != terminal_observation["resources"]:
        raise PreserveError("terminal raw-log resources differ from the observed snapshot")

    file_hashes = {path: _sha(raw) for path, raw in sorted(source_map.items())}
    discovery = {
        "schema": "actinv-p117-terminal-preservation-1",
        "checkpoint_commit": checkpoint,
        "terminal_verdict": terminal["verdict"],
        "terminal_gates": terminal["gates"],
        "amended_successful_gates": gate_records,
        "duplicate_invocation": {
            "observation_sha256": file_hashes[DUPLICATE_OBSERVATION],
            "output_sha256": file_hashes[DUPLICATE_LOG],
            "observed_exit_code": duplicate_observation["observed_exit_code"],
            "child_exit_code": duplicate_observation["returned_recorder_report"]["child_exit_code"],
        },
        "terminal_verdict_observation": {
            "path": TERMINAL_OBSERVATION,
            "sha256": file_hashes[TERMINAL_OBSERVATION],
            "exit_code": terminal_observation["exit_code"],
            "entry_point": terminal_observation["entry_point"],
        },
        "absent_artifacts": list(ABSENT_ARTIFACTS),
        "original_initial_archive": {
            "checkpoint_commit": initial_discovery["checkpoint_commit"],
            "discovery_sha256": INITIAL_DISCOVERY_SHA256,
            "file_count": len(initial_hashes),
            "files_sha256": initial_hashes,
        },
        "amended_g0_control_sha256": control_hashes,
        "amended_g0_rust_sha256": rust_hashes,
        "preserved_files_sha256": file_hashes,
    }
    discovery_raw = (json.dumps(discovery, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()

    _ensure_destination()
    ARCHIVE.mkdir(parents=True)
    for relative, raw in source_map.items():
        _write_exclusive(ARCHIVE / Path(*_relative(relative).parts), raw)
    # Preserve the original initial archive exactly under a distinct prefix.
    for relative, raw_hash in initial_hashes.items():
        raw = _read_regular(relative, INITIAL_ARCHIVE)
        if _sha(raw) != raw_hash:
            raise PreserveError(f"initial archive changed while preserving: {relative}")
        _write_exclusive(ARCHIVE / "prior_initial_archive" / Path(*_relative(relative).parts), raw)
    _write_exclusive(ARCHIVE / "prior_initial_archive/discovery.json",
                     _read_regular("discovery.json", INITIAL_ARCHIVE))
    _write_exclusive(ARCHIVE / "discovery.json", discovery_raw)
    return {"archive": ARCHIVE.relative_to(ROOT).as_posix(),
            "preserved_files": len(source_map), "prior_archive_files": len(initial_hashes) + 1,
            "verdict": terminal["verdict"], "discovery_sha256": _sha(discovery_raw)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args(argv)
    try:
        result = preserve(args.checkpoint)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as error:
        print(f"P117 terminal preservation refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
