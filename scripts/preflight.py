#!/usr/bin/env python3
"""Run the bounded, release-facing checks against an exact Git snapshot.

The snapshot is exported from the index (or HEAD) into a temporary directory;
untracked files therefore cannot accidentally participate in a release check.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"MANIFEST.sha256", "results/g6_p12_complete.json", "results/verdict_p12.json"}
MEMORY_LIMIT = 6 * 1024**3
PID_LIMIT = 128
CGROUP_GUIDANCE = (
    "run systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 "
    "-p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 "
    "RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 python3 scripts/preflight.py"
)


class PreflightError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def tree_for(which: str) -> str:
    if which == "staged":
        if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0:
            raise PreflightError("staged snapshot requested, but tracked worktree changes are unstaged")
        return git("write-tree")
    if which == "head":
        return git("rev-parse", "HEAD")
    raise ValueError(which)


def verify_manifest(tree: str) -> None:
    paths = [p for p in git("ls-tree", "-r", "--name-only", tree).splitlines() if p not in EXCLUDED]
    expected = []
    for path in sorted(paths):
        content = subprocess.check_output(["git", "show", f"{tree}:{path}"], cwd=ROOT)
        expected.append(f"{hashlib.sha256(content).hexdigest()}  ./{path}\n")
    try:
        actual = subprocess.check_output(["git", "show", f"{tree}:MANIFEST.sha256"], cwd=ROOT).decode()
    except subprocess.CalledProcessError as error:
        raise PreflightError("snapshot does not contain MANIFEST.sha256") from error
    if actual != "".join(expected):
        raise PreflightError("tracked-file manifest does not match the selected snapshot")


def run_step(label: str, command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print(f"[preflight] {label}: {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode:
        raise PreflightError(f"{label} failed with exit code {result.returncode}")


def desktop_executable_name(platform_name: str) -> str:
    return "actinv-gui.exe" if platform_name == "nt" else "actinv-gui"


def linux_safety_precheck() -> None:
    """Require a bounded cgroup v2 before starting any compiler process."""
    if sys.platform != "linux":
        return
    mount = Path("/sys/fs/cgroup")
    try:
        entries = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
        unified = [entry[3:] for entry in entries if entry.startswith("0::")]
        if len(unified) != 1 or not mount.is_dir():
            raise ValueError("unified cgroup v2 is unavailable")
        relative = Path(unified[0].lstrip("/"))
        if ".." in relative.parts:
            raise ValueError("cgroup path contains parent traversal")
        current = mount / relative
        current.relative_to(mount)
        memory_limits: list[int] = []
        pid_limits: list[int] = []
        while True:
            if current == mount:
                break
            for name, limits in (("memory.max", memory_limits), ("pids.max", pid_limits)):
                value = (current / name).read_text(encoding="ascii").strip()
                if value != "max":
                    parsed = int(value)
                    if parsed <= 0:
                        raise ValueError(f"invalid {name} value {value!r}")
                    limits.append(parsed)
            current = current.parent
        if not memory_limits or min(memory_limits) > MEMORY_LIMIT:
            raise ValueError("memory.max is unlimited or exceeds 6 GiB")
        if not pid_limits or min(pid_limits) > PID_LIMIT:
            raise ValueError("pids.max is unlimited or exceeds 128")
    except (OSError, ValueError) as error:
        raise PreflightError(
            f"Linux preflight requires bounded cgroup v2 ({error}); {CGROUP_GUIDANCE}"
        ) from error


def extract_archive(tree: str, destination: Path) -> None:
    process = subprocess.Popen(["git", "archive", tree], cwd=ROOT, stdout=subprocess.PIPE)
    assert process.stdout is not None
    failed = True
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise PreflightError(f"unsafe path in Git snapshot: {member.name}")
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise PreflightError(f"unsupported entry in Git snapshot: {member.name}")
                archive.extract(member, destination)
        failed = False
    finally:
        process.stdout.close()
        if failed and process.poll() is None:
            process.kill()
        process.wait()
    if process.returncode:
        raise PreflightError("could not export selected Git snapshot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", choices=("auto", "staged", "head"), default="auto")
    parser.add_argument("--mode", choices=("quick", "full"), default="full")
    parser.add_argument("--require-native", action="store_true", help="fail unless desktop rendering completes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.require_native and args.mode != "full":
            raise PreflightError("--require-native requires --mode full; quick mode skips desktop checks")
        linux_safety_precheck()
        snapshot = args.snapshot
        if snapshot == "auto":
            if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode:
                raise PreflightError("auto snapshot is ambiguous: tracked worktree changes are unstaged; use --snapshot head or stage them")
            snapshot = "staged" if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode else "head"
        tree = tree_for(snapshot)
        verify_manifest(tree)
        with tempfile.TemporaryDirectory(prefix="actinv-preflight-") as directory:
            checkout = Path(directory) / "snapshot"
            checkout.mkdir()
            extract_archive(tree, checkout)
            env = dict(
                os.environ,
                CARGO_NET_OFFLINE="true",
                CARGO_BUILD_JOBS="1",
                RUST_TEST_THREADS="1",
                RAYON_NUM_THREADS="2",
            )
            target = Path(directory) / "target"
            env["CARGO_TARGET_DIR"] = str(target)
            if args.mode == "full":
                environment = Path(directory) / "venv"
                venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
                python = environment / ("Scripts/python.exe" if (environment / "Scripts").is_dir() else "bin/python")
                if not shutil.which("cargo"):
                    raise PreflightError("full mode requires cargo on PATH")
                if subprocess.run([sys.executable, "-m", "maturin", "--version"], env=env).returncode:
                    raise PreflightError("full mode requires maturin in the selected Python environment")
                if subprocess.run([str(python), "-c", "import numpy"], env=env).returncode:
                    raise PreflightError("full mode requires NumPy for scripts/test_python_objects.py")
            run_step("cargo fmt", ["cargo", "fmt", "--all", "--", "--check"], checkout, env)
            run_step("cargo check", ["cargo", "check", "--workspace", "--all-targets", "--all-features", "--locked"], checkout, env)
            if args.mode == "full":
                run_step("cargo clippy", ["cargo", "clippy", "--workspace", "--all-targets", "--all-features", "--locked", "--", "-D", "warnings"], checkout, env)
                run_step("cargo test", ["cargo", "test", "--workspace", "--all-targets", "--all-features", "--locked"], checkout, env)
                artifacts = Path(directory) / "artifacts"
                artifacts.mkdir()
                run_step("build wheel", [sys.executable, "-m", "maturin", "build", "--manifest-path", "python/Cargo.toml", "--release", "--locked", "--out", str(artifacts)], checkout, env)
                wheels = sorted(artifacts.glob("*.whl"))
                if len(wheels) != 1:
                    raise PreflightError(f"expected one built wheel, found {len(wheels)}")
                run_step("install built wheel", [str(python), "-m", "pip", "install", "--ignore-installed", "--no-deps", str(wheels[0])], checkout, env)
                clean_env = dict(env, PYTHONPATH="", PYTHONHOME="")
                run_step("installed wheel smoke", [str(python), str(checkout / "scripts/smoke_python_wheel.py"), str(wheels[0])], checkout, clean_env)
                run_step("verify wheel import", [str(python), "-c", "import actinv; assert str(actinv.__file__).startswith(str(__import__('sys').prefix))"], checkout, clean_env)
                run_step("Python object regression", [str(python), str(checkout / "scripts/test_python_objects.py")], checkout, clean_env)
                binary = target / "release" / desktop_executable_name(os.name)
                run_step("build desktop", ["cargo", "build", "--release", "--locked", "-p", "actinv-gui"], checkout, env)
                desktop_output = Path(directory) / "desktop-smoke"
                command = [sys.executable, str(checkout / "scripts/desktop_smoke.py"), str(binary), str(desktop_output)]
                if args.require_native:
                    command.append("--require-render")
                run_step("desktop model/native smoke", command, checkout, env)
            else:
                print("[preflight] quick mode: clippy, tests, wheel, Python regression, and desktop checks skipped")
        print(f"PREFLIGHT PASS ({args.mode}; snapshot={snapshot}; tree={tree})")
        return 0
    except (PreflightError, subprocess.CalledProcessError, OSError) as error:
        print(f"PREFLIGHT FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
