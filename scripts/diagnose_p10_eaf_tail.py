#!/usr/bin/env python3
"""Classify uncached P10 EAF sources without mutating the production cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import tempfile
import time


ADDRESS_SPACE_BYTES = 4 * 1024**3
BLOCK_BYTES = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def cached_source_hashes(cache: Path) -> set[str]:
    hashes = set()
    for path in cache.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("schema") == "actinv-target-checkpoint-2":
            hashes.add(str(record.get("source_sha256")))
    return hashes


def limit_child() -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def classify(
    binary: Path,
    source: Path,
    output: Path,
    cache: Path,
    timeout_seconds: float,
) -> dict[str, object]:
    command = [
        str(binary),
        "build-library",
        str(source),
        str(output),
        "--format",
        "eaf",
        "--projectile",
        "neutron",
        "--groups",
        "fispact-709",
        "--temperature-K",
        "293.6",
        "--workers",
        "1",
        "--cache",
        str(cache),
    ]
    environment = os.environ.copy()
    environment["OPENBLAS_NUM_THREADS"] = "1"
    environment["OMP_NUM_THREADS"] = "1"
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        preexec_fn=limit_child,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        status = "success" if process.returncode == 0 else "failure"
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        status = "timeout"
    stable_stderr = stderr.replace(str(source), source.name).replace(
        str(output.parent), "<WORK>"
    )
    return {
        "file": source.name,
        "source_sha256": sha256(source),
        "status": status,
        "returncode": process.returncode,
        "seconds": time.monotonic() - started,
        "stdout_tail": stdout.strip().splitlines()[-1:] or [],
        "stderr_tail": stable_stderr.strip().splitlines()[-3:] or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("production_cache", type=Path)
    parser.add_argument("--binary", type=Path, default=Path("target/release/actinv"))
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    arguments = parser.parse_args()
    seen = cached_source_hashes(arguments.production_cache)
    files = sorted(
        (
            path
            for path in arguments.source.iterdir()
            if path.is_file() and sha256(path) not in seen
        ),
        key=lambda path: path.name.encode(),
    )
    records = []
    with tempfile.TemporaryDirectory(prefix="actinv-p10-eaf-tail-") as raw:
        work = Path(raw)
        cache = work / "cache"
        for index, source in enumerate(files):
            record = classify(
                arguments.binary.resolve(),
                source,
                work / f"target-{index}.npz",
                cache,
                arguments.timeout_seconds,
            )
            records.append(record)
            print(
                f"{index + 1}/{len(files)} {source.name}: "
                f"{record['status']} in {record['seconds']:.3f} s",
                flush=True,
            )
    summary = {
        "schema": "actinv-p10-eaf-tail-diagnostic-1",
        "address_space_limit_bytes": ADDRESS_SPACE_BYTES,
        "timeout_seconds": arguments.timeout_seconds,
        "already_cached_sources": len(seen),
        "classified_sources": len(records),
        "status_counts": {
            status: sum(record["status"] == status for record in records)
            for status in ("success", "failure", "timeout")
        },
        "records": records,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
