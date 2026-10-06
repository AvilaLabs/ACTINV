#!/usr/bin/env python3
"""Verify the preserved terminal P117 failure after its CI workflow transition."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p116 as p116
import check_p117
import check_p117_verdict
import run_p117_gate

SCHEMA = "actinv-p117-history-verification-1"
CHECKPOINT = "b81e8c3365a5a08ed55e9f99c0c88f945709996d"
INITIAL_CHECKPOINT = "24929f477ab56ed026eabee4260e39572c226428"
TERMINAL = "results/failures/p117_terminal"
INITIAL = f"{TERMINAL}/prior_initial_archive"
TERMINAL_DISCOVERY_SHA256 = "0c736be95e42413481378d38ebaea66424caa5caab1aa7712699e93f1866a26e"
INITIAL_DISCOVERY_SHA256 = "73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a"
INITIAL_VERDICT_SHA256 = "871231b33029b8696e9ed2f0e260a1f8b44b01a49b08803fd9e7f866f16c96a8"
TERMINAL_VERDICT_SHA256 = "11ea137a5362b5cc42c771137771cbad08b5cd7084939480138b80e656ee4e9b"
DUPLICATE_OBSERVATION_SHA256 = "ce4ce3483ae7b1afb4a9a8947d83d1c9611c835d01b12e9fb4b042bf49e39b50"
DUPLICATE_OUTPUT_SHA256 = "4f989719df72b3ef41050748bb96939364f4f1b683b7545d4ee5529f3dbe7dee"
TERMINAL_GATES = {
    "CI_evidence_consistent": "PASS", "G0": "PASS", "G1": "PASS", "G2": "FAIL",
    "G3_CI": "PENDING", "G3_local": "FAIL", "historical_p116_ci_failure": "PASS",
    "protocol": "PASS", "source_commit": "PASS",
}
INITIAL_GATES = TERMINAL_GATES | {"G2": "PASS"}
TERMINAL_VERDICT_OBSERVED = "results/quality/p117/terminal_verdict_after_amendment_observed.json"
TERMINAL_VERDICT_LOG = "results/quality/p117/terminal_verdict_after_amendment.log"
DUPLICATE_OBSERVED = "results/quality/p117/duplicate_g0_replay_observed.json"
DUPLICATE_LOG = "results/quality/p117/duplicate_g0_replay.log"
OLD_P117_CI_STEP = b"""      - name: P117 verified CI input seed and unchanged twin qualification
        env:
          ACTINV_BIN: target/release/actinv
        run: |
          python controls/test_p117.py
          python controls/test_p117_verdict.py
          python scripts/test_run_p117_gate.py
          python controls/check_p117.py --no-write
          python controls/check_p117_verdict.py
"""
NEW_P117_CI_STEP = b"""      - name: P117 terminal failure remains immutable
        env:
          ACTINV_BIN: target/release/actinv
        run: |
          python controls/test_p117_history.py
          python controls/check_p117_history.py
"""
NEW_P118_CI_STEP = b"""      - name: P118 verified cached inputs and unchanged twin qualification
        env:
          ACTINV_BIN: target/release/actinv
        run: |
          python controls/test_p118.py
          python controls/test_p118_verdict.py
          python scripts/test_run_p118_gate.py
          python controls/check_p118.py --no-write
          python controls/check_p118_verdict.py
"""
NEW_P118_HISTORY_CI_STEP = b"""      - name: P118 terminal failure remains immutable
        env:
          ACTINV_BIN: target/release/actinv
        run: |
          python controls/test_p118_history.py
          python controls/check_p118_history.py
