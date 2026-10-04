#!/usr/bin/env python3
"""P117 source seal and exact replay for the unchanged P116 twin controls."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_p116 as p116
import check_p116_history
import check_p116_verdict
import p105_budget_control
import check_p112_verdict
import fetch_ci_seed
import run_p117_gate

PHASE = "P117"
PROTOCOL = "protocols/ACTINV-P117_PROTOCOL.md"
PROTOCOL_SHA256 = "624d80cfcdbcd2254f3b29f4b39c5220588bdaa9dd16b21c35d395c6aa1f7825"
IMPLEMENTATION_COMMIT = "2117f3b5df715ce832658f58250103789be845bb"
P116_G0_SHA256 = "7e08a5f94e2cce3d62229c1e1233938270420c27556abb1a81e3f304ebc4cf78"
P116_G1_SHA256 = "74f6eab80ee223c81b6f7391f3f4147d7fa38ffeac55485f3d2a05e8618a65b9"
P116_G2_SHA256 = "5e1cb32140fa7fe3000bf2d22e54b30af021d992f689d1a1a7ff1038b979fb6b"
P116_G3_SHA256 = "746876fc7c2d9da2432c5e1a816702a648f18c9ae920798c2639f9670082dd5b"
P116_VERDICT_SHA256 = "7d782e476b051cc2e7d9666e984511167f30d6e1e5ec73afc127d34ac9dac710"
P116_IMPLEMENTATION_SHA256 = "be2e96c12e5fd5356eb493bc14909a782f6f6cba2fb739645d264ae0f1b249fa"
P116_CI_SHA256 = "df939adb6bcb2a81bb9bca58df8638ea16ce0cdfcf94f89f1e07b3d302ff3c73"
P116_ARCHIVE_DISCOVERY_SHA256 = "c007355933dd2f685f6189714a96277e6448a9da0802c0c18db331f72d660039"
AMENDMENT = "protocols/ACTINV-P117_AMENDMENT_A.md"
AMENDMENT_SHA256 = "3b992672b5339f520beaff6450c227dd62d05516a70bc854b6df8dbe147b4025"
INITIAL_DISCOVERY = "results/failures/p117_initial/discovery.json"
INITIAL_DISCOVERY_SHA256 = "73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a"
INITIAL_CHECKPOINT = "24929f477ab56ed026eabee4260e39572c226428"
INITIAL_ARCHIVE = ROOT / "results/failures/p117_initial"
REPAIRED_SOURCES = {
    "controls/check_p117.py", "controls/check_p117_verdict.py",
    "controls/test_p117.py", "controls/test_p117_verdict.py",
}
MANIFEST = "scripts/ci_data_seed.json"
NOTICE = "docs/maintainers/CI_DATA_CACHE_NOTICE.md"
STAGE_SOURCE = "results/quality/p117/source/prepare_seed.py"
G0 = ROOT / "results/g0_p117_twin_waste.json"
G1 = ROOT / "results/g1_p117_twin_waste.json"
G2 = ROOT / "results/g2_p117_twin_waste.json"
G3 = ROOT / "results/g3_p117_quality.json"
P116_G0 = ROOT / "results/g0_p116_twin_waste.json"
P116_G1 = ROOT / "results/g1_p116_twin_waste.json"
P116_G2 = ROOT / "results/g2_p116_twin_waste.json"
P116_G3 = ROOT / "results/g3_p116_quality.json"
P116_VERDICT = ROOT / "results/p116_verdict.json"
P116_IMPLEMENTATION = ROOT / "results/p116_implementation_commit.json"
P116_CI = ROOT / "results/p116_ci_runs.json"
ACTINV = p116.ACTINV
FRESH_GATES = {
    "rust_fmt", "seed_regressions", "p116_history_regressions", "p116_history_replay",
    "p117_regressions", "p117_verdict_regressions", "recorder_regressions",
    "g0_seal", "g0_replay", "g1", "g2", "full_read_only_replay",
    "seed_local_verify", "seed_offline_install", "seed_release_download",
    "native_data_fetch", "fns_science", "fns_diagnosis", "fns_regressions",
}
RERUN_GATES = {
    "p117_regressions", "p117_verdict_regressions", "g0_seal", "g0_replay",
    "g1", "g2", "full_read_only_replay",
}
INITIAL_GATES = set(FRESH_GATES)
ADOPTED_INITIAL_GATES = INITIAL_GATES - RERUN_GATES
QUALITY_GATES = {"rust_fmt", "seed_local_verify", "seed_offline_install", "seed_release_download",
                 "native_data_fetch", "fns_science", "fns_diagnosis"}
RESOURCES = {
    "memory_max_bytes": 6442450944, "memory_swap_max_bytes": 0, "tasks_max": 128,
    "cpu_quota_percent": 200, "disk_tmpdir": "target/preflight-tmp", "coordinator_jobs": "serial",
}

# Bind every source/input already covered by the immutable P116 source seal, plus
# the small P117 transport boundary. Mutable roadmap/ledger/session files are excluded.
P117_FILES = (
    PROTOCOL, AMENDMENT, INITIAL_DISCOVERY, MANIFEST, NOTICE,
    "scripts/ci_data.sha256", "scripts/ci_tendl_files.txt",
    "crates/actinv-cli/data/actinv-data-catalog-v1.1.0.json", "examples/fns_iron/case.json",
    "scripts/fetch_ci_seed.py", "scripts/test_fetch_ci_seed.py", "scripts/fetch_ci_data.sh",
    ".github/workflows/ci.yml", ".github/workflows/fns-iron.yml",
    "scripts/run_p117_gate.py", "scripts/test_run_p117_gate.py", STAGE_SOURCE,
    "results/quality/p117/source/offline_install.py",
    "controls/check_p116_history.py", "controls/test_p116_history.py",
    "controls/check_p117.py", "controls/check_p117_verdict.py", "controls/test_p117.py",
    "controls/test_p117_verdict.py",
)


def _initial_archive_control_paths() -> tuple[str, ...]:
    try:
        raw = (ROOT / INITIAL_DISCOVERY).read_bytes()
        if hashlib.sha256(raw).hexdigest() != INITIAL_DISCOVERY_SHA256:
            return ()
        discovery = json.loads(raw.decode("utf-8"))
        files = discovery.get("preserved_files_sha256") if isinstance(discovery, dict) else None
        if not isinstance(files, dict) or len(files) != 70:
            return ()
        names = []
        for relative in files:
            path = PurePosixPath(relative) if isinstance(relative, str) else PurePosixPath("/")
            if (path.is_absolute() or not relative or ".." in path.parts or "." in path.parts
                    or "\\" in relative or path.as_posix() != relative):
                return ()
            names.append(f"results/failures/p117_initial/{relative}")
        return tuple(sorted(names))
    except (OSError, ValueError, TypeError, UnicodeError):
        return ()


INITIAL_ARCHIVE_FILES = _initial_archive_control_paths()
CONTROL_FILES = tuple(dict.fromkeys((*p116.CONTROL_FILES, *P117_FILES, *INITIAL_ARCHIVE_FILES)))


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_rel(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a nonempty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value or path.as_posix() != value:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def _safe_file(relative: object) -> Path:
    safe = _safe_rel(relative)
    path = ROOT / safe
    path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    current = ROOT
    for part in safe.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink in sealed source path")
    if not path.is_file():
        raise ValueError("sealed path is not a regular file")
    return path


def _control_hashes() -> dict[str, str]:
    return {name: _sha(_safe_file(name)) or "" for name in CONTROL_FILES}


def _p116_current_identity() -> tuple[dict, dict, dict, dict, dict]:
    g0, g1, g2, g3 = map(_read, (P116_G0, P116_G1, P116_G2, P116_G3))
    verdict, implementation, ci = map(_read, (P116_VERDICT, P116_IMPLEMENTATION, P116_CI))
    objects = (g0, g1, g2, g3, verdict, implementation)
    if (any(not isinstance(item, dict) for item in objects)
            or not isinstance(ci, list) or len(ci) != 6):
        raise ValueError("P116 immutable evidence is missing or malformed")
    fixed = ((P116_G0, P116_G0_SHA256), (P116_G1, P116_G1_SHA256),
             (P116_G2, P116_G2_SHA256), (P116_G3, P116_G3_SHA256),
             (P116_VERDICT, P116_VERDICT_SHA256),
             (P116_IMPLEMENTATION, P116_IMPLEMENTATION_SHA256), (P116_CI, P116_CI_SHA256))
    if any(_sha(path) != digest for path, digest in fixed):
        raise ValueError("an immutable P116 artifact differs from its pinned identity")
    if (implementation.get("commit_sha") != IMPLEMENTATION_COMMIT
            or verdict.get("verdict") != "P116-FAIL"
            or verdict.get("implementation_commit") != IMPLEMENTATION_COMMIT
            or _sha(ROOT / "results/failures/p116_ci/discovery.json") != P116_ARCHIVE_DISCOVERY_SHA256):
        raise ValueError("P116 terminal implementation/failure identity changed")
    return g0, g1, g2, g3, verdict


def _source_population_ok(g0: dict) -> tuple[bool, dict[str, str], dict[str, str]]:
    try:
        hashes = _control_hashes()
        old_control = g0.get("control_sha256")
        if not isinstance(old_control, dict) or any(hashes.get(k) != v for k, v in old_control.items()):
            return False, hashes, {}
        if not set(p116.CONTROL_FILES).issubset(hashes):
            return False, hashes, {}
        current_rust = p116._current_rust_source_hashes()
        old_rust = _read(P116_G3).get("production_rust_sha256")
        commit_paths = p116._rust_paths_at_commit(IMPLEMENTATION_COMMIT)
        if (not isinstance(old_rust, dict) or len(old_rust) != 100
                or set(current_rust) != set(old_rust) or set(current_rust) != commit_paths
                or current_rust != old_rust
                or not check_p116_verdict._source_commit_matches(_read(P116_G3), _read(P116_IMPLEMENTATION))):
            return False, hashes, current_rust
        return True, hashes, current_rust
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False, {}, {}


def _handbook_snapshot() -> tuple[dict[str, str], bool]:
    try:
        paths = check_p112_verdict._handbook_paths(IMPLEMENTATION_COMMIT)
        hashes = {path: _sha_bytes(p116._git_blob(IMPLEMENTATION_COMMIT, path))
                  for path in sorted(paths)}
        current_paths = check_p112_verdict._handbook_paths(None)
        current_ok = (current_paths == paths and all(_sha(ROOT / path) == digest
                                                     for path, digest in hashes.items()))
        return hashes, current_ok
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        return {}, False


def _unique_json(raw: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


def _archive_file(relative: str) -> Path:
    safe = _safe_rel(relative)
    path = INITIAL_ARCHIVE / Path(*safe.parts)
    path.resolve(strict=True).relative_to(INITIAL_ARCHIVE.resolve(strict=True))
    current = INITIAL_ARCHIVE
    for part in safe.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink in preserved P117 archive")
    if not path.is_file():
        raise ValueError("preserved P117 path is not a regular file")
    return path


def _archive_resource_ok(resources: object) -> bool:
    if not isinstance(resources, dict):
        return False
    tmp = resources.get("tmpdir")
    cgroup = resources.get("cgroup_path")
    pieces = cgroup.split("/") if isinstance(cgroup, str) else []
    return (resources.get("platform") == "linux"
            and isinstance(cgroup, str) and cgroup.startswith("/user.slice/")
            and cgroup.endswith(".scope")
            and not any(piece in {".", ".."} for piece in pieces)
            and not any(not piece for piece in pieces[1:])
            and resources.get("cgroup_limits") == {
                "memory.max": "6442450944", "memory.swap.max": "0",
                "pids.max": "128", "cpu.max": "200000 100000"}
            and resources.get("environment") == {
                "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            and isinstance(tmp, dict) and tmp.get("path") == "target/preflight-tmp"
            and isinstance(tmp.get("mount_point"), str) and Path(tmp["mount_point"]).is_absolute()
            and isinstance(tmp.get("filesystem"), str) and bool(tmp["filesystem"].strip())
            and tmp["filesystem"].lower() not in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"})


def _archived_gate_entry(name: str, initial_g3: dict) -> dict | None:
    receipt_rel = f"results/quality/p117/{name}.json"
    log_rel = f"results/quality/p117/{name}.log"
    target_rel = f"target/p117-{name}.log"
    try:
        receipt_raw = _archive_file(receipt_rel).read_bytes()
        copied_log = _archive_file(log_rel).read_bytes()
        raw_log = _archive_file(target_rel).read_bytes()
        receipt = _unique_json(receipt_raw)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    timeout = receipt.get("timeout_s") if isinstance(receipt, dict) else None
    cap = 1200 if name in QUALITY_GATES else 600
    argv = receipt.get("argv") if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != PHASE or receipt.get("gate") != name
            or receipt.get("status") != "completed" or type(receipt.get("child_exit_code")) is not int
            or receipt.get("child_exit_code") != 0 or receipt.get("cwd") != "."
            or receipt.get("log_path") != target_rel
            or receipt.get("log_sha256") != _sha_bytes(copied_log)
            or copied_log != raw_log
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or not 0 < timeout <= cap
            or receipt.get("error") is not None or not isinstance(argv, list) or not argv
            or any(not isinstance(item, str) or not item.strip() for item in argv)
            or run_p117_gate._has_placeholder(argv)
            or not _archive_resource_ok(receipt.get("resources"))):
        return None
    expected = {
        "name": name, "receipt_path": receipt_rel,
        "receipt_sha256": _sha_bytes(receipt_raw), "log_path": log_rel,
        "log_sha256": receipt["log_sha256"], "argv": argv,
        "exit_code": 0, "resources": receipt["resources"],
    }
    if initial_g3.get("fresh_gates", {}).get(name) != expected:
        return None
    return expected


def _repair_evidence() -> dict:
    evidence_hashes: dict[str, str] = {}
    initial = {}
    amendment_registered = False
    archive_ok = False
    source_ok = False
    failure_ok = False
    try:
        registry = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
        amendment_registered = (registry.count(f"{AMENDMENT_SHA256}  {AMENDMENT}") == 1
                                and _sha(ROOT / AMENDMENT) == AMENDMENT_SHA256)
        discovery_raw = _safe_file(INITIAL_DISCOVERY).read_bytes()
        if _sha_bytes(discovery_raw) != INITIAL_DISCOVERY_SHA256:
            raise ValueError("initial preservation discovery digest changed")
        discovery = _unique_json(discovery_raw)
        files = discovery.get("preserved_files_sha256")
        if not isinstance(files, dict) or len(files) != 70:
            raise ValueError("initial archive manifest must contain 70 retained files")
        safe_keys = {_safe_rel(key).as_posix() for key in files}
        physical = set()
        for path in INITIAL_ARCHIVE.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlink in initial P117 archive")
            if path.is_file():
                physical.add(path.relative_to(INITIAL_ARCHIVE).as_posix())
            elif not path.is_dir():
                raise ValueError("non-regular entry in initial P117 archive")
        if physical != safe_keys | {"discovery.json"}:
            raise ValueError("initial P117 archive has missing or extra physical files")
        for relative, expected in files.items():
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("malformed retained-file digest")
            archived = _archive_file(relative)
            if _sha(archived) != expected:
                raise ValueError("preserved initial P117 file digest changed")
            evidence_hashes[f"results/failures/p117_initial/{relative}"] = expected

        def archived_json(relative: str) -> dict:
            value = _unique_json(_archive_file(relative).read_bytes())
            if not isinstance(value, dict):
                raise ValueError(f"archived object {relative} is malformed")
            return value

        initial_g0 = archived_json("results/g0_p117_twin_waste.json")
        initial_g1 = archived_json("results/g1_p117_twin_waste.json")
        initial_g2 = archived_json("results/g2_p117_twin_waste.json")
        initial_g3 = archived_json("results/g3_p117_quality.json")
        verdict = archived_json("results/p117_verdict.json")
        observation = archived_json("target/p117-initial-verdict-observed.json")
        if (discovery.get("schema") != "actinv-p117-initial-preservation-1"
                or discovery.get("checkpoint_commit") != INITIAL_CHECKPOINT
                or discovery.get("initial_verdict") != "P117-FAIL"
                or initial_g0.get("schema") != "actinv-p117-twin-waste-g0-1"
                or initial_g0.get("pass") is not True or initial_g0.get("repair_rounds") != 0
                or initial_g1.get("schema") != "actinv-p117-twin-waste-g1-1"
                or initial_g1.get("pass") is not True
                or initial_g2.get("schema") != "actinv-p117-twin-waste-g2-1"
                or initial_g2.get("pass") is not True
                or initial_g3.get("schema") != "actinv-p117-quality-1"
                or initial_g3.get("pass") is not True or initial_g3.get("repair_rounds") != 0
                or verdict.get("schema") != "actinv-p117-verdict-1"
                or verdict.get("phase") != "P117" or verdict.get("verdict") != "P117-FAIL"
                or verdict.get("gates") != discovery.get("verdict_gates")
                or verdict.get("gates", {}).get("G3_local") != "FAIL"
                or verdict.get("gates", {}).get("G3_CI") != "PENDING"
                or any(status != ("FAIL" if name == "G3_local" else "PENDING" if name == "G3_CI" else "PASS")
                       for name, status in verdict.get("gates", {}).items())
                or verdict.get("evidence_sha256") != {
                    "results/g0_p117_twin_waste.json": files["results/g0_p117_twin_waste.json"],
                    "results/g1_p117_twin_waste.json": files["results/g1_p117_twin_waste.json"],
                    "results/g2_p117_twin_waste.json": files["results/g2_p117_twin_waste.json"],
                    "results/g3_p117_quality.json": files["results/g3_p117_quality.json"]}
                or initial_g3.get("g0_sha256") != files["results/g0_p117_twin_waste.json"]
                or initial_g3.get("g1_sha256") != files["results/g1_p117_twin_waste.json"]
                or initial_g3.get("g2_sha256") != files["results/g2_p117_twin_waste.json"]
                or initial_g1.get("fresh_p117_report") != initial_g1.get("p116_report_preserved")
                or initial_g1.get("request_count") != 35
                or initial_g1.get("component_target_count") != 138
                or initial_g1.get("independent_comparison_count") != 138
                or len(initial_g3.get("fresh_gates", {})) != len(INITIAL_GATES)
                or set(initial_g3.get("fresh_gates", {})) != INITIAL_GATES):
            raise ValueError("initial P117 failure artifacts do not match the registered snapshot")

        observed = discovery.get("observed_exit")
        if (not isinstance(observed, dict) or observed.get("status") != "completed"
                or type(observed.get("exit_code")) is not int or observed.get("exit_code") != 1
                or observed.get("entry_point") != ["python3", "controls/check_p117_verdict.py", "--write"]
                or observation.get("schema") != "actinv-p117-initial-verdict-observation-1"
                or observation.get("status") != "completed" or type(observation.get("exit_code")) is not int
                or observation.get("exit_code") != 1 or observation.get("entry_point") != observed["entry_point"]
                or observation.get("argv") != observed.get("argv")
                or observation.get("inspection_errors") != []
                or observation.get("resources") != observed.get("resources")
                or not _archive_resource_ok(observed.get("resources"))):
            raise ValueError("initial verdict failure observation is invalid")
        raw_log = _archive_file("target/p117-initial-verdict.log").read_bytes()
        log_lines = raw_log.decode("utf-8").splitlines()
        inspection_lines = [line for line in log_lines if line.startswith('{"errors"')]
        verdict_starts = [index for index, line in enumerate(log_lines) if line == "{"]
        inspected = (_unique_json(inspection_lines[0].encode("utf-8"))
                     if len(inspection_lines) == 1 else None)
        if inspected != {"resources": observed["resources"], "errors": []}:
            raise ValueError("initial verdict raw log does not match observed resource inspection")
        verdict_log = (_unique_json("\n".join(log_lines[verdict_starts[0]:]).encode("utf-8"))
                       if len(verdict_starts) == 1 else None)
        if verdict_log != {"gates": verdict["gates"], "persisted_matches": True,
                           "verdict": "P117-FAIL"}:
            raise ValueError("initial verdict raw log does not match saved terminal failure")

        old_controls = discovery.get("sealed_g0_control_sha256")
        old_rust = discovery.get("sealed_g0_rust_sha256")
        if (not isinstance(old_controls, dict) or len(old_controls) != 121
                or initial_g0.get("control_sha256") != old_controls
                or not isinstance(old_rust, dict) or len(old_rust) != 100
                or initial_g0.get("inherited_rust_sha256") != old_rust
                or initial_g3.get("current_rust_sha256") != old_rust
                or initial_g3.get("source_sha256") != old_controls):
            raise ValueError("initial G0 source seal differs from discovery")
        checkpoint_paths = p116._rust_paths_at_commit(INITIAL_CHECKPOINT)
        if set(old_rust) != checkpoint_paths:
            raise ValueError("initial checkpoint Rust population differs")
        current_rust = p116._current_rust_source_hashes()
        if current_rust != old_rust:
            raise ValueError("current Rust sources differ from initial P117 checkpoint")
        for path, expected in old_rust.items():
            if _sha_bytes(p116._git_blob(INITIAL_CHECKPOINT, path)) != expected:
                raise ValueError("initial Rust blob differs from checkpoint")

        changed_sources = set()
        for path, expected in old_controls.items():
            _safe_rel(path)
            if _sha_bytes(p116._git_blob(INITIAL_CHECKPOINT, path)) != expected:
                raise ValueError("initial control source differs from checkpoint Git blob")
            if _sha(ROOT / path) != expected:
                changed_sources.add(path)
        if not changed_sources.issubset(REPAIRED_SOURCES):
            raise ValueError("a source outside the registered four-file repair changed")
        source_ok = True

        initial_handbook = initial_g0.get("public_handbook_sha256")
        handbook_paths = check_p112_verdict._handbook_paths(INITIAL_CHECKPOINT)
        handbook = {path: _sha_bytes(p116._git_blob(INITIAL_CHECKPOINT, path))
                    for path in sorted(handbook_paths)}
        if (initial_handbook != handbook
                or initial_g3.get("p116_adopted", {}).get("public_handbook_sha256") != handbook
                or any(_sha(ROOT / path) != expected for path, expected in handbook.items())):
            raise ValueError("initial handbook identity differs from checkpoint")

        archived_gates = initial_g3["fresh_gates"]
        for name in sorted(INITIAL_GATES):
            if _archived_gate_entry(name, initial_g3) is None:
                raise ValueError(f"initial observed gate receipt/log invalid: {name}")
        failure_ok = True
        initial = {
            "checkpoint_commit": INITIAL_CHECKPOINT, "verdict": "P117-FAIL",
            "verdict_sha256": files["results/p117_verdict.json"],
            "g0_sha256": files["results/g0_p117_twin_waste.json"],
            "g1_sha256": files["results/g1_p117_twin_waste.json"],
            "g2_sha256": files["results/g2_p117_twin_waste.json"],
            "g3_sha256": files["results/g3_p117_quality.json"],
            "verdict_exit_code": 1, "verdict_status": "completed",
            "verdict_entry_point": observed["entry_point"],
            "verdict_resources": observed["resources"],
            "verdict_log_sha256": files["target/p117-initial-verdict.log"],
            "verdict_gate_outcomes": verdict["gates"],
            "adopted_gate_names": sorted(ADOPTED_INITIAL_GATES),
            "rerun_gate_names": sorted(RERUN_GATES),
            "control_sha256": old_controls, "rust_sha256": old_rust,
            "handbook_sha256": handbook,
            "adopted_initial_gates": {name: archived_gates[name] for name in sorted(ADOPTED_INITIAL_GATES)},
        }
        archive_ok = True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError,
            ImportError, UnicodeError, json.JSONDecodeError):
        archive_ok = source_ok = failure_ok = False
    return {
        "pass": bool(amendment_registered and archive_ok and source_ok and failure_ok),
        "amendment_sha256": AMENDMENT_SHA256,
        "amendment_registered": bool(amendment_registered),
        "discovery_sha256": INITIAL_DISCOVERY_SHA256,
        "evidence_sha256": evidence_hashes if archive_ok else {},
        "archive_matches": bool(archive_ok), "source_matches": bool(source_ok),
        "initial_failure_matches": bool(failure_ok), "initial_failure": initial,
    }


def _adopted_initial_entry_matches(name: str, expected: object) -> bool:
    if name not in ADOPTED_INITIAL_GATES or not isinstance(expected, dict):
        return False
    try:
        _receipt, live_entry = _safe_receipt(name)
        archive_receipt = _archive_file(f"results/quality/p117/{name}.json").read_bytes()
        archive_log = _archive_file(f"results/quality/p117/{name}.log").read_bytes()
        archive_target_log = _archive_file(f"target/p117-{name}.log").read_bytes()
        live_receipt = _safe_file(f"results/quality/p117/{name}.json").read_bytes()
        live_log = _safe_file(f"results/quality/p117/{name}.log").read_bytes()
        live_target_log = _safe_file(f"target/p117-{name}.log").read_bytes()
        return (live_entry == expected and _sha_bytes(archive_receipt) == expected.get("receipt_sha256")
                and _sha_bytes(archive_log) == expected.get("log_sha256")
                and archive_log == archive_target_log == live_log == live_target_log
                and archive_receipt == live_receipt)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _prior_summary() -> dict:
    g0, g1, g2, g3, verdict = _p116_current_identity()
    return {
        "checkpoint_commit": IMPLEMENTATION_COMMIT,
        "g0_sha256": P116_G0_SHA256, "g1_sha256": P116_G1_SHA256,
        "g2_sha256": P116_G2_SHA256, "g3_sha256": P116_G3_SHA256,
        "verdict_sha256": P116_VERDICT_SHA256,
        "implementation_record_sha256": P116_IMPLEMENTATION_SHA256,
        "ci_record_sha256": P116_CI_SHA256,
        "ci_archive_discovery_sha256": P116_ARCHIVE_DISCOVERY_SHA256,
        "verdict": verdict["verdict"], "p116_g1_counts": {
            "requests": g1["request_count"], "component_targets": g1["component_target_count"],
            "comparisons": g1["independent_comparison_count"],
            "mutations": len(g1["mutations_rejected"]),
            "refusals": len(g1["refusal_controls"]["checks"]),
        },
        "p116_g3_gate_count": len(g3["gates"]),
    }


def _g0_base() -> dict:
    prior: dict = {}
    hashes: dict[str, str] = {}
    history = {"pass": False}
    registered = False
    authorities = False
    rust_ok = False
    rust_hashes: dict[str, str] = {}
    manifest: dict = {}
    predecessor_ok = False
    handbook_hashes: dict[str, str] = {}
    handbook_ok = False
    repair = _repair_evidence()
    try:
        prior = _prior_summary()
        registry = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
        registered = (f"{PROTOCOL_SHA256}  {PROTOCOL}" in registry
                      and _sha(ROOT / PROTOCOL) == PROTOCOL_SHA256)
        manifest = fetch_ci_seed.load_manifest()
        authorities = fetch_ci_seed.validate_authorities(manifest) is True
        history = check_p116_history.verify()
        rust_ok, hashes, rust_hashes = _source_population_ok(_read(P116_G0))
        handbook_hashes, handbook_ok = _handbook_snapshot()
        predecessor_ok = (history.get("pass") is True
                          and history.get("derived_verdict_matches_persisted") is True
                          and _canonical(_read(P116_VERDICT)) == P116_VERDICT.read_bytes())
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        predecessor_ok = False
    ok = (registered and authorities and rust_ok and handbook_ok and repair.get("pass") is True
          and history.get("pass") is True and predecessor_ok)
    report = {
        "schema": "actinv-p117-twin-waste-g0-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(ok),
        "protocol_registered": bool(registered), "seed_authorities_match": bool(authorities),
        "historical_p116_verified": history.get("pass") is True,
        "p116_history": history, "predecessor_p116_fail_preserved": bool(predecessor_ok),
        "prior_p116": prior, "control_sha256": hashes,
        "source_population_matches_p116_commit": bool(rust_ok),
        "inherited_rust_source_count": len(rust_hashes),
        "inherited_rust_sha256": rust_hashes,
        "public_handbook_sha256": handbook_hashes,
        "public_handbook_matches_p116": bool(handbook_ok),
        "manifest_sha256": _sha(ROOT / MANIFEST), "seed_file_count": len(manifest.get("files", [])) if isinstance(manifest, dict) else 0,
        "seed_total_bytes": manifest.get("total_bytes") if isinstance(manifest, dict) else None,
        "repair_rounds": 1 if repair.get("pass") is True else None,
        "repair_amendment_sha256": repair.get("amendment_sha256"),
        "repair_amendment_registered": repair.get("amendment_registered") is True,
        "repair_discovery_sha256": repair.get("discovery_sha256"),
        "repair_evidence_sha256": repair.get("evidence_sha256", {}),
        "repair_evidence_matches": repair.get("pass") is True,
        "initial_failure": repair.get("initial_failure", {}),
    }
    return report


def _persist(path: Path, value: dict, no_write: bool) -> tuple[bool, bytes]:
    raw = _canonical(value)
    if no_write:
        return path.is_file() and path.read_bytes() == raw, raw
    if os.path.lexists(path):
        return path.is_file() and not path.is_symlink() and path.read_bytes() == raw, raw
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True, raw


def g0(*, seal: bool = False, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    report = _g0_base()
    if no_write:
        same = G0.is_file() and G0.read_bytes() == _canonical(report)
    else:
        same, _raw = _persist(G0, report, False)
    passed = report["pass"] and same
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if passed else 1), report


def _replay_old(output_name: str) -> dict:
    report = p116._run_g1(no_write=False, output_name=output_name)
    return report


def _campaign(output_name: str) -> tuple[dict, bool]:
    original = _read(P116_G1)
    fresh = _replay_old(output_name)
    equal = (isinstance(original, dict) and fresh == original
             and _canonical(fresh) == P116_G1.read_bytes())
    return fresh, bool(equal)


def _sealed_g0_present() -> bool:
    sealed = _read(G0)
    if not isinstance(sealed, dict) or sealed.get("pass") is not True:
        return False
    try:
        current_hashes = _control_hashes()
        current_rust = p116._current_rust_source_hashes()
        handbook_hashes, handbook_ok = _handbook_snapshot()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False
    return (sealed.get("phase") == PHASE and sealed.get("protocol_sha256") == PROTOCOL_SHA256
            and sealed.get("control_sha256") == current_hashes
            and sealed.get("inherited_rust_sha256") == current_rust
            and handbook_ok is True
            and sealed.get("public_handbook_sha256") == handbook_hashes
            and sealed.get("source_population_matches_p116_commit") is True)


def _g1_report(fresh: dict, equal: bool) -> dict:
    original = _read(P116_G1)
    return {
        "schema": "actinv-p117-twin-waste-g1-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(equal),
        "p116_g1_sha256": P116_G1_SHA256, "p116_report_preserved": original,
        "fresh_p117_report": fresh, "exact_canonical_match": bool(equal),
        "request_count": fresh.get("request_count"),
        "component_target_count": fresh.get("component_target_count"),
        "independent_comparison_count": fresh.get("independent_comparison_count"),
        "mutations_rejected": fresh.get("mutations_rejected"),
        "refusal_controls": fresh.get("refusal_controls"),
        "repeat_byte_identical": fresh.get("repeat_byte_identical"),
        "failures": [] if equal else ["fresh P117 campaign differs from immutable P116 report"],
    }


def g1(*, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    sealed = _sealed_g0_present()
    fresh, equal = _campaign("p117-after-amendment-g1") if sealed else ({}, False)
    report = _g1_report(fresh, equal and sealed)
    if not sealed:
        report["failures"] = ["P117 G0 seal is missing or differs from current source identities"]
    if no_write:
        same = G1.is_file() and G1.read_bytes() == _canonical(report)
        report["pass"] = report["pass"] and same
    else:
        same, _ = _persist(G1, report, False)
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def g2(*, no_write: bool = False, quiet: bool = False) -> tuple[int, dict]:
    g0_code, _ = g0(no_write=True, quiet=True)
    replay_fresh, replay_equal = _campaign("p117-after-amendment-g2") if g0_code == 0 else ({}, False)
    old = _read(P116_G1)
    expected_g1 = _g1_report(replay_fresh, replay_equal and _sealed_g0_present())
    g1_same = G1.is_file() and G1.read_bytes() == _canonical(expected_g1)
    equal = (g0_code == 0 and replay_equal and replay_fresh == old and g1_same)
    report = {
        "schema": "actinv-p117-twin-waste-g2-1", "phase": PHASE,
        "protocol_sha256": PROTOCOL_SHA256, "pass": bool(equal),
        "g0_exact_replay_equal": g0_code == 0, "g1_exact_replay_equal": replay_equal and g1_same,
        "separate_output_paths_byte_identical": replay_fresh.get("repeat_byte_identical") is True,
        "p116_g1_sha256": P116_G1_SHA256,
        "g0_result_sha256": _sha(G0), "g1_result_sha256": _sha(G1),
        "fresh_g1_report": replay_fresh,
    }
    if no_write:
        same = G2.is_file() and G2.read_bytes() == _canonical(report)
        report["pass"] = report["pass"] and same
    else:
        same, _ = _persist(G2, report, False)
    if not quiet:
        print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def _safe_receipt(name: str) -> tuple[dict | None, dict | None]:
    receipt_rel = f"results/quality/p117/{name}.json"
    log_rel = f"results/quality/p117/{name}.log"
    receipt_path = _safe_file(receipt_rel)
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        return None, None
    copied_log = _safe_file(log_rel).read_bytes()
    if _sha_bytes(copied_log) != receipt.get("log_sha256"):
        return None, None
    timeout = receipt.get("timeout_s")
    max_timeout = 1200 if name in QUALITY_GATES else 600
    resources = receipt.get("resources")
    argv = receipt.get("argv")
    if (receipt.get("schema") != "actinv-roadmap-gate-receipt-1"
            or receipt.get("phase") != PHASE or receipt.get("gate") != name
            or receipt.get("status") != "completed" or type(receipt.get("child_exit_code")) is not int
            or receipt.get("child_exit_code") != 0 or receipt.get("cwd") != "."
            or receipt.get("log_path") != f"target/p117-{name}.log"
            or receipt.get("log_sha256") != _sha_bytes(copied_log)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or timeout <= 0 or timeout > max_timeout
            or receipt.get("error") is not None or not isinstance(argv, list) or not argv
            or any(not isinstance(item, str) or not item.strip() for item in argv)
            or run_p117_gate._has_placeholder(argv)
            or not isinstance(resources, dict)):
        return None, None
    limits = resources.get("cgroup_limits")
    cgroup_path = resources.get("cgroup_path")
    path_parts = cgroup_path.split("/") if isinstance(cgroup_path, str) else []
    if (resources.get("platform") != "linux" or not isinstance(cgroup_path, str)
            or not cgroup_path.startswith("/user.slice/") or not cgroup_path.endswith(".scope")
            or any(part in {".", ".."} for part in path_parts)
            or any(not part for part in path_parts[1:])
            or limits != {"memory.max": "6442450944", "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
            or resources.get("environment") != {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"}
            or not isinstance(resources.get("tmpdir"), dict)
            or resources["tmpdir"].get("path") != "target/preflight-tmp"
            or not isinstance(resources["tmpdir"].get("filesystem"), str)
            or not resources["tmpdir"]["filesystem"].strip()
            or resources["tmpdir"]["filesystem"].lower() in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"}):
        return None, None
    entry = {
        "name": name, "receipt_path": receipt_rel, "receipt_sha256": _sha_bytes(raw),
        "log_path": log_rel, "log_sha256": receipt["log_sha256"], "argv": receipt["argv"],
        "exit_code": receipt["child_exit_code"], "resources": resources,
    }
    return receipt, entry


def quality(*, no_write: bool = False) -> tuple[int, dict]:
    prior_g3 = _read(P116_G3)
    history = check_p116_history.verify()
    repair = _repair_evidence()
    initial_failure = repair.get("initial_failure", {})
    adopted_initial = initial_failure.get("adopted_initial_gates", {}) if isinstance(initial_failure, dict) else {}
    adopted = prior_g3.get("gates") if isinstance(prior_g3, dict) else None
    if not isinstance(adopted, dict) or len(adopted) != 32:
        adopted = {}
    fresh: dict[str, dict] = {}
    failures = []
    if (not isinstance(adopted_initial, dict)
            or set(adopted_initial) != ADOPTED_INITIAL_GATES):
        failures.append("initial adopted P117 gate population is incomplete")
    else:
        for name in sorted(ADOPTED_INITIAL_GATES):
            if not _adopted_initial_entry_matches(name, adopted_initial[name]):
                failures.append(f"initial adopted P117 observation changed: {name}")
    for name in sorted(RERUN_GATES):
        try:
            _receipt, entry = _safe_receipt(name)
            if entry is not None:
                source_log = _safe_file(f"target/p117-{name}.log").read_bytes()
                copied_log = _safe_file(f"results/quality/p117/{name}.log").read_bytes()
                if source_log != copied_log or _sha_bytes(source_log) != entry["log_sha256"]:
                    entry = None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            entry = None
        if entry is None:
            failures.append(f"invalid or missing receipt/log for {name}")
        else:
            fresh[name] = entry
    source_ok, source_hashes, current_rust = _source_population_ok(_read(P116_G0))
    try:
        commit_paths = p116._rust_paths_at_commit(IMPLEMENTATION_COMMIT)
        rust_map = _read(P116_G3).get("production_rust_sha256")
        rust_git_ok = (isinstance(rust_map, dict) and len(rust_map) == 100
                       and set(rust_map) == commit_paths
                       and all(p116._sha_bytes(p116._git_blob(IMPLEMENTATION_COMMIT, path)) == digest
                               for path, digest in rust_map.items()))
        handbook_paths = check_p112_verdict._handbook_paths(IMPLEMENTATION_COMMIT)
        handbook = {path: p116._sha_bytes(p116._git_blob(IMPLEMENTATION_COMMIT, path))
                    for path in sorted(handbook_paths)}
        handbook_current = (set(check_p112_verdict._handbook_paths(None)) == set(handbook)
                            and all(_sha(ROOT / path) == digest for path, digest in handbook.items()))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        rust_git_ok = handbook_current = False
        rust_map = {}
        handbook = {}
    p116_quality_ok = (isinstance(prior_g3, dict) and check_p116_verdict._quality_ok(prior_g3, _read(P116_G0))
                       and prior_g3.get("pass") is True)
    release = prior_g3.get("release_build") if isinstance(prior_g3, dict) else {}
    g0_report, g1_report, g2_report = _read(G0), _read(G1), _read(G2)
    p117_science_ok = (
        isinstance(g0_report, dict) and g0_report.get("pass") is True
        and isinstance(g1_report, dict) and g1_report.get("pass") is True
        and g1_report.get("fresh_p117_report") == _read(P116_G1)
        and isinstance(g2_report, dict) and g2_report.get("pass") is True
        and g2_report.get("g0_result_sha256") == _sha(G0)
        and g2_report.get("g1_result_sha256") == _sha(G1)
    )
    report = {
        "schema": "actinv-p117-quality-1", "phase": PHASE,
        "pass": bool(not failures and len(adopted) == 32 and len(adopted_initial) == len(ADOPTED_INITIAL_GATES)
                      and set(adopted_initial) == ADOPTED_INITIAL_GATES
                      and repair.get("pass") is True and p116_quality_ok and history.get("pass") is True
                      and source_ok and rust_git_ok and handbook_current
                      and p117_science_ok
                      and isinstance(release, dict) and _sha(ACTINV) == release.get("binary_sha256")),
        "repair_rounds": 1 if repair.get("pass") is True else None,
        "repair_amendment_sha256": repair.get("amendment_sha256"),
        "repair_amendment_registered": repair.get("amendment_registered") is True,
        "repair_discovery_sha256": repair.get("discovery_sha256"),
        "repair_evidence_sha256": repair.get("evidence_sha256", {}),
        "repair_evidence_matches": repair.get("pass") is True,
        "initial_failure": initial_failure,
        "adopted_initial_gates": adopted_initial,
        "adopted_initial_gate_names": sorted(adopted_initial) if isinstance(adopted_initial, dict) else [],
        "rerun_gate_names": sorted(RERUN_GATES),
        "resource_limits": RESOURCES,
        "g0_sha256": _sha(G0), "g1_sha256": _sha(G1), "g2_sha256": _sha(G2),
        "p116_adopted": {
            "quality_sha256": P116_G3_SHA256, "implementation_commit": IMPLEMENTATION_COMMIT,
            "verdict_sha256": P116_VERDICT_SHA256, "gates": adopted,
            "gate_names": sorted(adopted), "successful_gate_count": len(adopted),
            "workspace_tests": prior_g3.get("workspace_tests") if isinstance(prior_g3, dict) else None,
            "release_build": release, "production_rust_sha256": rust_map,
            "public_handbook_sha256": handbook,
            "quality_validated": bool(p116_quality_ok), "not_rerun": True,
        },
        "adopted_p116_gates": adopted,
        "fresh_gates": fresh, "fresh_gate_names": sorted(fresh),
        "p116_history_verified": history.get("pass") is True,
        "current_rust_matches_p116": bool(rust_git_ok and source_ok),
        "current_rust_sha256": current_rust,
        "source_sha256": source_hashes,
        "p117_science_evidence_matches": bool(p117_science_ok),
        "public_handbook_matches_p116": bool(handbook_current),
        "qualified_binary_sha256": _sha(ACTINV),
        "qualified_binary_matches_p116": bool(
            isinstance(release, dict) and _sha(ACTINV) == release.get("binary_sha256")),
        "failures": failures,
    }
    same, _ = _persist(G3, report, no_write)
    if no_write:
        report["pass"] = report["pass"] and same
    print(json.dumps({**report, "persisted_result_matches": same}, sort_keys=True, indent=2))
    return (0 if report["pass"] and same else 1), report


def _g0_replay_ok() -> bool:
    with contextlib.redirect_stdout(io.StringIO()):
        return g0(no_write=True, quiet=True)[0] == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--g0-only", action="store_true")
    group.add_argument("--g1-only", action="store_true")
    group.add_argument("--g2-only", action="store_true")
    group.add_argument("--quality", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if args.quality and (args.g0_only or args.g1_only or args.g2_only):
        parser.error("--quality cannot be combined with a single-gate selector")
    if args.quality:
        return quality(no_write=args.no_write)[0]
    if args.g0_only:
        return g0(seal=not args.no_write, no_write=args.no_write)[0]
    if args.g1_only:
        return g1(no_write=args.no_write)[0]
    if args.g2_only:
        return g2(no_write=args.no_write)[0]
    if args.no_write:
        # The replay path performs all stable comparisons first; output is delayed
        # until after both G0 and the complete G1 campaign have been checked.
        g0_ok = _g0_replay_ok()
        if g0_ok:
            fresh, g1_ok = _campaign("p117-after-amendment-full-replay")
            g1_report = _g1_report(fresh, g1_ok and _sealed_g0_present())
            g1_ok = (g1_ok and fresh == _read(P116_G1) and G1.is_file()
                     and G1.read_bytes() == _canonical(g1_report))
        else:
            fresh, g1_ok = {}, False
        expected_g2 = {
            "schema": "actinv-p117-twin-waste-g2-1", "phase": PHASE,
            "protocol_sha256": PROTOCOL_SHA256,
            "pass": bool(g0_ok and g1_ok),
            "g0_exact_replay_equal": bool(g0_ok),
            "g1_exact_replay_equal": bool(g1_ok),
            "separate_output_paths_byte_identical": fresh.get("repeat_byte_identical") is True,
            "p116_g1_sha256": P116_G1_SHA256,
            "g0_result_sha256": _sha(G0), "g1_result_sha256": _sha(G1),
            "fresh_g1_report": fresh,
        }
        g2_ok = G2.is_file() and G2.read_bytes() == _canonical(expected_g2)
        passed = g0_ok and g1_ok and g2_ok
        result = {"schema": "actinv-p117-full-replay-1", "phase": PHASE,
                  "pass": bool(passed), "g0_exact_replay_equal": bool(g0_ok),
                  "g1_exact_replay_equal": bool(g1_ok), "g2_exact_replay_equal": bool(g2_ok),
                  "p116_g1_sha256": P116_G1_SHA256,
                  "request_count": fresh.get("request_count"),
                  "component_target_count": fresh.get("component_target_count"),
                  "mutations_rejected": fresh.get("mutations_rejected"),
                  "refusal_controls": fresh.get("refusal_controls"),
                  "repeat_byte_identical": fresh.get("repeat_byte_identical")}
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if passed else 1
    code0, _ = g0(seal=True)
    if code0 != 0:
        return 1
    code1, _ = g1()
    if code1 != 0:
        return 1
    return g2()[0]


if __name__ == "__main__":
    raise SystemExit(main())
