#!/usr/bin/env python3
"""P12-G3: deterministic, bounded verification of every production input-reader family."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g3_p12_parser_fuzz.json"
CONTROL = Path(__file__).resolve()
PROTOCOL = ROOT / "protocols" / "ACTINV-P12_PROTOCOL.md"
PROBE_SOURCE = ROOT / "crates" / "actinv-core" / "src" / "bin" / "parser_fuzz_probe.rs"
PROBE = ROOT / "target" / "release" / "parser_fuzz_probe"
MEMORY_LIMIT_BYTES = 1_073_741_824
BUILD_MEMORY_LIMIT_BYTES = 4_294_967_296
SMOKE_CASES = 10_000
FULL_CASES = 1_000_000
EXPECTED_FAMILIES = {
    "run_spec": 109_000,
    "mesh_spec": 109_000,
    "photon_response": 109_000,
    "group_structure": 109_000,
    "endf_records_sections": 109_000,
    "activation_evaluation": 109_000,
    "mf33_covariance": 109_000,
    "decay": 109_000,
    "fission_yields": 108_000,
    "activation_library_npz": 10_000,
    "canonical_flux_stream": 10_000,
}
EXPECTED_OPERATORS = {
    "truncate",
    "insert",
    "delete",
    "replace_bit",
    "duplicate_span",
    "count_value",
    "invalid_encoding",
    "numeric_edge",
}
READER_SOURCES = [
    PROBE_SOURCE,
    ROOT / "crates" / "actinv-data" / "src" / "endf.rs",
    ROOT / "crates" / "actinv-data" / "src" / "decay.rs",
    ROOT / "crates" / "actinv-data" / "src" / "fission.rs",
    ROOT / "crates" / "actinv-data" / "src" / "library.rs",
]
REGRESSIONS = {
    "endf_non_ascii_fixed_width": (
        ROOT / "crates" / "actinv-data" / "src" / "endf.rs",
        "fixed_width_helpers_reject_non_ascii_without_slicing_inside_a_codepoint",
    ),
    "endf_declared_payload": (
        ROOT / "crates" / "actinv-data" / "src" / "endf.rs",
        "checked_records_reject_counts_larger_than_the_remaining_payload",
    ),
    "decay_declared_spectra": (
        ROOT / "crates" / "actinv-data" / "src" / "decay.rs",
        "rejects_declared_spectra_before_reserving_memory",
    ),
    "fission_declared_energies": (
        ROOT / "crates" / "actinv-data" / "src" / "fission.rs",
        "rejects_declared_incident_energies_before_reserving_memory",
    ),
    "npz_declared_shape": (
        ROOT / "crates" / "actinv-data" / "src" / "library.rs",
        "declared_shape_must_fit_inside_the_archive_member",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def build_probe() -> None:
    temporary = ROOT / "target" / "p12-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "1"
    environment["TMPDIR"] = str(temporary)
    command = [
        "prlimit",
        f"--as={BUILD_MEMORY_LIMIT_BYTES}",
        "--",
        "cargo",
        "build",
        "--release",
        "--package",
        "actinv-core",
        "--bin",
        "parser_fuzz_probe",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if completed.returncode:
        tail = (completed.stdout + completed.stderr)[-4000:]
        raise RuntimeError(f"P12-G3 probe build failed ({completed.returncode})\n{tail}")
    if not PROBE.is_file():
        raise RuntimeError(f"P12-G3 probe build did not create {PROBE}")


def run_probe(cases: int, scratch: Path, timeout: int) -> dict:
    command = [
        "prlimit",
        f"--as={MEMORY_LIMIT_BYTES}",
        "--",
        str(PROBE),
        "--cases",
        str(cases),
        "--scratch",
        str(scratch),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"P12-G3 {cases}-case partition exceeded {timeout} seconds") from error
    if completed.returncode:
        tail = (completed.stdout + completed.stderr)[-4000:]
        if completed.returncode < 0:
            raise RuntimeError(
                f"P12-G3 {cases}-case partition ended by signal {-completed.returncode}\n{tail}"
            )
        raise RuntimeError(
            f"P12-G3 {cases}-case partition failed ({completed.returncode})\n{tail}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("P12-G3 probe did not emit one JSON report") from error
    report["exit_code"] = completed.returncode
    report["timed_out"] = False
    report["signalled"] = False
    return report


def report_invariants(report: dict, cases: int) -> dict:
    deterministic = report.get("deterministic", {})
    families = deterministic.get("families", [])
    family_counts = {family.get("family"): family.get("cases") for family in families}
    outcomes_close = all(
        family.get("accepted", 0) + family.get("rejected", 0) + family.get("panics", 0)
        == family.get("cases")
        for family in families
    )
    checks = {
        "case_count_exact": deterministic.get("cases") == cases,
        "family_case_sum_exact": sum(family_counts.values()) == cases,
        "every_family_present": set(family_counts) == set(EXPECTED_FAMILIES),
        "family_outcomes_close": outcomes_close,
        "zero_panics": all(family.get("panics") == 0 for family in families),
        "no_first_panic": deterministic.get("first_panic") is None,
        "all_operators_present": set(deterministic.get("operators", {})) == EXPECTED_OPERATORS,
        "operator_case_sum_exact": sum(deterministic.get("operators", {}).values()) == cases,
        "memory_measured": report.get("peak_rss_bytes", 0) > 0,
        "below_memory_limit": 0 < report.get("peak_rss_bytes", 0) < MEMORY_LIMIT_BYTES,
        "completed_without_signal_or_timeout": report.get("exit_code") == 0
        and not report.get("timed_out")
        and not report.get("signalled"),
        "probe_pass": report.get("pass") is True,
        "version_exact": report.get("probe_version") == "1.0.0",
    }
    if cases == FULL_CASES:
        checks["fixed_full_partition_exact"] = family_counts == EXPECTED_FAMILIES
        checks["container_minimums"] = (
            family_counts.get("activation_library_npz", 0) >= 10_000
            and family_counts.get("canonical_flux_stream", 0) >= 10_000
        )
    return checks


def source_checks() -> dict:
    regression_checks = {
        name: marker in path.read_text() for name, (path, marker) in REGRESSIONS.items()
    }
    source_text = "\n".join(path.read_text() for path in READER_SOURCES)
    return {
        "reader_source_sha256": {
            str(path.relative_to(ROOT)): sha256(path) for path in READER_SOURCES
        },
        "regression_tests": regression_checks,
        "all_regressions_present": all(regression_checks.values()),
        "unsafe_absent": re.search(r"\bunsafe\b", source_text) is None,
        "nightly_features_absent": "#![feature(" not in source_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run only the repeated 10,000-case CI partition and do not update results",
    )
    arguments = parser.parse_args()
    if shutil.which("prlimit") is None:
        raise RuntimeError("P12-G3 requires prlimit to enforce its process-memory ceiling")

    build_probe()
    scratch_root = ROOT / "target" / "p12-g3"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=scratch_root) as directory:
        work = Path(directory)
        smoke_a = run_probe(SMOKE_CASES, work / "smoke-a", 120)
        smoke_b = run_probe(SMOKE_CASES, work / "smoke-b", 120)
        smoke_checks = report_invariants(smoke_a, SMOKE_CASES)
        smoke_checks["repeat_invariants"] = all(
            report_invariants(smoke_b, SMOKE_CASES).values()
        )
        smoke_checks["deterministic_summary_identical"] = (
            smoke_a["deterministic"] == smoke_b["deterministic"]
        )
        smoke_output = {
            "deterministic": smoke_a["deterministic"],
            "first_elapsed_seconds": smoke_a["elapsed_seconds"],
            "second_elapsed_seconds": smoke_b["elapsed_seconds"],
            "first_peak_rss_bytes": smoke_a["peak_rss_bytes"],
            "second_peak_rss_bytes": smoke_b["peak_rss_bytes"],
            "checks": smoke_checks,
            "pass": all(smoke_checks.values()),
        }
        if arguments.smoke:
            output = {
                "gate": "P12-G3-smoke",
                "memory_limit_bytes": MEMORY_LIMIT_BYTES,
                "smoke": smoke_output,
                "pass": smoke_output["pass"],
            }
            print(json.dumps(output, indent=1))
            raise SystemExit(0 if output["pass"] else 1)

        full = run_probe(FULL_CASES, work / "full", 300)
        full_checks = report_invariants(full, FULL_CASES)
        sources = source_checks()
        amendments = {
            path.stem: sha256(path)
            for path in sorted((ROOT / "protocols").glob("ACTINV-P12_AMENDMENT_*.md"))
        }
        output = {
            "schema": "actinv-p12-g3-result-1",
            "gate": "P12-G3",
            "control_sha256": sha256(CONTROL),
            "protocol_sha256": sha256(PROTOCOL),
            "amendment_sha256": amendments,
            "memory_limit_bytes": MEMORY_LIMIT_BYTES,
            "timeouts_seconds": {"smoke": 120, "full": 300},
            "smoke": smoke_output,
            "full": full,
            "full_checks": full_checks,
            "source": sources,
            "repair_record": {
                "amendment": "ACTINV-P12_AMENDMENT_D",
                "pre_full_partition_discoveries": list(REGRESSIONS),
                "discovery_count": len(REGRESSIONS),
                "full_partition_discoveries": 0,
                "all_discoveries_have_regressions": sources["all_regressions_present"],
            },
        }
        output["pass"] = bool(
            smoke_output["pass"]
            and all(full_checks.values())
            and sources["all_regressions_present"]
            and sources["unsafe_absent"]
            and sources["nightly_features_absent"]
            and len(amendments) >= 4
        )
        RESULT.write_text(json.dumps(output, indent=1) + "\n")
        print(json.dumps(output, indent=1))
        raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
