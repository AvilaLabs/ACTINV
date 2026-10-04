#!/usr/bin/env python3
"""Independent P116 controls for optional nominal waste output in `actinv twin`.

This checker never launches work itself except through the reviewed bounded
P105 child runner. G0 and all frozen expected records use synthetic inputs.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import check_p105
import p105_budget_control as bounded
import p113_twin_control as oracle
import p113_legacy_control as legacy

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P116_PROTOCOL.md"
PROTOCOL_SHA256 = "cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa"
P116_CHECKPOINT = "6db45f75b96fbcdfd2bfda0f6a603c10fbf88672"
P116_AMENDMENT = ROOT / "protocols/ACTINV-P116_AMENDMENT_A.md"
P116_AMENDMENT_SHA256 = "3fa71d7c7537dbe9132f5de8d9f4a64d33126f065f4f41aef7496fab38be372c"
P116_DISCOVERY = ROOT / "results/failures/p116_initial/discovery.json"
P116_DISCOVERY_SHA256 = "15f73cb664ff13a4ca75d7edcf098d2ee2ce557ff00f9d95cb23c366b612e441"
P116_ORIGINAL_CHECKPOINT = "cb786f75acc456cb0157c6d5f38d9b6308140d85"
P116_ORIGINAL_G0_SHA256 = "2b1b8cbaad1073be4b16e5d21f284d41702ad2c0b53a36e6ee7b7f4e3e32af93"
P116_ORIGINAL_G1_SHA256 = "75e94554ac5d9d909e5eac59bd676c649db685eb52ffbba8a2340d5603251c21"
P116_ORIGINAL_G1_LOG_SHA256 = "a836211e55cbd461718935edf88e91b07f9ecd572007f9eadd0028623a15cde2"
PRODUCTION_CHANGE_PATH = "crates/actinv-cli/src/twin_waste.rs"
P115_HISTORY_RECORD = ROOT / "results/p115_failure_commit.json"
P115_PROTOCOL_SHA256 = "266dd88898b2e64b77c517d18a221576bf9d1c6fda107ccdf62c45e0321f95ba"
P115_VERDICT_SHA256 = "4e09b66d5b1c74cafecdc278d9d5ce786292007a7dea0ab6e8c8588f7255faac"
P115_FINAL_G3_SHA256 = "9f48cde1106ebc7174961aa594fbf57e5c772c3aeed3a06a133558a6946aa45d"
P115_AMENDED_FAILURE_LOG_SHA256 = "b5f4ed19ca056a563cda1b3fe5f11a542c9e6edc788d8524766c543aaa870c6c"
P115_AMENDMENT_A = ROOT / "protocols/ACTINV-P115_AMENDMENT_A.md"
P115_AMENDMENT_A_SHA256 = "d2c6543d4a420e50b8697538411a163542e16d4fcb3fd3d2ff61b75af86170b3"
P115_INITIAL_CHECKPOINT = "887b28d94f32342ea2e3b3410dbb0f0e2aaf2a14"
P115_DISCOVERY = ROOT / "results/failures/p115_initial/discovery.json"
P115_DISCOVERY_SHA256 = "22f03d9115f7c2ef5b4c98d19d9fe1cce85e62c38747036f1e130e4c711632db"
P115_ARCHIVE_PREFIX = "results/failures/p115_initial/"
P115_FAILURE_LOG_SHA256 = "51a89379d6fde40f857f9f5b499d79f571b220976ac667ffb420bbf7bcc4945c"
P115_SOURCE_FILE_COUNT = 180
P115_RUST_SOURCE_COUNT = 100
P115_RETAINED_FILE_COUNT = 68
P115_OBSERVED_GATE_COUNT = 22
P115_SUCCESSFUL_GATE_COUNT = 21
INHERITED_PROTOCOL = ROOT / "protocols/ACTINV-P113_PROTOCOL.md"
INHERITED_PROTOCOL_SHA256 = "0495157f3e8308d6a28475b94363e24b932f462c10f901518fa1c809a68ebbfa"
AMENDMENT = ROOT / "protocols/ACTINV-P113_AMENDMENT_A.md"
AMENDMENT_SHA256 = "6a0191ccc3500ebaeb3165a2b630d0c857d18335ce8fe70dcf07d9d7445ac46c"
DISCOVERY = ROOT / "results/failures/p113_initial_seal/discovery.json"
DISCOVERY_SHA256 = "0f099f92637eea2e3346f02be0d588f9f6d96c95cdd41aae0d7050b1fbd36c4f"
INITIAL_G0_SHA256 = "61d8c6faaf7eb93d8f3a4d9e369e8710eee45b93f946d405f54c216dd730a037"
FIXTURE_SHA256 = "e6bcebc856c7faaa571455a45c81da49e988a34bfd1f7149bc67656d93d5f956"
P113_FAILURE_VERDICT_SHA256 = "ae000348b4e6c58eb217d1ce9c5e9946fcf7b440562835855f724f6a1650e461"
P113_PARTIAL_G3_SHA256 = "529fb6b0f5c1542165300240a2d59cd2cd6ad9fa84c11d4f0748d48b72da2b01"
P113_CHECKPOINT = "81db5c03eeb876a88b6ddd7a2d2b8975308178e5"
P114_CHECKPOINT = "88e9251a6c7a61db2293ee9772457e4f51842253"
P114_FAILURE_VERDICT_SHA256 = "3d9208cae1ac6522e4b2c3d8c21440ecb4bc481f02faa46fb88f4068151bd382"
P114_FAILURE_G3_SHA256 = "053cc816a07e97bb9831cdcf84f96a1d5d8f2f263219b87d5a10f4fb99813fbb"
P114_AMENDMENT_SHA256 = "b320e9fe34dfa34c5bae88a10b7486f2618b41ac124790f5ac984e4332ab5c9e"
P114_INITIAL_DISCOVERY_SHA256 = "ac3f2638af88dedb56472ecbeda835efbf8c339316ece680867471043de87c7a"
P114_HISTORY_RECORD = ROOT / "results/p114_failure_commit.json"
INITIAL_LOGS = (
    "target/p113-g0-identities.log", "target/p113-g0-replay.log", "target/p113-g0-seal.log",
    "target/p113-oracle-regressions.log", "target/p113-p105-child-lifecycle-regressions.log",
    "target/p113-prepare-fixture.log", "target/p113-release-build.log", "target/p113-resource-limits.log",
    "target/p113-seal-regressions.log", "target/p113-verdict-regressions.log",
    "target/p113-workspace-check-final.log", "target/p113-workspace-check.log",
    "target/p113-workspace-clippy.log", "target/p113-workspace-fmt-final.log",
    "target/p113-workspace-fmt.log", "target/p113-workspace-tests.log",
)
PACK = ROOT / "data/waste_us_nrc_61_55_v1.json"
MIRROR = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
FIXTURE = ROOT / "controls/fixtures/p113/cases.json"
G0 = ROOT / "results/g0_p116_twin_waste.json"
G1 = ROOT / "results/g1_p116_twin_waste.json"
G2 = ROOT / "results/g2_p116_twin_waste.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
WORK = ROOT / "target/p116-controls"
CLASS_NAMES = ("A", "B", "C", "above_class_c", "unknown")

CONTROL_FILES = (
    "protocols/ACTINV-P116_PROTOCOL.md",
    "protocols/ACTINV-P116_AMENDMENT_A.md",
    "results/failures/p116_initial/discovery.json",
    "controls/check_p116.py", "controls/check_p116_verdict.py",
    "controls/test_p116_oracle.py", "controls/test_p116_seal.py", "controls/test_p116_verdict.py",
    "controls/p116_repair_control.py", "controls/test_p116_repair.py",
    "controls/check_p107_history.py",
    "controls/check_p115_history.py", "controls/test_p115_history.py",
    "results/p115_failure_commit.json", "results/p115_verdict.json", "results/g3_p115_quality.json",
    "results/failures/p115_initial/discovery.json",
    "results/failures/p115_initial/results/quality/p115/p115_verdict_regressions.json",
    "results/failures/p115_initial/target/p115-p115_verdict_regressions.log",
    "results/failures/p115_after_amendment/results/quality/p115/p115_verdict_regressions.json",
    "results/failures/p115_after_amendment/target/p115-p115_verdict_regressions.log",
    "scripts/run_p116_gate.py", "scripts/test_run_p116_gate.py",
    "protocols/ACTINV-P115_PROTOCOL.md",
    "protocols/ACTINV-P115_AMENDMENT_A.md",
    "protocols/ACTINV-P114_PROTOCOL.md",
    "protocols/ACTINV-P114_AMENDMENT_A.md",
    "controls/check_p115.py", "controls/check_p115_verdict.py", "controls/test_p115_oracle.py",
    "controls/test_p115_seal.py", "controls/test_p115_verdict.py",
    "protocols/ACTINV-P113_PROTOCOL.md",
    "protocols/ACTINV-P113_AMENDMENT_A.md",
    "controls/check_p114.py", "controls/check_p114_verdict.py", "controls/test_p114_verdict.py",
    "controls/check_p113.py", "controls/p113_twin_control.py", "controls/p113_legacy_control.py",
    "controls/test_p113_oracle.py", "controls/test_p113_seal.py",
    "controls/check_p113_verdict.py", "controls/test_p113_verdict.py",
    "controls/check_p113_history.py", "controls/test_p113_history.py",
    "controls/test_p114_oracle.py", "controls/test_p114_seal.py",
    "scripts/run_roadmap_gate.py", "scripts/test_run_roadmap_gate.py",
    "controls/fixtures/p113/cases.json",
    "data/waste_us_nrc_61_55_v1.json", "crates/actinv-core/data/waste_us_nrc_61_55_v1.json",
    "controls/check_p105.py", "controls/check_p105_verdict.py", "controls/p105_budget_control.py",
    "controls/p103_vector_fixture.py", "controls/fixtures/p103/classification_vectors.json",
    "controls/fixtures/p103/61.55_2025.xml", "docs/maintainers/WASTE_RULE_SOURCE_REVIEW.md",
    "results/g0_p103_seals.json", "results/g0b_p103_vectors.json",
    "results/g0_p105_successor.json", "results/g1_p105_classes.json",
    "results/g2_p105_budget.json", "results/g3_p105_quality.json",
    "results/p105_ci_runs.json", "results/p105_verdict.json",
    "controls/check_p112.py", "controls/check_p112_verdict.py",
    "controls/p111_intrusion_control.py", "controls/check_p111_history.py",
    "results/g0_p112_intrusion_screen.json", "results/g1_p112_intrusion_screen.json",
    "results/g2_p112_intrusion_screen.json", "results/g3_p112_quality.json",
    "results/p112_ci_runs.json", "results/p112_implementation_commit.json", "results/p112_verdict.json",
    "results/p107_verdict.json", "results/p108_verdict.json", "results/p109_verdict.json",
    "results/p110_verdict.json", "results/p111_verdict.json",
    "results/g0_p113_twin_waste.json",
    "results/p113_verdict.json", "results/g3_p113_quality.json",
    "results/failures/p113_after_amendment/invocation.json",
    "results/failures/p113_after_amendment/target/p113-workspace-tests-final-confirmed.scope.log",
    "results/p113_failure_commit.json",
    "results/failures/p113_initial_seal/discovery.json",
    "controls/check_p114_history.py", "controls/test_p114_history.py",
    "results/p114_failure_commit.json", "results/p114_verdict.json", "results/g3_p114_quality.json",
    "results/failures/p114_initial/discovery.json",
    "scripts/run_p115_gate.py", "scripts/test_run_p115_gate.py",
)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _registered() -> bool:
    registry = ROOT / "protocols/protocol_hash.txt"
    want = f"{PROTOCOL_SHA256}  protocols/ACTINV-P116_PROTOCOL.md"
    try:
        return _sha(PROTOCOL) == PROTOCOL_SHA256 and want in registry.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False


def _inherited_registered() -> bool:
    registry = ROOT / "protocols/protocol_hash.txt"
    want = f"{INHERITED_PROTOCOL_SHA256}  protocols/ACTINV-P113_PROTOCOL.md"
    try:
        return (_sha(INHERITED_PROTOCOL) == INHERITED_PROTOCOL_SHA256
                and want in registry.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def _amendment_registered() -> bool:
    registry = ROOT / "protocols/protocol_hash.txt"
    want = f"{AMENDMENT_SHA256}  protocols/ACTINV-P113_AMENDMENT_A.md"
    try:
        return _sha(AMENDMENT) == AMENDMENT_SHA256 and want in registry.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False


def _repair_evidence() -> tuple[dict[str, str | None], bool]:
    """Verify the discovery record and all 59 byte-preserved initial-seal files."""
    discovery = _read_json(DISCOVERY)
    retained = discovery.get("retained_sha256") if isinstance(discovery, dict) else None
    if (_sha(DISCOVERY) != DISCOVERY_SHA256 or not isinstance(retained, dict)
            or len(retained) != 59 or discovery.get("schema") != "actinv-p113-static-control-discovery-1"
            or discovery.get("initial_g0_sha256") != INITIAL_G0_SHA256
            or discovery.get("initial_fixture_sha256") != FIXTURE_SHA256
            or discovery.get("g1_executed") is not False or discovery.get("g2_executed") is not False
            or discovery.get("failed_gate") is not None):
        return {}, False
    observed: dict[str, str | None] = {}
    archive_root = ROOT / "results/failures/p113_initial_seal"
    safe_root = ROOT.resolve(strict=True)
    old_g0 = _read_json(archive_root / "results/g0_p113_twin_waste.json")
    original_controls = old_g0.get("control_sha256") if isinstance(old_g0, dict) else None
    if not isinstance(original_controls, dict) or not original_controls:
        return {}, False
    archived_control_paths = {f"results/failures/p113_initial_seal/{name}" for name in original_controls}
    archive_prefix = "results/failures/p113_initial_seal/"
    archived_logs = {f"{archive_prefix}{name}" for name in INITIAL_LOGS}
    archived_g0 = f"{archive_prefix}results/g0_p113_twin_waste.json"
    expected_paths = archived_control_paths | archived_logs | {archived_g0}
    if len(expected_paths) != 59 or set(retained) != expected_paths:
        return {}, False
    for relative, expected in retained.items():
        if (not isinstance(relative, str) or not relative.startswith("results/failures/p113_initial_seal/")
                or ".." in Path(relative).parts or not isinstance(expected, str)):
            return {}, False
        path = ROOT / relative
        try:
            path.resolve(strict=True).relative_to(safe_root)
        except (OSError, ValueError):
            return {}, False
        cursor = ROOT
        if any((cursor := cursor / part).is_symlink() for part in Path(relative).parts):
            return {}, False
        if not path.is_file():
            return {}, False
        observed[relative] = _sha(path)
        if observed[relative] != expected:
            return observed, False
    initial_g0 = archive_root / "results/g0_p113_twin_waste.json"
    archived_fixture = archive_root / "controls/fixtures/p113/cases.json"
    controls_match = all(observed.get(f"results/failures/p113_initial_seal/{name}") == digest
                         for name, digest in original_controls.items())
    matches = (_sha(initial_g0) == INITIAL_G0_SHA256 and _sha(archived_fixture) == FIXTURE_SHA256
               and _sha(FIXTURE) == FIXTURE_SHA256 and controls_match)
    return observed, bool(matches)


def _safe_control_hashes(hashes: object) -> bool:
    if not isinstance(hashes, dict) or set(hashes) != set(CONTROL_FILES):
        return False
    root = ROOT.resolve(strict=True)
    for relative, digest in hashes.items():
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            return False
        path = ROOT / relative
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            return False
        cursor = ROOT
        if any((cursor := cursor / part).is_symlink() for part in Path(relative).parts):
            return False
        if not path.is_file() or not isinstance(digest, str) or _sha(path) != digest:
            return False
    return True


def _p115_repair_evidence() -> tuple[dict[str, str], dict[str, str], bool]:
    """Verify P115's registered one-round failure archive and checkpoint blobs."""
    discovery = _read_json(P115_DISCOVERY)
    if (P115_DISCOVERY.is_symlink() or not P115_DISCOVERY.is_file()
            or _sha(P115_DISCOVERY) != P115_DISCOVERY_SHA256
            or not isinstance(discovery, dict)
            or discovery.get("schema") != "actinv-p115-initial-failure-1"
            or discovery.get("phase") != "P115"
            or discovery.get("gate") != "p115_verdict_regressions"
            or type(discovery.get("exit_code")) is not int or discovery.get("exit_code") != 1
            or type(discovery.get("failed_gate_count")) is not int or discovery.get("failed_gate_count") != 1
            or type(discovery.get("source_file_count")) is not int
            or discovery.get("source_file_count") != P115_SOURCE_FILE_COUNT
            or type(discovery.get("rust_source_file_count")) is not int
            or discovery.get("rust_source_file_count") != P115_RUST_SOURCE_COUNT
            or type(discovery.get("retained_file_count")) is not int
            or discovery.get("retained_file_count") != P115_RETAINED_FILE_COUNT
            or type(discovery.get("observed_test_count")) is not int
            or discovery.get("observed_test_count") != 8
            or type(discovery.get("observed_error_count")) is not int
            or discovery.get("observed_error_count") != 1
            or discovery.get("g0_sealed") is not False
            or discovery.get("g1_executed") is not False
            or discovery.get("g2_executed") is not False):
        return {}, {}, False
    source_map = discovery.get("source_sha256")
    retained_map = discovery.get("files_sha256")
    gate_codes = discovery.get("observed_gate_exit_codes")
    if (not isinstance(source_map, dict) or len(source_map) != P115_SOURCE_FILE_COUNT
            or not isinstance(retained_map, dict) or len(retained_map) != P115_RETAINED_FILE_COUNT
            or not isinstance(gate_codes, dict) or len(gate_codes) != P115_OBSERVED_GATE_COUNT
            or type(sum(1 for value in gate_codes.values() if type(value) is int and value == 0)) is not int
            or sum(1 for value in gate_codes.values() if type(value) is int and value == 0) != P115_SUCCESSFUL_GATE_COUNT
            or any(type(value) is not int for value in gate_codes.values())
            or gate_codes.get("p115_verdict_regressions") != 1
            or sum(1 for value in gate_codes.values() if value == 1) != 1):
        return {}, {}, False

    safe_root = ROOT.resolve(strict=True)
    observed_sources: dict[str, str] = {}
    rust_sources: dict[str, str] = {}
    for relative, expected in source_map.items():
        if (not isinstance(relative, str) or relative.startswith("/")
                or ".." in Path(relative).parts or Path(relative).as_posix() != relative
                or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            return {}, {}, False
        try:
            raw = _git_blob(P115_INITIAL_CHECKPOINT, relative)
        except (OSError, ValueError, RuntimeError, KeyError):
            return {}, {}, False
        actual = _sha_bytes(raw)
        if actual != expected:
            return observed_sources, {}, False
        observed_sources[relative] = actual
        if relative.startswith("crates/") and relative.endswith(".rs"):
            rust_sources[relative] = actual
    if len(rust_sources) != P115_RUST_SOURCE_COUNT:
        return observed_sources, {}, False
    # The archived source map is verified against the pinned Git checkpoint
    # here. Fresh G0 independently compares the live Rust population to that
    # checkpoint; keeping that check outside this history helper lets later
    # verdict derivation validate the sealed historical evidence.

    observed_files: dict[str, str] = {}
    expected_physical = set(retained_map) | {P115_DISCOVERY.relative_to(ROOT).as_posix()}
    archive_root = ROOT / "results/failures/p115_initial"
    if archive_root.is_symlink() or not archive_root.is_dir():
        return observed_sources, {}, False
    physical: set[str] = set()
    for directory, directories, filenames in os.walk(archive_root, followlinks=False):
        base = Path(directory)
        for name in directories:
            if (base / name).is_symlink():
                return observed_sources, {}, False
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                return observed_sources, {}, False
            physical.add(path.relative_to(ROOT).as_posix())
    if physical != expected_physical:
        return observed_sources, {}, False
    for relative, expected in retained_map.items():
        if (not isinstance(relative, str) or not relative.startswith(P115_ARCHIVE_PREFIX)
                or relative.startswith("/") or ".." in Path(relative).parts
                or Path(relative).as_posix() != relative
                or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            return observed_sources, observed_files, False
        path = ROOT / relative
        try:
            path.resolve(strict=True).relative_to(safe_root)
        except (OSError, ValueError):
            return observed_sources, observed_files, False
        cursor = ROOT
        if any((cursor := cursor / part).is_symlink() for part in Path(relative).parts):
            return observed_sources, observed_files, False
        if not path.is_file():
            return observed_sources, observed_files, False
        current = _sha(path)
        try:
            git_current = _sha_bytes(_git_blob(P115_INITIAL_CHECKPOINT, relative))
        except (OSError, ValueError, RuntimeError, KeyError):
            return observed_sources, observed_files, False
        observed_files[relative] = current
        if current != expected or git_current != expected:
            return observed_sources, observed_files, False

    receipt_rel = P115_ARCHIVE_PREFIX + "results/quality/p115/p115_verdict_regressions.json"
    log_rel = P115_ARCHIVE_PREFIX + "target/p115-p115_verdict_regressions.log"
    receipt = _read_json(ROOT / receipt_rel)
    receipt_ok = (isinstance(receipt, dict)
        and receipt.get("schema") == "actinv-roadmap-gate-receipt-1"
        and receipt.get("phase") == "P115" and receipt.get("gate") == "p115_verdict_regressions"
        and receipt.get("argv") == ["python3", "controls/test_p115_verdict.py"]
        and receipt.get("cwd") == "." and receipt.get("status") == "child_failed"
        and type(receipt.get("child_exit_code")) is int and receipt.get("child_exit_code") == 1
        and receipt.get("log_path") == "target/p115-p115_verdict_regressions.log"
        and receipt.get("log_sha256") == P115_FAILURE_LOG_SHA256
        and observed_files.get(log_rel) == P115_FAILURE_LOG_SHA256
        and observed_files.get(receipt_rel) == _sha(ROOT / receipt_rel))
    return observed_sources, observed_files, bool(receipt_ok)


def _p116_amendment_registered() -> bool:
    registry = ROOT / "protocols/protocol_hash.txt"
    expected = f"{P116_AMENDMENT_SHA256}  protocols/ACTINV-P116_AMENDMENT_A.md"
    try:
        return (_sha(P116_AMENDMENT) == P116_AMENDMENT_SHA256
                and expected in registry.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def _p116_repair_evidence() -> dict:
    try:
        import p116_repair_control
        return p116_repair_control.verify(ROOT)
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return {"pass": False}


def _repair_policy(g0: object, repair_evidence: object | None = None) -> bool:
    if (not isinstance(g0, dict) or type(g0.get("repair_rounds")) is not int
            or g0.get("repair_rounds") != 1 or g0.get("historical_p115_verified") is not True
            or g0.get("repair_amendment_sha256") != P116_AMENDMENT_SHA256
            or g0.get("repair_amendment_registered") is not True or not _p116_amendment_registered()):
        return False
    current = repair_evidence if isinstance(repair_evidence, dict) else _p116_repair_evidence()
    recorded = g0.get("p116_repair_evidence")
    if not isinstance(current, dict) or current.get("pass") is not True:
        return False
    try:
        return _json_bytes(current) == _json_bytes(recorded)
    except (TypeError, ValueError, OverflowError):
        return False


def _current_round_is_zero() -> bool:
    """Historical opening-state check; the live registered P116 round is one."""
    amendment_path = ROOT / "protocols/ACTINV-P116_AMENDMENT_A.md"
    failure_archive = ROOT / "results/failures/p116_initial"
    registry = ROOT / "protocols/protocol_hash.txt"
    line = "  protocols/ACTINV-P116_AMENDMENT_A.md"
    try:
        return (not amendment_path.exists() and not amendment_path.is_symlink()
                and not failure_archive.exists() and not failure_archive.is_symlink()
                and not any(row.endswith(line) for row in registry.read_text(encoding="utf-8").splitlines()))
    except OSError:
        return False


def _prior_verdicts() -> tuple[dict, bool, bool, bool, bool]:
    p105_ok = p112_ok = p113_history_ok = True
    p105_path = ROOT / "results/p105_verdict.json"
    p112_path = ROOT / "results/p112_verdict.json"
    p105 = _read_json(p105_path)
    p112 = _read_json(p112_path)
    try:
        import check_p105_verdict
        with contextlib.redirect_stdout(io.StringIO()):
            p105_ok = check_p105_verdict.main(False) == 0 and p105 and p105.get("verdict") == "P105-PASS"
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError):
        p105_ok = False
    try:
        import check_p112_verdict
        derived = check_p112_verdict.derive()
        p112_ok = isinstance(derived, dict) and derived == p112 and derived.get("verdict") == "P112-PASS"
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError):
        p112_ok = False
    try:
        import check_p113_history
        history = check_p113_history.verify()
        p113_history_ok = isinstance(history, dict) and history.get("pass") is True
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
        p113_history_ok = False
    phases = ("p105", "p107", "p108", "p109", "p110", "p111", "p112", "p113", "p114", "p115")
    verdicts = {phase.upper(): _read_json(ROOT / f"results/{phase}_verdict.json") for phase in phases}
    hashes = {f"results/{phase}_verdict.json": _sha(ROOT / f"results/{phase}_verdict.json") for phase in phases}
    return ({"prior_verdict_details": verdicts, "prior_verdict_sha256": hashes},
            p105_ok, p112_ok, p113_history_ok, all(hashes.values()))


def _fixture_document() -> dict:
    return oracle.frozen_fixture()


def _fixture_check() -> dict:
    expected = _fixture_document()
    expected_bytes = _json_bytes(expected)
    actual = _read_json(FIXTURE)
    counts = oracle.fixture_counts(actual if isinstance(actual, dict) else {})
    return {**counts, "pass": counts["pass"] and actual == expected and FIXTURE.is_file()
                    and FIXTURE.read_bytes() == expected_bytes,
            "fixture_sha256": _sha(FIXTURE), "expected_fixture_sha256": _sha_bytes(expected_bytes)}


def _git_blob(commit: str, path: str) -> bytes:
    from check_p107_history import _git_blob as read_blob
    return read_blob(commit, path, root=ROOT)


def _rust_paths_at_commit(commit: str) -> set[str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid Rust checkpoint identity")
    result = bounded._run(["git", "ls-tree", "-r", "--full-tree", commit, "--", "crates"],
                          cwd=ROOT, timeout_s=120)
    if result.returncode != 0:
        raise ValueError("cannot enumerate the inherited Rust source tree")
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        try:
            metadata, raw_path = line.split("\t", 1)
            mode, kind, _object_id = metadata.split()
        except ValueError as error:
            raise ValueError("malformed checkpoint Git tree entry") from error
        path = Path(raw_path)
        if raw_path.startswith("crates/") and raw_path.endswith(".rs"):
            if kind != "blob" or mode not in {"100644", "100755"} or path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe inherited Rust source entry")
            paths.add(raw_path)
    if not paths:
        raise ValueError("commit contains no Rust source files")
    return paths


def _rust_paths_at_checkpoint() -> set[str]:
    paths = _rust_paths_at_commit(P116_CHECKPOINT)
    if len(paths) != 100:
        raise ValueError(f"checkpoint must contain exactly 100 Rust files, found {len(paths)}")
    return paths


def _checkpoint_source_hashes() -> dict[str, str]:
    return {path: _sha_bytes(_git_blob(P116_CHECKPOINT, path)) for path in sorted(_rust_paths_at_checkpoint())}


def _current_rust_source_hashes() -> dict[str, str]:
    result = bounded._run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                          cwd=ROOT, timeout_s=120)
    if result.returncode != 0:
        raise ValueError("cannot enumerate the current Rust source population")
    root = ROOT.resolve(strict=True)
    paths: set[str] = set()
    for raw in result.stdout.splitlines():
        if raw.startswith("crates/") and raw.endswith(".rs"):
            path = Path(raw)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != raw:
                raise ValueError("unsafe current Rust source path")
            physical = ROOT / path
            physical.resolve(strict=True).relative_to(root)
            cursor = ROOT
            if any((cursor := cursor / part).is_symlink() for part in path.parts):
                raise ValueError("current Rust source path traverses a symlink")
            if not physical.is_file():
                raise ValueError("current Rust source is not a regular file")
            paths.add(raw)
    if len(paths) != 100:
        raise ValueError(f"current checkout must contain exactly 100 Rust files, found {len(paths)}")
    return {path: _sha(ROOT / path) for path in sorted(paths)}


def _production_population_allowed(inherited: object, production: object) -> bool:
    """Allow exactly the registered production edit in the 100-file Rust tree."""
    if not isinstance(inherited, dict) or not isinstance(production, dict):
        return False
    if set(inherited) != set(production) or len(inherited) != 100:
        return False
    if any(not isinstance(path, str) or not isinstance(value, str)
           or re.fullmatch(r"[0-9a-f]{64}", value) is None
           for mapping in (inherited, production) for path, value in mapping.items()):
        return False
    changed = {path for path in inherited if inherited[path] != production[path]}
    return changed == {PRODUCTION_CHANGE_PATH}


def _production_rust_candidate(checkpoint_hashes: dict[str, str], *, verify_current_sources: bool) -> dict[str, str]:
    if verify_current_sources:
        return _current_rust_source_hashes()
    sealed = _read_json(G0)
    candidate = sealed.get("production_rust_sha256") if isinstance(sealed, dict) else None
    if not isinstance(candidate, dict):
        raise ValueError("round-one G0 production Rust map is missing")
    normalized = dict(candidate)
    if not _production_population_allowed(checkpoint_hashes, normalized):
        raise ValueError("sealed production map is outside the single permitted Rust change")
    return normalized


def _g0_base(*, verify_current_sources: bool = True) -> dict:
    fixture_check = _fixture_check()
    prior, historical_p105, historical_p112, historical_p113, prior_hashes_present = _prior_verdicts()
    try:
        p103_source, p103_vectors, p103_identity = check_p105.verify_frozen_vectors()
        p103_seal = _read_json(ROOT / "results/g0_p103_seals.json")
        source_vectors = (isinstance(p103_seal, dict) and p103_seal.get("sealed") is True
                          and p103_vectors and p103_identity.get("vector_count") == 126
                          and _read_json(ROOT / "results/g0b_p103_vectors.json") is not None)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        source_vectors = False
    pack_hash = _sha(PACK)
    mirror_hash = _sha(MIRROR)
    pack_mirror = bool(pack_hash and pack_hash == mirror_hash and PACK.read_bytes() == MIRROR.read_bytes())
    hashes = {name: _sha(ROOT / name) for name in CONTROL_FILES}
    try:
        checkpoint_hashes = _checkpoint_source_hashes()
        production_hashes = _production_rust_candidate(checkpoint_hashes,
                                                       verify_current_sources=verify_current_sources)
        checkpoint_sources_match = (len(checkpoint_hashes) == 100 and len(production_hashes) == 100
            and all(isinstance(value, str) and len(value) == 64 for value in checkpoint_hashes.values())
            and production_hashes == checkpoint_hashes)
        production_sources_allowed = _production_population_allowed(checkpoint_hashes, production_hashes)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        checkpoint_hashes, production_hashes, checkpoint_sources_match = {}, {}, False
        production_sources_allowed = False
    try:
        retained_hashes, retained_matches = _repair_evidence()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        retained_hashes, retained_matches = {}, False
    repair_evidence = _p116_repair_evidence()
    request_ids = fixture_check.get("request_ids", [])
    counts = fixture_check.get("request_component_target_counts", [])
    p113_failure = _read_json(ROOT / "results/p113_verdict.json")
    p113_failure_record = _read_json(ROOT / "results/p113_failure_commit.json")
    p114_failure_record = _read_json(P114_HISTORY_RECORD)
    p114_failure = _read_json(ROOT / "results/p114_verdict.json")
    p114_g3 = _read_json(ROOT / "results/g3_p114_quality.json")
    p114_initial_discovery = _read_json(ROOT / "results/failures/p114_initial/discovery.json")
    p114_initial_archive = (p114_initial_discovery.get("files_sha256")
                            if isinstance(p114_initial_discovery, dict) else None)
    p114_final_archive = (p114_g3.get("retained_after_amendment_sha256")
                          if isinstance(p114_g3, dict) else None)
    try:
        import check_p114_history
        p114_history = check_p114_history.verify()
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
        p114_history = {"pass": False}
        historical_p114_verified = False
    else:
        historical_p114_verified = isinstance(p114_history, dict) and p114_history.get("pass") is True
    p115_failure_record = _read_json(P115_HISTORY_RECORD)
    p115_failure_verdict = _read_json(ROOT / "results/p115_verdict.json")
    p115_failure_g3 = _read_json(ROOT / "results/g3_p115_quality.json")
    try:
        import check_p115_history
        p115_history = check_p115_history.verify()
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
        p115_history = {"pass": False}
        historical_p115_verified = False
    else:
        historical_p115_verified = isinstance(p115_history, dict) and p115_history.get("pass") is True
    p115_failure_record_matches = (isinstance(p115_failure_record, dict)
        and p115_failure_record.get("schema") == "actinv-p115-failure-implementation-1"
        and p115_failure_record.get("phase") == "P115"
        and p115_failure_record.get("verdict") == "P115-FAIL"
        and p115_failure_record.get("ci_qualification") == "none; local failed checkpoint"
        and p115_failure_record.get("commit_sha") == P116_CHECKPOINT
        and p115_failure_record.get("original_checkpoint_commit") == P115_INITIAL_CHECKPOINT
        and p115_failure_record.get("terminal_g3_sha256") == P115_FINAL_G3_SHA256
        and p115_failure_record.get("terminal_verdict_sha256") == P115_VERDICT_SHA256
        and p115_failure_record.get("discovery_sha256") == P115_DISCOVERY_SHA256
        and p115_failure_record.get("amendment_sha256") == P115_AMENDMENT_A_SHA256
        and p115_failure_record.get("implementation_record_present") is False
        and p115_failure_record.get("ci_record_present") is False
        and type(p115_failure_record.get("repair_rounds")) is int
        and p115_failure_record.get("repair_rounds") == 1
        and p115_failure_record.get("protocol_sha256") == P115_PROTOCOL_SHA256
        and p115_failure_record.get("protocol_pinned_by_p116_sha256") == PROTOCOL_SHA256
        and p115_failure_record.get("initial_source_count") == 180
        and p115_failure_record.get("initial_rust_source_count") == 100
        and p115_failure_record.get("initial_retained_file_count") == 68
        and p115_failure_record.get("initial_archive_physical_file_count") == 69
        and p115_failure_record.get("terminal_source_count") == 182
        and p115_failure_record.get("terminal_rust_source_count") == 100
        and p115_failure_record.get("terminal_retained_file_count") == 68
        and p115_failure_record.get("initial_gate_count") == 22
        and p115_failure_record.get("terminal_gate_count") == 22
        and p115_failure_record.get("failed_gate") == "p115_verdict_regressions"
        and type(p115_failure_record.get("initial_failure_exit_code")) is int
        and p115_failure_record.get("initial_failure_exit_code") == 1
        and type(p115_failure_record.get("terminal_failure_exit_code")) is int
        and p115_failure_record.get("terminal_failure_exit_code") == 1
        and p115_failure_record.get("initial_failure_log_sha256") == P115_FAILURE_LOG_SHA256
        and p115_failure_record.get("terminal_failure_log_sha256") == P115_AMENDED_FAILURE_LOG_SHA256
        and p115_failure_record.get("g0_status") == "not_sealed"
        and p115_failure_record.get("g1_status") == "not_run"
        and p115_failure_record.get("g2_status") == "not_run"
        and p115_failure_record.get("checker_error") == {
            "exception_type": "NameError", "name": "historical_p114",
            "message": "name 'historical_p114' is not defined",
            "failure_projection": "unsealed_G0_forced_false_only",
            "original_derivation_completed": False}
        and isinstance(p115_failure_verdict, dict) and p115_failure_verdict.get("verdict") == "P115-FAIL"
        and _sha(ROOT / "results/p115_verdict.json") == P115_VERDICT_SHA256
        and isinstance(p115_failure_g3, dict) and _sha(ROOT / "results/g3_p115_quality.json") == P115_FINAL_G3_SHA256)
    p114_history_record_matches = (isinstance(p114_failure_record, dict)
        and p114_failure_record.get("schema") == "actinv-p114-failure-implementation-1"
        and p114_failure_record.get("verdict") == "P114-FAIL"
        and p114_failure_record.get("commit_sha") == P114_CHECKPOINT
        and p114_failure_record.get("terminal_verdict_sha256") == P114_FAILURE_VERDICT_SHA256
        and p114_failure_record.get("partial_g3_sha256") == P114_FAILURE_G3_SHA256
        and p114_failure_record.get("amendment_sha256") == P114_AMENDMENT_SHA256
        and p114_failure_record.get("initial_discovery_sha256") == P114_INITIAL_DISCOVERY_SHA256
        and p114_failure_record.get("initial_archive_file_count") == 176
        and p114_failure_record.get("final_archive_file_count") == 196
        and p114_failure_record.get("rust_source_count") == 100
        and p114_failure_record.get("executed_gate_receipt_count") == 9
        and p114_failure_record.get("successful_gate_count") == 8
        and p114_failure_record.get("failed_gate") == "p114_seal_regressions"
        and type(p114_failure_record.get("failure_exit_code")) is int
        and p114_failure_record.get("failure_exit_code") == 1
        and type(p114_failure_record.get("repair_rounds")) is int
        and p114_failure_record.get("repair_rounds") == 1
        and p114_failure_record.get("g0_sealed") is False
        and p114_failure_record.get("g1_executed") is False
        and p114_failure_record.get("g2_executed") is False
        and isinstance(p114_failure, dict) and p114_failure.get("verdict") == "P114-FAIL"
        and _sha(ROOT / "results/p114_verdict.json") == P114_FAILURE_VERDICT_SHA256
        and isinstance(p114_g3, dict) and _sha(ROOT / "results/g3_p114_quality.json") == P114_FAILURE_G3_SHA256)
    g0 = {"schema": "actinv-p116-twin-waste-g0-1", "phase": "P116",
          "protocol_sha256": _sha(PROTOCOL), "protocol_registered": _registered(),
          "inherited_protocol_sha256": _sha(INHERITED_PROTOCOL),
          "inherited_protocol_registered": _inherited_registered(),
          "p113_amendment_registered": _amendment_registered(),
          "pack_sha256": pack_hash, "pack_mirror_identical": pack_mirror,
          "fixture_population_matches": fixture_check["pass"], "fixture_check": fixture_check,
          "p105_verified": historical_p105, "p112_verified": historical_p112,
          "historical_p105_verified": historical_p105, "historical_p112_verified": historical_p112,
          "historical_p113_verified": historical_p113,
          "historical_p115_verified": historical_p115_verified,
          "p105_source_vector_seals_match": bool(source_vectors),
          **prior, "prior_verdicts_match": prior_hashes_present,
          "request_count": fixture_check.get("request_count", 0), "request_ids": request_ids,
          "case_targets": counts, "request_component_target_counts": counts,
          "repair_rounds": (1 if repair_evidence.get("pass") is True
                            and _p116_amendment_registered() else None),
          "repair_amendment_sha256": P116_AMENDMENT_SHA256,
          "repair_amendment_registered": _p116_amendment_registered(),
          "repair_discovery_sha256": _sha(P116_DISCOVERY),
          "p116_repair_evidence": repair_evidence,
          "p113_inherited_g0_sha256": INITIAL_G0_SHA256,
          "p113_g0_file_sha256": _sha(ROOT / "results/g0_p113_twin_waste.json"),
          "p113_failure_verdict_sha256": _sha(ROOT / "results/p113_verdict.json"),
          "p113_partial_g3_sha256": _sha(ROOT / "results/g3_p113_quality.json"),
          "p113_failure_verdict_is_terminal_fail": (isinstance(p113_failure, dict)
              and p113_failure.get("verdict") == "P113-FAIL"
              and _sha(ROOT / "results/p113_verdict.json") == P113_FAILURE_VERDICT_SHA256),
          "p113_failure_record_matches": (isinstance(p113_failure_record, dict)
              and p113_failure_record.get("schema") == "actinv-p113-failure-implementation-1"
              and p113_failure_record.get("commit_sha") == P113_CHECKPOINT
              and p113_failure_record.get("verdict") == "P113-FAIL"
              and p113_failure_record.get("terminal_verdict_sha256") == P113_FAILURE_VERDICT_SHA256
              and p113_failure_record.get("partial_g3_sha256") == P113_PARTIAL_G3_SHA256
              and type(p113_failure_record.get("failure_exit_code")) is int
              and p113_failure_record.get("failure_exit_code") == 127
              and p113_failure_record.get("amended_g0_executed") is False
              and p113_failure_record.get("g1_executed") is False
              and p113_failure_record.get("g2_executed") is False
              and p113_failure_record.get("initial_g0_sha256") == INITIAL_G0_SHA256
              and p113_failure_record.get("retained_initial_g0_artifact_sha256") == INITIAL_G0_SHA256
              and p113_failure_record.get("implementation_record") is None
              and p113_failure_record.get("ci_record") is None),
          "p113_unexecuted_artifacts_absent": not any(path.exists() for path in (
              ROOT / "results/g1_p113_twin_waste.json", ROOT / "results/g2_p113_twin_waste.json",
              ROOT / "results/p113_implementation_commit.json", ROOT / "results/p113_ci_runs.json")),
          "p113_initial_archive_sha256": retained_hashes,
          "p113_initial_archive_matches": retained_matches,
          "historical_p114_verified": historical_p114_verified,
          "p114_history_summary": p114_history,
          "p114_failure_record_matches": p114_history_record_matches,
          "p114_failure_verdict_sha256": _sha(ROOT / "results/p114_verdict.json"),
          "p114_failure_record_sha256": _sha(P114_HISTORY_RECORD),
          "p114_partial_g3_sha256": _sha(ROOT / "results/g3_p114_quality.json"),
          "p114_initial_discovery_sha256": _sha(ROOT / "results/failures/p114_initial/discovery.json"),
          "p114_initial_archive_sha256": p114_initial_archive,
          "p114_final_archive_sha256": p114_final_archive,
          "p115_history_summary": p115_history,
          "p115_failure_record_matches": p115_failure_record_matches,
          "p115_failure_record_sha256": _sha(P115_HISTORY_RECORD),
          "p115_failure_verdict_sha256": _sha(ROOT / "results/p115_verdict.json"),
          "p115_failure_g3_sha256": _sha(ROOT / "results/g3_p115_quality.json"),
          "p115_initial_discovery_sha256": _sha(P115_DISCOVERY),
          "p115_initial_archive_sha256": (_read_json(P115_DISCOVERY).get("files_sha256")
                                             if isinstance(_read_json(P115_DISCOVERY), dict) else None),
          "p115_amended_archive_sha256": (p115_failure_g3.get("retained_after_amendment_sha256")
                                             if isinstance(p115_failure_g3, dict) else None),
          "p115_amended_source_sha256": (p115_failure_g3.get("source_at_failure_sha256")
                                           if isinstance(p115_failure_g3, dict) else None),
          "p115_unexecuted_artifacts_absent": not any(path.exists() for path in (
              ROOT / "results/g0_p115_twin_waste.json", ROOT / "results/g1_p115_twin_waste.json",
              ROOT / "results/g2_p115_twin_waste.json", ROOT / "results/p115_implementation_commit.json",
              ROOT / "results/p115_ci_runs.json")),
          "inherited_rust_checkpoint": P116_CHECKPOINT,
          "inherited_rust_source_count": len(checkpoint_hashes),
          "inherited_rust_sha256": checkpoint_hashes,
          "inherited_rust_current_sha256": production_hashes,
          "inherited_rust_population_matches": checkpoint_sources_match,
          "production_rust_sha256": production_hashes,
          "production_rust_source_count": len(production_hashes),
          "production_rust_population_allowed": production_sources_allowed,
          "initial_g0_sha256": INITIAL_G0_SHA256,
          "original_fixture_sha256": FIXTURE_SHA256,
          "component_target_count": fixture_check.get("component_target_count", 0),
          "control_sha256": hashes}
    g0["pass"] = bool(g0["protocol_registered"] and g0["inherited_protocol_registered"]
        and g0["p113_amendment_registered"]
        and pack_mirror and g0["fixture_population_matches"]
        and historical_p105 and historical_p112 and historical_p113 and historical_p114_verified
        and historical_p115_verified and p115_failure_record_matches
        and g0["p115_unexecuted_artifacts_absent"]
        and isinstance(g0["p115_initial_archive_sha256"], dict)
        and len(g0["p115_initial_archive_sha256"]) == 68
        and isinstance(g0["p115_amended_archive_sha256"], dict)
        and len(g0["p115_amended_archive_sha256"]) == 68
        and isinstance(g0["p115_amended_source_sha256"], dict)
        and len(g0["p115_amended_source_sha256"]) == 182
        and source_vectors and prior_hashes_present
        and type(g0["request_count"]) is int and g0["request_count"] >= 24
        and type(g0["component_target_count"]) is int and g0["component_target_count"] >= 32
        and all(hashes.values()) and _safe_control_hashes(hashes)
        and _sha(P116_DISCOVERY) == P116_DISCOVERY_SHA256
        and g0["repair_amendment_registered"] is True
        and repair_evidence.get("pass") is True
        and _repair_policy(g0, repair_evidence)
        and g0["p113_failure_verdict_is_terminal_fail"]
        and g0["p113_failure_record_matches"]
        and g0["p113_g0_file_sha256"] == INITIAL_G0_SHA256
        and g0["p113_unexecuted_artifacts_absent"]
        and g0["p113_partial_g3_sha256"] == P113_PARTIAL_G3_SHA256
        and retained_matches and len(retained_hashes) == 59
        and g0["initial_g0_sha256"] == INITIAL_G0_SHA256
        and g0["original_fixture_sha256"] == FIXTURE_SHA256
        and production_sources_allowed and len(production_hashes) == 100
        and g0["p114_failure_record_matches"]
        and g0["p114_failure_verdict_sha256"] == P114_FAILURE_VERDICT_SHA256
        and g0["p114_partial_g3_sha256"] == P114_FAILURE_G3_SHA256
        and g0["p114_initial_discovery_sha256"] == P114_INITIAL_DISCOVERY_SHA256
        and isinstance(g0["p114_initial_archive_sha256"], dict)
        and len(g0["p114_initial_archive_sha256"]) == 176
        and isinstance(g0["p114_final_archive_sha256"], dict)
        and len(g0["p114_final_archive_sha256"]) == 196
        and g0["historical_p114_verified"] is True)
    return g0


def g0(*, seal: bool = False, no_write: bool = False) -> int:
    report = _g0_base()
    if no_write:
        equal = G0.is_file() and G0.read_bytes() == _json_bytes(report)
        report["persisted_seal_matches"] = equal
        report["pass"] = report["pass"] and equal
    elif seal:
        G0.parent.mkdir(parents=True, exist_ok=True)
        G0.write_bytes(_json_bytes(report))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("pass") is True else 1


def _close(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a)-float(b)) <= max(1e-12, 1e-12 * max(abs(float(a)), abs(float(b))))


