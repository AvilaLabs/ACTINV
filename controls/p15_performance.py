#!/usr/bin/env python3
"""P15 G5: interleaved exact-opening/candidate wall, p95, RSS and cold-cache evidence."""
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
import shutil
import statistics
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/p15_performance.json"
OPENING_COMMIT = "5f7289a44c2686505d0e1b40f4b00ef5c8e4a9ab"
OPENING = Path(
    os.environ.get(
        "ACTINV_P15_OPENING_BIN",
        "/tmp/actinv-p15-opening.5W7JZ2/target/release/actinv",
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
    os.environ.get("ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
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
ADDRESS_SPACE_BYTES = 12_000_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
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


def environment(cache: Path | None = None) -> dict[str, str]:
    value = os.environ.copy()
    for name in THREADS:
        value[name] = "1"
    if cache is None:
        value.pop("ACTINV_CACHE_DIR", None)
    else:
        value["ACTINV_CACHE_DIR"] = str(cache)
    return value


def run_once(binary: Path, spec: Path, output: Path, cache: Path | None) -> float:
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
    wall_ms = (time.perf_counter_ns() - started) * 1.0e-6
    if completed.returncode:
        raise RuntimeError(
            f"{binary} failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr[-4000:]}"
        )
    return wall_ms


def timed_rss(binary: Path, spec: Path, output: Path, cache: Path | None) -> tuple[float, int]:
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
    wall_ms = (time.perf_counter_ns() - started) * 1.0e-6
    if completed.returncode:
        raise RuntimeError(f"RSS run failed for {binary}: {completed.stderr[-4000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr)
    if match is None:
        raise RuntimeError("GNU time did not report peak RSS")
    return wall_ms, int(match.group(1)) * 1024


def normalized(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("ms", None)
    return value


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def public_spec(work: Path) -> Path:
    specification = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
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


def compiler_identity() -> str:
    rustc = Path(os.environ.get("RUSTC", Path.home() / ".cargo/bin/rustc"))
    return subprocess.run(
        [str(rustc), "--version", "--verbose"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def cache_inventory(cache: Path) -> dict[str, object]:
    files = sorted(path for path in cache.rglob("*") if path.is_file())
    return {
        "files": [
            {
                "relative_path": path.relative_to(cache).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        ],
        "bytes": sum(path.stat().st_size for path in files),
    }


def planted_comparator_values(candidate: dict[str, object]) -> dict[str, dict[str, object]]:
    mutations: dict[str, dict[str, object]] = {}
    certificate = copy.deepcopy(candidate)
    certificate["certificate"]["inputs"]["library"]["sha256"] = "0" * 64
    mutations["certificate_input_hash"] = certificate
    inventory = copy.deepcopy(candidate)
    inventory["steps"][0]["inventory"][0]["atoms_per_g"] += 1.0
    mutations["inventory_value"] = inventory
    ledger = copy.deepcopy(candidate)
    ledger["ledger"]["decay_daughters_missing"] += 1
    mutations["ledger_value"] = ledger
    return mutations


def planted_comparator_checks(candidate: dict[str, object]) -> dict[str, bool]:
    return {
        name: value != candidate
        for name, value in planted_comparator_values(candidate).items()
    }


def planted_comparator_hashes(candidate: dict[str, object]) -> dict[str, str]:
    return {
        name: canonical_sha256(value)
        for name, value in planted_comparator_values(candidate).items()
    }


def main() -> None:
    if not OPENING.is_file() or not CANDIDATE.is_file():
        raise FileNotFoundError(f"opening/candidate binary missing: {OPENING}, {CANDIDATE}")
    identities = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "expected_sha256": EXPECTED[name],
            "actual_sha256": sha256(path),
        }
        for name, path in FILES.items()
    }
    if any(value["actual_sha256"] != value["expected_sha256"] for value in identities.values()):
        raise AssertionError("production input identity differs")
    resolved_opening = subprocess.run(
        ["git", "rev-parse", OPENING_COMMIT],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="actinv-p15-performance-") as directory:
        work = Path(directory)
        spec = public_spec(work)
        opening_output = work / "opening.json"
        candidate_output = work / "candidate.json"
        candidate_cache = work / "candidate-cache"

        empty_wall_ms, empty_peak_rss = timed_rss(
            CANDIDATE, spec, candidate_output, candidate_cache
        )
        initial_cache = cache_inventory(candidate_cache)

        for index in range(WARMUPS):
            order = ("opening", "candidate") if index % 2 == 0 else ("candidate", "opening")
            for name in order:
                if name == "opening":
                    run_once(OPENING, spec, opening_output, None)
                else:
                    run_once(CANDIDATE, spec, candidate_output, candidate_cache)

        raw: dict[str, list[float]] = {"opening": [], "candidate": []}
        for index in range(SAMPLES):
            order = ("opening", "candidate") if index % 2 == 0 else ("candidate", "opening")
            for name in order:
                if name == "opening":
                    raw[name].append(run_once(OPENING, spec, opening_output, None))
                else:
                    raw[name].append(
                        run_once(CANDIDATE, spec, candidate_output, candidate_cache)
                    )

        _, opening_peak_rss = timed_rss(OPENING, spec, opening_output, None)
        _, candidate_peak_rss = timed_rss(CANDIDATE, spec, candidate_output, candidate_cache)
        opening_result = normalized(opening_output)
        candidate_result = normalized(candidate_output)

        shutil.rmtree(candidate_cache)
        recreated_output = work / "recreated.json"
        recreated_wall_ms = run_once(CANDIDATE, spec, recreated_output, candidate_cache)
        recreated_cache = cache_inventory(candidate_cache)
        recreated_result = normalized(recreated_output)

        opening_stats = stats(raw["opening"])
        candidate_stats = stats(raw["candidate"])
        ratios = {
            "median_wall_candidate_over_opening": candidate_stats["median_ms"]
            / opening_stats["median_ms"],
            "p95_wall_candidate_over_opening": candidate_stats["p95_ms"]
            / opening_stats["p95_ms"],
            "peak_rss_candidate_over_opening": candidate_peak_rss / opening_peak_rss,
        }
        gates = {
            "opening_commit_exact": resolved_opening == OPENING_COMMIT,
            "input_hashes_exact": all(
                value["actual_sha256"] == value["expected_sha256"]
                for value in identities.values()
            ),
            "normalized_result_exact": opening_result == candidate_result == recreated_result,
            "comparator_plants_detected": all(planted_comparator_checks(candidate_result).values()),
            "warm_median_at_most_two_thirds": ratios[
                "median_wall_candidate_over_opening"
            ]
            <= 2.0 / 3.0,
            "warm_p95_at_most_two_thirds": ratios["p95_wall_candidate_over_opening"]
            <= 2.0 / 3.0,
            "warm_peak_rss_at_most_one_half": ratios["peak_rss_candidate_over_opening"]
            <= 0.5,
            "empty_peak_rss_at_most_512_mib": empty_peak_rss <= 512 * 1024**2,
            "empty_wall_at_most_twice_opening_median": empty_wall_ms
            <= 2.0 * opening_stats["median_ms"],
            "deletion_recreates_identical_cache": initial_cache == recreated_cache,
        }
        evidence = {
            "schema": "actinv-p15-performance-1",
            "opening": {
                "commit": resolved_opening,
                "binary": str(OPENING),
                "binary_sha256": sha256(OPENING),
                "wall": opening_stats,
                "peak_rss_bytes": opening_peak_rss,
            },
            "candidate": {
                "binary": str(CANDIDATE),
                "binary_sha256": sha256(CANDIDATE),
                "warm_wall": candidate_stats,
                "warm_peak_rss_bytes": candidate_peak_rss,
                "empty_cache": {
                    "wall_ms": empty_wall_ms,
                    "peak_rss_bytes": empty_peak_rss,
                    "inventory": initial_cache,
                },
                "after_deletion": {
                    "wall_ms": recreated_wall_ms,
                    "inventory": recreated_cache,
                },
            },
            "comparison": {
                "warmups_per_binary": WARMUPS,
                "samples_per_binary": SAMPLES,
                "alternating_order": True,
                "thread_environment": {name: "1" for name in THREADS},
                "ratios": ratios,
                "normalized_result_sha256": {
                    "opening": canonical_sha256(opening_result),
                    "candidate": canonical_sha256(candidate_result),
                    "after_deletion": canonical_sha256(recreated_result),
                },
                "stretch": {
                    "warm_median_at_most_one_second": candidate_stats["median_ms"] <= 1000.0,
                    "warm_peak_rss_at_most_512_mib": candidate_peak_rss <= 512 * 1024**2,
                },
                "comparator_plants": planted_comparator_checks(candidate_result),
                "comparator_plant_sha256": planted_comparator_hashes(candidate_result),
            },
            "inputs": identities,
            "host": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "logical_cpus": os.cpu_count(),
                "compiler": compiler_identity(),
            },
            "gates": gates,
            "pass": all(gates.values()),
        }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=1, sort_keys=True))
    if not evidence["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
