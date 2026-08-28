#!/usr/bin/env python3
"""Independently rederive the frozen P14 performance result and closure status."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P14_PROTOCOL.md"
PROTOCOL_SHA256 = "0f7f385f09174279384a873755a9584327bf064a0ad6c892ca598e59c1491275"
PERFORMANCE = RESULTS / "p14_performance.json"
QUALITY = RESULTS / "p14_quality.json"
VERDICT = RESULTS / "verdict_p14.json"
OPENING_COMMIT = "d7f934dad677f128395443d10a57444c7b213472"
EXPECTED_INPUTS = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
}
CORE_STAGES = {
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
EXTRA_STAGES = {
    "core_uninstrumented",
    "spec_read_parse",
    "serialization",
    "output_write",
    "process_startup_and_cli_uninstrumented",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def close(left: object, right: object) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and math.isfinite(float(left))
        and math.isfinite(float(right))
        and math.isclose(float(left), float(right), rel_tol=2e-12, abs_tol=2e-9)
    )


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def protocol_check() -> dict[str, object]:
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P14_PROTOCOL.md"
    ledger = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    return {
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": actual,
        "ledger_entry": line in ledger,
        "pass": actual == PROTOCOL_SHA256 and line in ledger,
    }


def timing_checks(value: dict[str, object]) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    timings = value.get("warm_process_wall", {})
    for name in ("opening", "candidate"):
        record = timings.get(name, {}) if isinstance(timings, dict) else {}
        raw = [float(item) for item in record.get("raw_ms", [])]
        checks[f"{name}_sample_count"] = len(raw) == 15 and record.get("samples") == 15
        if len(raw) == 15:
            checks[f"{name}_minimum"] = close(record.get("minimum_ms"), min(raw))
            checks[f"{name}_median"] = close(record.get("median_ms"), statistics.median(raw))
            checks[f"{name}_p95"] = close(record.get("p95_ms"), quantile(raw, 0.95))
            checks[f"{name}_mean"] = close(record.get("mean_ms"), statistics.fmean(raw))
            checks[f"{name}_stdev"] = close(
                record.get("sample_standard_deviation_ms"), statistics.stdev(raw)
            )
    return checks


def derive_g1(value: dict[str, object] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"performance_present": False}, "pass": False}
    inputs = value.get("inputs", {})
    environment = value.get("environment", {})
    implementations = value.get("implementations", {})
    checks = {
        "schema": value.get("schema") == "actinv-p14-performance-1",
        "opening_commit": value.get("opening_source_commit") == OPENING_COMMIT,
        "input_inventory": set(inputs) == set(EXPECTED_INPUTS),
        "input_identities": set(inputs) == set(EXPECTED_INPUTS)
        and all(
            item.get("expected_sha256") == EXPECTED_INPUTS[name]
            and item.get("actual_sha256") == EXPECTED_INPUTS[name]
            and item.get("matches") is True
            and int(item.get("bytes", 0)) > 0
            for name, item in inputs.items()
        ),
        "input_byte_sum": value.get("input_bytes_hashed_and_parsed")
        == sum(int(item.get("bytes", 0)) for item in inputs.values()),
        "environment": environment.get("warmups_per_binary") == 5
        and environment.get("measured_processes_per_binary") == 15
        and environment.get("clock") == "time.perf_counter_ns"
        and isinstance(environment.get("compiler"), str)
        and str(environment.get("compiler")).startswith("rustc ")
        and set(environment.get("thread_variables", {}).values()) == {"1"},
        "binary_identities": all(
            re.fullmatch(r"[0-9a-f]{64}", str(implementations.get(name))) is not None
            for name in ("opening_binary_sha256", "candidate_binary_sha256")
        ),
        **timing_checks(value),
    }
    return {"checks": checks, "pass": all(checks.values())}


def derive_g2(value: dict[str, object] | None) -> dict[str, object]:
    stages = value.get("stage_attribution", {}) if value else {}
    raw = stages.get("stage_raw_ms", {}) if isinstance(stages, dict) else {}
    medians = stages.get("stage_median_ms", {}) if isinstance(stages, dict) else {}
    expected_stages = CORE_STAGES | EXTRA_STAGES
    stage_values = {
        name: [float(item) for item in values]
        for name, values in raw.items()
        if isinstance(values, list)
    }
    checks = {
        "stage_set": set(stage_values) == expected_stages,
        "sample_counts": set(stage_values) == expected_stages
        and all(len(values) == 15 for values in stage_values.values()),
        "finite_values": all(math.isfinite(item) for values in stage_values.values() for item in values),
        "reported_value_check": stages.get("all_values_finite_nonnegative_except_process_remainder")
        is True,
        "reconciliation": close(stages.get("maximum_reconciliation_fraction"), 0.0),
        "profile_samples": stages.get("samples") == 15,
    }
    if set(stage_values) == expected_stages:
        derived_medians = {name: statistics.median(values) for name, values in stage_values.items()}
        checks["medians"] = set(medians) == expected_stages and all(
            close(medians.get(name), result) for name, result in derived_medians.items()
        )
        dominant = max(CORE_STAGES, key=lambda name: derived_medians[name])
        checks["dominant_stage"] = (
            stages.get("dominant_core_stage") == dominant == "activation_read_validation"
        )
    else:
        checks["medians"] = False
        checks["dominant_stage"] = False
    return {"checks": checks, "pass": all(checks.values())}


def derive_g3(value: dict[str, object] | None) -> dict[str, object]:
    semantic = value.get("semantic_comparison", {}) if value else {}
    plants = semantic.get("planted_failures", {}) if isinstance(semantic, dict) else {}
    checks = {
        "normalization": semantic.get("normalization") == ["top-level ms"],
        "production_identity": semantic.get("production_equal") is True,
        "compact_identity": semantic.get("compact_equal") is True,
        "three_plants": set(plants)
        == {"certificate_input_hash", "inventory_value", "ledger_value"}
        and all(item is True for item in plants.values()),
    }
    return {"checks": checks, "pass": all(checks.values())}


def derive_g4(value: dict[str, object] | None) -> dict[str, object]:
    timings = value.get("warm_process_wall", {}) if value else {}
    rss = value.get("peak_rss_bytes", {}) if value else {}
    reported = value.get("ratios_candidate_over_opening", {}) if value else {}
    try:
        median_ratio = timings["candidate"]["median_ms"] / timings["opening"]["median_ms"]
        p95_ratio = timings["candidate"]["p95_ms"] / timings["opening"]["p95_ms"]
        rss_ratio = rss["candidate"] / rss["opening"]
    except (KeyError, TypeError, ZeroDivisionError):
        median_ratio = p95_ratio = rss_ratio = math.inf
    threshold = median_ratio <= 0.90 or rss_ratio <= 0.90
    other_primary = (median_ratio <= 0.90 and rss_ratio <= 1.05) or (
        rss_ratio <= 0.90 and median_ratio <= 1.05
    )
    checks = {
        "median_ratio": close(reported.get("median_wall"), median_ratio),
        "p95_ratio": close(reported.get("p95_wall"), p95_ratio),
        "rss_ratio": close(reported.get("peak_rss"), rss_ratio),
        "p95_not_regressed": p95_ratio <= 1.05,
        "both_primary_metrics_not_regressed": median_ratio <= 1.05 and rss_ratio <= 1.05,
        "other_primary_not_regressed_if_threshold_met": other_primary,
        "frozen_ten_percent_threshold": threshold,
    }
    return {
        "ratios_candidate_over_opening": {
            "median_wall": median_ratio,
            "p95_wall": p95_ratio,
            "peak_rss": rss_ratio,
        },
        "improvement_percent": {
            "median_wall": (1.0 - median_ratio) * 100.0,
            "p95_wall": (1.0 - p95_ratio) * 100.0,
            "peak_rss": (1.0 - rss_ratio) * 100.0,
        },
        "checks": checks,
        "pass": all(checks.values()),
    }


def cargo_path() -> str:
    configured = os.environ.get("CARGO")
    if configured:
        return configured
    candidate = Path.home() / ".cargo/bin/cargo"
    return str(candidate) if candidate.is_file() else "cargo"


def command_result(arguments: list[str], environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    executable = Path(arguments[0]).name
    display = ["cargo" if executable == "cargo" else "python" if executable.startswith("python") else executable]
    display.extend(arguments[1:])
    return {
        "command": " ".join(display),
        "returncode": completed.returncode,
        "error_tail": "" if completed.returncode == 0 else "\n".join(
            (completed.stdout + completed.stderr).replace(str(ROOT), "<ROOT>").splitlines()[-30:]
        ),
        "pass": completed.returncode == 0,
    }


def run_quality() -> dict[str, object]:
    cargo = cargo_path()
    python = sys.executable
    environment = os.environ.copy()
    environment["CARGO"] = cargo
    environment["ACTINV_BIN"] = str(ROOT / "target/release/actinv")
    environment["ACTINV_PYTHON_LIBRARY"] = str(
        ROOT / "python/target/release/libactinv.so"
    )
    if (Path("/tmp/actinv-ci-data")).is_dir():
        environment["ACTINV_CI_DATA"] = "/tmp/actinv-ci-data"
    environment["ACTINV_CI_OUT"] = "/tmp/actinv-p14-ci"
    commands = [
        [cargo, "fmt", "--all", "--", "--check"],
        [cargo, "check", "--workspace", "--all-targets", "--all-features"],
        [cargo, "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
        [cargo, "test", "--workspace", "--all-targets", "--all-features"],
        [cargo, "fmt", "--manifest-path", "python/Cargo.toml", "--", "--check"],
        [cargo, "check", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features"],
        [cargo, "clippy", "--manifest-path", "python/Cargo.toml", "--all-targets", "--all-features", "--", "-D", "warnings"],
        [python, "controls/check_prior_verdicts.py"],
        [python, "controls/check_release_notes.py"],
        [python, "controls/check_dependencies.py"],
        [python, "controls/check_public_examples.py"],
        [python, "controls/g1_self_contained.py"],
        [python, "controls/ci_end_to_end.py"],
        [python, "controls/g6_p10_projectile_runtime.py"],
    ]
    records = [command_result(command, environment) for command in commands]
    output = {
        "schema": "actinv-p14-quality-1",
        "commands": records,
        "pass": all(record["pass"] for record in records),
    }
    QUALITY.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return output


def derive_g5(full: bool) -> dict[str, object]:
    quality = run_quality() if full else load(QUALITY)
    source_files = [
        ROOT / "crates/actinv-cli/src/command.rs",
        ROOT / "crates/actinv-core/src/chain.rs",
        ROOT / "crates/actinv-core/src/run.rs",
        ROOT / "crates/actinv-data/src/decay.rs",
        ROOT / "crates/actinv-data/src/endf.rs",
        ROOT / "crates/actinv-data/src/library.rs",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    session = (ROOT / "docs/history/sessions/P14.md").read_text(encoding="utf-8")
    quality_commands = quality.get("commands", []) if quality else []
    checks = {
        "quality_record": quality is not None
        and quality.get("schema") == "actinv-p14-quality-1"
        and len(quality_commands) == 14
        and quality.get("pass") is True
        and all(record.get("pass") is True for record in quality_commands),
        "no_unsafe": re.search(r"\bunsafe\b", source) is None,
        "instrumentation_opt_in": source.count("ACTINV_P14_PROFILE") >= 2,
        "regressions_present": "derivative_free_assembly_preserves_rates_and_ledger" in source
        and "archive_reader_rejects_invalid_cross_sections_during_decode" in source,
        "verified_crc_boundary": "read_npz_after_sha256_verification" in source
        and "library_ref.sha256.is_some()" in source,
        "p14_in_ci": "controls/check_p14.py --no-write" in ci,
        "session_documents_threshold": "P14-CLOSED-BELOW-THRESHOLD" in session
        and "10%" in session,
    }
    return {"checks": checks, "quality": quality, "pass": all(checks.values())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="run all local P14 quality commands")
    parser.add_argument("--no-write", action="store_true", help="derive without replacing the verdict")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    performance = load(PERFORMANCE)
    protocol = protocol_check()
    gates = {
        "G1": derive_g1(performance),
        "G2": derive_g2(performance),
        "G3": derive_g3(performance),
        "G4": derive_g4(performance),
        "G5": derive_g5(arguments.full),
    }
    phase_pass = protocol["pass"] and all(gate["pass"] for gate in gates.values())
    below_threshold_close = (
        protocol["pass"]
        and all(gates[name]["pass"] for name in ("G1", "G2", "G3", "G5"))
        and gates["G4"]["pass"] is False
        and gates["G4"]["checks"]["frozen_ten_percent_threshold"] is False
        and gates["G4"]["checks"]["p95_not_regressed"] is True
        and gates["G4"]["checks"]["both_primary_metrics_not_regressed"] is True
    )
    closed = phase_pass or below_threshold_close
    verdict = (
        "P14-PASS"
        if phase_pass
        else "P14-CLOSED-BELOW-THRESHOLD"
        if below_threshold_close
        else "P14-FAIL"
    )
    output = {
        "schema": "actinv-p14-verdict-1",
        "protocol": protocol,
        "performance_evidence_sha256": sha256(PERFORMANCE) if PERFORMANCE.is_file() else None,
        "quality_evidence_sha256": sha256(QUALITY) if QUALITY.is_file() else None,
        "gates": gates,
        "phase_pass": phase_pass,
        "closed": closed,
        "verdict": verdict,
        "pass": closed,
    }
    if not arguments.no_write:
        VERDICT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    displayed = output if arguments.verbose else {
        "schema": output["schema"],
        "protocol": protocol["pass"],
        "gates": {name: gate["pass"] for name, gate in gates.items()},
        "improvement_percent": gates["G4"]["improvement_percent"],
        "phase_pass": phase_pass,
        "closed": closed,
        "verdict": verdict,
        "pass": closed,
    }
    print(json.dumps(displayed, indent=1, sort_keys=True))
    raise SystemExit(0 if closed else 1)


if __name__ == "__main__":
    main()
