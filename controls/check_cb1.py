#!/usr/bin/env python3
"""Independently rederive the CB1 scorecard and close verdict."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROTOCOL = ROOT / "protocols" / "ACTINV-CB1_PROTOCOL.md"
PROTOCOL_SHA256 = "627990751a4730fe22e457ea2fa334fca25ae0eae7f463c8677e488e5dbb7398"
VERDICT = RESULTS / "verdict_cb1.json"
EVIDENCE = {
    "access": "cb1_access.json",
    "numerical": "cb1_numerical.json",
    "alara": "cb1_alara.json",
    "fns": "cb1_fns.json",
    "prior_validation": "cb1_prior_validation.json",
    "performance": "cb1_performance.json",
    "mesh_performance": "cb1_mesh_performance.json",
    "first_use": "cb1_first_use.json",
    "capabilities": "cb1_capabilities.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def close(left: object, right: object, *, relative: float = 2e-12, absolute: float = 2e-14) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and math.isfinite(float(left))
        and math.isfinite(float(right))
        and math.isclose(float(left), float(right), rel_tol=relative, abs_tol=absolute)
    )


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile of empty values")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def protocol_integrity() -> dict[str, object]:
    ledger = (ROOT / "protocols" / "protocol_hash.txt").read_text(encoding="utf-8").splitlines()
    actual = sha256(PROTOCOL) if PROTOCOL.is_file() else None
    expected_line = f"{PROTOCOL_SHA256}  protocols/ACTINV-CB1_PROTOCOL.md"
    return {
        "expected_sha256": PROTOCOL_SHA256,
        "actual_sha256": actual,
        "ledger_entry": expected_line in ledger,
        "pass": actual == PROTOCOL_SHA256 and expected_line in ledger,
    }


def rederive_fns(value: dict[str, object]) -> dict[str, object]:
    records = value.get("records")
    if not isinstance(records, list):
        return {"error": "records are missing", "pass": False}

    def aggregate(pair_key: str) -> dict[str, object]:
        pooled_ratios: list[float] = []
        sigma_residuals: list[float] = []
        experiment_geomeans: list[float] = []
        experiment_maxima: list[float] = []
        all_within = 0
        unscored = 0
        experiments = 0
        for record in records:
            pairs = record.get("pairs", []) if isinstance(record, dict) else []
            ratios: list[float] = []
            for row in pairs:
                if not isinstance(row, dict):
                    continue
                calculation = float(row.get(pair_key, 0.0))
                measurement = float(row.get("measured_uW_g", 0.0))
                if calculation > 0.0 and measurement > 0.0:
                    ratio = calculation / measurement
                    ratios.append(ratio)
                    pooled_ratios.append(ratio)
                    sigma = float(row.get("sigma_uW_g", 0.0))
                    if sigma > 0.0:
                        sigma_residuals.append((calculation - measurement) / sigma)
                else:
                    unscored += 1
            if ratios:
                experiments += 1
                logs = [math.log(ratio) for ratio in ratios]
                absolute_logs = [abs(item) for item in logs]
                experiment_geomeans.append(math.exp(statistics.fmean(logs)))
                experiment_maxima.append(max(absolute_logs))
                all_within += int(all(item <= math.log(1.3) for item in absolute_logs))
        pooled_logs = [math.log(ratio) for ratio in pooled_ratios]
        absolute_logs = [abs(item) for item in pooled_logs]
        return {
            "experiments_scored": experiments,
            "experiments_total": len(records),
            "points_scored": len(pooled_ratios),
            "pooled_geometric_mean_C_over_E": math.exp(statistics.fmean(pooled_logs)),
            "median_experiment_geometric_mean_C_over_E": statistics.median(experiment_geomeans),
            "median_pooled_abs_log_C_over_E": statistics.median(absolute_logs),
            "p90_pooled_abs_log_C_over_E": quantile(absolute_logs, 0.9),
            "median_experiment_maximum_abs_log_C_over_E": statistics.median(experiment_maxima),
            "experiments_all_points_within_30_percent": all_within,
            "fraction_experiments_all_points_within_30_percent": all_within / experiments,
            "positive_sigma_points": len(sigma_residuals),
            "rms_measurement_sigma": math.sqrt(statistics.fmean(item * item for item in sigma_residuals)),
            "unscored_nonpositive_calculation": unscored,
        }

    derived = {
        "actinv_tendl2025": aggregate("actinv_tendl2025_uW_g"),
        "fispact_4_tendl2017_published": aggregate("fispact_tendl2017_uW_g"),
    }
    reported = value.get("summary", {})
    comparisons: dict[str, bool] = {}
    for product, metrics in derived.items():
        reported_metrics = reported.get(product, {}) if isinstance(reported, dict) else {}
        for name, result in metrics.items():
            expected = reported_metrics.get(name) if isinstance(reported_metrics, dict) else None
            comparisons[f"{product}.{name}"] = (
                result == expected
                if isinstance(result, int) and not isinstance(result, bool)
                else close(result, expected)
            )
    return {"derived": derived, "comparisons": comparisons, "pass": all(comparisons.values())}


def repeated_measurement_checks(value: object) -> dict[str, bool]:
    checks: dict[str, bool] = {}

    def visit(node: object, path: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("raw_ms"), list):
                raw = [float(item) for item in node["raw_ms"]]
                checks[f"{path}.samples"] = node.get("samples") == len(raw)
                checks[f"{path}.minimum"] = close(node.get("minimum_ms"), min(raw))
                checks[f"{path}.mean"] = close(node.get("mean_ms"), statistics.fmean(raw))
                checks[f"{path}.median"] = close(node.get("median_ms"), statistics.median(raw))
                checks[f"{path}.p95"] = close(node.get("p95_ms"), quantile(raw, 0.95))
                checks[f"{path}.stdev"] = close(
                    node.get("sample_standard_deviation_ms"), statistics.stdev(raw)
                )
            elif isinstance(node.get("raw"), list) and all(
                key in node for key in ("minimum", "mean", "median", "p95", "sample_standard_deviation")
            ):
                raw = [float(item) for item in node["raw"]]
                checks[f"{path}.samples"] = node.get("samples") == len(raw)
                checks[f"{path}.minimum"] = close(node.get("minimum"), min(raw))
                checks[f"{path}.mean"] = close(node.get("mean"), statistics.fmean(raw))
                checks[f"{path}.median"] = close(node.get("median"), statistics.median(raw))
                checks[f"{path}.p95"] = close(node.get("p95"), quantile(raw, 0.95))
                checks[f"{path}.stdev"] = close(
                    node.get("sample_standard_deviation"), statistics.stdev(raw)
                )
            for key, child in node.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "")
    return checks


def evidence_integrity(values: dict[str, dict[str, object] | None]) -> dict[str, object]:
    checks = {
        f"{name}_present_and_passed": value is not None and value.get("pass") is True
        for name, value in values.items()
    }
    numerical = values["numerical"] or {}
    worst = numerical.get("worst", {}) if isinstance(numerical, dict) else {}
    checks.update(
        {
            "numerical_relative_bound": float(worst.get("relative_above_tolerance_crossover", math.inf))
            <= 5e-12,
            "numerical_absolute_bound": float(worst.get("absolute_over_initial_norm", math.inf)) <= 5e-14,
            "numerical_split_bound": float(worst.get("split_merged_absolute_over_initial_norm", math.inf))
            <= 5e-14,
        }
    )
    alara = values["alara"] or {}
    reaction = alara.get("reaction_rate", {}) if isinstance(alara, dict) else {}
    checks.update(
        {
            "alara_rate_bound": float(reaction.get("relative_difference", math.inf)) <= 1e-12,
            "alara_inventory_bound": float(
                alara.get("maximum_inventory_relative_above_1e-10_initial", math.inf)
            )
            <= 5e-4,
            "alara_fresh_rerun": (alara.get("rerun") or {}).get("official_reference_conversion_and_run")
            is True,
        }
    )
    prior = values["prior_validation"] or {}
    provenance = prior.get("provenance", {}) if isinstance(prior, dict) else {}
    checks["prior_evidence_hashes"] = bool(provenance) and all(
        row.get("matches") is True and row.get("actual_sha256") == row.get("expected_sha256")
        for row in provenance.values()
        if isinstance(row, dict)
    )
    first = values["first_use"] or {}
    actinv = first.get("ACTINV", {}) if isinstance(first, dict) else {}
    alara_first = first.get("ALARA", {}) if isinstance(first, dict) else {}
    checks.update(
        {
            "first_use_actinv_release_identity": (actinv.get("artifact_measurement") or {}).get(
                "matches_published_release_record"
            )
            is True,
            "first_use_actinv_data_identity": (actinv.get("default_data") or {}).get("all_artifacts_match")
            is True,
            "first_use_actinv_diagnostics": all(
                row.get("nonzero") is True and row.get("names_offending_item") is True
                for row in (actinv.get("diagnostics") or {}).values()
            ),
            "first_use_alara_diagnostics": all(
                row.get("nonzero") is True and row.get("names_offending_item") is True
                for row in (alara_first.get("diagnostics") or {}).values()
            ),
        }
    )
    capability = values["capabilities"] or {}
    products = capability.get("products", []) if isinstance(capability, dict) else []
    axes = capability.get("axes", []) if isinstance(capability, dict) else []
    allowed = {"verified", "partial", "absent", "unverified", "not-applicable"}
    checks["capability_matrix_complete"] = (
        len(products) == 5
        and len(axes) >= 16
        and all(set(axis.get("cells", {})) == set(products) for axis in axes if isinstance(axis, dict))
        and all(
            row.get("status") in allowed and bool(row.get("sources"))
            for axis in axes
            for row in axis.get("cells", {}).values()
            if isinstance(axis, dict) and isinstance(row, dict)
        )
    )
    performance_checks = repeated_measurement_checks(values["performance"])
    mesh_checks = repeated_measurement_checks(values["mesh_performance"])
    checks["all_performance_statistics_rederived"] = bool(performance_checks) and all(
        performance_checks.values()
    )
    checks["all_mesh_statistics_rederived"] = bool(mesh_checks) and all(mesh_checks.values())
    mesh = values["mesh_performance"] or {}
    one = mesh.get("one_thread_256", {}) if isinstance(mesh, dict) else {}
    four = next(
        (
            row
            for row in mesh.get("four_thread_scaling", [])
            if isinstance(row, dict) and row.get("cells") == 256
        ),
        {},
    )
    derived_speedup = (one.get("reported_wall_s") or {}).get("median", math.nan) / (
        four.get("reported_wall_s") or {}
    ).get("median", math.inf)
    checks["mesh_speedup_rederived"] = close(
        derived_speedup, mesh.get("four_thread_speedup_at_256_cells")
    )
    checks["million_cell_result_is_explicitly_unexecuted"] = (
        "not executed"
        in (mesh.get("million_cell_linear_extrapolation_not_executed") or {}).get("warning", "")
    )
    return {
        "checks": checks,
        "performance_statistic_checks": performance_checks,
        "mesh_statistic_checks": mesh_checks,
        "pass": all(checks.values()),
    }


def documentation_check(values: dict[str, dict[str, object] | None]) -> dict[str, object]:
    report_path = ROOT / "docs" / "COMPETITIVE_BENCHMARK.md"
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required = {
        "protocol_hash": PROTOCOL_SHA256,
        "no_composite_winner": "There is deliberately no composite winner score",
        "fispact_loss_visible": "FISPACT leads the typical point",
        "data_confounding_visible": "solver and data effects remain confounded",
        "fns_actinv_median": "`0.1392`",
        "fns_fispact_median": "`0.1053`",
        "fns_actinv_p90": "`0.6637`",
        "fns_fispact_p90": "`0.6846`",
        "alara_inventory": "`4.12e-8`",
        "public_example_memory": "`1.09 GB`",
        "million_cell_warning": "that run was not executed",
        "licensed_access_limit": "no fresh FISPACT-II or SCALE/ORIGEN executable",
        "typed_units_gap": "unit mistakes harder",
    }
    checks = {name: token in report for name, token in required.items()}
    checks["readme_links_scorecard"] = "docs/COMPETITIVE_BENCHMARK.md" in readme
    return {"checks": checks, "pass": all(checks.values())}


def run_rust_gates() -> dict[str, object]:
    environment = os.environ.copy()
    default_cargo = Path.home() / ".rustup" / "toolchains" / "stable-x86_64-unknown-linux-gnu" / "bin" / "cargo"
    cargo = environment.get("CARGO", str(default_cargo) if default_cargo.is_file() else "cargo")
    commands = [
        [cargo, "fmt", "--all", "--", "--check"],
        [cargo, "check", "--workspace", "--all-targets", "--all-features"],
        [cargo, "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
        [cargo, "test", "--workspace", "--all-targets", "--all-features"],
    ]
    results = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        results.append(
            {
                "command": " ".join(["cargo", *command[1:]]),
                "returncode": completed.returncode,
                "error_tail": ""
                if completed.returncode == 0
                else "\n".join((completed.stdout + completed.stderr).splitlines()[-30:]),
                "pass": completed.returncode == 0,
            }
        )
    return {"commands": results, "pass": all(item["pass"] for item in results)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="also run the four required Rust quality gates")
    args = parser.parse_args()
    values = {name: load(RESULTS / filename) for name, filename in EVIDENCE.items()}
    protocol = protocol_integrity()
    evidence = evidence_integrity(values)
    fns = rederive_fns(values["fns"] or {})
    documentation = documentation_check(values)
    rust = run_rust_gates() if args.full else {"executed": False, "pass": None}
    evidence_pass = protocol["pass"] and evidence["pass"] and fns["pass"] and documentation["pass"]
    complete = evidence_pass and args.full and rust["pass"] is True
    output = {
        "schema": "actinv-cb1-verdict-1",
        "protocol": protocol,
        "evidence": evidence,
        "fns_rederivation": fns,
        "documentation": documentation,
        "rust_gates": rust,
        "evidence_pass": evidence_pass,
        "complete": complete,
        "verdict": "CB1-COMPLETE" if complete else ("CB1-EVIDENCE-PASS" if evidence_pass else "CB1-FAIL"),
        "pass": complete if args.full else evidence_pass,
    }
    VERDICT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
