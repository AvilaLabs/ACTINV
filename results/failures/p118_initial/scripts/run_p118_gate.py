#!/usr/bin/env python3
"""Run and durably record one bounded P118 quality gate.

The caller must launch this recorder inside the protocol's systemd cgroup.
Resource inspection is delegated to the reviewed P117 implementation and the
child command to P105's bounded terminate/kill/reap runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCIENCE_TIMEOUT_S = 600
QUALITY_TIMEOUT_S = 1200
CLEANUP_HEADROOM_S = 15
GATES = {
    "rust_fmt", "p118_regressions", "p118_verdict_regressions",
    "p117_history_regressions", "p117_history_replay", "recorder_regressions",
    "g0_seal", "g0_replay", "g1", "g2", "full_read_only_replay",
}
REQUIRED_ENV = {
    "CARGO_BUILD_JOBS": "1",
    "RUST_TEST_THREADS": "1",
    "RAYON_NUM_THREADS": "2",
}

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "controls"))
import run_p117_gate  # noqa: E402  (reuse pinned resource inspection)
import p105_budget_control  # noqa: E402  (reuse bounded child lifecycle)


class GateError(ValueError):
    """Invalid invocation or unsafe local environment."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _has_placeholder(argv: list[str]) -> bool:
    for token in argv:
        stripped = token.strip()
        if re.match(r"(?i)^(?:undefined|none)(?:\s|$)", stripped):
            return True
        if re.search(r"(?i)(?:^|=)(?:undefined|none)$", stripped):
            return True
    return False


def _safe_parents(root: Path, relpath: Path, *, create: bool = False) -> Path:
    """Check/create expected parent directories without following symlinks."""
    current = root
    if current.is_symlink() or not current.is_dir():
        raise GateError("repository root is not a regular directory")
    for part in relpath.parts[:-1]:
        current = current / part
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise GateError("output path has a symlink or non-directory parent")
        elif create:
            current.mkdir()
            if current.is_symlink() or not current.is_dir():
                raise GateError("output directory creation was redirected")
        else:
            # Missing ancestors are safe at validation time; they are created
            # one at a time only after the invocation itself is accepted.
            break
    return root.joinpath(*relpath.parts)


def _resolve_output_path(raw: str, expected_rel: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise GateError(f"{label} path must be nonempty")
    supplied = Path(raw)
    if ".." in supplied.parts:
        raise GateError(f"unsafe {label} path traversal")
    root = ROOT.resolve(strict=True)
    candidate = supplied if supplied.is_absolute() else ROOT / supplied
    try:
        absolute = Path(os.path.abspath(candidate))
        absolute.relative_to(root)
    except (OSError, ValueError):
        raise GateError(f"{label} path escapes repository root") from None
    expected = root / expected_rel
    if absolute != expected:
        raise GateError(f"{label} path must be {expected_rel.as_posix()}")
    _safe_parents(root, expected_rel)
    if os.path.lexists(expected):
        raise GateError(f"refusing to overwrite existing {label}: {expected_rel.as_posix()}")
    return expected


def validate_invocation(
    phase: str,
    name: str,
    timeout_s: float,
    log_arg: str,
    receipt_arg: str,
    argv: list[str],
) -> tuple[Path, Path]:
    if phase != "P118":
        raise GateError("phase must be P118")
    if name not in GATES:
        raise GateError(f"unknown P118 gate name: {name!r}")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise GateError("timeout must be a finite number of seconds")
    if not math.isfinite(float(timeout_s)) or timeout_s <= 0:
        raise GateError("timeout must be finite and positive")
    maximum = QUALITY_TIMEOUT_S if name == "rust_fmt" else SCIENCE_TIMEOUT_S
    if timeout_s > maximum:
        raise GateError(f"{name} timeout exceeds its {maximum}s protocol limit")
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(item, str) or not item.strip() for item in argv)):
        raise GateError("command argv must be a nonempty list of nonempty strings")
    if _has_placeholder(argv):
        raise GateError("command argv contains an unresolved undefined/None placeholder")

    log_rel = Path("target") / f"p118-{name}.log"
    receipt_rel = Path("results/quality/p118") / f"{name}.json"
    return (_resolve_output_path(log_arg, log_rel, "log"),
            _resolve_output_path(receipt_arg, receipt_rel, "receipt"))


def _make_parent(path: Path) -> None:
    root = ROOT.resolve(strict=True)
    relpath = path.relative_to(root)
    _safe_parents(root, relpath, create=True)


def _atomic_create(path: Path, data: bytes) -> None:
    """Atomically create and fsync bytes without replacing prior evidence."""
    _make_parent(path)
    if os.path.lexists(path):
        raise FileExistsError(path)
    fd, temp_name = tempfile.mkstemp(prefix=".p118-", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
        temp.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.lexists(temp):
            temp.unlink()


def _as_bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="backslashreplace")
    return repr(value).encode("utf-8", errors="backslashreplace")


def _log_bytes(stdout: object = "", stderr: object = "", error: str | None = None) -> bytes:
    parts = [b"=== stdout ===\n", _as_bytes(stdout), b"\n=== stderr ===\n", _as_bytes(stderr)]
    if error is not None:
        parts.extend([b"\n=== recorder error ===\n", error.encode("utf-8", errors="backslashreplace")])
    return b"".join(parts)


