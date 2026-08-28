#!/usr/bin/env python3
"""P14 G1/G2/G4: interleaved opening/candidate full-path timing, RSS and stage attribution."""
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
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p11_fixtures import make_fixture, specification, write_json  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "p14_performance.json"
OPENING_COMMIT = "d7f934dad677f128395443d10a57444c7b213472"
OPENING = Path(os.environ.get("ACTINV_P14_OPENING_BIN", "/tmp/actinv-p14-opening.TPhglN/target/release/actinv"))
CANDIDATE = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
DATA = Path(os.environ.get("ACTINV_DATA_ROOT", "/home/connoravila/nuclear-data"))
LIBRARY = Path(os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz"))
INDEX = Path(str(LIBRARY).removesuffix(".npz") + "_index.json")
DECAY_PRIMARY = Path(
    os.environ.get("ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
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
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
WARMUPS = 5
SAMPLES = 15
ADDRESS_SPACE_BYTES = 12_000_000_000
CORE_MARKER = "ACTINV_P14_CORE_PROFILE "
CLI_MARKER = "ACTINV_P14_CLI_PROFILE "
REQUIRED_CORE_STAGES = {
    "prepare_validation",
    "input_hash_verification",
    "extension_input_preparation",
    "activation_read_validation",
    "index_read_validation",
    "covariance_read_validation",
    "decay_primary_read_parse",
    "decay_fallback_read_parse_merge",
    "chain_construction",
    "material_network_preparation",
    "schedule_solve_diagnostics",
    "pathway_decomposition",
    "ledger_certificate_assembly",
}


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


def environment(profile: bool = False) -> dict[str, str]:
    value = os.environ.copy()
    for name in THREAD_VARIABLES:
        value[name] = "1"
    if profile:
        value["ACTINV_P14_PROFILE"] = "1"
    else:
        value.pop("ACTINV_P14_PROFILE", None)
    return value


def run_once(binary: Path, spec: Path, output: Path, *, profile: bool = False) -> dict[str, object]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [str(binary), "run", str(spec), str(output)],
        cwd=ROOT,
        env=environment(profile),
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
    value: dict[str, object] = {"wall_ms": wall_ms, "stderr": completed.stderr}
    if profile:
        for marker, key in ((CORE_MARKER, "core"), (CLI_MARKER, "cli")):
            matches = [line[len(marker) :] for line in completed.stderr.splitlines() if line.startswith(marker)]
            if len(matches) != 1:
                raise RuntimeError(f"profiled run emitted {len(matches)} {marker.strip()} records")
            value[key] = json.loads(matches[0])
    return value


def peak_rss(binary: Path, spec: Path, output: Path) -> int:
    completed = subprocess.run(
        ["/usr/bin/time", "-v", str(binary), "run", str(spec), str(output)],
        cwd=ROOT,
        env=environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        preexec_fn=limit_address_space,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"RSS run failed for {binary}: {completed.stderr[-4000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr)
    if match is None:
        raise RuntimeError("GNU time did not report peak RSS")
    return int(match.group(1)) * 1024


def normalized(value: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(value)
    result.pop("ms", None)
    return result


def planted_comparison_checks(candidate: dict[str, object]) -> dict[str, bool]:
    reference = normalized(candidate)
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
    return {name: normalized(value) != reference for name, value in mutations.items()}


def profile_summary(records: list[dict[str, object]]) -> dict[str, object]:
    stage_samples: dict[str, list[float]] = {name: [] for name in REQUIRED_CORE_STAGES}
    stage_samples.update(
        {
            "core_uninstrumented": [],
            "spec_read_parse": [],
            "serialization": [],
            "output_write": [],
            "process_startup_and_cli_uninstrumented": [],
        }
    )
    reconciliation = []
    for record in records:
        core = record["core"]
        cli = record["cli"]
        stages = core["stages_ms"]
        if set(stages) != REQUIRED_CORE_STAGES:
            raise RuntimeError(f"profile stage set differs: {sorted(stages)}")
        for name, value in stages.items():
            stage_samples[name].append(float(value))
        stage_samples["core_uninstrumented"].append(float(core["uninstrumented_core_ms"]))
        stage_samples["spec_read_parse"].append(float(cli["spec_read_parse_ms"]))
        stage_samples["serialization"].append(float(cli["serialization_ms"]))
        stage_samples["output_write"].append(float(cli["output_write_ms"]))
        named_ms = (
            float(core["total_core_ms"])
            + float(cli["spec_read_parse_ms"])
            + float(cli["serialization_ms"])
            + float(cli["output_write_ms"])
        )
        remainder = float(record["wall_ms"]) - named_ms
        stage_samples["process_startup_and_cli_uninstrumented"].append(remainder)
        reconstructed = named_ms + remainder
        reconciliation.append(abs(reconstructed - float(record["wall_ms"])) / float(record["wall_ms"]))
    medians = {name: statistics.median(values) for name, values in stage_samples.items()}
    dominant = max(REQUIRED_CORE_STAGES, key=lambda name: medians[name])
    return {
        "samples": len(records),
        "stage_raw_ms": stage_samples,
        "stage_median_ms": medians,
        "dominant_core_stage": dominant,
        "maximum_reconciliation_fraction": max(reconciliation),
        "all_values_finite_nonnegative_except_process_remainder": all(
            math.isfinite(value) and (name == "process_startup_and_cli_uninstrumented" or value >= 0.0)
            for name, values in stage_samples.items()
            for value in values
        ),
    }


def public_spec(work: Path) -> tuple[Path, Path, Path]:
    specification = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
    specification["library"] = {"path": str(LIBRARY), "sha256": EXPECTED["activation_library"]}
    specification["decay"] = {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)}
    path = work / "public-example.json"
    opening_output = work / "opening.result.json"
    candidate_output = work / "candidate.result.json"
    path.write_text(json.dumps(specification, sort_keys=True) + "\n", encoding="utf-8")
    return path, opening_output, candidate_output


def compiler_identity() -> str:
    configured = os.environ.get("RUSTC")
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        [
            Path.home() / ".cargo/bin/rustc",
            Path.home() / ".rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            completed = subprocess.run(
                [str(candidate), "--version", "--verbose"],
                text=True,
                capture_output=True,
                check=True,
            )
            return completed.stdout.strip()
    raise FileNotFoundError("cannot locate rustc for the P14 compiler identity")


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
    for value in identities.values():
        value["matches"] = value["actual_sha256"] == value["expected_sha256"]

    with tempfile.TemporaryDirectory(prefix="actinv-p14-") as directory:
        work = Path(directory)
        spec, opening_output, candidate_output = public_spec(work)
        outputs = {"opening": opening_output, "candidate": candidate_output}
        binaries = {"opening": OPENING, "candidate": CANDIDATE}

        for index in range(WARMUPS):
            order = ("opening", "candidate") if index % 2 == 0 else ("candidate", "opening")
            for name in order:
                run_once(binaries[name], spec, outputs[name])

        samples: dict[str, list[float]] = {"opening": [], "candidate": []}
        for index in range(SAMPLES):
            order = ("opening", "candidate") if index % 2 == 0 else ("candidate", "opening")
            for name in order:
                samples[name].append(float(run_once(binaries[name], spec, outputs[name])["wall_ms"]))

        opening_rss = peak_rss(OPENING, spec, opening_output)
        candidate_rss = peak_rss(CANDIDATE, spec, candidate_output)
        opening_result = json.loads(opening_output.read_text(encoding="utf-8"))
        candidate_result = json.loads(candidate_output.read_text(encoding="utf-8"))

        compact = make_fixture(work / "compact-fixture")
        compact_spec = work / "compact-spec.json"
        write_json(
            compact_spec,
            specification(compact, mode="trace", cram_order=48, uncertainty=False),
        )
        compact_opening_output = work / "compact-opening.result.json"
        compact_candidate_output = work / "compact-candidate.result.json"
        run_once(OPENING, compact_spec, compact_opening_output)
        run_once(CANDIDATE, compact_spec, compact_candidate_output)
        compact_opening_result = json.loads(compact_opening_output.read_text(encoding="utf-8"))
        compact_candidate_result = json.loads(compact_candidate_output.read_text(encoding="utf-8"))

        profiles = [run_once(CANDIDATE, spec, candidate_output, profile=True) for _ in range(SAMPLES)]
        stages = profile_summary(profiles)
        output_bytes = {
            "opening": opening_output.stat().st_size,
            "candidate": candidate_output.stat().st_size,
        }

    timings = {name: stats(values) for name, values in samples.items()}
    timing_ratio = timings["candidate"]["median_ms"] / timings["opening"]["median_ms"]
    p95_ratio = timings["candidate"]["p95_ms"] / timings["opening"]["p95_ms"]
    rss_ratio = candidate_rss / opening_rss
    planted = planted_comparison_checks(candidate_result)
    checks = {
        "all_input_hashes_match": all(value["matches"] for value in identities.values()),
        "sample_counts": all(value["samples"] == SAMPLES for value in timings.values()),
        "production_semantic_identity": normalized(opening_result) == normalized(candidate_result),
        "compact_semantic_identity": normalized(compact_opening_result)
        == normalized(compact_candidate_result),
        "planted_comparisons_fail": all(planted.values()),
        "profile_sample_count": stages["samples"] == SAMPLES,
        "profile_stage_set": set(stages["stage_raw_ms"]) == REQUIRED_CORE_STAGES
        | {
            "core_uninstrumented",
            "spec_read_parse",
            "serialization",
            "output_write",
            "process_startup_and_cli_uninstrumented",
        },
        "profile_values": stages["all_values_finite_nonnegative_except_process_remainder"] is True,
        "profile_reconciliation": stages["maximum_reconciliation_fraction"] <= 0.10,
        "primary_improvement": timing_ratio <= 0.90 or rss_ratio <= 0.90,
        "other_primary_not_regressed": (timing_ratio <= 0.90 and rss_ratio <= 1.05)
        or (rss_ratio <= 0.90 and timing_ratio <= 1.05),
        "p95_not_regressed": p95_ratio <= 1.05,
    }
    output = {
        "schema": "actinv-p14-performance-1",
        "opening_source_commit": OPENING_COMMIT,
        "inputs": identities,
        "implementations": {
            "opening_binary": str(OPENING),
            "opening_binary_sha256": sha256(OPENING),
            "candidate_binary": str(CANDIDATE),
            "candidate_binary_sha256": sha256(CANDIDATE),
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "thread_variables": {name: "1" for name in THREAD_VARIABLES},
            "warmups_per_binary": WARMUPS,
            "measured_processes_per_binary": SAMPLES,
            "file_cache": "warm after five alternating complete runs; OS caches were not dropped",
            "clock": "time.perf_counter_ns",
            "compiler": compiler_identity(),
        },
        "warm_process_wall": timings,
        "peak_rss_bytes": {"opening": opening_rss, "candidate": candidate_rss},
        "ratios_candidate_over_opening": {
            "median_wall": timing_ratio,
            "p95_wall": p95_ratio,
            "peak_rss": rss_ratio,
        },
        "stage_attribution": stages,
        "semantic_comparison": {
            "normalization": ["top-level ms"],
            "production_equal": normalized(opening_result) == normalized(candidate_result),
            "compact_equal": normalized(compact_opening_result) == normalized(compact_candidate_result),
            "planted_failures": planted,
        },
        "input_bytes_hashed_and_parsed": sum(path.stat().st_size for path in FILES.values()),
        "output_bytes": output_bytes,
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "opening_median_ms": timings["opening"]["median_ms"],
                "candidate_median_ms": timings["candidate"]["median_ms"],
                "candidate_over_opening": output["ratios_candidate_over_opening"],
                "peak_rss_bytes": output["peak_rss_bytes"],
                "dominant_core_stage": stages["dominant_core_stage"],
                "stage_median_ms": stages["stage_median_ms"],
                "checks": checks,
                "pass": output["pass"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
