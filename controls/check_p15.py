#!/usr/bin/env python3
"""Independently rederive the frozen P15 prepared-data verdict."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import struct
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols/ACTINV-P15_PROTOCOL.md"
PROTOCOL_SHA256 = "09da6b3f011f7d837be36a233b6fe13117c90a71df629708df5ac90e1b5b12de"
OPENING_COMMIT = "5f7289a44c2686505d0e1b40f4b00ef5c8e4a9ab"
PREPARED = RESULTS / "p15_prepared_identity.json"
CACHE = RESULTS / "p15_cache_integrity.json"
INTERFACES = RESULTS / "p15_interfaces.json"
PERFORMANCE = RESULTS / "p15_performance.json"
QUALITY = RESULTS / "p15_quality.json"
SESSION = RESULTS / "session_p15.json"
VERDICT = RESULTS / "verdict_p15.json"
EXPECTED_INPUTS = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
}
EXPECTED_PRODUCTION = {
    "rows": 167_735,
    "groups": 709,
    "boundaries": 710,
    "dense_sig_bytes": 951_392_920,
    "nonzero_values": 33_597_258,
    "retained_values": 33_597_258,
    "prepared_bytes": 275_493_400,
    "collapsed_bytes": 6_888_807,
    "prepared_sha256": "6800a17c993710e8ca61aa52c1a24538778e7c4238711507f43fccd7fc0a7745",
    "collapsed_sha256": "ee362142b91abc8dbd836f3092413e8b4e15572458d530ff0c52d675f8714a2b",
}
EXPECTED_SELECTIONS = {"empty", "iron", "noncontiguous", "all"}
EXPECTED_MUTATIONS = {
    "collapsed_magic",
    "collapsed_schema_version",
    "collapsed_source_library",
    "collapsed_source_index",
    "collapsed_flux_hash",
    "collapsed_row_descriptor",
    "collapsed_selected_value",
    "collapsed_offset",
    "collapsed_declared_count",
    "collapsed_integrity_trailer",
    "collapsed_truncation",
    "collapsed_trailing",
    "prepared_magic",
    "prepared_schema_version",
    "prepared_source_library",
    "prepared_source_index",
    "prepared_row_descriptor",
    "prepared_selected_value",
    "prepared_offset",
    "prepared_declared_count",
    "prepared_integrity_trailer",
    "prepared_truncation",
    "prepared_trailing",
}
PREPARED_ALGORITHM = (
    b"actinv-prepared-library-1\nnpz-f64-spans-v1\nsource-row-order-v1\n"
)
COLLAPSED_ALGORITHM = (
    b"actinv-collapsed-spectrum-1\nopening-collapse-order-v1\n"
    b"fission-spectrum-average-v1\n"
)
THREADS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "RAYON_NUM_THREADS": "1",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def close(left: object, right: object) -> bool:
    return (
        finite_number(left)
        and finite_number(right)
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


def canonical_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def protocol_check() -> dict[str, object]:
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    expected_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-P15_PROTOCOL.md"
    try:
        ledger = (ROOT / "protocols/protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        ledger = []
    return {
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": actual,
        "ledger_entry": expected_line in ledger,
        "pass": actual == PROTOCOL_SHA256 and expected_line in ledger,
    }


def input_checks(inputs: object) -> bool:
    return (
        isinstance(inputs, dict)
        and set(inputs) == set(EXPECTED_INPUTS)
        and all(
            isinstance(inputs[name], dict)
            and inputs[name].get("actual_sha256") == digest
            and inputs[name].get("expected_sha256") == digest
            and isinstance(inputs[name].get("bytes"), int)
            and inputs[name]["bytes"] > 0
            for name, digest in EXPECTED_INPUTS.items()
        )
    )


def derive_prepared(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    production = value.get("production", {})
    preparations = value.get("preparations", {})
    selections = value.get("indexed_selections", {})
    expected_ratio = EXPECTED_PRODUCTION["prepared_bytes"] / EXPECTED_PRODUCTION["dense_sig_bytes"]
    result_hashes = preparations.get("normalized_result_sha256", {})
    checks = {
        "schema": value.get("schema") == "actinv-p15-prepared-identity-1",
        "reported_pass": value.get("pass") is True,
        "inputs": input_checks(value.get("inputs")),
        "production_inventory": isinstance(production, dict)
        and all(production.get(name) == expected for name, expected in EXPECTED_PRODUCTION.items()),
        "prepared_fraction_rederived": close(
            production.get("prepared_to_dense_fraction"), expected_ratio
        )
        and expected_ratio <= 0.35,
        "all_bits_checked": production.get("descriptor_or_retained_bit_mismatches") == 0
        and production.get("boundary_bit_mismatches") == 0
        and production.get("collapsed_row_mismatches") == 0
        and production.get("collapse_bit_mismatches") == 0,
        "deterministic_artifacts": preparations.get("prepared_byte_identical") is True
        and preparations.get("collapsed_byte_identical") is True,
        "result_hash_identity": isinstance(result_hashes, dict)
        and set(result_hashes) == {"first", "second"}
        and canonical_sha(result_hashes.get("first"))
        and result_hashes.get("first") == result_hashes.get("second")
        and preparations.get("normalized_result_identical") is True,
        "flux_bits": preparations.get("flux_bit_identity") is True,
        "cold_times_visible": finite_number(preparations.get("first_cold_ms"))
        and float(preparations["first_cold_ms"]) > 0.0
        and finite_number(preparations.get("second_cold_ms"))
        and float(preparations["second_cold_ms"]) > 0.0,
        "selection_set": isinstance(selections, dict) and set(selections) == EXPECTED_SELECTIONS,
        "selection_identity": isinstance(selections, dict)
        and set(selections) == EXPECTED_SELECTIONS
        and all(
            item.get("identity") is True
            and item.get("allocation_bound") is True
            and item.get("selection_sha256") == item.get("expected_sha256")
            and item.get("materialized_bytes", math.inf)
            <= item.get("selected_payload_bytes", -math.inf) + 16 * 1024**2
            and item.get("source_rows") == EXPECTED_PRODUCTION["rows"]
            and item.get("groups") == EXPECTED_PRODUCTION["groups"]
            for item in selections.values()
            if isinstance(item, dict)
        ),
        "empty_selection": isinstance(selections, dict)
        and selections.get("empty", {}).get("selected_rows") == 0
        and selections.get("empty", {}).get("selected_values") == 0,
        "all_selection": isinstance(selections, dict)
        and selections.get("all", {}).get("selected_rows") == EXPECTED_PRODUCTION["rows"]
        and selections.get("all", {}).get("selected_values") == EXPECTED_PRODUCTION["retained_values"],
    }
    return {"checks": checks, "pass": all(checks.values())}


def same_result_hash(value: dict[str, Any], expected: str) -> bool:
    actual = value.get("normalized_result_sha256")
    return canonical_sha(actual) and actual == expected


def derive_cache(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    fixture = value.get("fixture", {})
    warm = value.get("warm_reuse", {})
    plants = value.get("mutation_plants", {})
    deletion = value.get("deletion_recreation", {})
    interrupted = value.get("interrupted_publication", {})
    concurrent = value.get("concurrent_preparation", {})
    reference_hash = fixture.get("normalized_result_sha256") if isinstance(fixture, dict) else None
    concurrent_hashes = concurrent.get("normalized_result_sha256", [])
    checks = {
        "schema": value.get("schema") == "actinv-p15-cache-integrity-1",
        "reported_pass": value.get("pass") is True,
        "fixture_contract": isinstance(fixture, dict)
        and fixture.get("prepared_sha256") == "cc9d399de0481744d4652b994f20851da1a4dc3725062790fd56a40f09ff4cce"
        and fixture.get("collapsed_sha256") == "0f6f8127562d64eeec8ab0f73215144c993add57075ab71e36187389c5382e98"
        and fixture.get("prepared_bytes") == 632
        and fixture.get("collapsed_bytes") == 613
        and all(
            fixture.get(name) is True
            for name in (
                "leading_zero_groups",
                "trailing_zero_groups",
                "internal_zero_groups",
                "zero_only_row",
            )
        )
        and canonical_sha(reference_hash),
        "warm_exact": isinstance(warm, dict)
        and warm.get("returncode") == 0
        and warm.get("result_identical") is True
        and warm.get("prepared_unchanged") is True
        and warm.get("collapsed_unchanged") is True
        and same_result_hash(warm, reference_hash),
        "plant_set": isinstance(plants, dict) and set(plants) == EXPECTED_MUTATIONS,
        "plants_fail_closed": isinstance(plants, dict)
        and set(plants) == EXPECTED_MUTATIONS
        and all(
            isinstance(item, dict)
            and isinstance(item.get("returncode"), int)
            and item["returncode"] != 0
            and item.get("diagnostic_class") is True
            and item.get("result_not_published") is True
            and item.get("artifact_not_overwritten") is True
            for item in plants.values()
        ),
        "deletion_exact": isinstance(deletion, dict)
        and deletion.get("returncode") == 0
        and deletion.get("result_identical") is True
        and deletion.get("prepared_identical") is True
        and deletion.get("collapsed_identical") is True
        and same_result_hash(deletion, reference_hash),
        "interrupted_publication": isinstance(interrupted, dict)
        and interrupted.get("returncode") == 0
        and interrupted.get("partial_not_accepted") is True
        and interrupted.get("result_identical") is True
        and interrupted.get("prepared_identical") is True
        and interrupted.get("collapsed_identical") is True
        and same_result_hash(interrupted, reference_hash),
        "concurrent_publication": isinstance(concurrent, dict)
        and concurrent.get("returncodes") == [0, 0]
        and concurrent.get("results_identical") is True
        and concurrent.get("prepared_identical") is True
        and concurrent.get("collapsed_identical") is True
        and isinstance(concurrent_hashes, list)
        and len(concurrent_hashes) == 2
        and all(item == reference_hash for item in concurrent_hashes),
    }
    return {"checks": checks, "pass": all(checks.values())}


def derive_interfaces(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    checks_reported = value.get("checks", {})
    result_hashes = value.get("normalized_result_sha256", {})
    cache = value.get("cache", [])
    cli_rss = value.get("cli_peak_rss_bytes")
    python_rss = value.get("python_peak_rss_bytes")
    overhead = value.get("python_minus_cli_peak_rss_bytes")
    checks = {
        "schema": value.get("schema") == "actinv-p15-interface-identity-1",
        "reported_pass": value.get("pass") is True
        and isinstance(checks_reported, dict)
        and len(checks_reported) == 6
        and all(item is True for item in checks_reported.values()),
        "result_hash_identity": isinstance(result_hashes, dict)
        and set(result_hashes) == {"cli", "python"}
        and canonical_sha(result_hashes.get("cli"))
        and result_hashes.get("cli") == result_hashes.get("python"),
        "rss_arithmetic": finite_number(cli_rss)
        and finite_number(python_rss)
        and finite_number(overhead)
        and python_rss - cli_rss == overhead,
        "python_memory_bound": finite_number(python_rss)
        and python_rss <= 512 * 1024**2
        and overhead <= 192 * 1024**2,
        "cache_inventory": isinstance(cache, list)
        and len(cache) == 2
        and {item.get("sha256") for item in cache if isinstance(item, dict)}
        == {
            EXPECTED_PRODUCTION["prepared_sha256"],
            EXPECTED_PRODUCTION["collapsed_sha256"],
        }
        and sum(item.get("bytes", 0) for item in cache if isinstance(item, dict))
        == EXPECTED_PRODUCTION["prepared_bytes"] + EXPECTED_PRODUCTION["collapsed_bytes"],
    }
    return {"checks": checks, "pass": all(checks.values())}


def timing_checks(record: object, name: str) -> dict[str, bool]:
    if not isinstance(record, dict):
        return {f"{name}_record": False}
    try:
        raw = [float(item) for item in record.get("raw_ms", [])]
    except (TypeError, ValueError):
        raw = []
    checks = {f"{name}_sample_count": len(raw) == 15 and record.get("samples") == 15}
    if len(raw) == 15 and all(math.isfinite(item) and item > 0.0 for item in raw):
        checks.update(
            {
                f"{name}_minimum": close(record.get("minimum_ms"), min(raw)),
                f"{name}_median": close(record.get("median_ms"), statistics.median(raw)),
                f"{name}_p95": close(record.get("p95_ms"), quantile(raw, 0.95)),
                f"{name}_mean": close(record.get("mean_ms"), statistics.fmean(raw)),
                f"{name}_stdev": close(
                    record.get("sample_standard_deviation_ms"), statistics.stdev(raw)
                ),
            }
        )
    return checks


def artifact_inventory(value: object) -> bool:
    if not isinstance(value, dict) or value.get("bytes") != 282_382_207:
        return False
    files = value.get("files")
    return (
        isinstance(files, list)
        and len(files) == 2
        and {item.get("sha256") for item in files if isinstance(item, dict)}
        == {
            EXPECTED_PRODUCTION["prepared_sha256"],
            EXPECTED_PRODUCTION["collapsed_sha256"],
        }
        and sum(item.get("bytes", 0) for item in files if isinstance(item, dict))
        == value.get("bytes")
    )


def derive_performance(value: dict[str, Any] | None) -> dict[str, object]:
    if value is None:
        return {"checks": {"evidence_present": False}, "pass": False}
    opening = value.get("opening", {})
    candidate = value.get("candidate", {})
    comparison = value.get("comparison", {})
    opening_wall = opening.get("wall", {}) if isinstance(opening, dict) else {}
    candidate_wall = candidate.get("warm_wall", {}) if isinstance(candidate, dict) else {}
    reported_ratios = comparison.get("ratios", {}) if isinstance(comparison, dict) else {}
    try:
        median_ratio = candidate_wall["median_ms"] / opening_wall["median_ms"]
        p95_ratio = candidate_wall["p95_ms"] / opening_wall["p95_ms"]
        rss_ratio = candidate["warm_peak_rss_bytes"] / opening["peak_rss_bytes"]
    except (KeyError, TypeError, ZeroDivisionError):
        median_ratio = p95_ratio = rss_ratio = math.inf
    result_hashes = comparison.get("normalized_result_sha256", {})
    plant_hashes = comparison.get("comparator_plant_sha256", {})
    plants = comparison.get("comparator_plants", {})
    baseline_hash = result_hashes.get("candidate") if isinstance(result_hashes, dict) else None
    empty = candidate.get("empty_cache", {}) if isinstance(candidate, dict) else {}
    deleted = candidate.get("after_deletion", {}) if isinstance(candidate, dict) else {}
    checks = {
        "schema": value.get("schema") == "actinv-p15-performance-1",
        "reported_pass": value.get("pass") is True
        and isinstance(value.get("gates"), dict)
        and len(value["gates"]) == 10
        and all(item is True for item in value["gates"].values()),
        "opening_commit": isinstance(opening, dict) and opening.get("commit") == OPENING_COMMIT,
        "binary_hashes": isinstance(opening, dict)
        and canonical_sha(opening.get("binary_sha256"))
        and isinstance(candidate, dict)
        and canonical_sha(candidate.get("binary_sha256")),
        "inputs": input_checks(value.get("inputs")),
        "host": isinstance(value.get("host"), dict)
        and isinstance(value["host"].get("compiler"), str)
        and value["host"]["compiler"].startswith("rustc ")
        and isinstance(value["host"].get("logical_cpus"), int),
        "sampling": isinstance(comparison, dict)
        and comparison.get("warmups_per_binary") == 5
        and comparison.get("samples_per_binary") == 15
        and comparison.get("alternating_order") is True
        and comparison.get("thread_environment") == THREADS,
        **timing_checks(opening_wall, "opening"),
        **timing_checks(candidate_wall, "candidate"),
        "median_ratio": close(
            reported_ratios.get("median_wall_candidate_over_opening"), median_ratio
        )
        and median_ratio <= 2.0 / 3.0,
        "p95_ratio": close(reported_ratios.get("p95_wall_candidate_over_opening"), p95_ratio)
        and p95_ratio <= 2.0 / 3.0,
        "rss_ratio": close(reported_ratios.get("peak_rss_candidate_over_opening"), rss_ratio)
        and rss_ratio <= 0.5,
        "empty_bounds": finite_number(empty.get("wall_ms"))
        and empty["wall_ms"] <= 2.0 * opening_wall.get("median_ms", 0.0)
        and empty.get("peak_rss_bytes", math.inf) <= 512 * 1024**2,
        "cache_recreation": artifact_inventory(empty.get("inventory"))
        and artifact_inventory(deleted.get("inventory"))
        and empty.get("inventory") == deleted.get("inventory"),
        "result_hash_identity": isinstance(result_hashes, dict)
        and set(result_hashes) == {"opening", "candidate", "after_deletion"}
        and canonical_sha(baseline_hash)
        and len(set(result_hashes.values())) == 1,
        "comparator_plants": isinstance(plants, dict)
        and set(plants) == {"certificate_input_hash", "inventory_value", "ledger_value"}
        and all(item is True for item in plants.values())
        and isinstance(plant_hashes, dict)
        and set(plant_hashes) == set(plants)
        and all(canonical_sha(item) and item != baseline_hash for item in plant_hashes.values()),
        "stretch": comparison.get("stretch")
        == {
            "warm_median_at_most_one_second": candidate_wall.get("median_ms", math.inf)
            <= 1000.0,
            "warm_peak_rss_at_most_512_mib": candidate.get(
                "warm_peak_rss_bytes", math.inf
            )
            <= 512 * 1024**2,
        },
    }
    return {
        "checks": checks,
        "ratios_candidate_over_opening": {
            "median_wall": median_ratio,
            "p95_wall": p95_ratio,
            "peak_rss": rss_ratio,
        },
        "improvement_factors": {
            "median_wall": math.inf if median_ratio == 0.0 else 1.0 / median_ratio,
            "p95_wall": math.inf if p95_ratio == 0.0 else 1.0 / p95_ratio,
            "peak_rss": math.inf if rss_ratio == 0.0 else 1.0 / rss_ratio,
        },
        "pass": all(checks.values()),
    }


def unpack(data: bytes, fmt: str, offset: int) -> tuple[Any, ...]:
    return struct.unpack_from("<" + fmt, data, offset)


def finite_nonnegative_f64_payload(data: bytes) -> bool:
    return len(data) % 8 == 0 and all(
        math.isfinite(value[0]) and value[0] >= 0.0
        for value in struct.iter_unpack("<d", data)
    )


def increasing_boundaries(data: bytes) -> bool:
    if len(data) < 16 or len(data) % 8:
        return False
    values = [item[0] for item in struct.iter_unpack("<d", data)]
    return all(
        math.isfinite(value) and value > 0.0 for value in values
    ) and all(right > left for left, right in zip(values, values[1:]))


def parse_prepared_schema(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 256 or data[:8] != b"ACTPLB01":
        raise ValueError("prepared magic/length")
    version, header_bytes = unpack(data, "II", 8)
    row_count, groups, bounds_count = unpack(data, "QQQ", 16)
    row_bytes, flags = unpack(data, "II", 40)
    rows_offset, values_offset, bounds_offset, value_count, dense_count, payload_end, artifact_len = unpack(
        data, "QQQQQQQ", 48
    )
    if (version, header_bytes, row_bytes, flags) != (1, 224, 40, 0):
        raise ValueError("prepared header contract")
    if data[168:200] != hashlib.sha256(PREPARED_ALGORITHM).digest() or any(data[200:224]):
        raise ValueError("prepared algorithm/reserved")
    expected_values = 224 + row_count * 40
    expected_bounds = expected_values + value_count * 8
    expected_end = expected_bounds + bounds_count * 8
    if (
        row_count == 0
        or groups == 0
        or bounds_count != groups + 1
        or dense_count != row_count * groups
        or (rows_offset, values_offset, bounds_offset, payload_end, artifact_len)
        != (224, expected_values, expected_bounds, expected_end, expected_end + 32)
        or len(data) != artifact_len
        or hashlib.sha256(data[:payload_end]).digest() != data[payload_end:]
    ):
        raise ValueError("prepared layout/integrity")
    rows: list[tuple[int, int, int, int, int]] = []
    expected_index = 0
    for ordinal in range(row_count):
        offset = rows_offset + ordinal * row_bytes
        target, mt, zap, lfs, lmf, first, count, index = unpack(data, "QiiiiIIQ", offset)
        if index != expected_index or (count == 0 and first != 0) or first + count > groups:
            raise ValueError("prepared descriptor")
        expected_index += count
        rows.append((target, mt, zap, lfs, lmf))
    if expected_index != value_count:
        raise ValueError("prepared span coverage")
    if not finite_nonnegative_f64_payload(data[values_offset:bounds_offset]):
        raise ValueError("prepared values")
    if not increasing_boundaries(data[bounds_offset:payload_end]):
        raise ValueError("prepared boundaries")
    return {
        "rows": rows,
        "row_count": row_count,
        "groups": groups,
        "bounds_count": bounds_count,
        "value_count": value_count,
        "library_sha256": data[104:136].hex(),
        "index_sha256": data[136:168].hex(),
        "integrity_sha256": data[payload_end:].hex(),
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_bytes": len(data),
        "boundary_bytes": data[bounds_offset:payload_end],
    }


def parse_collapsed_schema(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 320 or data[:8] != b"ACTCOL01":
        raise ValueError("collapsed magic/length")
    version, header_bytes = unpack(data, "II", 8)
    row_count, groups, bounds_count = unpack(data, "QQQ", 16)
    row_bytes, flags = unpack(data, "II", 40)
    bounds_offset, flux_offset, rows_offset, collapsed_offset, fission_offset, presence_offset, payload_end, artifact_len = unpack(
        data, "QQQQQQQQ", 48
    )
    if (version, header_bytes, row_bytes, flags) != (1, 288, 24, 0):
        raise ValueError("collapsed header contract")
    if data[240:272] != hashlib.sha256(COLLAPSED_ALGORITHM).digest() or any(data[272:288]):
        raise ValueError("collapsed algorithm/reserved")
    expected_flux = 288 + bounds_count * 8
    expected_rows = expected_flux + groups * 8
    expected_collapsed = expected_rows + row_count * 24
    expected_fission = expected_collapsed + row_count * 8
    expected_presence = expected_fission + row_count * 8
    expected_end = expected_presence + row_count
    if (
        row_count == 0
        or groups == 0
        or bounds_count != groups + 1
        or (
            bounds_offset,
            flux_offset,
            rows_offset,
            collapsed_offset,
            fission_offset,
            presence_offset,
            payload_end,
            artifact_len,
        )
        != (
            288,
            expected_flux,
            expected_rows,
            expected_collapsed,
            expected_fission,
            expected_presence,
            expected_end,
            expected_end + 32,
        )
        or len(data) != artifact_len
        or hashlib.sha256(data[:payload_end]).digest() != data[payload_end:]
    ):
        raise ValueError("collapsed layout/integrity")
    boundary_bytes = data[bounds_offset:flux_offset]
    flux_bytes = data[flux_offset:rows_offset]
    rows = [unpack(data, "Qiiii", rows_offset + ordinal * row_bytes) for ordinal in range(row_count)]
    if not increasing_boundaries(boundary_bytes):
        raise ValueError("collapsed boundaries")
    if not finite_nonnegative_f64_payload(flux_bytes):
        raise ValueError("collapsed flux")
    if hashlib.sha256(flux_bytes).digest() != data[176:208]:
        raise ValueError("collapsed flux hash")
    if not finite_nonnegative_f64_payload(data[collapsed_offset:fission_offset]):
        raise ValueError("collapsed values")
    if any(value[0] not in (0, 1) for value in struct.iter_unpack("<B", data[presence_offset:payload_end])):
        raise ValueError("collapsed fission presence")
    return {
        "rows": rows,
        "row_count": row_count,
        "groups": groups,
        "bounds_count": bounds_count,
        "library_sha256": data[112:144].hex(),
        "index_sha256": data[144:176].hex(),
        "flux_sha256": data[176:208].hex(),
        "prepared_integrity_sha256": data[208:240].hex(),
        "integrity_sha256": data[payload_end:].hex(),
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_bytes": len(data),
        "boundary_bytes": boundary_bytes,
    }


def schema_smoke() -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "controls"))
    from p11_fixtures import make_fixture, sha256 as fixture_sha, specification, write_json

    binary = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
    if not binary.is_file():
        return {"checks": {"release_binary_present": False}, "pass": False}
    with tempfile.TemporaryDirectory(prefix="actinv-p15-schema-check-") as directory:
        work = Path(directory)
        fixture = make_fixture(work / "fixture")
        spec_value = specification(
            fixture, mode="trace", cram_order=48, uncertainty=False
        )
        spec = work / "spec.json"
        write_json(spec, spec_value)
        cache = work / "cache"
        output = work / "result.json"
        environment = os.environ.copy()
        environment.update(THREADS)
        environment["ACTINV_CACHE_DIR"] = str(cache)
        completed = subprocess.run(
            [str(binary), "run", str(spec), str(output)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        prepared_paths = list(cache.glob("prepared-v1/*/library.actp"))
        collapsed_paths = list(cache.glob("prepared-v1/*/spectrum-*.actc"))
        if completed.returncode or len(prepared_paths) != 1 or len(collapsed_paths) != 1:
            return {
                "checks": {
                    "run": completed.returncode == 0,
                    "artifact_pair": len(prepared_paths) == len(collapsed_paths) == 1,
                },
                "error_tail": (completed.stdout + completed.stderr)[-2000:],
                "pass": False,
            }
        try:
            prepared = parse_prepared_schema(prepared_paths[0])
            collapsed = parse_collapsed_schema(collapsed_paths[0])
        except (OSError, ValueError, struct.error) as error:
            return {"checks": {"independent_parse": False}, "error": str(error), "pass": False}
        library = fixture["library"]
        index = library.with_name(library.stem + "_index.json")
        checks = {
            "independent_parse": True,
            "source_hashes": prepared["library_sha256"]
            == collapsed["library_sha256"]
            == fixture_sha(library)
            and prepared["index_sha256"] == collapsed["index_sha256"] == fixture_sha(index),
            "row_identity": prepared["rows"] == collapsed["rows"],
            "dimensions": prepared["row_count"] == collapsed["row_count"] == 4
            and prepared["groups"] == collapsed["groups"] == 1
            and prepared["bounds_count"] == collapsed["bounds_count"] == 2,
            "boundary_bits": prepared["boundary_bytes"] == collapsed["boundary_bytes"],
            "prepared_binding": collapsed["prepared_integrity_sha256"]
            == prepared["integrity_sha256"],
            "result_published": output.is_file(),
        }
        return {
            "checks": checks,
            "prepared": {key: prepared[key] for key in (
                "row_count", "groups", "bounds_count", "value_count", "artifact_sha256", "artifact_bytes"
            )},
            "collapsed": {key: collapsed[key] for key in (
                "row_count", "groups", "bounds_count", "flux_sha256", "artifact_sha256", "artifact_bytes"
            )},
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
    display = "cargo" if executable == "cargo" else "python" if executable.startswith("python") else executable
    return {
        "command": " ".join([display, *arguments[1:]]),
        "returncode": completed.returncode,
        "error_tail": "" if completed.returncode == 0 else "\n".join(
            (completed.stdout + completed.stderr).replace(str(ROOT), "<ROOT>").splitlines()[-30:]
        ),
        "pass": completed.returncode == 0,
    }


def quality_commands() -> list[list[str]]:
    cargo = cargo_path()
    python = sys.executable
    return [
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
        [python, "controls/check_p12.py", "--through-g5"],
        [python, "controls/check_p13.py", "--no-write"],
        [python, "controls/check_cb1.py", "--no-write"],
        [python, "controls/check_p14.py", "--no-write"],
        [python, "controls/g1_self_contained.py"],
        [python, "controls/ci_end_to_end.py"],
        [python, "controls/g3_p12_parser_fuzz.py", "--smoke"],
        [python, "controls/g6_p10_projectile_runtime.py"],
        [python, "controls/g5_p8_mesh_identity.py"],
        [python, "controls/g3_p9_coupled_auto.py"],
        [python, "controls/g5_p11_entry_points.py"],
        [python, "controls/g1_p12_radiological.py"],
    ]


def run_quality() -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(THREADS)
    environment["CARGO"] = cargo_path()
    cargo_directory = str(Path(cargo_path()).resolve().parent)
    environment["PATH"] = cargo_directory + os.pathsep + environment.get("PATH", "")
    environment["ACTINV_BIN"] = str(ROOT / "target/release/actinv")
    environment["ACTINV_PYTHON_LIBRARY"] = str(ROOT / "python/target/release/libactinv.so")
    if Path("/tmp/actinv-ci-data").is_dir():
        environment["ACTINV_CI_DATA"] = "/tmp/actinv-ci-data"
    environment["ACTINV_CI_OUT"] = "/tmp/actinv-p15-ci"
    environment["ACTINV_CACHE_DIR"] = "/tmp/actinv-p15-quality-cache"
    records = []
    commands = quality_commands()
    for position, command in enumerate(commands, 1):
        print(f"P15 quality {position}/{len(commands)}: {' '.join(command[1:])}", flush=True)
        records.append(command_result(command, environment))
    result = {
        "schema": "actinv-p15-quality-1",
        "commands": records,
        "pass": all(record["pass"] for record in records),
    }
    QUALITY.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return result


def source_contract() -> dict[str, object]:
    paths = [
        ROOT / "crates/actinv-data/src/prepared.rs",
        ROOT / "crates/actinv-data/src/library.rs",
        ROOT / "crates/actinv-core/src/chain.rs",
        ROOT / "crates/actinv-core/src/run.rs",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    data_doc = (ROOT / "docs/DATA.md").read_text(encoding="utf-8")
    technical = ROOT / "docs/maintainers/PREPARED_DATA.md"
    technical_text = technical.read_text(encoding="utf-8") if technical.is_file() else ""
    checks = {
        "no_unsafe": re.search(r"\bunsafe\b", source) is None,
        "schemas": "ACTPLB01" in source
        and "ACTCOL01" in source
        and "actinv-prepared-library-1" in source
        and "actinv-collapsed-spectrum-1" in source,
        "indexed_reader": "read_prepared_targets" in source,
        "source_bound_cache": "library_sha256" in source and "index_sha256" in source,
        "atomic_publication": "create_new(true)" in source
        and "std::fs::rename" in source
        and "sync_directory" in source,
        "same_core_path": "load_or_prepare_collapsed_after_sha256_verification" in source
        and "ActivationLibrary" in source,
        "ci": "controls/g3_p15_cache_integrity.py" in ci
        and "controls/check_p15.py --no-write" in ci,
        "user_docs": all(
            token in readme
            for token in ("first run", "ACTINV_CACHE_DIR", "Deleting the cache")
        )
        and all(token in data_doc for token in ("Prepared calculation cache", "ACTINV_CACHE_DIR")),
        "technical_docs": all(
            token in technical_text
            for token in (
                "actinv-prepared-library-1",
                "actinv-collapsed-spectrum-1",
                "fail closed",
                "SHA-256",
            )
        ),
    }
    return {"checks": checks, "pass": all(checks.values())}


def evidence_plant_checks(
    prepared: dict[str, Any] | None,
    cache: dict[str, Any] | None,
    interfaces: dict[str, Any] | None,
    performance: dict[str, Any] | None,
) -> dict[str, bool]:
    if None in (prepared, cache, interfaces, performance):
        return {"evidence_present": False}
    planted_prepared = copy.deepcopy(prepared)
    planted_prepared["production"]["rows"] += 1
    planted_cache = copy.deepcopy(cache)
    planted_cache["mutation_plants"]["prepared_magic"]["diagnostic_class"] = False
    planted_interfaces = copy.deepcopy(interfaces)
    planted_interfaces["normalized_result_sha256"]["python"] = "0" * 64
    planted_performance = copy.deepcopy(performance)
    planted_performance["candidate"]["warm_wall"]["median_ms"] += 1.0
    planted_input = copy.deepcopy(performance)
    planted_input["inputs"]["activation_library"]["actual_sha256"] = "0" * 64
    return {
        "prepared_count": not derive_prepared(planted_prepared)["pass"],
        "cache_plant": not derive_cache(planted_cache)["pass"],
        "interface_hash": not derive_interfaces(planted_interfaces)["pass"],
        "performance_statistic": not derive_performance(planted_performance)["pass"],
        "input_hash": not derive_performance(planted_input)["pass"],
    }


def derive_quality(full: bool, schema: dict[str, object], plants: dict[str, bool]) -> dict[str, object]:
    quality = run_quality() if full else load(QUALITY)
    commands = quality.get("commands", []) if quality else []
    contract = source_contract()
    checks = {
        "quality_record": quality is not None
        and quality.get("schema") == "actinv-p15-quality-1"
        and len(commands) == len(quality_commands())
        and quality.get("pass") is True
        and all(record.get("pass") is True for record in commands),
        "schema_parser": schema.get("pass") is True,
        "evidence_plants": len(plants) == 5 and all(plants.values()),
        "source_contract": contract["pass"] is True,
    }
    return {
        "checks": checks,
        "quality": quality,
        "schema_parser": schema,
        "evidence_plants": plants,
        "source_contract": contract,
        "pass": all(checks.values()),
    }


def git_file_sha(commit: str, relative: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


def manifest_reproduces() -> bool:
    sys.path.insert(0, str(ROOT / "controls"))
    import g6_p12_complete

    try:
        return g6_p12_complete.manifest_evidence()["pass"] is True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def session_check(evidence_hashes: dict[str, str | None]) -> dict[str, object]:
    value = load(SESSION)
    if value is None:
        return {"present": False, "pass": False}
    commit = value.get("source_evidence_commit")
    workflow = value.get("workflow", {})
    valid_commit = isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
    commit_files = {
        name: git_file_sha(commit, path.relative_to(ROOT).as_posix()) if valid_commit else None
        for name, path in {
            "prepared": PREPARED,
            "cache": CACHE,
            "interfaces": INTERFACES,
            "performance": PERFORMANCE,
            "quality": QUALITY,
        }.items()
    }
    checks = {
        "schema": value.get("schema") == "actinv-p15-session-1",
        "protocol": value.get("protocol_sha256") == PROTOCOL_SHA256,
        "source_commit": valid_commit,
        "workflow": isinstance(workflow, dict)
        and workflow.get("head_sha") == commit
        and workflow.get("status") == "completed"
        and workflow.get("conclusion") == "success"
        and isinstance(workflow.get("run_id"), int)
        and workflow["run_id"] > 0,
        "evidence_hashes": value.get("evidence_sha256") == evidence_hashes,
        "source_commit_evidence": commit_files == evidence_hashes,
        "manifest": manifest_reproduces(),
        "reported_pass": value.get("pass") is True and value.get("verdict") == "P15-PASS",
    }
    return {
        "present": True,
        "checks": checks,
        "source_commit_evidence_sha256": commit_files,
        "pass": all(checks.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="run every local P15 quality command")
    parser.add_argument("--no-write", action="store_true", help="derive without replacing the verdict")
    parser.add_argument("--require-closure", action="store_true", help="require recorded green GitHub workflow")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    prepared = load(PREPARED)
    cache = load(CACHE)
    interfaces = load(INTERFACES)
    performance = load(PERFORMANCE)
    protocol = protocol_check()
    schema = schema_smoke()
    plants = evidence_plant_checks(prepared, cache, interfaces, performance)
    gates = {
        "G1_G2": derive_prepared(prepared),
        "G3": derive_cache(cache),
        "G4": derive_interfaces(interfaces),
        "G5": derive_performance(performance),
        "G6": derive_quality(arguments.full, schema, plants),
    }
    source_pass = protocol["pass"] and all(gate["pass"] for gate in gates.values())
    evidence_hashes = {
        "prepared": sha256(PREPARED) if PREPARED.is_file() else None,
        "cache": sha256(CACHE) if CACHE.is_file() else None,
        "interfaces": sha256(INTERFACES) if INTERFACES.is_file() else None,
        "performance": sha256(PERFORMANCE) if PERFORMANCE.is_file() else None,
        "quality": sha256(QUALITY) if QUALITY.is_file() else None,
    }
    session = session_check(evidence_hashes)
    closed = source_pass and session["pass"]
    verdict = "P15-PASS" if closed else "P15-SOURCE-PASS" if source_pass else "P15-FAIL"
    output = {
        "schema": "actinv-p15-verdict-1",
        "protocol": protocol,
        "evidence_sha256": evidence_hashes,
        "gates": gates,
        "source_pass": source_pass,
        "session": session,
        "closed": closed,
        "verdict": verdict,
        "pass": closed if arguments.require_closure else source_pass,
    }
    if not arguments.no_write:
        VERDICT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    displayed = output if arguments.verbose else {
        "schema": output["schema"],
        "protocol": protocol["pass"],
        "gates": {name: gate["pass"] for name, gate in gates.items()},
        "improvement_factors": gates["G5"].get("improvement_factors"),
        "source_pass": source_pass,
        "session": session["pass"],
        "closed": closed,
        "verdict": verdict,
        "pass": output["pass"],
    }
    print(json.dumps(displayed, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