def _compare(actual: object, expected: object, path: str = "result") -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return [f"{path}: object keys differ"]
        errors=[]
        for key in expected:
            errors.extend(_compare(actual[key], expected[key], f"{path}.{key}"))
        return errors
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: list length/type differs"]
        errors=[]
        for i,(got,want) in enumerate(zip(actual,expected)):
            errors.extend(_compare(got,want,f"{path}[{i}]"))
        return errors
    if isinstance(expected, bool):
        return [] if type(actual) is bool and actual is expected else [f"{path}: boolean differs"]
    if (re.fullmatch(r".*\.evaluation\.row_fractions\[\d+\]\.limit", path)
            and isinstance(expected, (int, float)) and not isinstance(expected, bool)):
        return [] if _close(actual, expected) else [f"{path}: numeric limit differs"]
    if isinstance(expected, (int,float)):
        if isinstance(expected, int):
            return [] if type(actual) is int and actual == expected else [f"{path}: integer value/type differs"]
        return [] if _close(actual,expected) else [f"{path}: numeric value differs"]
    return [] if actual == expected else [f"{path}: value differs"]


def _same_json_document(left: object, right: object) -> bool:
    """Compare mutation documents without Python's int/float equality alias."""
    try:
        return _json_dump(left) == _json_dump(right)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_waste_output(output: object, case: dict) -> list[str]:
    if not isinstance(output, dict):
        return ["twin output is not an object"]
    expected = case["expected"]
    errors = _compare(output.get("waste_classification"), expected["waste_classification"], "waste_classification")
    errors.extend(_compare(output.get("waste_facility_coverage"), expected["waste_facility_coverage"], "waste_facility_coverage"))
    return errors


