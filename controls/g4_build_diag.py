#!/usr/bin/env python3
"""G4 diagnostic candidate library builds.

Builds the per-projectile candidate activation libraries for the P18b G4
diagnostic comparison: the diagnostic-family targets plus the metastable-target
evaluations that populate the residual-state catalog for diagnostic products.

Phase 1 builds each input file as a single-file library into the shared
checkpoint cache; a file that fails under the frozen G3 contract is recorded
with the builder's own diagnostic line and skipped. Phase 2 runs the full
directory build, which loads every passing file from its checkpoint and applies
the cross-source state catalog and product-state mapping in one pass.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
WORK = ROOT / "target/g4-p18b"

PARAMS = {
    "neutron": ("fispact-709", "293.6"),
    "proton": ("fispact-162", "0"),
    "deuteron": ("fispact-162", "0"),
    "alpha": ("fispact-162", "0"),
}


def run_build(source: Path, output: Path, projectile: str, groups: str,
              temperature: str, cache: Path, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(ACTINV), "build-library", str(source), str(output),
            "--format", "tendl", "--projectile", projectile,
            "--groups", groups, "--temperature-K", temperature,
            "--workers", "2", "--cache", str(cache),
        ],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def attempt_one(args):
    source, projectile, groups, temperature, cache, scratch_root = args
    scratch = Path(
        tempfile.mkdtemp(dir=scratch_root, prefix=f"{source.stem}-")
    )
    try:
        completed = run_build(
            source, scratch / "one.npz", projectile, groups, temperature, cache,
            timeout=3600,
        )
        return source, completed
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def build_projectile(projectile: str, jobs: int = 1) -> dict:
    groups, temperature = PARAMS[projectile]
    inputs = sorted((WORK / f"inputs-{projectile}").glob("*.tendl"))
    cache = WORK / f"cache-{projectile}"
    cache.mkdir(exist_ok=True)
    output = WORK / f"candidate-{projectile}.npz"
    failures: dict[str, str] = {}
    failed_dir = WORK / f"failed-{projectile}"
    for prior in failed_dir.glob("*.tendl"):
        failures[prior.name] = "quarantined in an earlier run"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(
        dir=ROOT / "target/preflight-tmp", prefix=f"g4-{projectile}-"
    ) as scratch_root:
        work = [
            (s, projectile, groups, temperature, cache, scratch_root)
            for s in inputs
        ]
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for source, completed in pool.map(attempt_one, work):
                done += 1
                if completed.returncode == 0:
                    continue
                line = (
                    completed.stdout.strip().splitlines()[-1]
                    if completed.stdout else ""
                )
                failures[source.name] = line[:400]
                print(
                    f"{projectile} [{done}/{len(inputs)}] {source.name}: FAIL "
                    f"{line[:110]}",
                    flush=True,
                )
                (WORK / f"failed-{projectile}").mkdir(exist_ok=True)
                source.rename(WORK / f"failed-{projectile}" / source.name)
    remaining = WORK / f"inputs-{projectile}"
    completed = run_build(remaining, output, projectile, groups, temperature, cache)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{projectile}: aggregate build failed after per-file phase:\n"
            f"{completed.stdout[-3000:]}"
        )
    return {
        "projectile": projectile,
        "elapsed_s": round(time.monotonic() - started, 3),
        "built_files": len(list(remaining.glob("*.tendl"))),
        "quarantined": failures,
        "output": str(output),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "index": str(output.with_name(f"{output.stem}_index.json")),
    }


def main() -> int:
    jobs = int(os.environ.get("G4_BUILD_JOBS", "1"))
    report = WORK / "build_report.json"
    merged = {}
    if report.is_file():
        try:
            for r in json.loads(report.read_text()):
                merged[r["projectile"]] = r
        except json.JSONDecodeError:
            pass
    for projectile in sys.argv[1:] or list(PARAMS):
        result = build_projectile(projectile, jobs)
        merged[projectile] = result
        print(
            f"{projectile}: built {result['built_files']} files, "
            f"{len(result['quarantined'])} quarantined, {result['elapsed_s']} s",
            flush=True,
        )
        report.write_text(json.dumps(list(merged.values()), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
