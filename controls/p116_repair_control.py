#!/usr/bin/env python3
"""Read-only verifier for the preserved first P116 G1 failure."""
from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

CHECKPOINT = "cb786f75acc456cb0157c6d5f38d9b6308140d85"
AMENDMENT_SHA256 = "3fa71d7c7537dbe9132f5de8d9f4a64d33126f065f4f41aef7496fab38be372c"
DISCOVERY_REL = "results/failures/p116_initial/discovery.json"
DISCOVERY_SHA256 = "15f73cb664ff13a4ca75d7edcf098d2ee2ce557ff00f9d95cb23c366b612e441"
ARCHIVE_PREFIX = "results/failures/p116_initial/"
SOURCE_COUNT = 200
RUST_COUNT = 100
RETAINED_COUNT = 88
QUALITY_GATE_COUNT = 25
ORIGINAL_G0_SHA256 = "2b1b8cbaad1073be4b16e5d21f284d41702ad2c0b53a36e6ee7b7f4e3e32af93"
ORIGINAL_G1_SHA256 = "75e94554ac5d9d909e5eac59bd676c649db685eb52ffbba8a2340d5603251c21"
ORIGINAL_G1_LOG_SHA256 = "a836211e55cbd461718935edf88e91b07f9ecd572007f9eadd0028623a15cde2"
ORIGINAL_G0_REL = ARCHIVE_PREFIX + "results/g0_p116_twin_waste.json"
ORIGINAL_G1_REL = ARCHIVE_PREFIX + "results/g1_p116_twin_waste.json"
ORIGINAL_G1_LOG_REL = ARCHIVE_PREFIX + "target/p116-g1.log"
METADATA_SOURCES = {
    ".github/workflows/ci.yml", "docs/ROADMAP.md", "docs/history/sessions/P116.md",
    "ledger.md", "protocols/protocol_hash.txt",
}
EXPECTED_GATE_NAMES = {
    "gate_recorder_regressions", "handbook_build", "handbook_chromium", "handbook_links",
    "historical_p107_replay", "historical_p108_replay", "historical_p109_replay",
    "historical_p110_replay", "historical_p111_replay", "historical_p112_replay",
    "historical_p113_replay", "historical_p114_replay", "historical_p115_replay",
    "p105_child_lifecycle_regressions", "p113_history_regressions", "p114_history_regressions",
    "p115_history_regressions", "p116_oracle_regressions", "p116_seal_regressions",
    "p116_verdict_regressions", "release_build", "rust_fmt", "workspace_check",
    "workspace_clippy", "workspace_tests",
}
RESOURCE_LIMITS = {
    "cpu.max": "200000 100000", "memory.max": "6442450944",
    "memory.swap.max": "0", "pids.max": "128",
}
RESOURCE_ENV = {"CARGO_BUILD_JOBS": "1", "RAYON_NUM_THREADS": "2", "RUST_TEST_THREADS": "1"}
SOURCE_GIT_READER = "controls/p105_budget_control.py"
P116_PROTOCOL_SHA256 = "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa"

