#!/usr/bin/env python3
"""P16 G5: interleaved v1.0.1/candidate warm runtime and peak-RSS compatibility."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import statistics
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/p16_performance.json"
RELEASE_COMMIT = "0332779401363d2f39722efe7a0b7218afcfb270"
RELEASE = Path(
    os.environ.get(
        "ACTINV_P16_RELEASE_BIN", ROOT / "target/p16-opening-target/release/actinv"
    )
)
CANDIDATE = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA = Path(os.environ.get("ACTINV_DATA_ROOT", "/home/connoravila/nuclear-data"))
LIBRARY = Path(
    os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz")
)
INDEX = Path(str(LIBRARY).removesuffix(".npz") + "_index.json")
DECAY_PRIMARY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    )
)
DECAY_FALLBACK = Path(
    os.environ.get(
        "ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"
    )
)
EXPECTED = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
}
FILES = {
    "activation_library": LIBRARY,
    "activation_index": INDEX,
    "decay_primary": DECAY_PRIMARY,
    "decay_fallback": DECAY_FALLBACK,
}
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
WARMUPS = 5
SAMPLES = 15
ADDRESS_SPACE_BYTES = 2 * 1024**3
MEDIAN_RATIO_LIMIT = 1.10
P95_RATIO_LIMIT = 1.15
RSS_RATIO_LIMIT = 1.10
RSS_ABSOLUTE_ALLOWANCE = 16 * 1024**2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def stats(values: list[float]) -> dict[str, object]:
    return {
        "samples": len(values),
        "raw_ms": values,
        "minimum_ms": min(values),
        "median_ms": statistics.median(values),
        "p95_ms": quantile(values, 0.95),
        "mean_ms": statistics.fmean(values),
        "sample_standard_deviation_ms": statistics.stdev(values),
    }


def limit_address_space() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        value[name] = "1"
    return value


def run_once(binary: Path, spec: Path, output: Path, cache: Path) -> float:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [str(binary), "run", str(spec), str(output)],
        cwd=ROOT,
        env=environment(cache),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        preexec_fn=limit_address_space,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) * 1.0e-6
    if completed.returncode:
        raise RuntimeError(
            f"{binary} failed ({completed.returncode}):\n"
            f"{completed.stdout}{completed.stderr[-4000:]}"
        )
    return elapsed_ms


def timed_rss(binary: Path, spec: Path, output: Path, cache: Path) -> tuple[float, int]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        ["/usr/bin/time", "-v", str(binary), "run", str(spec), str(output)],
        cwd=ROOT,
        env=environment(cache),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        preexec_fn=limit_address_space,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) * 1.0e-6
    if completed.returncode:
        raise RuntimeError(f"RSS run failed for {binary}: {completed.stderr[-4000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr)
    if match is None:
        raise RuntimeError("GNU time did not report peak RSS")
    return elapsed_ms, int(match.group(1)) * 1024


def normalized(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("ms", None)
    return value


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def public_spec(work: Path) -> Path:
    specification = json.loads(
        (ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8")
    )
    specification["library"] = {
        "path": str(LIBRARY),
        "sha256": EXPECTED["activation_library"],
    }
    specification["decay"] = {
        "primary": str(DECAY_PRIMARY),
        "fallback": str(DECAY_FALLBACK),
    }
    path = work / "public.json"
    path.write_text(json.dumps(specification, sort_keys=True) + "\n", encoding="utf-8")
    return path


def command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    return completed.stdout.strip()


def main() -> int:
    if not RELEASE.is_file() or not CANDIDATE.is_file():
        raise FileNotFoundError(f"release/candidate binary missing: {RELEASE}, {CANDIDATE}")
    identities = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "expected_sha256": EXPECTED[name],
            "actual_sha256": sha256(path),
        }
        for name, path in FILES.items()
    }
    if any(row["actual_sha256"] != row["expected_sha256"] for row in identities.values()):
        raise AssertionError("production input identity differs")
    peeled_tag = command(["git", "rev-parse", "v1.0.1^{}"])
    if peeled_tag != RELEASE_COMMIT:
        raise AssertionError(f"v1.0.1 resolves to {peeled_tag}, expected {RELEASE_COMMIT}")

    work = ROOT / "target/p16-performance-work"
    work.mkdir(parents=True, exist_ok=True)
    spec = public_spec(work)
    outputs = {
        "release": work / "release.json",
        "candidate": work / "candidate.json",
    }
    caches = {
        "release": work / "release-cache",
        "candidate": work / "candidate-cache",
    }
    binaries = {"release": RELEASE, "candidate": CANDIDATE}

    cold_ms = {
        name: run_once(binary, spec, outputs[name], caches[name])
        for name, binary in binaries.items()
    }
    for index in range(WARMUPS):
        order = ("release", "candidate") if index % 2 == 0 else ("candidate", "release")
        for name in order:
            run_once(binaries[name], spec, outputs[name], caches[name])

    raw: dict[str, list[float]] = {"release": [], "candidate": []}
    for index in range(SAMPLES):
        order = ("release", "candidate") if index % 2 == 0 else ("candidate", "release")
        for name in order:
            raw[name].append(run_once(binaries[name], spec, outputs[name], caches[name]))

    rss = {}
    rss_wall = {}
    for name in ("release", "candidate"):
        rss_wall[name], rss[name] = timed_rss(
            binaries[name], spec, outputs[name], caches[name]
        )
    release_result = normalized(outputs["release"])
    candidate_result = normalized(outputs["candidate"])
    exact_result = release_result == candidate_result
    planted = copy.deepcopy(candidate_result)
    planted["steps"][0]["heat_W_per_g"]["total"] += 1.0
    plant_rejected = planted != release_result

    measured = {name: stats(values) for name, values in raw.items()}
    median_ratio = measured["candidate"]["median_ms"] / measured["release"]["median_ms"]
    p95_ratio = measured["candidate"]["p95_ms"] / measured["release"]["p95_ms"]
    rss_ratio = rss["candidate"] / rss["release"]
    rss_ceiling = max(
        rss["release"] * RSS_RATIO_LIMIT,
        rss["release"] + RSS_ABSOLUTE_ALLOWANCE,
    )
    checks = {
        "samples": all(row["samples"] == SAMPLES for row in measured.values()),
        "median": median_ratio <= MEDIAN_RATIO_LIMIT,
        "p95": p95_ratio <= P95_RATIO_LIMIT,
        "peak_rss": rss["candidate"] <= rss_ceiling,
        "normalized_result_exact": exact_result,
        "comparator_plant_rejected": plant_rejected,
    }
    output = {
        "schema": "actinv-p16-performance-1",
        "gate": "P16-G5",
        "release_commit": RELEASE_COMMIT,
        "resolved_signed_tag_commit": peeled_tag,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "thread_environment": {name: "1" for name in THREADS},
        },
        "binaries": {
            name: {
                "path": str(binary),
                "bytes": binary.stat().st_size,
                "sha256": sha256(binary),
            }
            for name, binary in binaries.items()
        },
        "inputs": identities,
        "workload": "examples/fns_fe_5min.json with exact data-v1.0.0 paths",
        "warmups_per_binary": WARMUPS,
        "samples_per_binary": SAMPLES,
        "cold_cache_ms": cold_ms,
        "warm": measured,
        "rss_measurement_wall_ms": rss_wall,
        "peak_rss_bytes": rss,
        "ratios": {
            "candidate_over_release_median": median_ratio,
            "candidate_over_release_p95": p95_ratio,
            "candidate_over_release_peak_rss": rss_ratio,
        },
        "limits": {
            "median_ratio": MEDIAN_RATIO_LIMIT,
            "p95_ratio": P95_RATIO_LIMIT,
            "rss_ratio": RSS_RATIO_LIMIT,
            "rss_absolute_allowance_bytes": RSS_ABSOLUTE_ALLOWANCE,
            "effective_rss_ceiling_bytes": rss_ceiling,
        },
        "normalized_result_sha256": canonical_sha256(candidate_result),
        "checks": checks,
    }
    output["pass"] = all(checks.values())
    RESULT.parent.mkdir(exist_ok=True)
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
