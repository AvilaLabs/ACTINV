#!/usr/bin/env python3
"""Run ACTINV's verification suites serially inside an already bounded cgroup.

Invoke using the systemd-run command in docs/maintainers/VERIFICATION.md.
Tool failures and timeouts fail the suite; a timeout is never a passed check.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from check_verification_lock import check as check_lock

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "verification"
MANIFEST = "verification/Cargo.toml"
TOOLS = tomllib.loads((ROOT / "verification/tools.toml").read_text())
SUITES = ("properties", "kani", "fuzz", "mutants", "loom", "miri")
KANI_HARNESSES = (
    "quantity_constructors_cover_every_binary64_encoding",
    "flux_to_rate_conversion_preserves_finite_nonnegative_domain",
    *(f"csc_shift_pattern_{mask:02x}" for mask in range(16)),
    "endf_full_ascii_record_preserves_field_boundaries",
)
KANI_EXPERIMENTAL_HARNESSES = (
    *(f"endf_short_ascii_records_{low:02d}_{low+10:02d}" for low in range(0,66,11)),
    "endf_non_ascii_record_is_rejected",
    "endf_non_ascii_with_valid_tail_is_rejected",
)


def verify_limits() -> dict[str, str]:
    if sys.platform != "linux":
        raise RuntimeError("this runner requires Linux cgroup v2; use a bounded Linux CI runner")
    entries = Path("/proc/self/cgroup").read_text().splitlines()
    paths = [line[3:] for line in entries if line.startswith("0::")]
    if len(paths) != 1 or ".." in Path(paths[0]).parts:
        raise RuntimeError("cannot locate the current cgroup v2")
    directory = Path("/sys/fs/cgroup") / paths[0].lstrip("/")
    limits = {name: (directory / name).read_text().strip() for name in
              ("memory.max", "memory.swap.max", "pids.max", "cpu.max")}
    if limits["memory.max"] == "max" or not 0 < int(limits["memory.max"]) <= 6 * 1024**3:
        raise RuntimeError("memory.max must be at most 6 GiB")
    if limits["memory.swap.max"] != "0":
        raise RuntimeError("memory.swap.max must be 0")
    if limits["pids.max"] == "max" or not 0 < int(limits["pids.max"]) <= 128:
        raise RuntimeError("pids.max must be at most 128")
    quota, period = limits["cpu.max"].split()
    if quota == "max" or not 0 < int(quota) <= 2 * int(period):
        raise RuntimeError("cpu.max must enforce at most two CPUs")
    return limits


def run(command: list[str], seconds: int, env: dict[str, str], expected_version: str | None = None) -> dict:
    print(f"[verify] {shlex.join(command)} (limit {seconds}s)", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True,
        stdout=subprocess.PIPE if expected_version is not None else None,
        stderr=subprocess.STDOUT if expected_version is not None else None, text=True)
    timed_out = False
    observed = ""
    try:
        if expected_version is None:
            code = process.wait(timeout=seconds)
        else:
            observed,_ = process.communicate(timeout=seconds)
            code = process.returncode
            print(observed, end="", flush=True)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        timed_out = True
        # All descendants stay in the enforced cgroup. Kill this job's process
        # group only; never touch another assistant's or user's processes.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        code = process.wait(timeout=5)
    result = {"command": command, "seconds": round(time.monotonic()-started, 3),
              "returncode": code, "timed_out": timed_out, "passed": code == 0 and not timed_out}
    if expected_version is not None:
        result.update(observed_version=observed.strip(), expected_version=expected_version)
        pattern = r"(?<![0-9A-Za-z.+-])"+re.escape(expected_version)+r"(?![0-9A-Za-z.+-])"
        result["passed"] = result["passed"] and re.search(pattern,observed) is not None
    return result


def version_probes(suite: str) -> list[tuple[list[str],str]]:
    probes = [(["cargo", f"+{TOOLS['rust']}", "--version"], TOOLS["rust"])]
    if suite in {"kani","mutants","fuzz"}:
        tool = {"kani":"kani","mutants":"cargo-mutants","fuzz":"cargo-fuzz"}[suite]
        probes.append((["cargo",suite,"--version"],TOOLS[tool]))
    if suite == "miri":
        probes.append((["cargo",f"+{TOOLS['nightly']}","miri","--version"],"miri"))
    return probes


def commands(suite: str, extended: bool, harness: str | None = None, fuzz_target: str | None = None) -> list[tuple[list[str], int]]:
    cargo = ["cargo", f"+{TOOLS['rust']}"]
    nightly = ["cargo", f"+{TOOLS['nightly']}"]
    common = ["--manifest-path", MANIFEST, "--locked"]
    if suite == "workspace":
        targets = ["--workspace","--all-targets","--all-features","--locked"]
        return [(cargo+["fmt","--all","--","--check"],60),
                (cargo+["fmt","--manifest-path",MANIFEST,"--all","--","--check"],60),
                (cargo+["check",*targets],1800),
                (cargo+["clippy",*targets,"--","-D","warnings"],1800),
                (cargo+["test",*targets],1800),
                (cargo+["clippy",*common,"--workspace","--all-targets","--all-features","--","-D","warnings"],900)]
    if suite == "properties":
        return [(cargo+["test", *common, "-p", "actinv-verification-kernels", "--test", "properties", "--test", "worker_protocol"], 600),
                (cargo+["test", *common, "-p", "actinv-verification-properties"], 600)]
    if suite == "loom":
        return [(cargo+["test", *common, "-p", "actinv-verification-kernels", "--test", "loom_worker"], 300)]
    if suite == "kani":
        # Routine checks contain completed proofs. Rejection experiments remain
        # explicit selections; failure or timeout still fails their report.
        harnesses = [harness] if harness else KANI_HARNESSES
        return [(["cargo", "kani", "--manifest-path", MANIFEST, "-p", "actinv-verification-kernels",
                  "--lib", "--output-format", "terse",
                  "--log-file",str(WORK / f"kani-{harness}.log"),"--harness", harness], 300) for harness in harnesses]
    if suite == "fuzz":
        result = [( [sys.executable, "scripts/verification_corpus.py"], 30)]
        for target in ("endf", "npz", "spec"):
            if fuzz_target and target != fuzz_target:
                continue
            corpus = str(WORK / "corpus" / target)
            features = ["--features",target] if target != "endf" else []
            result.append((nightly+["fuzz", "run", "--codegen-units", "16", *features, "--fuzz-dir", "verification/fuzz", target, corpus,
                "--", f"-max_total_time={300 if extended else 20}", "-timeout=5", "-rss_limit_mb=2048",
                "-verbosity=0", "-print_final_stats=1",
                "-max_len=65536", f"-artifact_prefix={WORK / 'artifacts'}/"], 1800 if target == "spec" else 900))
        return result
    if suite == "mutants":
        snapshot = mutation_snapshot()
        # cargo-mutants enforces serial execution for --in-place and rejects
        # --jobs even when its value is 1.
        return [(cargo+["mutants", "--config", "verification/mutants.toml",
            "--manifest-path", str(snapshot / "Cargo.toml"), "--in-place",
            "--timeout", "60", "--build-timeout", "600", "--output", str(WORK / "mutation"),
            "--test-package", "actinv-core", "--cargo-arg=--lib"], 1800)]
    if suite == "miri":
        return [(nightly+["miri", "test", *common, "-p", "actinv-verification-kernels",
                          "--test", "properties", "--test", "worker_protocol"], 900)]
    raise ValueError(suite)


def mutation_snapshot() -> Path:
    """Include tracked worktree content and our additions, never unrelated untracked files."""
    snapshot = WORK / "mutation-source"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir()
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, timeout=10).split(b"\0")
    additions = subprocess.check_output(["git", "ls-files", "-z", "--others", "--exclude-standard", "--",
        "verification", "crates/actinv-gui/src/worker_wait.rs", "scripts/verify.py",
        "scripts/test_verify.py", "scripts/verification_corpus.py"], cwd=ROOT, timeout=10).split(b"\0")
    for raw in set(tracked + additions):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        source = ROOT / relative
        if relative.is_absolute() or ".." in relative.parts or source.is_symlink():
            raise RuntimeError(f"unsafe snapshot path: {relative}")
        if not source.exists():  # A tracked deletion remains deleted in the snapshot.
            continue
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return snapshot


def checked_mutation_summary(summary: dict) -> bool:
    return (summary.get("total_mutants",0) > 0 and summary.get("caught",0) > 0
            and summary.get("missed",0) == 0 and summary.get("timeout",0) == 0
            and summary.get("end_time") is not None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=(*SUITES, "workspace", "all"))
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--harness",choices=KANI_HARNESSES+KANI_EXPERIMENTAL_HARNESSES)
    parser.add_argument("--target",choices=("endf","npz","spec"))
    args = parser.parse_args()
    if args.harness and args.suite != "kani":
        parser.error("--harness requires the kani suite")
    if args.target and args.suite != "fuzz":
        parser.error("--target requires the fuzz suite")
    limits = verify_limits()  # Fail before starting any compiler or executable test.
    check_lock()
    WORK.mkdir(parents=True, exist_ok=True)
    # The lock prevents two invocations of this runner sharing a build directory.
    with (WORK / "runner.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another verification runner is active; wait for it to finish") from error
        temporary = WORK / "tmp"
        temporary.mkdir(exist_ok=True)
        filesystem = subprocess.check_output(["findmnt", "--noheadings", "--output", "FSTYPE", "--target", str(temporary)], text=True, timeout=5).strip()
        if filesystem in {"tmpfs", "ramfs"}:
            raise RuntimeError("verification temporary storage must be disk backed")
        (WORK / "artifacts").mkdir(exist_ok=True)
        env = os.environ.copy()
        env.update(TMPDIR=str(temporary), CARGO_BUILD_JOBS="1", RUST_TEST_THREADS="1", RAYON_NUM_THREADS="2",
                   CARGO_TARGET_DIR=str(WORK / "build"), PROPTEST_CASES="2048" if args.extended else "128")
        if args.suite == "miri":
            env["PROPTEST_CASES"] = "8"
            env["PROPTEST_RNG_SEED"] = "159"
        report = {"started_at":datetime.now(timezone.utc).isoformat(), "limits": limits,
                  "required_tools": TOOLS, "extended": args.extended, "steps": [], "passed": False}
        selected = args.harness or args.target
        output = WORK / f"{args.suite}{'-'+selected if selected else ''}.json"
        try:
            for suite in SUITES if args.suite == "all" else (args.suite,):
                suite_env = env.copy()
                if suite == "miri":
                    suite_env.update(PROPTEST_CASES="8", PROPTEST_RNG_SEED="159",PROPTEST_DISABLE_FAILURE_PERSISTENCE="1")
                    # Miri isolates the interpreted program from host env vars.
                    # Set only these test controls inside the interpreter, keeping
                    # filesystem isolation and every memory/UB check enabled.
                    suite_env["MIRIFLAGS"] = (
                        "-Zmiri-env-set=PROPTEST_CASES=8 "
                        "-Zmiri-env-set=PROPTEST_RNG_SEED=159 "
                        "-Zmiri-env-set=PROPTEST_DISABLE_FAILURE_PERSISTENCE=1 "
                        "-Zmiri-env-set=RUST_TEST_THREADS=1"
                    )
                for command,version in version_probes(suite):
                    result = run(command,30,suite_env,version)
                    report["steps"].append({"suite":suite,**result})
                    output.write_text(json.dumps(report,indent=2)+"\n")
                    if not result["passed"]:
                        return 1
                for command, seconds in commands(suite, args.extended,args.harness,args.target):
                    result = run(command, seconds, suite_env)
                    if suite == "mutants":
                        path = WORK / "mutation/mutants.out/outcomes.json"
                        summary = json.loads(path.read_text()) if path.exists() else {}
                        result["mutation_summary"] = {key:value for key,value in summary.items() if key != "outcomes"}
                        result["passed"] = result["passed"] and checked_mutation_summary(summary)
                    report["steps"].append({"suite": suite, **result})
                    output.write_text(json.dumps(report, indent=2)+"\n")
                    if not result["passed"]:
                        return 1
            report["passed"] = True
            return 0
        finally:
            output.write_text(json.dumps(report, indent=2)+"\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[verify] {error}", file=sys.stderr)
        raise SystemExit(1)