"""


def _sha(raw: bytes) -> str:
    if not isinstance(raw, bytes):
        raise TypeError("SHA-256 input must be bytes")
    return hashlib.sha256(raw).hexdigest()


def _unique(raw: bytes):
    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=hook)


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("empty or non-string evidence path")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value
            or path.as_posix() != value):
        raise ValueError(f"unsafe evidence path: {value!r}")
    return path


def _safe_file(relative: str) -> Path:
    rel = _safe_rel(relative)
    path = ROOT.joinpath(*rel.parts)
    path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    cursor = ROOT
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in evidence path: {relative}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {relative}")
    return path


def _read_json(relative: str):
    return _unique(_safe_file(relative).read_bytes())


def _archive_map(root_relative: str, expected: dict[str, str]) -> dict[str, str]:
    """Verify an archive subtree is exactly its declared regular-file population."""
    base_rel = _safe_rel(root_relative)
    base = ROOT.joinpath(*base_rel.parts)
    cursor = ROOT
    for part in base_rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink in archive root")
    if not base.is_dir():
        raise ValueError("archive root is not a directory")
    base.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    mapped = {}
    for name, digest in expected.items():
        rel = _safe_rel(name)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("bad archive digest")
        path = base.joinpath(*rel.parts)
        path.resolve(strict=True).relative_to(base.resolve(strict=True))
        cursor = base
        for part in rel.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("symlink in preserved archive")
        if not path.is_file() or _sha(path.read_bytes()) != digest:
            raise ValueError(f"archive file mismatch: {name}")
        mapped[f"{root_relative.rstrip('/')}/{name}"] = digest
    physical = set()
    for path in base.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in preserved archive")
        if path.is_file():
            physical.add(path.relative_to(base).as_posix())
        elif not path.is_dir():
            raise ValueError("non-regular archive entry")
    if physical != set(expected):
        raise ValueError("archive contains missing or extra files")
    return mapped


def _resource_ok(resource: object) -> bool:
    if not isinstance(resource, dict):
        return False
    tmp = resource.get("tmpdir")
    cgroup = resource.get("cgroup_path")
    pieces = cgroup.split("/") if isinstance(cgroup, str) else []
    return (resource.get("platform") == "linux"
            and isinstance(cgroup, str) and cgroup.startswith("/user.slice/")
            and cgroup.endswith(".scope")
            and not any(piece in {".", ".."} for piece in pieces)
            and not any(not piece for piece in pieces[1:])
            and resource.get("cgroup_limits") == {
                "memory.max": "6442450944", "memory.swap.max": "0",
                "pids.max": "128", "cpu.max": "200000 100000"}
            and resource.get("environment") == {
                "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            and isinstance(tmp, dict) and tmp.get("path") == "target/preflight-tmp"
            and isinstance(tmp.get("mount_point"), str) and Path(tmp["mount_point"]).is_absolute()
            and isinstance(tmp.get("filesystem"), str) and bool(tmp["filesystem"].strip())
            and tmp["filesystem"].lower() not in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"})


def _ci_transition_matches() -> bool:
    """Require the P117 edit and either registered P118 workflow state."""
    try:
        previous = p116._git_blob(CHECKPOINT, ".github/workflows/ci.yml")
        current = _safe_file(".github/workflows/ci.yml").read_bytes()
        if previous.count(OLD_P117_CI_STEP) != 1:
            return False
        candidates = (
            NEW_P117_CI_STEP + b"\n" + NEW_P118_CI_STEP,
            NEW_P117_CI_STEP + b"\n" + NEW_P118_HISTORY_CI_STEP,
        )
        return any(current == previous.replace(OLD_P117_CI_STEP, candidate, 1)
                   for candidate in candidates)
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


def _terminal_archive() -> tuple[dict, dict[str, str], dict[str, str]]:
    discovery_path = f"{TERMINAL}/discovery.json"
    raw = _safe_file(discovery_path).read_bytes()
    if _sha(raw) != TERMINAL_DISCOVERY_SHA256:
        raise ValueError("terminal discovery digest changed")
    discovery = _unique(raw)
    direct = discovery.get("preserved_files_sha256")
    initial = discovery.get("original_initial_archive", {})
    initial_files = initial.get("files_sha256") if isinstance(initial, dict) else None
    if (discovery.get("schema") != "actinv-p117-terminal-preservation-1"
            or discovery.get("checkpoint_commit") != CHECKPOINT
            or discovery.get("terminal_verdict") != "P117-FAIL"
            or discovery.get("terminal_gates") != TERMINAL_GATES
            or discovery.get("absent_artifacts") != ["results/g2_p117_twin_waste.json", "results/g3_p117_quality.json"]
            or not isinstance(direct, dict) or len(direct) != 28
            or not isinstance(initial_files, dict) or len(initial_files) != 70
            or initial.get("checkpoint_commit") != INITIAL_CHECKPOINT
            or initial.get("discovery_sha256") != INITIAL_DISCOVERY_SHA256
            or initial.get("file_count") != 70):
        raise ValueError("terminal discovery metadata differs")
    full_terminal_files = dict(direct)
    full_terminal_files["discovery.json"] = TERMINAL_DISCOVERY_SHA256
    full_terminal_files.update({f"prior_initial_archive/{name}": digest
                                for name, digest in initial_files.items()})
    full_terminal_files["prior_initial_archive/discovery.json"] = INITIAL_DISCOVERY_SHA256
    terminal_map = _archive_map(TERMINAL, full_terminal_files)
    initial_expected = dict(initial_files)
    initial_expected["discovery.json"] = INITIAL_DISCOVERY_SHA256
    initial_map = _archive_map(INITIAL, initial_expected)
    # Verify the nested initial discovery is byte-identical to the pinned original.
    nested = _safe_file(f"{INITIAL}/discovery.json").read_bytes()
    original = _unique(nested)
    if _sha(nested) != INITIAL_DISCOVERY_SHA256:
        raise ValueError("nested original discovery digest mismatch")
    _validate_initial_discovery(original, initial_files)
    initial_current_expected = dict(initial_files)
    initial_current_expected["discovery.json"] = INITIAL_DISCOVERY_SHA256
    _archive_map("results/failures/p117_initial", initial_current_expected)
    if _sha(_safe_file("results/failures/p117_initial/discovery.json").read_bytes()) != INITIAL_DISCOVERY_SHA256:
        raise ValueError("live initial discovery differs")
    if len(terminal_map) != 100 or len(initial_map) != 71:
        raise ValueError("terminal archive population count differs")
    if discovery.get("amended_g0_control_sha256") is None or len(discovery["amended_g0_control_sha256"]) != 193:
        raise ValueError("terminal source map missing")
    if discovery.get("amended_g0_rust_sha256") is None or len(discovery["amended_g0_rust_sha256"]) != 100:
        raise ValueError("terminal Rust map missing")
    return discovery, terminal_map, initial_map


def _validate_initial_discovery(original: dict, initial_files: dict[str, str]) -> None:
    """Validate the actual P117 initial discovery schema and complete file map."""
    original_files = original.get("preserved_files_sha256")
    if (original.get("schema") != "actinv-p117-initial-preservation-1"
            or original.get("checkpoint_commit") != INITIAL_CHECKPOINT
            or not isinstance(original_files, dict) or len(original_files) != 70
            or original_files != initial_files):
        raise ValueError("nested original discovery mismatch")


def _verify_initial(initial_files: dict[str, str]) -> dict:
    nested_root = f"{INITIAL}/"
    def data(relative: str):
        path = _safe_file(nested_root + relative)
        expected = initial_files.get(relative)
        if expected is None or _sha(path.read_bytes()) != expected:
            raise ValueError(f"initial archive artifact mismatch: {relative}")
        return _unique(path.read_bytes())

    g0 = data("results/g0_p117_twin_waste.json")
    g1 = data("results/g1_p117_twin_waste.json")
    g2 = data("results/g2_p117_twin_waste.json")
    g3 = data("results/g3_p117_quality.json")
    verdict = data("results/p117_verdict.json")
    initial_discovery = _unique(_safe_file(f"{INITIAL}/discovery.json").read_bytes())
    if (verdict.get("schema") != "actinv-p117-verdict-1" or verdict.get("phase") != "P117"
            or verdict.get("verdict") != "P117-FAIL" or verdict.get("gates") != INITIAL_GATES
            or verdict.get("gates", {}).get("G3_local") != "FAIL"
            or verdict.get("gates", {}).get("G3_CI") != "PENDING"
            or any(value != ("FAIL" if key == "G3_local" else "PENDING" if key == "G3_CI" else "PASS")
                   for key, value in verdict.get("gates", {}).items())
            or any(item.get("schema") != schema for item, schema in (
                (g0, "actinv-p117-twin-waste-g0-1"), (g1, "actinv-p117-twin-waste-g1-1"),
                (g2, "actinv-p117-twin-waste-g2-1"), (g3, "actinv-p117-quality-1")))
            or any(item.get("phase") != "P117" for item in (g0, g1, g2, g3))
            or g0.get("pass") is not True or g1.get("pass") is not True or g2.get("pass") is not True
            or g3.get("pass") is not True or type(g0.get("repair_rounds")) is not int
            or g0.get("repair_rounds") != 0 or type(g3.get("repair_rounds")) is not int
            or g3.get("repair_rounds") != 0
            or type(g1.get("request_count")) is not int or g1.get("request_count") != 35
            or type(g1.get("component_target_count")) is not int or g1.get("component_target_count") != 138
            or type(g1.get("independent_comparison_count")) is not int
            or g1.get("independent_comparison_count") != 138
            or initial_discovery.get("initial_verdict") != "P117-FAIL"
            or initial_discovery.get("checkpoint_commit") != INITIAL_CHECKPOINT
            or initial_discovery.get("verdict_gates") != verdict.get("gates")
            or initial_discovery.get("preserved_files_sha256", {}).get("results/p117_verdict.json")
            != INITIAL_VERDICT_SHA256):
        raise ValueError("initial P117 failure disposition differs")
    expected_gates = set(check_p117.INITIAL_GATES)
    fresh = g3.get("fresh_gates")
    if not isinstance(fresh, dict) or set(fresh) != expected_gates or len(fresh) != 19:
        raise ValueError("initial P117 gate population differs")
    for name in sorted(expected_gates):
        if check_p117._archived_gate_entry(name, g3) is None:
            raise ValueError(f"initial P117 receipt/log not independently valid: {name}")
    controls = initial_discovery.get("sealed_g0_control_sha256")
    rust = initial_discovery.get("sealed_g0_rust_sha256")
    if (not isinstance(controls, dict) or len(controls) != 121 or g0.get("control_sha256") != controls
            or not isinstance(rust, dict) or len(rust) != 100
            or g0.get("inherited_rust_sha256") != rust or g3.get("current_rust_sha256") != rust
            or g3.get("source_sha256") != controls):
        raise ValueError("initial P117 checkpoint maps differ")
    if set(rust) != p116._rust_paths_at_commit(INITIAL_CHECKPOINT):
        raise ValueError("initial P117 Rust population differs")
    for path, digest in {**controls, **rust}.items():
        if _sha(p116._git_blob(INITIAL_CHECKPOINT, path)) != digest:
            raise ValueError(f"initial checkpoint Git blob mismatch: {path}")
    observed = initial_discovery.get("observed_exit")
    initial_observed_raw = _safe_file(f"{INITIAL}/target/p117-initial-verdict-observed.json").read_bytes()
    initial_observed = _unique(initial_observed_raw)
    initial_log_raw = _safe_file(f"{INITIAL}/target/p117-initial-verdict.log").read_bytes()
    if (not isinstance(observed, dict) or type(observed.get("exit_code")) is not int
            or observed.get("exit_code") != 1 or observed.get("status") != "completed"
            or observed.get("entry_point") != ["python3", "controls/check_p117_verdict.py", "--write"]
            or not isinstance(observed.get("argv"), list) or len(observed["argv"]) != 3
            or observed["argv"][:2] != ["python3", "-c"]
            or 'check_p117_verdict.main(["--write"])' not in observed["argv"][2]
            or run_p117_gate._has_placeholder(observed["argv"])
            or observed.get("resources") is None or not _resource_ok(observed.get("resources"))
            or initial_observed.get("schema") != "actinv-p117-initial-verdict-observation-1"
            or initial_observed.get("status") != "completed"
            or type(initial_observed.get("exit_code")) is not int or initial_observed.get("exit_code") != 1
            or initial_observed.get("entry_point") != observed.get("entry_point")
            or initial_observed.get("argv") != observed.get("argv")
            or initial_observed.get("resources") != observed.get("resources")
            or initial_observed.get("inspection_errors") != []
            or initial_observed.get("raw_log_sha256") not in (None, _sha(initial_log_raw))):
        raise ValueError("initial terminal verdict actual exit/resource evidence differs")
    initial_lines = initial_log_raw.decode("utf-8").splitlines()
    initial_inspection = [line for line in initial_lines if line.startswith('{"errors"')]
    initial_starts = [index for index, line in enumerate(initial_lines) if line == "{"]
    if (len(initial_inspection) != 1 or len(initial_starts) != 1
            or _unique(initial_inspection[0].encode()) != {"resources": observed["resources"], "errors": []}
            or _unique("\n".join(initial_lines[initial_starts[0]:]).encode()) != {
                "gates": verdict["gates"], "persisted_matches": True, "verdict": "P117-FAIL"}):
        raise ValueError("initial verdict log does not match its preserved observation")
    return {"verdict": "P117-FAIL", "verdict_sha256": INITIAL_VERDICT_SHA256,
            "checkpoint_commit": INITIAL_CHECKPOINT, "gate_names": sorted(expected_gates),
            "control_source_count": len(controls), "rust_source_count": len(rust)}


def _verify_terminal_sources(discovery: dict) -> tuple[dict[str, str], dict[str, str], bool]:
    sources = discovery["amended_g0_control_sha256"]
    rust = discovery["amended_g0_rust_sha256"]
    if (not isinstance(sources, dict) or len(sources) != 193
            or set(sources) != set(check_p117.CONTROL_FILES)
            or not isinstance(rust, dict) or len(rust) != 100
            or set(rust) != p116._rust_paths_at_commit(CHECKPOINT)):
        raise ValueError("terminal source population differs")
    for relative, digest in sources.items():
        _safe_rel(relative)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("malformed terminal source map")
        if _sha(p116._git_blob(CHECKPOINT, relative)) != digest:
            raise ValueError(f"terminal control Git blob mismatch: {relative}")
    for relative, digest in rust.items():
        if _sha(p116._git_blob(CHECKPOINT, relative)) != digest:
            raise ValueError(f"terminal Rust Git blob mismatch: {relative}")
    current_sources_match = True
    for relative, digest in sources.items():
        try:
            current = _sha(_safe_file(relative).read_bytes())
        except (OSError, ValueError):
            current = None
        if relative == ".github/workflows/ci.yml":
            if current is None or not _ci_transition_matches():
                current_sources_match = False
        elif current != digest:
            current_sources_match = False
    current_rust = p116._current_rust_source_hashes()
    if current_rust != rust:
        raise ValueError("current Rust sources differ from the terminal P117 snapshot")
    return sources, rust, current_sources_match


def _verify_terminal_disposition(discovery: dict) -> tuple[dict, bool]:
    verdict_path = "results/p117_verdict.json"
    verdict_raw = _safe_file(f"{TERMINAL}/{verdict_path}").read_bytes()
    verdict = _unique(verdict_raw)
    if (_sha(verdict_raw) != TERMINAL_VERDICT_SHA256
            or verdict.get("schema") != "actinv-p117-verdict-1"
            or verdict.get("phase") != "P117" or verdict.get("verdict") != "P117-FAIL"
            or verdict.get("gates") != TERMINAL_GATES
            or discovery.get("terminal_gates") != verdict.get("gates")):
        raise ValueError("terminal P117 verdict identity differs")
    g0raw = _safe_file(f"{TERMINAL}/results/g0_p117_twin_waste.json").read_bytes()
    g1raw = _safe_file(f"{TERMINAL}/results/g1_p117_twin_waste.json").read_bytes()
    g0 = _unique(g0raw)
    g1 = _unique(g1raw)
    if (_sha(g0raw) != discovery["preserved_files_sha256"]["results/g0_p117_twin_waste.json"]
            or _sha(g1raw) != discovery["preserved_files_sha256"]["results/g1_p117_twin_waste.json"]
            or g0.get("pass") is not True or g1.get("pass") is not True
            or g0.get("phase") != "P117" or g1.get("phase") != "P117"
            or g0.get("control_sha256") != discovery["amended_g0_control_sha256"]
            or g0.get("inherited_rust_sha256") != discovery["amended_g0_rust_sha256"]
            or type(g1.get("request_count")) is not int or g1.get("request_count") != 35
            or type(g1.get("component_target_count")) is not int or g1.get("component_target_count") != 138
            or type(g1.get("independent_comparison_count")) is not int
            or g1.get("independent_comparison_count") != 138):
        raise ValueError("terminal G0/G1 evidence differs from its seal")
    if (_safe_file("results/g0_p117_twin_waste.json").read_bytes() != g0raw
            or _safe_file("results/g1_p117_twin_waste.json").read_bytes() != g1raw
            or _safe_file("results/p117_verdict.json").read_bytes() != verdict_raw):
        raise ValueError("live P117 terminal artifacts differ from the preserved snapshot")
    if check_p117.G2.exists() or check_p117.G2.is_symlink() or check_p117.G3.exists() or check_p117.G3.is_symlink():
        raise ValueError("P117 terminal G2/G3 must be absent")
    if check_p117_verdict.IMPLEMENTATION.exists() or check_p117_verdict.IMPLEMENTATION.is_symlink():
        raise ValueError("P117 terminal implementation record must be absent")
    if check_p117_verdict.CI.exists() or check_p117_verdict.CI.is_symlink():
        raise ValueError("P117 terminal CI record must be absent")

    amended_gates = discovery.get("amended_successful_gates")
    if not isinstance(amended_gates, dict) or set(amended_gates) != {
            "p117_regressions", "p117_verdict_regressions", "g0_seal", "g0_replay"}:
        raise ValueError("amended P117 successful gate list differs")
    for gate, row in amended_gates.items():
        if gate not in {"p117_regressions", "p117_verdict_regressions", "g0_seal", "g0_replay"}:
            raise ValueError("unexpected amended P117 gate")
        receipt_rel = f"results/quality/p117/{gate}.json"
        log_rel = f"results/quality/p117/{gate}.log"
        target_rel = f"target/p117-{gate}.log"
        receipt_raw = _safe_file(f"{TERMINAL}/{receipt_rel}").read_bytes()
        receipt = _unique(receipt_raw)
        log_raw = _safe_file(f"{TERMINAL}/{log_rel}").read_bytes()
        target_raw = _safe_file(f"{TERMINAL}/{target_rel}").read_bytes()
        timeout = receipt.get("timeout_s")
        argv = receipt.get("argv")
        if (not isinstance(row, dict) or type(row.get("child_exit_code")) is not int
                or row.get("child_exit_code") != 0
                or receipt.get("child_exit_code") != 0 or type(receipt.get("child_exit_code")) is not int
                or receipt.get("status") != "completed" or receipt.get("gate") != gate
                or receipt.get("phase") != "P117" or receipt.get("error") is not None
                or receipt.get("cwd") != "." or receipt.get("log_path") != target_rel
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(float(timeout)) or not 0 < timeout <= 1200
                or not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or not arg.strip() for arg in argv)
                or run_p117_gate._has_placeholder(argv)
                or _sha(receipt_raw) != row.get("receipt_sha256")
                or _sha(log_raw) != row.get("copied_log_sha256")
                or log_raw != target_raw or _sha(target_raw) != row.get("raw_log_sha256")
                or receipt.get("log_sha256") != _sha(log_raw)
                or not _resource_ok(receipt.get("resources"))):
            raise ValueError(f"amended P117 successful receipt invalid: {gate}")
        if (_safe_file(receipt_rel).read_bytes() != receipt_raw
                or _safe_file(log_rel).read_bytes() != log_raw):
            raise ValueError(f"live amended P117 receipt/log differs from archive: {gate}")

    duplicate = discovery.get("duplicate_invocation")
    obs_raw = _safe_file(f"{TERMINAL}/{DUPLICATE_OBSERVED}").read_bytes()
    obs = _unique(obs_raw)
    output_raw = _safe_file(f"{TERMINAL}/{DUPLICATE_LOG}").read_bytes()
    duplicate_report = obs.get("returned_recorder_report", {})
    duplicate_cgroup = duplicate_report.get("resources", {}).get("cgroup_path")
    if (not isinstance(duplicate, dict) or type(duplicate.get("child_exit_code")) is not int
            or duplicate.get("child_exit_code") != 0
            or type(duplicate.get("observed_exit_code")) is not int or duplicate.get("observed_exit_code") != 1
            or _sha(obs_raw) != DUPLICATE_OBSERVATION_SHA256
            or _sha(output_raw) != DUPLICATE_OUTPUT_SHA256
            or duplicate.get("observation_sha256") != _sha(obs_raw)
            or duplicate.get("output_sha256") != _sha(output_raw)
            or obs.get("schema") != "actinv-p117-post-amendment-recorder-observation-1"
            or obs.get("phase") != "P117" or obs.get("gate") != "g0_replay"
            or obs.get("status") != "completed" or obs.get("observed_exit_code") != 1
            or type(obs.get("observed_exit_code")) is not int
            or obs.get("execution_interface") != "exec_command/write_stdin"
            or type(obs.get("exec_session_id")) is not int
            or obs.get("resource_inspection_errors") != []
            or not _resource_ok(duplicate_report.get("resources"))
            or not isinstance(duplicate_cgroup, str)
            or obs.get("scope") != duplicate_cgroup.rsplit("/", 1)[-1]
            or duplicate_report.get("child_exit_code") != 0
            or type(duplicate_report.get("child_exit_code")) is not int
            or duplicate_report.get("status") != "setup_failed"
            or duplicate_report.get("phase") != "P117"
            or duplicate_report.get("gate") != "g0_replay"
            or duplicate_report.get("cwd") != "."
            or duplicate_report.get("log_path") != "target/p117-g0_replay.log"
            or duplicate_report.get("timeout_s") != 600
            or duplicate_report.get("log_sha256") is not None
            or not isinstance(duplicate_report.get("error"), str)
            or "FileExistsError" not in duplicate_report["error"]
            or not isinstance(obs.get("argv"), list)
            or obs["argv"] != ["python3", "scripts/run_p117_gate.py", "--phase", "P117",
                               "--name", "g0_replay", "--timeout-s", "600", "--log",
                               "target/p117-g0_replay.log", "--receipt",
                               "results/quality/p117/g0_replay.json", "--", "python3",
                               "controls/check_p117.py", "--g0-only", "--no-write"]
            or not isinstance(duplicate_report.get("argv"), list)
            or duplicate_report.get("argv") != ["python3", "controls/check_p117.py", "--g0-only", "--no-write"]
            or b"log persistence failed" not in output_raw):
        raise ValueError("duplicate recorder collision evidence differs")
    if (_safe_file(DUPLICATE_OBSERVED).read_bytes() != obs_raw
            or _safe_file(DUPLICATE_LOG).read_bytes() != output_raw):
        raise ValueError("live duplicate recorder evidence differs from archive")
    terminal_obs_raw = _safe_file(f"{TERMINAL}/{TERMINAL_VERDICT_OBSERVED}").read_bytes()
    terminal_obs = _unique(terminal_obs_raw)
    terminal_log = _safe_file(f"{TERMINAL}/{TERMINAL_VERDICT_LOG}").read_bytes()
    if (_sha(terminal_obs_raw) != discovery.get("terminal_verdict_observation", {}).get("sha256")
            or _sha(terminal_log) != discovery["preserved_files_sha256"].get(TERMINAL_VERDICT_LOG)
            or terminal_obs.get("schema") != "actinv-p117-terminal-verdict-observation-1"
            or terminal_obs.get("status") != "completed"
            or type(terminal_obs.get("exit_code")) is not int or terminal_obs.get("exit_code") != 1
            or terminal_obs.get("entry_point") != ["python3", "controls/check_p117_verdict.py", "--write"]
            or not isinstance(terminal_obs.get("argv"), list)
            or len(terminal_obs["argv"]) != 3
            or terminal_obs["argv"][:2] != ["python3", "-c"]
            or "check_p117_verdict.main([\"--write\"])" not in terminal_obs["argv"][2]
            or terminal_obs.get("inspection_errors") != []
            or terminal_obs.get("resources") is None or not _resource_ok(terminal_obs.get("resources"))
            or terminal_obs.get("raw_log_sha256") != _sha(terminal_log)):
        raise ValueError("terminal verdict actual-exit evidence differs")
    if (_safe_file(TERMINAL_VERDICT_OBSERVED).read_bytes() != terminal_obs_raw
            or _safe_file(TERMINAL_VERDICT_LOG).read_bytes() != terminal_log):
        raise ValueError("live terminal verdict observation/log differs from archive")
    log_lines = terminal_log.decode("utf-8").splitlines()
    inspected = [line for line in log_lines if line.startswith('{"errors"')]
    starts = [index for index, line in enumerate(log_lines) if line == "{"]
    if len(inspected) != 1 or len(starts) != 1:
        raise ValueError("terminal output lacks resource or verdict JSON")
    resource_line = _unique(inspected[0].encode())
    report = _unique("\n".join(log_lines[starts[0]:]).encode())
    if (resource_line != {"resources": terminal_obs["resources"], "errors": []}
            or report != {"gates": TERMINAL_GATES, "persisted_matches": True, "verdict": "P117-FAIL"}):
        raise ValueError("terminal output does not match observation and persisted verdict")
    if _sha(_safe_file(f"{TERMINAL}/results/p117_verdict.json").read_bytes()) != TERMINAL_VERDICT_SHA256:
        raise ValueError("terminal archived verdict differs")
    return verdict, True


def _derive_historical_failure(g0: dict, terminal_verdict: dict) -> dict:
    """Project only the historical G0/source predicates after their snapshot is proven."""
    old_g0_ok = check_p117_verdict._g0_ok
    old_source_match = check_p117_verdict._source_commit_matches
    try:
        def snapshot_g0(value):
            return value == g0 and g0.get("pass") is True
        def snapshot_source(value, record):
            return record is None and value == g0 and g0.get("pass") is True
        check_p117_verdict._g0_ok = snapshot_g0
        check_p117_verdict._source_commit_matches = snapshot_source
        derived = check_p117_verdict.derive()
    finally:
        check_p117_verdict._g0_ok = old_g0_ok
        check_p117_verdict._source_commit_matches = old_source_match
    return derived


def verify() -> dict:
    """Return deterministic verification of P117's original and terminal FAIL records."""
    try:
        discovery, terminal_archive, initial_archive = _terminal_archive()
        initial_files = discovery["original_initial_archive"]["files_sha256"]
        initial = _verify_initial(initial_files)
        sources, rust, current_match = _verify_terminal_sources(discovery)
        terminal_verdict, disposition_ok = _verify_terminal_disposition(discovery)
        derived = _derive_historical_failure(
            _unique(_safe_file(f"{TERMINAL}/results/g0_p117_twin_waste.json").read_bytes()), terminal_verdict)
        derived_matches = (derived == terminal_verdict and derived.get("verdict") == "P117-FAIL"
                           and derived.get("gates") == TERMINAL_GATES
                           and derived.get("gates", {}).get("historical_p116_ci_failure") == "PASS")
        if not derived_matches:
            raise ValueError("historical P117 terminal FAIL derivation differs")
        if not current_match:
            raise ValueError("current source tree differs beyond the registered CI workflow transition")
        return {
            "schema": SCHEMA, "pass": True, "phase": "P117", "verdict": "P117-FAIL",
            "checkpoint_commit": CHECKPOINT, "derived_verdict_matches_persisted": True,
            "historical_p116_verified": True, "initial_failure": initial,
            "terminal_gates": TERMINAL_GATES, "control_source_count": 193,
            "terminal_source_sha256": sources, "current_source_matches_except_ci": True,
            "current_rust_source_count": 100, "inherited_rust_sha256": rust,
            "terminal_archive_files_sha256": terminal_archive,
            "initial_archive_files_sha256": initial_archive,
            "terminal_verdict_sha256": TERMINAL_VERDICT_SHA256,
            "terminal_discovery_sha256": TERMINAL_DISCOVERY_SHA256,
            "initial_discovery_sha256": INITIAL_DISCOVERY_SHA256,
            "duplicate_observation_sha256": DUPLICATE_OBSERVATION_SHA256,
            "duplicate_output_sha256": DUPLICATE_OUTPUT_SHA256,
            "terminal_disposition_verified": disposition_ok,
        }
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError, AttributeError,
            ImportError, UnicodeError, json.JSONDecodeError):
        return {"schema": SCHEMA, "pass": False, "phase": "P117", "verdict": "P117-FAIL",
                "checkpoint_commit": CHECKPOINT, "derived_verdict_matches_persisted": False,
                "historical_p116_verified": False, "control_source_count": 0,
                "current_rust_source_count": 0, "terminal_archive_files_sha256": {},
                "initial_archive_files_sha256": {}, "terminal_gates": TERMINAL_GATES}


if __name__ == "__main__":
    report = verify()
    print(json.dumps(report, sort_keys=True, indent=2))
    raise SystemExit(0 if report.get("pass") is True else 1)
