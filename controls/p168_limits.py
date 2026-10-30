#!/usr/bin/env python3
"""P168 harness (protocols/ACTINV-P168_PROTOCOL.md): ACTINV's solver against high-precision references in extreme regimes.

For every case of controls/p168/cases.py and each CRAM order, writes the synthetic decay file, library and problem into
a run directory, runs `$ACTINV_BIN run problem.json result.json` under a bounded timeout (killed and reaped at the
limit), scores the result inventory against results/p168/refs (metrics M1-M3, controls/p168/compare.py) and writes
results/p168_limits.json, which controls/check_p168.py judges. Run it inside the bounded cgroup scope of AGENTS.md.
Needs numpy; mpmath is not needed (the references are committed).

Usage: ACTINV_BIN=target/release/actinv p168_limits.py [--run-dir DIR] [--record FILE] [--cases NAME,...]
                                                       [--resume] [--keep-results] [--generate-only]
"""
import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
from p168 import cases, compare, to_actinv  # noqa: E402

REFS = ROOT / "results" / "p168" / "refs"
MANIFEST = ROOT / "results" / "p168" / "refs_manifest.json"
RECORD = ROOT / "results" / "p168_limits.json"
RUN_DIR = ROOT / "target" / "p168-run"
SCHEMA = "actinv-p168-limits-1"
STDERR_TAIL = 2000


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def cgroup_record():
    try:
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return {"path": None}
    base = Path("/sys/fs/cgroup") / relative.lstrip("/")
    record = {"path": relative}
    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        try:
            record[name] = (base / name).read_text().strip()
        except OSError:
            record[name] = None
    return record


def run_actinv(binary, problem, result, stderr_path, timeout):
    """Run one solve in its own process group; kill and reap the group at the timeout. Returns (status, timed_out, wall_s)."""
    started = time.perf_counter()
    timed_out = False
    with open(stderr_path, "wb") as stderr:
        proc = subprocess.Popen([str(binary), "run", str(problem), str(result)], stdout=subprocess.DEVNULL,
                                stderr=stderr, start_new_session=True)
        try:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            try:  # also reaps any worker the solve left in its group
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return proc.returncode, timed_out, time.perf_counter() - started


def tail(path):
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - STDERR_TAIL))
        return stream.read().decode("utf-8", "replace")


def one_run(binary, binary_sha, case, order, run_dir, resume, keep_results, generate_only):
    name = case["name"]
    ref_doc = json.loads((REFS / f"{name}.json").read_text())
    out = run_dir / name / f"cram{order}"
    info = to_actinv.generate(ref_doc, out, order, case["mode"], case["pulses"])
    base = {"case": name, "cram_order": order, "mode": case["mode"], "pulses_run": case["pulses"],
            "steps_scheduled": info["steps"], "decay_only": info["decay_only"], "binary_sha256": binary_sha}
    if generate_only:
        return None
    saved = out / "record.json"
    if resume and saved.is_file():
        previous = json.loads(saved.read_text())
        if previous.get("binary_sha256") == binary_sha and previous.get("steps_scheduled") == info["steps"]:
            print(f"{name} cram{order}: reused {previous['status']}", flush=True)
            return previous
    result, stderr_path = out / "result.json", out / "stderr.txt"
    result.unlink(missing_ok=True)
    status, timed_out, wall = run_actinv(binary, out / "problem.json", result, stderr_path, cases.TIMEOUT_S)
    record = dict(base, wall_s=wall, exit_status=status, timed_out=timed_out, stderr_tail=tail(stderr_path),
                  result_bytes=result.stat().st_size if result.is_file() else None, steps_compared=None, time_error=None)
    scored, reason = None, None
    if status == 0 and not timed_out:
        try:
            steps = json.loads(result.read_text())["steps"]
            if steps and float(steps[0]["t_s"]) == 0.0:
                steps = steps[1:]
            reference = compare.Reference(ref_doc, info["steps"])
            scored = compare.compare_run(reference, steps, case["trace"], info["steps"])
            record["steps_compared"], record["time_error"] = scored["steps_compared"], scored["time_error"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = f"unreadable result: {error}"
    record.update(compare.classify_run(status, timed_out, scored, reason))
    if not keep_results:
        result.unlink(missing_ok=True)
    saved.write_text(json.dumps(record, indent=1))
    first = record["first_failure"]
    print(f"{name} cram{order}: {record['status']} in {wall:.1f} s"
          + (f" (first failure {first['metric']} {first['value']:.2e} at step {first['step']}, {first['nuclide']})" if first else "")
          + (f" [{record['reason']}]" if record["reason"] else ""), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--cases", help="comma-separated case names (default: all; a partial record fails the checker)")
    parser.add_argument("--resume", action="store_true", help="reuse per-run records of this binary in the run directory")
    parser.add_argument("--keep-results", action="store_true", help="keep each result.json (large for the pulse trains)")
    parser.add_argument("--generate-only", action="store_true", help="write the inputs and stop (for actinv validate)")
    args = parser.parse_args()
    if not os.environ.get("ACTINV_BIN"):
        sys.exit("ACTINV_BIN is required")
    binary = Path(os.environ["ACTINV_BIN"]).resolve()
    if not binary.is_file():
        sys.exit(f"ACTINV_BIN {binary} is not a file")
    selected = args.cases.split(",") if args.cases else [case["name"] for case in cases.CASES]
    unknown = [name for name in selected if name not in cases.BY_NAME]
    if unknown:
        sys.exit(f"unknown cases {unknown}")
    binary_sha = sha256(binary)
    status = git("status", "--porcelain", "--untracked-files=no")
    environment = {"binary_sha256": binary_sha, "git_head": git("rev-parse", "HEAD"),
                   "crates_tree": git("rev-parse", "HEAD:crates"), "clean_tree": status == "",
                   "cgroup": cgroup_record(), "python": platform.python_version(), "platform": platform.platform()}
    try:
        import numpy
        environment["numpy"] = numpy.__version__
    except ImportError:
        sys.exit("numpy is required to write the one-group libraries")
    runs = []
    for name in selected:
        for order in cases.ORDERS:
            record = one_run(binary, binary_sha, cases.BY_NAME[name], order, args.run_dir, args.resume,
                             args.keep_results, args.generate_only)
            if record is not None:
                runs.append(record)
    if args.generate_only:
        print(f"inputs written under {args.run_dir}")
        return
    document = {"schema": SCHEMA, "protocol": "ACTINV-P168", "environment": environment,
                "refs_manifest_sha256": sha256(MANIFEST),
                "constants": {"orders": list(cases.ORDERS), "timeout_s": cases.TIMEOUT_S,
                              "thresholds": cases.THRESHOLDS, "activity_fraction": cases.ACTIVITY_FRACTION},
                "runs": runs}
    args.record.write_text(json.dumps(document, indent=1) + "\n")
    print(f"wrote {args.record}")


if __name__ == "__main__":
    main()
