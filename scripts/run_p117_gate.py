#!/usr/bin/env python3
"""Run and durably record one bounded P117 local quality gate.

The caller must launch this recorder inside the protocol's systemd cgroup.
The recorder inspects that live scope and the required environment before it
delegates the single command to the previously reviewed P105 bounded runner.
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
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
CGROUP_ROOT = Path("/sys/fs/cgroup")
TMP_RELATIVE = Path("target/preflight-tmp")
MAX_TIMEOUT_S = 1200
SCIENCE_TIMEOUT_S = 600
QUALITY_GATES = {
    "rust_fmt", "seed_local_verify", "seed_offline_install",
    "seed_release_download", "native_data_fetch", "fns_science", "fns_diagnosis",
}
GATES = {
    "rust_fmt", "seed_regressions", "p116_history_regressions", "p116_history_replay",
    "p117_regressions", "p117_verdict_regressions", "recorder_regressions",
    "g0_seal", "g0_replay", "g1", "g2", "full_read_only_replay",
    "seed_local_verify", "seed_offline_install", "seed_release_download",
    "native_data_fetch", "fns_science", "fns_diagnosis", "fns_regressions",
}
REQUIRED_ENV = {
    "CARGO_BUILD_JOBS": "1",
    "RUST_TEST_THREADS": "1",
    "RAYON_NUM_THREADS": "2",
}

sys.path.insert(0, str(ROOT / "controls"))
import p105_budget_control  # noqa: E402  (reuse its bounded terminate/kill/reap runner)


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


def validate_invocation(
    phase: str,
    name: str,
    timeout_s: float,
    log_arg: str,
    receipt_arg: str,
    argv: list[str],
) -> tuple[Path, Path]:
    if phase != "P117":
        raise GateError("phase must be P117")
    if name not in GATES:
        raise GateError(f"unknown P117 gate name: {name!r}")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise GateError("timeout must be a finite number of seconds")
    if not math.isfinite(float(timeout_s)) or timeout_s <= 0:
        raise GateError("timeout must be finite and positive")
    maximum = MAX_TIMEOUT_S if name in QUALITY_GATES else SCIENCE_TIMEOUT_S
    if timeout_s > maximum:
        raise GateError(f"{name} timeout exceeds its {maximum}s protocol limit")
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item.strip() for item in argv):
        raise GateError("command argv must be a nonempty list of nonempty strings")
    if _has_placeholder(argv):
        raise GateError("command argv contains an unresolved undefined/None placeholder")

    expected_log = Path("target") / f"p117-{name}.log"
    expected_receipt = Path("results/quality/p117") / f"{name}.json"
    log_path = _resolve_output_path(log_arg, expected_log, "log")
    receipt_path = _resolve_output_path(receipt_arg, expected_receipt, "receipt")
    return log_path, receipt_path


def _resolve_output_path(raw: str, expected_rel: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise GateError(f"{label} path must be nonempty")
    supplied = Path(raw)
    if ".." in supplied.parts:
        raise GateError(f"unsafe {label} path traversal")
    root = ROOT.resolve(strict=True)
    candidate = supplied if supplied.is_absolute() else ROOT / supplied
    expected = root / expected_rel
    try:
        candidate_abs = Path(os.path.abspath(candidate))
        candidate_abs.relative_to(root)
    except (OSError, ValueError):
        raise GateError(f"{label} path escapes repository root") from None
    if candidate_abs != expected:
        raise GateError(f"{label} path must be {expected_rel.as_posix()}")

    # Refuse symlinks in every existing path component. The last component is
    # checked with lexists so dangling symlinks also count as prior evidence.
    current = root
    for part in expected_rel.parts[:-1]:
        current = current / part
        if os.path.lexists(current) and current.is_symlink():
            raise GateError(f"{label} path has a symlink parent")
    if os.path.lexists(expected):
        raise GateError(f"refusing to overwrite existing {label}: {expected_rel.as_posix()}")
    return expected


def _filesystem_for(path: Path, mountinfo: str) -> tuple[str, str]:
    resolved = path.resolve(strict=True)
    matches: list[tuple[int, str, str]] = []
    for line in mountinfo.splitlines():
        pieces = line.split(" - ", 1)
        if len(pieces) != 2:
            continue
        left, right = pieces
        fields = left.split()
        tail = right.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        mount_point = fields[4]
        for escaped, decoded in (("\\040", " "), ("\\011", "\t"), ("\\134", "\\")):
            mount_point = mount_point.replace(escaped, decoded)
        point = Path(mount_point)
        try:
            resolved.relative_to(point)
        except ValueError:
            continue
        matches.append((len(point.parts), str(point), tail[0]))
    if not matches:
        raise GateError("cannot identify TMPDIR backing filesystem from mountinfo")
    _, mount_point, fs_type = max(matches)
    if fs_type in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"}:
        raise GateError(f"TMPDIR is on non-disk filesystem {fs_type}")
    return mount_point, fs_type


def _cgroup_limit_errors(values: dict[str, str]) -> list[str]:
    errors = []
    for name, expected in (("memory.max", "6442450944"),
                           ("memory.swap.max", "0"), ("pids.max", "128")):
        if values.get(name) != expected:
            errors.append(f"{name} must equal {expected}, observed {values.get(name)!r}")
    cpu = values.get("cpu.max", "").split()
    if (len(cpu) != 2 or cpu[0] == "max" or not cpu[0].isdigit()
            or not cpu[1].isdigit() or int(cpu[0]) <= 0 or int(cpu[1]) <= 0):
        errors.append("cpu.max must be a finite positive 200% quota")
    elif int(cpu[0]) * 100 != int(cpu[1]) * 200:
        errors.append(f"cpu.max must equal 200%, observed {' '.join(cpu)!r}")
    return errors


def _resource_snapshot_errors(resources: object) -> list[str]:
    if not isinstance(resources, dict):
        return ["resource inspection must be an object"]
    errors: list[str] = []
    if resources.get("platform") != "linux":
        errors.append("resource inspection platform must be linux")
    cgroup_path = resources.get("cgroup_path")
    if not isinstance(cgroup_path, str) or not cgroup_path.startswith("/") or ".." in PurePosixPath(cgroup_path).parts:
        errors.append("resource inspection has no safe unified cgroup path")
    limits = resources.get("cgroup_limits")
    if not isinstance(limits, dict) or any(not isinstance(value, str) for value in limits.values()):
        errors.append("resource inspection cgroup_limits is malformed")
    else:
        errors.extend(_cgroup_limit_errors(limits))
    environment = resources.get("environment")
    if not isinstance(environment, dict):
        errors.append("resource inspection environment is malformed")
    else:
        for key, expected in REQUIRED_ENV.items():
            if environment.get(key) != expected:
                errors.append(f"resource inspection {key} must equal {expected}")
    tmpdir = resources.get("tmpdir")
    if not isinstance(tmpdir, dict):
        errors.append("resource inspection tmpdir is malformed")
    else:
        if tmpdir.get("path") != TMP_RELATIVE.as_posix():
            errors.append("resource inspection TMPDIR path is incorrect")
        if not isinstance(tmpdir.get("mount_point"), str) or not tmpdir.get("mount_point"):
            errors.append("resource inspection TMPDIR mount point is missing")
        if (not isinstance(tmpdir.get("filesystem"), str)
                or tmpdir.get("filesystem") in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs"}):
            errors.append("resource inspection TMPDIR filesystem is missing or non-disk")
    return errors


def inspect_resources(environ: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    """Read live cgroup, environment and TMPDIR facts without changing them."""
    env = os.environ if environ is None else environ
    observed: dict = {
        "platform": sys.platform,
        "cgroup_path": None,
        "cgroup_limits": {},
        "environment": {key: env.get(key) for key in REQUIRED_ENV},
        "tmpdir": {"path": env.get("TMPDIR"), "mount_point": None, "filesystem": None},
    }
    errors: list[str] = []
    for key, expected in REQUIRED_ENV.items():
        if env.get(key) != expected:
            errors.append(f"{key} must equal {expected}")
    try:
        if sys.platform != "linux":
            raise GateError("P117 gate recording requires Linux cgroup v2")
        entries = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
        unified = [line[3:] for line in entries if line.startswith("0::")]
        if len(unified) != 1 or not unified[0].startswith("/"):
            raise GateError("could not identify one unified cgroup-v2 path")
        relative = PurePosixPath(unified[0])
        if ".." in relative.parts:
            raise GateError("cgroup path contains parent traversal")
        cgroup = CGROUP_ROOT / str(relative).lstrip("/")
        cgroup.resolve(strict=True).relative_to(CGROUP_ROOT.resolve(strict=True))
        observed["cgroup_path"] = str(relative)
        files = ("memory.max", "memory.swap.max", "pids.max")
        for filename in files:
            actual = (cgroup / filename).read_text(encoding="ascii").strip()
            observed["cgroup_limits"][filename] = actual
        cpu = (cgroup / "cpu.max").read_text(encoding="ascii").split()
        observed["cgroup_limits"]["cpu.max"] = " ".join(cpu)
        errors.extend(_cgroup_limit_errors(observed["cgroup_limits"]))
    except (OSError, ValueError, GateError) as error:
        errors.append(f"cgroup inspection failed: {error}")

    try:
        tmp_raw = env.get("TMPDIR", "")
        tmp = Path(tmp_raw)
        expected_tmp = (ROOT / TMP_RELATIVE).resolve(strict=True)
        current = ROOT.resolve(strict=True)
        for part in TMP_RELATIVE.parts:
            current = current / part
            if current.is_symlink():
                raise GateError("target/preflight-tmp path contains a symlink")
        if not tmp.is_absolute() or tmp.resolve(strict=True) != expected_tmp:
            raise GateError(f"TMPDIR must resolve exactly to {TMP_RELATIVE.as_posix()}")
        if tmp.is_symlink() or not tmp.is_dir():
            raise GateError("TMPDIR must be an existing regular directory, not a symlink")
        mount_point, fs_type = _filesystem_for(tmp, Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
        observed["tmpdir"].update({
            "path": TMP_RELATIVE.as_posix(),
            "mount_point": mount_point,
            "filesystem": fs_type,
        })
    except (OSError, ValueError, GateError) as error:
        errors.append(f"TMPDIR inspection failed: {error}")
    return observed, errors


def _atomic_create(path: Path, data: bytes) -> None:
    """Atomically create bytes at path without replacing any existing entry."""
    fd, temp_name = tempfile.mkstemp(prefix=".p117-", suffix=".tmp", dir=path.parent)
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


def _log_bytes(stdout: object = "", stderr: object = "", error: str | None = None) -> bytes:
    parts = [b"=== stdout ===\n", _as_bytes(stdout), b"\n=== stderr ===\n", _as_bytes(stderr)]
    if error is not None:
        parts.extend([b"\n=== recorder error ===\n", error.encode("utf-8", errors="backslashreplace")])
    return b"".join(parts)


def _as_bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="backslashreplace")
    return repr(value).encode("utf-8", errors="backslashreplace")


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
    log_path, receipt_path = validate_invocation(phase, name, timeout_s, log_arg, receipt_arg, argv)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(log_path) or os.path.lexists(receipt_path):
        raise GateError("refusing to overwrite existing gate log or receipt")

    inspect = inspect_resources if inspector is None else inspector
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
        except Exception as exc:  # runner owns process cleanup; record its failure verbatim
            status = "child_failed"
            error = f"{type(exc).__name__}: {exc}"
            result_code = 1

    log_data = _log_bytes(stdout, stderr, error)
    log_sha = _sha(log_data)
    log_rel = log_path.relative_to(ROOT).as_posix()
    receipt_rel = receipt_path.relative_to(ROOT).as_posix()
    receipt = {
        "schema": "actinv-roadmap-gate-receipt-1",
        "phase": phase,
        "gate": name,
        "argv": argv,
        "cwd": ".",
        "timeout_s": timeout_s,
        "status": status,
        "child_exit_code": child_exit_code,
        "log_path": log_rel,
        "log_sha256": log_sha,
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
        # If the receipt itself cannot be committed, its absence remains an
        # explicit unqualified state; never print or infer a passing receipt.
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
        print(f"P117 gate recorder rejected invocation: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
