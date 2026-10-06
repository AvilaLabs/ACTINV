#!/usr/bin/env python3
"""Replay the frozen P103–P118 workflow against its registered source commit.

The caller is responsible for the local systemd scope. Each build/gate child is
bounded and its actual argv, exit, output, and hashes are retained under target.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = "d1d14b33471f1286baa50f3ffd6615cfd11070a1"
WORKFLOW_SHA256 = "e60c5a22639d6f8890612ca713df84b0d0affda594616ef7e710fc8d779b3c9b"
PROTOCOL_SHA256 = "9707a33b791a57e3435039478c4c475415754d84b66d33fb8dda820b072245de"
MAX_SCIENCE_S = 1200
MAX_BUILD_S = 1800

class ReplayError(RuntimeError):
    pass


class ReplayCancelled(ReplayError):
    """The caller asked the replay to stop; all active children were reaped."""

    def __init__(self, message: str, child_exit_code: int | None = None):
        super().__init__(message)
        self.child_exit_code = child_exit_code


_ACTIVE_CHILD: subprocess.Popen | None = None
_CANCEL_SIGNAL: int | None = None


def _cancel_handler(signum, _frame) -> None:
    global _CANCEL_SIGNAL
    _CANCEL_SIGNAL = signum
    child = _ACTIVE_CHILD
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _check_cancelled(child_exit_code: int | None = None) -> None:
    if _CANCEL_SIGNAL is not None:
        name = signal.Signals(_CANCEL_SIGNAL).name
        raise ReplayCancelled(f"historical replay cancelled by {name}", child_exit_code)


def _cancellation_guard():
    """Install scoped handlers so caller cancellation also stops gate process groups."""
    from contextlib import contextmanager

    @contextmanager
    def guard():
        global _CANCEL_SIGNAL
        previous = {signum: signal.getsignal(signum)
                    for signum in (signal.SIGTERM, signal.SIGINT)}
        _CANCEL_SIGNAL = None
        try:
            for signum in previous:
                signal.signal(signum, _cancel_handler)
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            _CANCEL_SIGNAL = None
    return guard()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _terminate_and_reap(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.communicate(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        child.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            child.kill()
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=3)
        finally:
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()


def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: int):
    global _ACTIVE_CHILD
    _check_cancelled()
    child = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    _ACTIVE_CHILD = child
    try:
        if _CANCEL_SIGNAL is not None:
            _cancel_handler(_CANCEL_SIGNAL, None)
        started = time.monotonic()
        cancel_started = None
        kill_sent = False
        while True:
            remaining = float(timeout_s) - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout_s)
            try:
                out, err = child.communicate(timeout=min(remaining, 0.2))
                break
            except subprocess.TimeoutExpired:
                if _CANCEL_SIGNAL is not None:
                    now = time.monotonic()
                    if cancel_started is None:
                        cancel_started = now
                    elif not kill_sent and now - cancel_started >= 3:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        kill_sent = True
    except subprocess.TimeoutExpired:
        _terminate_and_reap(child)
        if _CANCEL_SIGNAL is not None:
            _check_cancelled(child.returncode)
        raise RuntimeError(f"command timed out after {timeout_s}s: {argv!r}")
    except KeyboardInterrupt:
        _terminate_and_reap(child)
        raise
    finally:
        _ACTIVE_CHILD = None
    _check_cancelled(child.returncode)
    return subprocess.CompletedProcess(argv, child.returncode, out, err)


def _frozen_commands(workflow: bytes) -> list[list[str]]:
    """Extract Python invocations from exactly the pinned historical block."""
    marker_a = b"      - name: P103 source-seal integrity (historical)\n"
    marker_b = b"      - name: Unit probe (constant source, analytic answer)\n"
    start = workflow.find(marker_a)
    end = workflow.find(marker_b)
    if start < 0 or end <= start:
        raise ReplayError("pinned workflow historical block boundaries are missing")
    block = workflow[start:end]
    commands = []
    for line in block.decode("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("python "):
            commands.append(stripped.split())
    return commands


def _expected_commands() -> list[list[str]]:
    return [
        ["python", "controls/check_p103.py", "--g0-only", "--no-write"],
        ["python", "controls/check_p103_failure.py"],
        ["python", "controls/check_p104_failure.py"],
        ["python", "controls/test_p105_children.py"],
        ["python", "controls/check_p105.py", "--no-write"],
        ["python", "controls/check_p105_verdict.py"],
        ["python", "controls/test_p106_bounds.py"],
        ["python", "controls/test_p107_seal.py"],
        ["python", "controls/test_p107_history.py"],
        ["python", "controls/check_p107.py", "--no-write"],
        ["python", "controls/check_p107_history.py"],
        ["python", "controls/test_p108_seal.py"],
        ["python", "controls/check_p108.py", "--no-write"],
        ["python", "controls/check_p108_verdict.py"],
        ["python", "controls/test_p109_native_identity.py"],
        ["python", "controls/test_p109_history.py"],
        ["python", "controls/check_p109.py", "--g0-only", "--no-write"],
        ["python", "controls/check_p109_history.py"],
        ["python", "controls/test_p110_seal.py"],
        ["python", "controls/test_p110_native_identity.py"],
        ["python", "controls/check_p110.py", "--no-write"],
        ["python", "controls/check_p110_verdict.py"],
        ["python", "controls/test_p111_oracle.py"],
        ["python", "controls/test_p111_seal.py"],
        ["python", "controls/test_p111_verdict.py"],
        ["python", "controls/test_p111_history.py"],
        ["python", "controls/check_p111.py", "--no-write"],
        ["python", "controls/check_p111_history.py"],
        ["python", "controls/test_p112_seal.py"],
        ["python", "controls/test_p112_verdict.py"],
        ["python", "controls/check_p112.py", "--no-write"],
        ["python", "controls/check_p112_verdict.py"],
        ["python", "controls/test_p113_history.py"],
        ["python", "controls/check_p113_history.py"],
        ["python", "controls/test_p114_history.py"],
        ["python", "controls/check_p114_history.py"],
        ["python", "controls/test_p115_history.py"],
        ["python", "controls/check_p115_history.py"],
        ["python", "controls/test_p116_oracle.py"],
        ["python", "controls/test_p116_seal.py"],
        ["python", "controls/test_p116_verdict.py"],
        ["python", "scripts/test_run_p116_gate.py"],
        ["python", "controls/test_p105_children.py"],
        ["python", "controls/check_p116.py", "--no-write"],
        ["python", "controls/test_p116_history.py"],
        ["python", "controls/check_p116_history.py"],
        ["python", "controls/test_p117_history.py"],
        ["python", "controls/check_p117_history.py"],
        ["python", "controls/test_p118_history.py"],
        ["python", "controls/check_p118_history.py"],
    ]


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _safe_target_path(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ReplayError(f"{label} must be an absolute path")
    absolute = Path(os.path.abspath(path))
    target_root = ROOT / "target"
    try:
        rel = absolute.relative_to(target_root)
    except ValueError:
        raise ReplayError(f"{label} must stay under the repository target directory") from None
    current = ROOT
    for part in ("target", *rel.parts[:-1]):
        current = current / part
        if os.path.lexists(current) and (current.is_symlink() or not current.is_dir()):
            raise ReplayError(f"{label} has an unsafe parent directory")
    if absolute.exists() or absolute.is_symlink():
        raise ReplayError(f"refusing to overwrite existing {label}")
    return absolute


def _record_command(evidence: Path, name: str, argv: list[str], cwd: Path,
                    env: dict[str, str], timeout_s: int, invoke,
                    bindings: dict | None = None) -> dict:
    started = time.monotonic()
    try:
        result = invoke(argv, cwd, env, timeout_s)
        stdout, stderr = str(result.stdout or ""), str(result.stderr or "")
        exit_code = result.returncode if type(result.returncode) is int else None
        error = None
    except Exception as exc:
        stdout, stderr, exit_code = "", "", None
        if isinstance(exc, ReplayCancelled):
            exit_code = exc.child_exit_code
        error = f"{type(exc).__name__}: {exc}"
    log = ("=== stdout ===\n" + stdout + "\n=== stderr ===\n" + stderr
           + ("\n=== runner error ===\n" + error if error else "")).encode("utf-8", errors="backslashreplace")
    log_path = evidence / f"{name}.log"
    _write_new(log_path, log)
    receipt = {"name": name, "argv": argv, "cwd": str(cwd), "timeout_s": timeout_s,
               "elapsed_s": time.monotonic() - started, "child_exit_code": exit_code,
               "status": "completed" if exit_code == 0 and error is None else "failed",
               "error": error, "log_path": str(log_path.relative_to(ROOT)),
               "log_sha256": _sha(log)}
    if bindings:
        receipt.update(bindings)
    _write_new(evidence / f"{name}.json", json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n")
    return receipt


def replay(*, candidate_bin: Path, run_id: str | None = None,
           summary_out: Path | None = None, reference_target_dir: Path | None = None,
           runner=None) -> dict:
    with _cancellation_guard():
        return _replay_impl(candidate_bin=candidate_bin, run_id=run_id,
                            summary_out=summary_out,
                            reference_target_dir=reference_target_dir,
                            runner=runner)


def _replay_impl(*, candidate_bin: Path, run_id: str | None = None,
                 summary_out: Path | None = None, reference_target_dir: Path | None = None,
                 runner=None) -> dict:
    global _CANCEL_SIGNAL
    if not candidate_bin.is_absolute() or not candidate_bin.is_file() or candidate_bin.is_symlink():
        raise ReplayError("candidate binary must be an existing absolute regular file")
    if summary_out is not None:
        summary_out = _safe_target_path(summary_out, "replay summary output")
    workflow_result = _run(["git", "show", f"{BASE}:.github/workflows/ci.yml"], ROOT, os.environ.copy(), 30)
    if workflow_result.returncode != 0:
        raise ReplayError("could not read registered base workflow")
    workflow = workflow_result.stdout.encode()
    if _sha(workflow) != WORKFLOW_SHA256:
        raise ReplayError("registered base workflow SHA-256 mismatch")
    commands = _frozen_commands(workflow)
    if commands != _expected_commands():
        raise ReplayError("historical Python command sequence differs from the registered workflow")

    protocol = ROOT / "protocols/ACTINV-P120_PROTOCOL.md"
    if _sha(protocol.read_bytes()) != PROTOCOL_SHA256:
        raise ReplayError("P120 protocol SHA-256 mismatch")
    run_id = run_id or f"{time.time_ns()}-{os.getpid()}"
    if not run_id.isascii() or not run_id.replace("-", "").isalnum():
        raise ReplayError("run id contains unsafe path characters")
    evidence = ROOT / "target/p120-history" / run_id
    worktree = evidence / "worktree"
    if (ROOT / "target").is_symlink() or not (ROOT / "target").is_dir():
        raise ReplayError("repository target directory is not a regular directory")
    evidence_base = ROOT / "target/p120-history"
    if evidence_base.exists() and (evidence_base.is_symlink() or not evidence_base.is_dir()):
        raise ReplayError("historical evidence directory has an unsafe type")
    if evidence_base.is_symlink():
        raise ReplayError("historical evidence directory is a symlink")
    target_dir = _safe_target_path(reference_target_dir or evidence / "reference-target",
                                   "reference target directory")
    candidate_resolved = candidate_bin.resolve(strict=True)
    if candidate_resolved.is_relative_to(target_dir):
        raise ReplayError("reference target directory would overwrite candidate binary")
    if evidence.exists() or evidence.is_symlink():
        raise ReplayError("refusing to overwrite historical replay evidence")
    evidence.mkdir(parents=True)
    worktree_added = False
    worktree_attempted = False
    invoke = runner or _run
    receipts: list[dict] = []
    failure = None
    summary = {"schema": "actinv-p120-history-replay-1", "base_commit": BASE,
               "workflow_sha256": WORKFLOW_SHA256, "protocol_sha256": PROTOCOL_SHA256,
               "candidate_bin": str(candidate_bin.resolve()),
               "candidate_bin_sha256": _sha(candidate_bin.read_bytes()), "gates": receipts}
    try:
        base_env = os.environ.copy()
        if not base_env.get("GITHUB_ACTIONS"):
            import run_p117_gate
            resources, resource_errors = run_p117_gate.inspect_resources()
            if resource_errors or run_p117_gate._resource_snapshot_errors(resources):
                raise ReplayError(f"local resource guard failed: {resource_errors}")
        worktree_attempted = True
        add_receipt = _record_command(evidence, "worktree-add",
            ["git", "worktree", "add", "--detach", str(worktree), BASE], ROOT,
            base_env, 120, invoke)
        if add_receipt["child_exit_code"] != 0:
            raise ReplayError("could not create detached historical worktree")
        worktree_added = True
        head = _record_command(evidence, "worktree-head", ["git", "rev-parse", "HEAD"],
                               worktree, base_env, 30, invoke)
        status = _record_command(evidence, "worktree-clean", ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                                 worktree, base_env, 30, invoke)
        head_text = (evidence / "worktree-head.log").read_text().split("=== stderr ===")[0].replace("=== stdout ===\n", "").strip()
        status_text = (evidence / "worktree-clean.log").read_text().split("=== stderr ===")[0].replace("=== stdout ===\n", "").strip()
        if (head["child_exit_code"] != 0 or head_text != BASE
                or status["child_exit_code"] != 0 or status_text):
            raise ReplayError("historical worktree HEAD or cleanliness check failed")
        env = os.environ.copy()
        env.update({"CARGO_TARGET_DIR": str(target_dir), "TMPDIR": str(worktree / "target/preflight-tmp"),
                    "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"})
        (worktree / "target/preflight-tmp").mkdir(parents=True)
        build_argv = ["cargo", "build", "--release", "-p", "actinv-cli"]
        build = _record_command(evidence, "reference-build", build_argv, worktree,
            env, MAX_BUILD_S, invoke,
            {key: env[key] for key in ("CARGO_TARGET_DIR", "TMPDIR", "CARGO_BUILD_JOBS",
                                       "RUST_TEST_THREADS", "RAYON_NUM_THREADS")})
        reference_bin = target_dir / "release/actinv"
        if build["child_exit_code"] != 0 or not reference_bin.is_file() or reference_bin.is_symlink():
            raise ReplayError("reference CLI build failed or produced no regular binary")
        reference_bin = reference_bin.resolve(strict=True)
        if reference_bin == candidate_bin.resolve(strict=True):
            raise ReplayError("reference CLI aliases the candidate binary")
        summary.update({"reference_binary": {"path": str(reference_bin),
                                               "sha256": _sha(reference_bin.read_bytes())},
                        "reference_bin": str(reference_bin),
                        "reference_bin_sha256": _sha(reference_bin.read_bytes()),
                        "worktree_head": head_text})
        env["ACTINV_BIN"] = str(reference_bin)
        for index, command in enumerate(commands):
            _check_cancelled()
            name = f"gate-{index + 1:02d}"
            receipt = _record_command(evidence, name, command, worktree, env, MAX_SCIENCE_S, invoke,
                {"candidate_bin": summary["candidate_bin"],
                 "candidate_bin_sha256": summary["candidate_bin_sha256"],
                 "reference_bin": summary["reference_bin"],
                 "reference_bin_sha256": summary["reference_bin_sha256"],
                 "ACTINV_BIN": env["ACTINV_BIN"],
                 "CARGO_BUILD_JOBS": env["CARGO_BUILD_JOBS"],
                 "RUST_TEST_THREADS": env["RUST_TEST_THREADS"],
                 "RAYON_NUM_THREADS": env["RAYON_NUM_THREADS"]})
            receipts.append(receipt)
            if receipt["child_exit_code"] != 0:
                summary["status"] = "failed"
                break
        summary.setdefault("status", "passed" if len(receipts) == len(commands) else "failed")
        if summary["status"] == "passed":
            _check_cancelled()
            final_status = _record_command(evidence, "worktree-final-clean",
                ["git", "status", "--porcelain=v1", "--untracked-files=all"], worktree,
                env, 30, invoke)
            final_status_text = (evidence / "worktree-final-clean.log").read_text().split("=== stderr ===")[0].replace("=== stdout ===\n", "").strip()
            if final_status["child_exit_code"] != 0 or final_status_text:
                raise ReplayError("historical replay modified tracked source or evidence")
            if _sha(candidate_bin.read_bytes()) != summary["candidate_bin_sha256"]:
                raise ReplayError("candidate binary changed during historical replay")
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        summary["status"] = "failed"
    finally:
        if worktree_attempted and (worktree_added or worktree.exists()):
            saved_cancel = _CANCEL_SIGNAL
            if saved_cancel is not None:
                # Honor the first cancellation after cleanup so the owned
                # worktree can still be removed and the failure report saved.
                _CANCEL_SIGNAL = None
            try:
                removed = _record_command(evidence, "worktree-remove",
                    ["git", "worktree", "remove", "--force", str(worktree)], ROOT,
                    os.environ.copy(), 120, invoke)
            finally:
                if saved_cancel is not None and _CANCEL_SIGNAL is None:
                    _CANCEL_SIGNAL = saved_cancel
            if removed["child_exit_code"] != 0:
                summary["cleanup_error"] = "owned historical worktree removal failed"
                summary["status"] = "failed"
    if failure:
        summary["error"] = failure
    if _CANCEL_SIGNAL is not None:
        summary["status"] = "failed"
        summary["error"] = summary.get("error") or f"cancelled by {signal.Signals(_CANCEL_SIGNAL).name}"
    summary_path = evidence / "summary.json"
    summary_bytes = json.dumps(summary, sort_keys=True, indent=2).encode() + b"\n"
    _write_new(summary_path, summary_bytes)
    if summary_out is not None:
        _write_new(summary_out, summary_bytes)
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-bin", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--summary-out", type=Path)
    parser.add_argument("--reference-target-dir", type=Path)
    args = parser.parse_args()
    try:
        summary = replay(candidate_bin=args.candidate_bin, run_id=args.run_id,
                         summary_out=args.summary_out,
                         reference_target_dir=args.reference_target_dir)
    except Exception as exc:
        print(f"P120 historical replay failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