def _json_dump(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def normalized_output_sha256(output: object, expected_absolute_mesh: Path, declared_mesh: str) -> str | None:
    """Hash the complete output after normalizing only a verified mesh path."""
    if not isinstance(output, dict) or not isinstance(declared_mesh, str) or not declared_mesh:
        return None
    if not expected_absolute_mesh.is_absolute():
        return None
    expected = str(expected_absolute_mesh)
    if output.get("mesh_output") != expected:
        return None
    normalized = copy.deepcopy(output)
    normalized["mesh_output"] = declared_mesh
    try:
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return _sha_bytes(canonical)


def _materialize(case: dict, root: Path) -> Path:
    case_dir = root / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "mesh.ndjson").write_text(case["mesh_ndjson"], encoding="utf-8")
    spec = copy.deepcopy(case["spec"])
    (case_dir / "twin.json").write_bytes(_json_dump(spec))
    for name, document in case.get("assay_files", {}).items():
        (case_dir / name).write_bytes(_json_dump(document))
    return case_dir / "twin.json"


def _invoke_twin(spec_path: Path, output_path: Path) -> tuple[bool, str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = bounded._run([str(ACTINV), "twin", str(spec_path), str(output_path)], cwd=ROOT, timeout_s=120)
    except (OSError, RuntimeError, ValueError) as error:
        return False, "", str(error)
    return result.returncode == 0, result.stdout, result.stderr


def _read_output(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _compatibility_errors(output: dict, case: dict, case_dir: Path, baseline_dir: Path) -> list[str]:
    """Compare pre-existing twin fields against the same request with waste omitted."""
    legacy_spec = copy.deepcopy(case["spec"])
    legacy_spec.pop("waste", None)
    legacy_path = case_dir / "legacy-twin.json"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(_json_dump(legacy_spec))
    legacy_out = baseline_dir / f"{case['id']}.json"
    legacy_out.unlink(missing_ok=True)
    ok, _, error = _invoke_twin(legacy_path, legacy_out)
    if not ok:
        return [f"legacy twin request failed: {error[-500:]}"]
    legacy = _read_output(legacy_out)
    if not isinstance(legacy, dict):
        return ["legacy twin output is not JSON object"]
    preexisting = {key: value for key, value in output.items()
                   if key not in {"waste_classification", "waste_facility_coverage"}}
    return _compare(preexisting, legacy, "legacy-compatible-twin-output")


def _band_signature(document: object, cell_id: str | None = None) -> tuple:
    if not isinstance(document, dict) or not isinstance(document.get("per_cell"), list):
        return ()
    result = []
    for cell in document["per_cell"]:
        if cell_id is not None and cell.get("cell") != cell_id:
            continue
        entries = tuple((entry.get("t_s"), entry.get("limit"), tuple(entry.get("band", [])),
                         entry.get("margin"), entry.get("clears")) for entry in cell.get("entries", []))
        result.append((cell.get("cell"), entries))
    return tuple(result)


def _run_population(root: Path, output_name: str) -> dict:
    fixture = _read_json(FIXTURE)
    requests = fixture.get("requests", []) if isinstance(fixture, dict) else []
    failures: list[str] = []
    evidence: list[dict] = []
    records: list[tuple[dict, Path, dict]] = []
    comparisons = 0
    baseline_dir = root / f"{output_name}-legacy"
    output_dir = root / output_name
    for case in requests:
        spec_path = _materialize(case, root / "inputs")
        output_path = output_dir / case["id"] / "out.json"
        output_path.unlink(missing_ok=True)
        ok, _, error = _invoke_twin(spec_path, output_path)
        if not ok:
            failures.append(f"{case.get('id')}: twin failed: {error[-500:]}")
            continue
        actual = _read_output(output_path)
        if not isinstance(actual, dict):
            failures.append(f"{case.get('id')}: output is not JSON object")
            continue
        errors = validate_waste_output(actual, case)
        if errors:
            failures.extend(f"{case['id']}: {item}" for item in errors)
        errors = legacy.validate_legacy(actual, case, spec_path.parent, _compare)
        if errors:
            failures.extend(f"{case['id']}: {item}" for item in errors)
        errors = _compatibility_errors(actual, case, spec_path.parent, baseline_dir)
        if errors:
            failures.extend(f"{case['id']}: {item}" for item in errors)
        expected_count = case["expected"]["request_component_target_count"]
        comparisons += expected_count
        declared_mesh = case["spec"].get("mesh_output")
        normalized_sha = normalized_output_sha256(actual, spec_path.parent / declared_mesh, declared_mesh) \
            if isinstance(declared_mesh, str) else None
        if normalized_sha is None:
            failures.append(f"{case['id']}: output mesh path failed exact-path verification/normalization")
        evidence.append({"id": case["id"], "component_target_count": expected_count,
            "input_sha256": _sha_bytes(case["mesh_ndjson"].encode()),
            "output_sha256": normalized_sha,
            "ordinary_waste_sha256": actual.get("waste_classification", {}).get("waste_sha256")})
        records.append((case, output_path, actual))
    by_id = {case["id"]: (path, actual) for case, path, actual in records}
    baseline_legacy = by_id.get("assay_baseline")
    if baseline_legacy is not None:
        baseline_path = root / f"{output_name}-legacy" / "assay_baseline.json"
        baseline_doc = _read_output(baseline_path)
        for assay_id in ("scalar_assay", "mixture_assay", "propagated_assay", "dose_assay"):
            pair = by_id.get(assay_id)
            assay_doc = _read_output(root / f"{output_name}-legacy" / f"{assay_id}.json")
            if pair is None or not isinstance(baseline_doc, dict) or not isinstance(assay_doc, dict):
                failures.append(f"{assay_id}: assay compatibility output is missing")
                continue
            # Compare only certified per-cell band/margin evidence; the
            # no-waste documents otherwise differ by their source directories.
            if not _band_signature(assay_doc) or _band_signature(assay_doc) == _band_signature(baseline_doc):
                failures.append(f"{assay_id}: assay did not alter certified cell bands/margins")
            facility = assay_doc.get("facility", {})
            assimilated = facility.get("assimilated_cells", []) if isinstance(facility, dict) else []
            if not isinstance(assimilated, list) or not assimilated:
                failures.append(f"{assay_id}: no assimilation targets were reported")
            if assay_id == "propagated_assay" and _band_signature(assay_doc, "cell-1") == _band_signature(baseline_doc, "cell-1"):
                failures.append("propagated_assay: declared sibling cell band did not change")
            if assay_id == "dose_assay":
                dose_records = facility.get("dose_assimilations", []) if isinstance(facility, dict) else []
                if not isinstance(dose_records, list) or not dose_records:
                    failures.append("dose_assay: dose assimilation evidence is empty")
            for sibling in ("waste_classification", "waste_facility_coverage"):
                if pair[1].get(sibling) != baseline_legacy[1].get(sibling):
                    failures.append(f"{assay_id}: {sibling} changed with assay adjustment")
    return {"failures": failures, "request_evidence": evidence,
            "request_count": len(requests), "component_target_count": comparisons,
            "independent_comparison_count": comparisons, "records": records}


def _mutations(records: list[tuple[dict, Path, dict]]) -> dict[str, bool]:
    tests: dict[str, bool] = {}
    case, _, baseline = next((x for x in records if x[0].get("id") == "two_table_mixture"), (None, None, None))
    if baseline is None:
        return {"fixture_available": False}
    mutations = []
    doc = copy.deepcopy(baseline)
    doc["waste_facility_coverage"]["inventory_basis"] = "assay_adjusted"
    mutations.append(("basis", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["assigned_cell_ids"] = []
    mutations.append(("membership", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["target_class_counts"][0]["counts"]["A"] += 1
    mutations.append(("class_counts_first_target", doc))
    for name, path, transform in (
        ("inventory", ("waste_classification", "components", 0, "targets", 0, "inventory_activity_bq"),
         lambda x: {**x, "C14": x.get("C14", 0.0) + 1.0}),
        ("external_activity", ("waste_classification", "components", 0, "targets", 0, "external_tritium_activity_bq"), lambda x: float(x) + 1.0),
        ("external_status", ("waste_classification", "components", 0, "external_tritium", "status"), lambda x: "required" if x != "required" else "not_applicable"),
        ("volume", ("waste_classification", "components", 0, "displaced_volume_cm3"), lambda x: float(x) + 1.0),
        ("waste_type", ("waste_classification", "components", 0, "waste_type"), lambda x: "activated_metal" if x == "general" else "general"),
    ):
        doc = copy.deepcopy(baseline)
        node = doc
        for key in path[:-1]: node = node[key]
        node[path[-1]] = transform(node[path[-1]])
        mutations.append((name, doc))
    doc = copy.deepcopy(baseline)
    doc["waste_facility_coverage"]["assigned_cell_count"] = float(doc["waste_facility_coverage"]["assigned_cell_count"])
    mutations.append(("integer_count_type", doc))
    # Mutate the independently checked core claims, not a scalar compared to
    # itself. Each alternate must differ from the sealed baseline.
    fields = ("normalized_margin", "source_sum_fraction", "normalized_sum",
              "contributor_count", "strict", "passes", "target_class", "table")
    for index, field in enumerate(fields):
        doc = copy.deepcopy(baseline)
        target = doc["waste_classification"]["components"][0]["targets"][index % len(doc["waste_classification"]["components"][0]["targets"])]
        constraints = target["evaluation"]["constraints"]
        constraint = constraints[index % len(constraints)]
        if field in ("strict", "passes"):
            constraint[field] = not constraint[field]
        elif field in ("contributor_count", "table"):
            constraint[field] = constraint[field] + 1
        elif field == "target_class":
            constraint[field] = "C" if constraint[field] == "A" else "A"
        else:
            constraint[field] = float(constraint[field]) + 0.01
        mutations.append((f"constraint_{field}_{index}", doc))
    for index, field in enumerate(("fraction", "concentration", "limit", "row_id", "nuclide")):
        doc = copy.deepcopy(baseline)
        target = doc["waste_classification"]["components"][0]["targets"][0]
        rows = target["evaluation"]["row_fractions"]
        if rows:
            row = rows[index % len(rows)]
            if field in ("fraction", "concentration", "limit") and row[field] is not None:
                row[field] = float(row[field]) + 0.01
            elif field == "row_id":
                row[field] = "changed-row"
            else:
                row[field] = "ChangedNuclide"
            mutations.append((f"row_{field}_{index}", doc))
    for index, field in enumerate(("normalized_sum", "normalized_margin", "contributor_count", "strict", "passes")):
        doc = copy.deepcopy(baseline)
        bindings = doc["waste_classification"]["components"][0]["targets"][0]["evaluation"]["binding_constraints"]
        item = bindings[index % len(bindings)]
        item[field] = (not item[field] if isinstance(item[field], bool)
                       else item[field] + 1 if isinstance(item[field], int)
                       else float(item[field]) + 0.01)
        mutations.append((f"binding_{field}_{index}", doc))
    doc = copy.deepcopy(baseline); first = doc["waste_classification"]["components"][0]["targets"][0]
    first["coverage"] = "incomplete" if first["coverage"] == "complete" else "complete"
    mutations.append(("coverage", doc))
    doc = copy.deepcopy(baseline); first = doc["waste_classification"]["components"][0]["targets"][0]
    first["class"] = "B" if first["class"] != "B" else "A"
    mutations.append(("class", doc))
    doc = copy.deepcopy(baseline); component = doc["waste_classification"]["components"][0]
    component["mass_g"] = float(component["mass_g"]) + 0.5
    mutations.append(("component_mass", doc))
    doc = copy.deepcopy(baseline); doc["waste_classification"]["status"] = "conditional" if doc["waste_classification"]["status"] == "nominal" else "nominal"
    mutations.append(("result_status", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["assay_adjustment"] = "applied"
    mutations.append(("assay_adjustment", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["membership_coverage"] = "partial"
    mutations.append(("membership_coverage", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["assigned_cell_count"] += 1
    mutations.append(("assigned_count", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["unassigned_cell_ids"] = ["phantom"]
    mutations.append(("unassigned_ids", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["target_class_counts"][0]["counts"]["unknown"] += 1
    mutations.append(("class_counts_unknown", doc))
    doc = copy.deepcopy(baseline); doc["waste_facility_coverage"]["component_count"] += 1
    mutations.append(("facility_component_count", doc))
    for name, changed in mutations:
        errors = validate_waste_output(changed, case)
        tests[name] = bool(errors and not _same_json_document(changed, baseline))
    for unknown_id in ("missing_metadata", "required_h3"):
        pair = next((row for row in records if row[0].get("id") == unknown_id), None)
        if pair is None:
            tests[f"{unknown_id}_coverage"] = False
            continue
        unknown_case, _, unknown_report = pair
        for field in ("coverage", "unknown_nuclides", "unknown_reason"):
            changed = copy.deepcopy(unknown_report)
            target = changed["waste_classification"]["components"][0]["targets"][0]
            if field == "coverage":
                target[field] = "complete" if target[field] != "complete" else "incomplete"
            elif field == "unknown_nuclides":
                original_unknowns = target["evaluation"][field]
                target["evaluation"][field] = [] if original_unknowns else ["Co60"]
            else:
                target[field] = None if target[field] is not None else "invented"
            tests[f"{unknown_id}_{field}"] = bool(
                validate_waste_output(changed, unknown_case)
                and not _same_json_document(changed, unknown_report))
    return tests


def _refusal_variants(case: dict) -> list[tuple[str, bytes, bytes]]:
    """Return independent malformed specs paired with unchanged valid mesh bytes."""
    base = copy.deepcopy(case["spec"])
    mesh = case["mesh_ndjson"].encode()
    variants: list[tuple[str, bytes, bytes]] = []
    def add(name: str, mutate) -> None:
        item = copy.deepcopy(base); mutate(item)
        variants.append((name, _json_dump(item), mesh))
    components = base["waste"]["components"]
    add("missing_waste_component", lambda x: x["waste"]["components"].pop("component-b"))
    add("extra_waste_component", lambda x: x["waste"]["components"].update({"extra": copy.deepcopy(x["waste"]["components"]["component-a"])}))
    add("empty_twin_group", lambda x: x["components"].update({"component-a": []}))
    add("duplicate_cell", lambda x: x["components"].update({"component-a": ["cell-0", "cell-0"]}))
    add("overlap", lambda x: x["components"].update({"component-b": ["cell-1", "cell-2"]}))
    add("missing_mass_cell", lambda x: x["waste"]["components"]["component-a"]["cell_masses_g"].pop("cell-0"))
    add("extra_mass_cell", lambda x: x["waste"]["components"]["component-a"]["cell_masses_g"].update({"cell-x": 1.0}))
    add("mass_closure", lambda x: x["waste"]["components"]["component-a"].update({"mass_g": 99.0}))
    add("both_geometry", lambda x: x["waste"]["components"]["component-a"].update({"density_g_cm3": 1.0}))
    add("neither_geometry", lambda x: x["waste"]["components"]["component-a"].pop("displaced_volume_cm3"))
    add("null_waste", lambda x: x.update({"waste": None}))
    add("bad_type", lambda x: x["waste"]["components"]["component-a"].update({"waste_type": "equipment"}))
    add("null_external", lambda x: x["waste"]["components"]["component-a"].update({"external_tritium": None}))
    add("missing_external_step", lambda x: x["waste"]["components"]["component-a"]["external_tritium"].update({"status":"declared","source":"x","excludes_activation":True,"activity_bq":{"1":1.0}}))
    add("bad_external_exclusion", lambda x: x["waste"]["components"]["component-a"]["external_tritium"].update({"status":"declared","source":"x","excludes_activation":False,"activity_bq":{"1":1.0,"2":1.0}}))
    add("duplicate_target", lambda x: x["waste"].update({"targets":[1,1]}))
    add("zero_target", lambda x: x["waste"].update({"targets":[0]}))
    add("null_properties", lambda x: x["waste"].update({"nuclide_properties":None}))
    add("zero_half_life", lambda x: x["waste"]["nuclide_properties"][next(iter(x["waste"]["nuclide_properties"]))].update({"half_life_s":0.0}))
    add("invalid_property_z", lambda x: x["waste"]["nuclide_properties"][next(iter(x["waste"]["nuclide_properties"]))].update({"z":0}))
    add("unknown_waste_field", lambda x: x["waste"].update({"surprise":True}))
    add("unknown_component_field", lambda x: x["waste"]["components"]["component-a"].update({"surprise":True}))
    add("unknown_property_field", lambda x: x["waste"]["nuclide_properties"][next(iter(x["waste"]["nuclide_properties"]))].update({"surprise":True}))
    add("unknown_external_field", lambda x: x["waste"]["components"]["component-a"]["external_tritium"].update({"surprise":True}))
    # Raw duplicate key in an otherwise valid outer spec.
    raw = _json_dump(base).decode().replace('"spec":"actinv-twin-1"', '"spec":"bad","spec":"actinv-twin-1"', 1).encode()
    variants.append(("raw_duplicate_key", raw, mesh))
    variants.append(("malformed_mesh_json", _json_dump(base), b'{"record":"header","record":"cell"}\n'))
    variants.append(("truncated_mesh", _json_dump(base), b'{"record":"header","schema":"actinv-mesh-result-1","cell_count":4}\n'))
    variants.append(("nonregular_input_path", _json_dump({**base, "mesh_output":"."}), mesh))
    variants.append(("unknown_rules", _json_dump({**base, "waste":{**base["waste"],"rules":"custom"}}), mesh))
    variants.append(("alias_property_duplicate", _json_dump({**base, "waste":{**base["waste"],
        "nuclide_properties":{**base["waste"]["nuclide_properties"], "Co-60":base["waste"]["nuclide_properties"]["Co60"]}}}), mesh))
    variants.append(("bad_component_name", _json_dump({**base, "components":{"missing":["cell-0"]}}), mesh))
    missing_cell = copy.deepcopy(base)
    missing_cell["components"]["component-a"][0] = "missing-cell"
    missing_cell["waste"]["components"]["component-a"]["cell_masses_g"]["missing-cell"] = missing_cell["waste"]["components"]["component-a"]["cell_masses_g"].pop("cell-0")
    variants.append(("missing_assigned_mesh_cell", _json_dump(missing_cell), mesh))
    variants.append(("bad_mass_value", _json_dump({**base, "waste":{**base["waste"],"components":{
        **base["waste"]["components"],"component-a":{**base["waste"]["components"]["component-a"],"cell_masses_g":{"cell-0":-1,"cell-1":2}}}}}), mesh))
    # All 65 selected steps are populated so this is a true target-count cap.
    long_records = [json.loads(line) for line in mesh.decode().splitlines()]
    for record in long_records:
        if record.get("record") == "cell":
            source = copy.deepcopy(record["result"]["steps"][0]["activity_Bq_per_g"])
            record["result"]["steps"] = [{"step": step, "t_s": float(step - 1),
                "activity_Bq_per_g": source, "photon_source":{"groups":[{"photons_s":10.0}]},
                "uncertainty":{"responses":{"heat.total":{"nominal":1.0,
                    "combined_standard_uncertainty":0.1,"normal_multiplier":1.959964,
                    "conservative_interval":[0.8,1.2]}}}} for step in range(1,66)]
    long_mesh = ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in long_records)).encode()
    variants.append(("too_many_targets", _json_dump({**base, "waste":{**base["waste"],"targets":list(range(1,66))}}), long_mesh))
    variants.append(("selected_target_missing_step", _json_dump({**base,"waste":{**base["waste"],"targets":[1,3]}}), mesh))
    variants.append(("bad_external_alias", _json_dump({**base, "waste":{**base["waste"],"components":{
        **base["waste"]["components"],"component-a":{**base["waste"]["components"]["component-a"],
        "external_tritium":{"status":"declared","source":"x","excludes_activation":True,"activity_bq":{"01":1,"1":1,"2":1}}}}}}), mesh))
    variants.append(("malformed_footer", _json_dump(base), mesh.replace(b'"record":"footer"',b'"record":"cell"')))
    variants.append(("duplicate_mesh_cell", _json_dump(base), mesh.replace(b'"id":"cell-3"',b'"id":"cell-2"')))
    unselected = copy.deepcopy(base); unselected["waste"]["targets"] = [1]
    unselected_records = [json.loads(line) for line in mesh.decode().splitlines()]
    for record in unselected_records:
        if record.get("record") == "cell":
            record["result"]["steps"][1]["activity_Bq_per_g"] = {"Co60": -1.0}
    unselected_mesh = ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in unselected_records)).encode()
    variants.append(("invalid_unselected_activity", _json_dump(unselected), unselected_mesh))
    unequal = [json.loads(line) for line in mesh.decode().splitlines()]
    for record in unequal:
        if record.get("record") == "cell" and record.get("id") == "cell-1":
            record["result"]["steps"][1]["t_s"] = math.nextafter(record["result"]["steps"][1]["t_s"], math.inf)
    variants.append(("unequal_component_timestamps", _json_dump(base),
        ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in unequal)).encode()))
    # Recursive raw duplicate and canonical alias collisions inside a valid
    # mesh record (JSON remains parseable absent the feature's duplicate guard).
    duplicate_inner = mesh.decode().replace('"record":"cell"', '"record":"cell","record":"cell"', 1).encode()
    variants.append(("raw_duplicate_mesh_record_key", _json_dump(base), duplicate_inner))
    alias_activity = re.sub(r'"Co60":([^,}]+)', r'"Co60":\1,"Co-60":\1', mesh.decode(), count=1)
    variants.append(("canonical_activity_alias_collision", _json_dump(base), alias_activity.encode()))
    # Oversized but otherwise valid outer JSON and NDJSON use trailing JSON
    # whitespace so the file-size cap is the first intended failure.
    spec_bytes = _json_dump(base).rstrip(b"\n")
    variants.append(("outer_spec_size_cap", spec_bytes + b" " * (8 * 1024 * 1024 + 1 - len(spec_bytes)), mesh))
    mesh_line = mesh.rstrip(b"\n")
    variants.append(("mesh_size_cap", _json_dump(base), mesh_line + b" " * (64 * 1024 * 1024 + 1 - len(mesh_line)) + b"\n"))
    # A complete, unique-cell mesh proves the structural population cap.
    cell_records = [row for row in long_records if row.get("record") == "cell"]
    cells129 = copy.deepcopy(cell_records)
    for index in range(125):
        row = copy.deepcopy(cell_records[index % len(cell_records)])
        row["id"] = f"cap-cell-{index}"
        row["ordinal"] = index + len(cell_records)
        cells129.append(row)
    for index, row in enumerate(cells129):
        row["ordinal"] = index
        row["bounds_cm"] = [[index, index + 1], [0, 1], [0, 1]]
    mesh129 = [{"record":"header","schema":"actinv-mesh-result-1","cell_count":129}, *cells129,
               {"record":"footer","cell_count":129}]
    variants.append(("mesh_cell_count_cap", _json_dump(base),
        ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in mesh129)).encode()))
    missing_assay = copy.deepcopy(base)
    missing_assay["assays"] = [{"cell":"cell-0", "assay":"missing-assay.json"}]
    variants.append(("bad_assay_missing", _json_dump(missing_assay), mesh))
    # Separate cap fixtures remain otherwise parseable and below all count limits.
    large_assay = copy.deepcopy(base)
    large_assay["assays"] = [{"cell":"cell-0","assay":"large-assay.json"}]
    variants.append(("assay_file_size_cap", _json_dump(large_assay), mesh))
    cap_names = [*(f"H{mass}" for mass in range(1,601)), *(f"He{mass}" for mass in range(1,426))]
    over_properties = copy.deepcopy(base)
    over_properties["waste"]["nuclide_properties"] = {name:{"z":2 if name.startswith("He") else 1,
        "half_life_s":1.0,"alpha_emitting":False} for name in cap_names}
    variants.append(("nuclide_property_count_cap", _json_dump(over_properties), mesh))
    activity_names = cap_names[:1024] + ["Li1"]
    over_activity_records = [json.loads(line) for line in mesh.decode().splitlines()]
    activity_map = {name:1.0 for name in activity_names}
    for record in over_activity_records:
        if record.get("record") == "cell":
            for row in record["result"]["steps"]: row["activity_Bq_per_g"] = activity_map
    over_activity = copy.deepcopy(base)
    over_activity["waste"]["nuclide_properties"] = {name:{"z":2 if name.startswith("He") else 1 if name.startswith("H") else 3,
        "half_life_s":1.0,"alpha_emitting":False} for name in activity_names[:1024]}
    variants.append(("positive_activity_count_cap", _json_dump(over_activity),
        ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in over_activity_records)).encode()))
    # Repeated unlisted-but-well-formed activities expand the final waste
    # report beyond 32 MiB while keeping every input under its individual
    # cell/target/property/activity cap. The ordinary twin output stays small.
    cap_symbols = [*(f"H{mass}" for mass in range(1,161)),
                   *(f"He{mass}" for mass in range(1,161))]
    cap_properties = {name:{"z":2 if name.startswith("He") else 1,"half_life_s":1.0,"alpha_emitting":False}
                      for name in cap_symbols}
    cap_mesh_records = [{"record":"header","schema":"actinv-mesh-result-1","cell_count":16}]
    cap_groups, cap_components = {}, {}
    for index in range(16):
        cell, component = f"large-cell-{index:03}", f"large-component-{index:03}"
        steps = [{"step":step,"t_s":float(step-1),"activity_Bq_per_g":{name:1.0 for name in cap_symbols},
            "photon_source":{"groups":[{"photons_s":1.0}]},"uncertainty":{"responses":{"heat.total":{
                "nominal":1.0,"combined_standard_uncertainty":0.1,"normal_multiplier":1.959964,
                "conservative_interval":[0.8,1.2]}}}} for step in range(1,65)]
        cap_mesh_records.append({"record":"cell","ordinal":index,"id":cell,
            "bounds_cm":[[index,index+1],[0,1],[0,1]],"volume_cm3":1.0,
            "result":{"entry_point":"synthetic","mode":"coupled","steps":steps}})
        cap_groups[component] = [cell]
        cap_components[component] = {"mass_g":1.0,"displaced_volume_cm3":1.0,
            "waste_type":"general","cell_masses_g":{cell:1.0},
            "external_tritium":{"status":"not_applicable"}}
    cap_mesh_records.append({"record":"footer","cell_count":16})
    cap_output_spec = {"spec":"actinv-twin-1","mesh_output":"mesh.ndjson",
        "limits":[{"name":"heat","response":"heat.total","limit":2.0}],"components":cap_groups,
        "waste":{"schema":"actinv-twin-waste-spec-1","rules":"us-nrc-10cfr61.55-v1",
            "targets":list(range(1,65)),"nuclide_properties":cap_properties,"components":cap_components}}
    variants.append(("serialized_output_size_cap", _json_dump(cap_output_spec),
        ("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in cap_mesh_records)).encode()))
    return variants


def _serialized_output_size_lower_bound() -> int:
    """Conservative row-only byte floor for the valid output-cap request."""
    symbols = [*(f"H{mass}" for mass in range(1,161)),
               *(f"He{mass}" for mass in range(1,161))]
    minimum_row_size = min(len(_json_dump({
        "table":2,"column":1,"row_id":"H-3","nuclide":name,
        "unit":"Ci/m3","concentration":0.0,"limit":0.0,"fraction":0.0,
    }).rstrip(b"\n")) for name in symbols)
    # The frozen input has 16 disjoint components and 64 targets. Each of the
    # 320 valid short-lived names requires at least one Table 2 row (H-3 has
    # its dedicated row; the others enter the short-lived aggregate). The
    # shortest actual row ID/value spellings provide a conservative floor.
    # Subtract one byte per row for array separators to keep it a lower bound.
    return (minimum_row_size - 1) * 16 * 64 * len(symbols)


def _run_refusals(case: dict, root: Path) -> dict:
    results: dict[str, bool] = {}
    base_dir = root / "refusals"
    base_dir.mkdir(parents=True, exist_ok=True)
    for name, spec_bytes, mesh_bytes in _refusal_variants(case):
        work = base_dir / name
        work.mkdir(parents=True, exist_ok=True)
        spec_path, mesh_path = work / "twin.json", work / "mesh.ndjson"
        spec_path.write_bytes(spec_bytes); mesh_path.write_bytes(mesh_bytes)
        # Assays are copied when the variant otherwise references one.
        for assay_name, doc in case.get("assay_files", {}).items():
            (work / assay_name).write_bytes(_json_dump(doc))
        if name == "assay_file_size_cap":
            valid = _json_dump({"schema":"actinv-assay-1","response":"heat.total","time_s":0.0,
                                "value":1.0,"standard_uncertainty":0.05}).rstrip(b"\n")
            (work / "large-assay.json").write_bytes(valid + b" " * (8 * 1024 * 1024 + 1 - len(valid)))
        sentinel = work / "out.json"
        sentinel_bytes = b"P116-SENTINEL\n"
        sentinel.write_bytes(sentinel_bytes)
        ok, stdout, error = _invoke_twin(spec_path, sentinel)
        refused_without_write = not ok and sentinel.read_bytes() == sentinel_bytes
        cap_markers = {
            "outer_spec_size_cap": ("size", "bytes", "8388608", "8 mib"),
            "mesh_size_cap": ("size", "bytes", "67108864", "64 mib"),
            "assay_file_size_cap": ("size", "bytes", "8388608", "8 mib"),
            "nuclide_property_count_cap": ("1024", "property"),
            "positive_activity_count_cap": ("1024", "activity"),
            "serialized_output_size_cap": ("size", "output", "33554432", "32 mib"),
            "mesh_cell_count_cap": ("128", "cell"),
            "too_many_targets": ("64", "target"),
        }
        markers = cap_markers.get(name)
        diagnostic = (error + "\n" + stdout).lower()
        category_checked = markers is None or any(marker in diagnostic for marker in markers)
        if name == "serialized_output_size_cap":
            output_size_error = ("33554432" in diagnostic or "32 mib" in diagnostic
                                 or ("output" in diagnostic and any(
                                     word in diagnostic for word in ("size", "exceed", "large"))))
            category_checked = (output_size_error
                                and _serialized_output_size_lower_bound() > 32 * 1024 * 1024)
        results[name] = refused_without_write and category_checked
    return {"pass": len(results) >= 30 and all(results.values()), "checks": results}


def _run_g1(no_write: bool = False, *, output_name: str = "g1") -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    run = _run_population(WORK, output_name)
    mutations = _mutations(run["records"])
    refusal_case = next((case for case in _read_json(FIXTURE)["requests"] if case["id"] == "class_b"), None)
    refusals = _run_refusals(refusal_case, WORK / output_name) if refusal_case else {"pass":False,"checks":{}}
    # Deterministic rerun is written to a sibling output path; inputs remain byte-identical.
    repeats = []
    for case, _first_path, _actual in run["records"]:
        spec_path = _materialize(case, WORK / "inputs")
        repeat_path = WORK / output_name / "repeat" / case["id"] / "out.json"
        repeat_path.unlink(missing_ok=True)
        ok, _, err = _invoke_twin(spec_path, repeat_path)
        first_path = WORK / output_name / case["id"] / "out.json"
        repeats.append(ok and first_path.is_file() and repeat_path.is_file()
                       and first_path.read_bytes() == repeat_path.read_bytes())
        if not ok:
            run["failures"].append(f"repeat {case['id']}: {err[-400:]}")
    report = {"schema":"actinv-p116-twin-waste-g1-1","phase":"P116",
        "protocol_sha256":PROTOCOL_SHA256,"pass":False,"request_count":run["request_count"],
        "component_target_count":run["component_target_count"],
        "independent_comparison_count":run["independent_comparison_count"],
        "request_evidence":run["request_evidence"],"failures":run["failures"],
        "mutations_rejected":mutations,"refusal_controls":refusals,
        "repeat_byte_identical":bool(repeats and all(repeats))}
    report["pass"] = (not report["failures"] and report["request_count"] >= 24
        and report["component_target_count"] >= 32 and report["independent_comparison_count"] == report["component_target_count"]
        and len(mutations) >= 30 and all(mutations.values()) and refusals.get("pass") is True
        and report["repeat_byte_identical"] is True)
    return report


def _persist(path: Path, report: dict, no_write: bool) -> tuple[dict, bool]:
    if no_write:
        return report, path.is_file() and path.read_bytes() == _json_bytes(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(report))
    return report, True


def g1(*, no_write: bool = False) -> int:
    report = _run_g1(no_write=no_write)
    _, persisted = _persist(G1, report, no_write)
    report["persisted_result_matches"] = persisted
    report["pass"] = report["pass"] and persisted
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["pass"] else 1


def g2(*, no_write: bool = False) -> int:
    # Full frozen replay includes source/seal G0 before the complete G1 campaign.
    # Defer G0 diagnostics until after the full replay has been compared.
    with contextlib.redirect_stdout(io.StringIO()):
        g0_ok = g0(no_write=True) == 0
    replay = _run_g1(no_write=False, output_name="g2")
    original = _read_json(G1) if G1.is_file() else None
    g1_same = (isinstance(original, dict) and original.get("pass") is True
        and replay.get("pass") is True and G1.read_bytes() == _json_bytes(replay))
    report = {"schema":"actinv-p116-twin-waste-g2-1","phase":"P116",
        "protocol_sha256":PROTOCOL_SHA256,"pass":bool(g0_ok and g1_same),
        "g0_exact_replay_equal":g0_ok,"exact_replay_equal":g1_same,
        "separate_output_paths_byte_identical":replay.get("repeat_byte_identical") is True,
        "g0_result_sha256":_sha(G0),"g1_result_sha256":_sha(G1)}
    _, persisted = _persist(G2, report, no_write)
    report["persisted_result_matches"] = persisted
    report["pass"] = report["pass"] and persisted
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--g0-only", action="store_true")
    group.add_argument("--g1-only", action="store_true")
    group.add_argument("--g2-only", action="store_true")
    parser.add_argument("--seal", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.g0_only or args.seal:
        return g0(seal=args.seal and not args.no_write, no_write=args.no_write)
    if args.g1_only:
        return g1(no_write=args.no_write)
    if args.g2_only:
        return g2(no_write=args.no_write)
    if g0(no_write=args.no_write) != 0:
        return 1
    if g1(no_write=args.no_write) != 0:
        return 1
    return g2(no_write=args.no_write)


if __name__ == "__main__":
    raise SystemExit(main())