EXPECTED_TIMEOUTS = {
    "rust_fmt": 1200.0, "workspace_check": 1200.0, "workspace_clippy": 1200.0,
    "workspace_tests": 1200.0, "release_build": 1200.0,
    "handbook_build": 120.0, "handbook_links": 120.0, "handbook_chromium": 150.0,
}
EXPECTED_ARGV = {
    "gate_recorder_regressions": ["python3", "scripts/test_run_p116_gate.py"],
    "handbook_build": ["/home/connoravila/Documents/actinv/scratch/mdbook-demo/bin/mdbook", "build"],
    "handbook_chromium": ["python3", "target/p103-docs-smoke.py"],
    "handbook_links": ["python3", "scripts/check_docs.py", "dist/docs"],
    "historical_p107_replay": ["python3", "controls/check_p107_history.py"],
    "historical_p108_replay": ["python3", "controls/check_p108_verdict.py"],
    "historical_p109_replay": ["python3", "controls/check_p109_history.py"],
    "historical_p110_replay": ["python3", "controls/check_p110_verdict.py"],
    "historical_p111_replay": ["python3", "controls/check_p111_history.py"],
    "historical_p112_replay": ["python3", "controls/check_p112_verdict.py"],
    "historical_p113_replay": ["python3", "controls/check_p113_history.py"],
    "historical_p114_replay": ["python3", "controls/check_p114_history.py"],
    "historical_p115_replay": ["python3", "controls/check_p115_history.py"],
    "p105_child_lifecycle_regressions": ["python3", "controls/test_p105_children.py"],
    "p113_history_regressions": ["python3", "controls/test_p113_history.py"],
    "p114_history_regressions": ["python3", "controls/test_p114_history.py"],
    "p115_history_regressions": ["python3", "controls/test_p115_history.py"],
    "p116_oracle_regressions": ["python3", "controls/test_p116_oracle.py"],
    "p116_seal_regressions": ["python3", "controls/test_p116_seal.py"],
    "p116_verdict_regressions": ["python3", "controls/test_p116_verdict.py"],
    "release_build": ["cargo", "build", "--release", "-p", "actinv-cli"],
    "rust_fmt": ["cargo", "fmt", "--all", "--", "--check"],
    "workspace_check": ["cargo", "check", "--workspace", "--all-targets", "--all-features"],
    "workspace_clippy": ["cargo", "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
    "workspace_tests": ["cargo", "test", "--workspace", "--all-targets", "--all-features"],
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a nonempty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"unsafe path: {value!r}")
    return path


def _regular(root: Path, relative: str) -> Path:
    safe = _safe_rel(relative)
    root_real = root.resolve(strict=True)
    path = root.joinpath(*safe.parts)
    path.resolve(strict=True).relative_to(root_real)
    cursor = root
    for part in safe.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink in evidence path: {relative}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {relative}")
    return path


def _json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _amendment_is_registered(root: Path) -> bool:
    registry = _regular(root, "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    protocol = f"{P116_PROTOCOL_SHA256}  protocols/ACTINV-P116_PROTOCOL.md"
    amendment = f"{AMENDMENT_SHA256}  protocols/ACTINV-P116_AMENDMENT_A.md"
    return registry.count(protocol) == 1 and registry.count(amendment) == 1


def _load_runner(root: Path, source_map: dict[str, str]):
    """Load the bounded P105 runner only after its original blob is pinned."""
    runner_path = _regular(root, SOURCE_GIT_READER)
    if _sha(runner_path.read_bytes()) != source_map.get(SOURCE_GIT_READER):
        raise ValueError("bounded Git reader does not match the initial source map")
    spec = importlib.util.spec_from_file_location("_p116_verified_bounded_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load the verified bounded Git runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._run


def _git_files(root: Path, commit: str, paths: set[str], source_map: dict[str, str]) -> dict[str, bytes]:
    """Read pinned UTF-8 source/evidence blobs through the reviewed bounded runner."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not paths:
        raise ValueError("invalid Git checkpoint or empty path population")
    run = _load_runner(root, source_map)
    output: dict[str, bytes] = {}
    total = 0
    for relative in sorted(paths):
        _safe_rel(relative)
        child = run(["git", "show", f"{commit}:{relative}"], cwd=root, timeout_s=120)
        if child.returncode != 0:
            raise ValueError(f"bounded git show failed for {relative}")
        raw = child.stdout.encode("utf-8")
        total += len(raw)
        if len(raw) > 64 * 1024 * 1024 or total > 256 * 1024 * 1024:
            raise ValueError("checkpoint evidence exceeds bounded read limits")
        output[relative] = raw
    return output


def _rust_paths_at_checkpoint(root: Path, source_map: dict[str, str]) -> set[str]:
    run = _load_runner(root, source_map)
    child = run(["git", "ls-tree", "-r", "--full-tree", CHECKPOINT, "--", "crates"], cwd=root, timeout_s=120)
    if child.returncode != 0:
        raise ValueError("cannot enumerate checkpoint Rust population")
    paths: set[str] = set()
    for line in child.stdout.splitlines():
        try:
            metadata, relative = line.split("\t", 1)
            mode, kind, _object_id = metadata.split()
        except ValueError as error:
            raise ValueError("malformed checkpoint tree entry") from error
        if relative.startswith("crates/") and relative.endswith(".rs"):
            safe = _safe_rel(relative)
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("checkpoint Rust member is not a regular Git blob")
            paths.add(safe.as_posix())
    if len(paths) != RUST_COUNT:
        raise ValueError("checkpoint Rust population count differs")
    return paths


def _source_population(g0: dict[str, Any], source_map: object) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(source_map, dict) or len(source_map) != SOURCE_COUNT:
        raise ValueError("initial source map population differs")
    controls = g0.get("control_sha256")
    rust = g0.get("inherited_rust_sha256")
    if (not isinstance(controls, dict) or len(controls) != 95
            or not isinstance(rust, dict) or len(rust) != RUST_COUNT):
        raise ValueError("initial G0 source populations differ")
    expected = set(controls) | set(rust) | METADATA_SOURCES
    if (len(expected) != SOURCE_COUNT or set(source_map) != expected
            or set(rust) != {p for p in source_map if p.startswith("crates/") and p.endswith(".rs")}
            or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
                   for v in source_map.values())):
        raise ValueError("initial source map is not the exact control/Rust/metadata population")
    if any(source_map.get(path) != digest for path, digest in controls.items()):
        raise ValueError("initial source map and G0 control hashes differ")
    if any(source_map.get(path) != digest for path, digest in rust.items()):
        raise ValueError("initial source map and G0 Rust hashes differ")
    original_sources = dict(source_map)
    original_rust = dict(rust)
    return original_sources, original_rust


def _verify_source_git(root: Path, g0: dict[str, Any], source_map: dict[str, str],
                       rust_map: dict[str, str], git_files: dict[str, bytes]) -> None:
    if not set(git_files).issuperset(source_map) or any(_sha(git_files[path]) != digest
                                                        for path, digest in source_map.items()):
        raise ValueError("initial source files differ from checkpoint Git blobs")
    if set(rust_map) != _rust_paths_at_checkpoint(root, source_map):
        raise ValueError("initial Rust map does not cover the complete checkpoint tree")
    checker = git_files.get("controls/check_p116.py")
    if checker is None:
        raise ValueError("initial checker source is unavailable")
    module = ast.parse(checker.decode("utf-8"), filename="controls/check_p116.py")
    control_files = None
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "CONTROL_FILES" for target in targets):
                control_files = ast.literal_eval(node.value)
                break
    if (not isinstance(control_files, (tuple, list))
            or any(type(path) is not str for path in control_files)
            or len(set(control_files)) != 95 or set(control_files) != set(g0.get("control_sha256", {}))):
        raise ValueError("original checker CONTROL_FILES population differs")


def _validate_archive_members(root: Path, retained: object, git_files: dict[str, bytes],
                              *, prefix: str, expected_count: int,
                              extra_paths: set[str] | None = None) -> dict[str, str]:
    if not isinstance(retained, dict) or len(retained) != expected_count:
        raise ValueError("retained map population differs")
    archive_root = root / prefix.rstrip("/")
    if archive_root.is_symlink() or not archive_root.is_dir():
        raise ValueError("initial archive root is missing or unsafe")
    physical: set[str] = set()
    for directory, directories, filenames in os.walk(archive_root, followlinks=False):
        base = Path(directory)
        for name in directories:
            if (base / name).is_symlink():
                raise ValueError("symlink in initial archive directory")
        for name in filenames:
            candidate = base / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("non-regular entry in initial archive")
            physical.add(candidate.relative_to(root).as_posix())
    expected_physical = set(retained) | (extra_paths or set())
    if physical != expected_physical:
        raise ValueError("initial archive physical members differ")
    checked: dict[str, str] = {}
    for relative, expected in retained.items():
        safe = _safe_rel(relative)
        if (not safe.as_posix().startswith(prefix) or not isinstance(expected, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            raise ValueError(f"invalid retained path/hash: {relative!r}")
        raw = _regular(root, relative).read_bytes()
        if _sha(raw) != expected:
            raise ValueError(f"retained bytes changed: {relative}")
        # Retained evidence was itself present at the original checkpoint.
        if relative not in git_files or _sha(git_files[relative]) != expected:
            raise ValueError(f"retained Git blob differs: {relative}")
        checked[relative] = expected
    return checked


def _validate_archive(root: Path, retained: object, git_files: dict[str, bytes],
                      *, prefix: str = ARCHIVE_PREFIX) -> dict[str, str]:
    checked = _validate_archive_members(root, retained, git_files, prefix=prefix,
                                        expected_count=RETAINED_COUNT,
                                        extra_paths={DISCOVERY_REL})
    discovery = _regular(root, DISCOVERY_REL).read_bytes()
    if (_sha(discovery) != DISCOVERY_SHA256 or DISCOVERY_REL not in git_files
            or _sha(git_files[DISCOVERY_REL]) != DISCOVERY_SHA256):
        raise ValueError("initial discovery bytes differ from the pinned checkpoint")
    return checked


def _validate_receipts(root: Path, retained: dict[str, str], gate_codes: object) -> dict[str, dict[str, str]]:
    if (not isinstance(gate_codes, dict) or set(gate_codes) != EXPECTED_GATE_NAMES
            or any(type(value) is not int or value != 0 for value in gate_codes.values())):
        raise ValueError("initial quality gate exits are not exactly 25 zeroes")
    records: dict[str, dict[str, str]] = {}
    for name in sorted(EXPECTED_GATE_NAMES):
        receipt_rel = f"{ARCHIVE_PREFIX}results/quality/p116/{name}.json"
        log_rel = f"{ARCHIVE_PREFIX}target/p116-{name}.log"
        receipt_sha = retained.get(receipt_rel)
        log_sha = retained.get(log_rel)
        if not isinstance(receipt_sha, str) or not isinstance(log_sha, str):
            raise ValueError(f"initial receipt/log missing for {name}")
        receipt_raw = _regular(root, receipt_rel).read_bytes()
        log_raw = _regular(root, log_rel).read_bytes()
        if _sha(receipt_raw) != receipt_sha or _sha(log_raw) != log_sha:
            raise ValueError(f"initial receipt/log bytes differ: {name}")
        receipt = json.loads(receipt_raw.decode("utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError(f"malformed initial receipt: {name}")
        resources = receipt.get("resources")
        if (receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
                or receipt.get("phase") != "P116" or receipt.get("gate") != name
                or receipt.get("status") != "completed" or type(receipt.get("child_exit_code")) is not int
                or receipt.get("child_exit_code") != 0 or receipt.get("cwd") != "."
                or receipt.get("log_path") != f"target/p116-{name}.log"
                or receipt.get("log_sha256") != log_sha
                or receipt.get("error") is not None
                or receipt.get("argv") != EXPECTED_ARGV[name]
                or not isinstance(receipt.get("argv"), list) or not receipt["argv"]
                or type(receipt.get("timeout_s")) not in (int, float)
                or isinstance(receipt.get("timeout_s"), bool)
                or receipt.get("timeout_s") != EXPECTED_TIMEOUTS.get(name, 600.0)
                or not isinstance(resources, dict)
                or resources.get("cgroup_limits") != RESOURCE_LIMITS
                or resources.get("environment") != RESOURCE_ENV
                or resources.get("platform") != "linux"
                or not isinstance(resources.get("cgroup_path"), str)
                or not resources["cgroup_path"].startswith("/user.slice/")
                or not resources["cgroup_path"].endswith(".scope")
                or not isinstance(resources.get("tmpdir"), dict)
                or resources["tmpdir"].get("path") != "target/preflight-tmp"
                or resources["tmpdir"].get("filesystem") != "ext4"
                or resources["tmpdir"].get("mount_point") != "/"):
            raise ValueError(f"initial receipt identity differs: {name}")
        records[name] = {"receipt_sha256": receipt_sha, "log_sha256": log_sha}
    return records


def _validate_failure(root: Path, discovery: dict[str, Any], source_map: dict[str, str],
                      retained: dict[str, str]) -> tuple[int, str]:
    g0_raw = _regular(root, ORIGINAL_G0_REL).read_bytes()
    g1_raw = _regular(root, ORIGINAL_G1_REL).read_bytes()
    log_raw = _regular(root, ORIGINAL_G1_LOG_REL).read_bytes()
    if (_sha(g0_raw) != ORIGINAL_G0_SHA256 or _sha(g1_raw) != ORIGINAL_G1_SHA256
            or _sha(log_raw) != ORIGINAL_G1_LOG_SHA256
            or retained.get(ORIGINAL_G0_REL) != ORIGINAL_G0_SHA256
            or retained.get(ORIGINAL_G1_REL) != ORIGINAL_G1_SHA256
            or retained.get(ORIGINAL_G1_LOG_REL) != ORIGINAL_G1_LOG_SHA256):
        raise ValueError("original P116 G0/G1/log identity differs")
    g0 = json.loads(g0_raw.decode("utf-8"))
    g1 = json.loads(g1_raw.decode("utf-8"))
    if (g0.get("schema") != "actinv-p116-twin-waste-g0-1" or g0.get("phase") != "P116"
            or g0.get("protocol_sha256") != P116_PROTOCOL_SHA256 or g0.get("protocol_registered") is not True
            or g0.get("pass") is not True or type(g0.get("repair_rounds")) is not int
            or g0.get("repair_rounds") != 0 or g0.get("control_sha256") != {
                path: source_map[path] for path in g0.get("control_sha256", {})}
            or g0.get("inherited_rust_sha256") != {
                path: source_map[path] for path in g0.get("inherited_rust_sha256", {})}
            or g0.get("inherited_rust_source_count") != RUST_COUNT
            or g0.get("inherited_rust_population_matches") is not True):
        raise ValueError("original P116 G0 content differs")
    failures = g1.get("failures")
    mutations = g1.get("mutations_rejected")
    refusal = g1.get("refusal_controls")
    refusal_checks = refusal.get("checks") if isinstance(refusal, dict) else None
    request_evidence = g1.get("request_evidence")
    expected_ids = g0.get("request_ids")
    expected_counts = g0.get("case_targets")
    if (g1.get("schema") != "actinv-p116-twin-waste-g1-1" or g1.get("phase") != "P116"
            or g1.get("protocol_sha256") != P116_PROTOCOL_SHA256
            or g1.get("pass") is not False or type(g1.get("request_count")) is not int
            or g1.get("request_count") != 35 or type(g1.get("component_target_count")) is not int
            or g1.get("component_target_count") != 138 or type(g1.get("independent_comparison_count")) is not int
            or g1.get("independent_comparison_count") != 138 or not isinstance(failures, list)
            or len(failures) != 186
            or any(not isinstance(item, str) or "row_fractions" not in item or ".limit:" not in item for item in failures)
            or not isinstance(mutations, dict) or len(mutations) < 30
            or any(type(value) is not bool for value in mutations.values())
            or set(k for k, v in mutations.items() if v is not True) != {"integer_count_type"}
            or not isinstance(refusal_checks, dict) or refusal.get("pass") is not False
            or len(refusal_checks) < 30 or any(type(value) is not bool for value in refusal_checks.values())
            or set(k for k, v in refusal_checks.items() if v is not True) != {"unknown_external_field"}
            or not isinstance(expected_ids, list) or len(expected_ids) != 35
            or not isinstance(expected_counts, list) or len(expected_counts) != 35
            or not isinstance(request_evidence, list) or len(request_evidence) != 35
            or [row.get("id") for row in request_evidence if isinstance(row, dict)] != expected_ids
            or [row.get("component_target_count") for row in request_evidence if isinstance(row, dict)] != expected_counts
            or any(not isinstance(row, dict) or any(type(row.get(field)) is not str
                   or re.fullmatch(r"[0-9a-f]{64}", row[field]) is None
                   for field in ("input_sha256", "output_sha256", "ordinary_waste_sha256"))
                   for row in request_evidence)
            or g1.get("repeat_byte_identical") is not True):
        raise ValueError("original P116 G1 failure disposition differs")
    return len(failures), ORIGINAL_G1_LOG_SHA256


def verify(root: Path) -> dict[str, Any]:
    """Verify the registered first-failure evidence without running checks."""
    root = Path(root)
    result: dict[str, Any] = {
        "schema": "actinv-p116-repair-evidence-1", "pass": False,
        "checkpoint": CHECKPOINT, "protocol_sha256": P116_PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "discovery_sha256": DISCOVERY_SHA256,
    }
    try:
        discovery_raw = _regular(root, DISCOVERY_REL).read_bytes()
        if _sha(discovery_raw) != DISCOVERY_SHA256:
            raise ValueError("initial discovery hash differs")
        discovery = json.loads(discovery_raw.decode("utf-8"))
        if (not isinstance(discovery, dict) or discovery.get("schema") != "actinv-p116-initial-failure-1"
                or discovery.get("phase") != "P116" or discovery.get("protocol_sha256") != "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa"
                or type(discovery.get("source_file_count")) is not int or discovery.get("source_file_count") != SOURCE_COUNT
                or type(discovery.get("rust_source_file_count")) is not int or discovery.get("rust_source_file_count") != RUST_COUNT
                or type(discovery.get("retained_file_count")) is not int or discovery.get("retained_file_count") != RETAINED_COUNT
                or type(discovery.get("observed_gate_count")) is not int or discovery.get("observed_gate_count") != QUALITY_GATE_COUNT
                or discovery.get("g0_sha256") != ORIGINAL_G0_SHA256
                or discovery.get("g0_sealed") is not True
                or type(discovery.get("g0_seal_exit_code")) is not int or discovery.get("g0_seal_exit_code") != 0
                or type(discovery.get("g0_replay_exit_code")) is not int or discovery.get("g0_replay_exit_code") != 0
                or discovery.get("g1_sha256") != ORIGINAL_G1_SHA256
                or type(discovery.get("exit_code")) is not int or discovery.get("exit_code") != 1 or discovery.get("gate") != "G1"
                or discovery.get("failed_log_path") != ORIGINAL_G1_LOG_REL
                or discovery.get("failed_log_sha256") != ORIGINAL_G1_LOG_SHA256
                or type(discovery.get("comparison_failure_count")) is not int or discovery.get("comparison_failure_count") != 186
                or discovery.get("failed_mutation") != "integer_count_type"
                or discovery.get("failed_refusal") != "unknown_external_field"
                or discovery.get("repeat_byte_identical") is not True
                or type(discovery.get("repair_rounds")) is not int or discovery.get("repair_rounds") != 0
                or discovery.get("g1_executed") is not True
                or discovery.get("g2_executed") is not False
                or discovery.get("implementation_record_present") is not False
                or discovery.get("ci_record_present") is not False
                or discovery.get("argv") != ["python3", "controls/check_p116.py", "--g1-only"]
                or discovery.get("cwd") != "."):
            raise ValueError("initial discovery fields differ")
        g0 = json.loads(_regular(root, ORIGINAL_G0_REL).read_text(encoding="utf-8"))
        source_map, rust_map = _source_population(g0, discovery.get("source_sha256"))
        retained_map = discovery.get("files_sha256")
        if not isinstance(retained_map, dict):
            raise ValueError("initial retained map is malformed")
        git_files = _git_files(root, CHECKPOINT,
                               set(source_map) | set(retained_map) | {DISCOVERY_REL}, source_map)
        _verify_source_git(root, g0, source_map, rust_map, git_files)
        retained = _validate_archive(root, retained_map, git_files)
        gates = _validate_receipts(root, retained, discovery.get("observed_gate_exit_codes"))
        comparisons, log_sha = _validate_failure(root, discovery, source_map, retained)
        if not _amendment_is_registered(root):
            raise ValueError("registered P116 amendment is missing")
        if _sha(_regular(root, "protocols/ACTINV-P116_PROTOCOL.md").read_bytes()) != P116_PROTOCOL_SHA256:
            raise ValueError("P116 protocol bytes differ")
        amendment = _regular(root, "protocols/ACTINV-P116_AMENDMENT_A.md").read_bytes()
        if _sha(amendment) != AMENDMENT_SHA256:
            raise ValueError("P116 amendment bytes differ")
        result.update({
            "original_source_sha256": source_map,
            "original_source_count": len(source_map),
            "original_retained_sha256": retained,
            "original_retained_count": len(retained),
            "original_rust_sha256": rust_map,
            "original_rust_source_count": len(rust_map),
            "original_g0_sha256": ORIGINAL_G0_SHA256,
            "original_g1_sha256": ORIGINAL_G1_SHA256,
            "original_g1_log_sha256": log_sha,
            "original_g0_seal_exit_code": 0,
            "original_g0_replay_exit_code": 0,
            "original_g1_exit_code": 1,
            "original_quality_gate_count": len(gates),
            "original_quality_gates": gates,
            "comparison_failure_count": comparisons,
            "failed_mutation": "integer_count_type",
            "failed_refusal": "unknown_external_field",
            "repeat_byte_identical": True,
            "g2_executed": False,
            "implementation_record_present": False,
            "ci_record_present": False,
            "pass": True,
        })
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired,
            ImportError, AttributeError, json.JSONDecodeError, UnicodeDecodeError, OverflowError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result