def _resource_snapshot_errors(resources: object) -> list[str]:
    if not isinstance(resources, dict):
        return ["resource inspection must be an object"]
    errors: list[str] = []
    if resources.get("platform") != "linux":
        errors.append("resource inspection platform must be linux")
    cgroup_path = resources.get("cgroup_path")
    cgroup_parts = cgroup_path.split("/") if isinstance(cgroup_path, str) else []
    if (not isinstance(cgroup_path, str) or not cgroup_path.startswith("/user.slice/")
            or not cgroup_path.endswith(".scope")
            or any(part in {".", ".."} for part in cgroup_parts)
            or any(not part for part in cgroup_parts[1:])):
        errors.append("resource inspection has no safe unified cgroup path")
    limits = resources.get("cgroup_limits")
    if limits != {"memory.max": "6442450944", "memory.swap.max": "0",
                  "pids.max": "128", "cpu.max": "200000 100000"}:
        errors.append("resource inspection cgroup limits do not match P118")
    if resources.get("environment") != REQUIRED_ENV:
        errors.append("resource inspection environment does not match P118")
    tmpdir = resources.get("tmpdir")
    mount_point = tmpdir.get("mount_point") if isinstance(tmpdir, dict) else None
    filesystem = tmpdir.get("filesystem") if isinstance(tmpdir, dict) else None
    if (not isinstance(tmpdir, dict) or tmpdir.get("path") != "target/preflight-tmp"
            or not isinstance(mount_point, str) or not mount_point.strip()
            or not isinstance(filesystem, str) or not filesystem.strip()
            or filesystem.strip().lower() in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"}):
        errors.append("resource inspection TMPDIR is missing or non-disk")
    return errors


def run_gate(
    phase: str,
    name: str,
    timeout_s: float,
    log_arg: str,
    receipt_arg: str,
    argv: list[str],
    *,
    runner: Callable | None = None,
    inspector: Callable | None = None,
) -> tuple[dict, int]:
    started = time.monotonic()
    log_path, receipt_path = validate_invocation(phase, name, timeout_s, log_arg, receipt_arg, argv)
    _make_parent(log_path)
    _make_parent(receipt_path)
    if os.path.lexists(log_path) or os.path.lexists(receipt_path):
        raise GateError("refusing to overwrite existing gate log or receipt")

    inspect = run_p117_gate.inspect_resources if inspector is None else inspector
    try:
        resources, setup_errors = inspect()
        if (not isinstance(resources, dict) or not isinstance(setup_errors, list)
                or any(not isinstance(item, str) for item in setup_errors)):
            raise TypeError("resource inspector returned an invalid result")
        setup_errors = [*setup_errors, *_resource_snapshot_errors(resources)]
    except Exception as exc:
        resources = {"inspection_error": f"{type(exc).__name__}: {exc}"}
        setup_errors = [resources["inspection_error"]]

    status = "setup_failed"
    child_exit_code: int | None = None
    stdout: object = ""
    stderr: object = ""
    error = "; ".join(setup_errors) if setup_errors else None
    result_code = 1
    if not setup_errors:
        bounded_runner = p105_budget_control._run if runner is None else runner
        try:
            result = bounded_runner(argv, cwd=ROOT, timeout_s=timeout_s)
            if type(result.returncode) is not int:
                raise RuntimeError("bounded runner returned a non-integer child exit status")
            child_exit_code = result.returncode
            stdout, stderr = result.stdout, result.stderr
            status = "completed" if child_exit_code == 0 else "child_failed"
            result_code = child_exit_code
        except Exception as exc:  # the bounded runner owns child cleanup/reaping
            status = "child_failed"
            error = f"{type(exc).__name__}: {exc}"
            result_code = 1

    elapsed_s = time.monotonic() - started
    if not math.isfinite(elapsed_s) or elapsed_s < 0:
        status, child_exit_code, result_code = "setup_failed", None, 1
        error = "monotonic elapsed time was invalid"
        elapsed_s = 0.0
    elif elapsed_s > float(timeout_s) + CLEANUP_HEADROOM_S:
        status, result_code = "child_failed", 1
        overrun = (f"elapsed time {elapsed_s:.6f}s exceeded selected timeout "
                   f"plus {CLEANUP_HEADROOM_S}s cleanup headroom")
        error = f"{error}; {overrun}" if error else overrun
    log_data = _log_bytes(stdout, stderr, error)
    receipt = {
        "schema": "actinv-roadmap-gate-receipt-1",
        "phase": phase,
        "gate": name,
        "argv": argv,
        "cwd": ".",
        "timeout_s": timeout_s,
        "elapsed_s": elapsed_s,
        "status": status,
        "child_exit_code": child_exit_code,
        "log_path": log_path.relative_to(ROOT).as_posix(),
        "log_sha256": _sha(log_data),
        "resources": resources,
        "error": error,
    }
    try:
        _atomic_create(log_path, log_data)
    except (OSError, ValueError, TypeError) as exc:
        receipt["status"] = "setup_failed"
        receipt["log_sha256"] = None
        persistence_error = f"log persistence failed: {type(exc).__name__}: {exc}"
        receipt["error"] = f"{receipt['error']}; {persistence_error}" if receipt["error"] else persistence_error
        result_code = 1
    try:
        receipt_data = (json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        _atomic_create(receipt_path, receipt_data)
    except (OSError, ValueError, TypeError) as exc:
        print(f"gate result could not be durably recorded: {type(exc).__name__}: {exc}", file=sys.stderr)
        return receipt, 1
    return receipt, result_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--name", "--gate", dest="name", required=True)
    parser.add_argument("--timeout-s", "--timeout", dest="timeout_s", type=float, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        receipt, code = run_gate(args.phase, args.name, args.timeout_s, args.log,
                                 args.receipt, command)
    except (GateError, OSError, ValueError) as exc:
        print(f"P118 gate recorder rejected invocation: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
