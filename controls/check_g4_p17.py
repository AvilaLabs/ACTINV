#!/usr/bin/env python3
"""Independent, bulk-data-free checker for P17 G4 diagnostic evidence.

This checker imports neither ACTINV nor any P17 generator/scoring module.  It
repeats row predicates, C/E arithmetic, population-linear percentiles,
controlled-substitution summaries, input-role partitions, and mismatch-ledger
coverage directly from the committed compact evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g4_p17_diagnostics.json"
LEDGER = ROOT / "results" / "p17_cause_ledger.json"
CHECK_RESULT = ROOT / "results" / "g4_p17_check.json"
DIAGNOSTIC_SCHEMA = ROOT / "controls" / "fixtures" / "p17_diagnostic_schema.json"
LEDGER_SCHEMA = ROOT / "controls" / "fixtures" / "p17_cause_ledger_schema.json"

PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
OPENING_SOURCE = "f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d"
EXPECTED_GLOBAL = {
    "fns_archive": "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d",
    "library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "library_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_endf": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_jeff": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "actinv_module": "a5be3011ce63e3ff62748de47cefa7c1f6e163657a55b1531cece2928ec95400",
}
EXPECTED_IRDFF = {
    "pdf": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    "irdff_group_archive": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
    "irdff_spectrum_archive": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
    "production_library": EXPECTED_GLOBAL["library"],
    "production_index": EXPECTED_GLOBAL["library_index"],
}
VARIANT_PRODUCTION = "actinv_tendl2025"
VARIANT_DECAY = "actinv_tendl2025_jeff_primary"
VARIANT_FISPACT_CONTEXT = "fispact4_tendl2017_published_context"
VARIANT_IRDFF = "irdff_ii_groupwise_diagnostic"
VARIANTS = {
    VARIANT_PRODUCTION,
    VARIANT_DECAY,
    VARIANT_FISPACT_CONTEXT,
    VARIANT_IRDFF,
}
CAUSES = {
    "solver",
    "chain-construction",
    "processor",
    "evaluation",
    "decay-yield",
    "measurement-definition",
    "unsupported-model",
    "unresolved",
}
TAXONOMY = [
    "solver",
    "chain-construction",
    "processor",
    "evaluation",
    "decay-yield",
    "measurement-definition",
    "unsupported-model",
    "unresolved",
]
TOP_LEVEL_REQUIRED = {
    "schema",
    "protocol_sha256",
    "opening_source_commit",
    "control_source_sha256",
    "scoring_source_sha256",
    "irdff_source_sha256",
    "checker_source_sha256",
    "schema_identities",
    "global_input_identities",
    "prior_layer_evidence",
    "variant_definitions",
    "input_sets",
    "controlled_substitutions",
    "source_diagnostics",
    "established_fns_identity",
    "row_counts",
    "rows",
    "family_metrics",
    "material_mismatches",
    "cause_ledger",
    "checks",
    "pass",
    "evidence_sha256",
}
ROW_KEYS = {
    "row_id",
    "family",
    "source_id",
    "source_record",
    "source_record_sha256",
    "observable",
    "unit",
    "experimental",
    "inclusion",
    "calculations",
}
CALCULATION_KEYS = {
    "status",
    "reason",
    "value",
    "ratio_C_over_E",
    "signed_log_C_over_E",
    "material_mismatch",
    "input_set_id",
    "interpretation",
}
METRIC_KEYS = {
    "scored_rows",
    "unscored_rows",
    "unscored_reasons",
    "geometric_mean_C_over_E",
    "median_abs_log_C_over_E",
    "p90_abs_log_C_over_E",
    "maximum_abs_log_C_over_E",
    "fraction_within_10_percent",
    "fraction_within_20_percent",
    "fraction_within_30_percent",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def close(left: Any, right: Any, context: str) -> None:
    if left is None or right is None:
        require(left is right, f"{context}: {left!r} != {right!r}")
        return
    require(
        math.isclose(float(left), float(right), rel_tol=2.0e-13, abs_tol=2.0e-15),
        f"{context}: {left!r} != {right!r}",
    )


def percentile_linear(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    require(bool(ordered), "percentile requested for an empty population")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metrics_for(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    ratios = []
    reasons: Counter[str] = Counter()
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            reasons[row["inclusion"]["reason"]] += 1
            continue
        calculation = row["calculations"].get(variant)
        if calculation is None:
            reasons["variant_reaction_unavailable"] += 1
        elif calculation["status"] != "scored":
            reasons[calculation["reason"]] += 1
        else:
            ratios.append(float(calculation["ratio_C_over_E"]))
    if ratios:
        logs = [math.log(value) for value in ratios]
        absolute = [abs(value) for value in logs]
        statistics = {
            "geometric_mean_C_over_E": math.exp(math.fsum(logs) / len(logs)),
            "median_abs_log_C_over_E": percentile_linear(absolute, 0.5),
            "p90_abs_log_C_over_E": percentile_linear(absolute, 0.9),
            "maximum_abs_log_C_over_E": max(absolute),
            "fraction_within_10_percent": sum(0.9 <= value <= 1.1 for value in ratios)
            / len(ratios),
            "fraction_within_20_percent": sum(0.8 <= value <= 1.2 for value in ratios)
            / len(ratios),
            "fraction_within_30_percent": sum((1.0 / 1.3) <= value <= 1.3 for value in ratios)
            / len(ratios),
        }
    else:
        statistics = {
            "geometric_mean_C_over_E": None,
            "median_abs_log_C_over_E": None,
            "p90_abs_log_C_over_E": None,
            "maximum_abs_log_C_over_E": None,
            "fraction_within_10_percent": None,
            "fraction_within_20_percent": None,
            "fraction_within_30_percent": None,
        }
    return {
        "scored_rows": len(ratios),
        "unscored_rows": len(rows) - len(ratios),
        "unscored_reasons": dict(sorted(reasons.items())),
        **statistics,
    }


def compare_metrics(actual: dict[str, Any], expected: dict[str, Any], context: str) -> None:
    require(set(actual) == METRIC_KEYS, f"{context}: metric keys changed")
    require(actual["scored_rows"] == expected["scored_rows"], f"{context}: scored count")
    require(actual["unscored_rows"] == expected["unscored_rows"], f"{context}: unscored count")
    require(
        actual["unscored_reasons"] == expected["unscored_reasons"],
        f"{context}: unscored reasons",
    )
    for key in METRIC_KEYS - {"scored_rows", "unscored_rows", "unscored_reasons"}:
        close(actual[key], expected[key], f"{context}/{key}")


def validate_schemas(result: dict[str, Any]) -> None:
    diagnostic = json.loads(DIAGNOSTIC_SCHEMA.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_SCHEMA.read_text(encoding="utf-8"))
    require(
        diagnostic["$id"] == "https://avilalabs.org/actinv/schemas/p17-diagnostic-1.json",
        "diagnostic schema id changed",
    )
    require(
        ledger["$id"] == "https://avilalabs.org/actinv/schemas/p17-cause-ledger-1.json",
        "cause-ledger schema id changed",
    )
    require(set(diagnostic["required"]) == TOP_LEVEL_REQUIRED, "diagnostic required fields changed")
    require(
        diagnostic["properties"]["schema"]["const"] == "actinv-p17-diagnostic-1",
        "diagnostic schema discriminator changed",
    )
    require(
        ledger["properties"]["schema"]["const"] == "actinv-p17-cause-ledger-1",
        "ledger schema discriminator changed",
    )
    require(
        result["schema_identities"]
        == {"diagnostic": sha256(DIAGNOSTIC_SCHEMA), "cause_ledger": sha256(LEDGER_SCHEMA)},
        "committed schema identities changed",
    )


def validate_input_sets(result: dict[str, Any]) -> set[str]:
    referenced = set()
    for identifier, input_set in result["input_sets"].items():
        require(identifier, "empty input-set identifier")
        require(
            set(input_set) == {"input_hashes", "identical_input_roles", "changed_input_roles"},
            f"{identifier}: input-set fields changed",
        )
        hashes = input_set["input_hashes"]
        require(bool(hashes), f"{identifier}: empty input hashes")
        require(all(sha256_text(value) for value in hashes.values()), f"{identifier}: invalid SHA-256")
        identical = input_set["identical_input_roles"]
        changed = input_set["changed_input_roles"]
        require(identical == sorted(set(identical)), f"{identifier}: identical roles not canonical")
        require(changed == sorted(set(changed)), f"{identifier}: changed roles not canonical")
        require(not set(identical) & set(changed), f"{identifier}: overlapping input roles")
        require(set(identical) | set(changed) == set(hashes), f"{identifier}: roles do not partition hashes")
        if identifier.endswith(":production"):
            require(not changed, f"{identifier}: production baseline marks changed roles")
        elif identifier.endswith(":jeff-primary"):
            require(
                set(changed) == {"decay_primary", "decay_fallback"},
                f"{identifier}: decay substitution roles changed",
            )
            require(hashes["decay_primary"] == EXPECTED_GLOBAL["decay_jeff"], f"{identifier}: JEFF hash")
            require(hashes["decay_fallback"] == EXPECTED_GLOBAL["decay_endf"], f"{identifier}: ENDF hash")
        elif identifier.endswith(":irdff-groupwise"):
            require(
                set(changed) == {"activation_data", "activation_data_index"},
                f"{identifier}: IRDFF substitution roles changed",
            )
            require(
                hashes["activation_data"] == EXPECTED_IRDFF["irdff_group_archive"],
                f"{identifier}: IRDFF data hash",
            )
        elif identifier.endswith(":fispact-context"):
            require(set(changed) == set(hashes), f"{identifier}: context inputs not all changed")
    for row in result["rows"]:
        for calculation in row["calculations"].values():
            referenced.add(calculation["input_set_id"])
    require(referenced == set(result["input_sets"]), "unreferenced or missing input set")
    return referenced


def validate_calculation(
    row: dict[str, Any], variant: str, calculation: dict[str, Any], input_sets: dict[str, Any]
) -> None:
    context = f"{row['row_id']}::{variant}"
    require(set(calculation) == CALCULATION_KEYS, f"{context}: calculation fields changed")
    require(variant in VARIANTS, f"{context}: unknown variant")
    require(calculation["input_set_id"] in input_sets, f"{context}: missing input set")
    require(bool(calculation["interpretation"]), f"{context}: empty interpretation")
    if calculation["status"] == "scored":
        require(row["inclusion"]["status"] == "scored", f"{context}: excluded row was scored")
        require(calculation["reason"] == "scored", f"{context}: scored reason changed")
        experimental = float(row["experimental"]["value"])
        value = float(calculation["value"])
        require(math.isfinite(value) and value > 0.0, f"{context}: invalid calculation value")
        ratio = value / experimental
        signed_log = math.log(ratio)
        mismatch = not ((1.0 / 1.3) <= ratio <= 1.3)
        close(calculation["ratio_C_over_E"], ratio, f"{context}: ratio")
        close(calculation["signed_log_C_over_E"], signed_log, f"{context}: signed log")
        require(calculation["material_mismatch"] is mismatch, f"{context}: mismatch flag")
    else:
        require(calculation["status"] in {"unscored", "context"}, f"{context}: status")
        require(calculation["reason"] != "scored", f"{context}: unscored reason")
        require(calculation["ratio_C_over_E"] is None, f"{context}: unscored ratio")
        require(calculation["signed_log_C_over_E"] is None, f"{context}: unscored log")
        require(calculation["material_mismatch"] is False, f"{context}: unscored mismatch")
    if variant == VARIANT_FISPACT_CONTEXT:
        require(calculation["status"] == "context", f"{context}: FISPACT context was scored")
        require(
            calculation["reason"] == "different_data_context",
            f"{context}: FISPACT context reason changed",
        )


def validate_rows(result: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    rows = result["rows"]
    require(len(rows) == 2531, f"row count changed: {len(rows)}")
    require(len({row["row_id"] for row in rows}) == len(rows), "duplicate row identity")
    require(len({row["source_id"] for row in rows}) == len(rows), "duplicate source identity")
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mismatches = []
    irdff_numbers: dict[int, list[int]] = defaultdict(list)
    fns_experiments = set()
    for row in rows:
        require(set(row) == ROW_KEYS, f"{row.get('row_id')}: row fields changed")
        require(
            row["source_record_sha256"] == canonical_sha256(row["source_record"]),
            f"{row['row_id']}: compact source record changed",
        )
        source = row["source_record"]
        if row["row_id"].startswith("irdff:"):
            table = int(source["table"])
            number = int(source["table_row"])
            require(table in {18, 19, 20}, f"{row['row_id']}: sealed/unknown IRDFF table")
            require(row["row_id"] == f"irdff:t{table}:r{number:03d}", f"{row['row_id']}: row id")
            require(row["family"] == f"irdff_table_{table}", f"{row['row_id']}: family")
            require(
                row["source_id"] == f"IRDFF-II:Table-{table}:row-{number}",
                f"{row['row_id']}: source id",
            )
            require(row["observable"] == "spectrum-averaged cross section", f"{row['row_id']}: observable")
            require(row["unit"] == "mb", f"{row['row_id']}: unit")
            close(row["experimental"]["value"], source["measured_mb"], f"{row['row_id']}: source value")
            close(
                row["experimental"]["uncertainty"],
                source["experimental_uncertainty_percent"],
                f"{row['row_id']}: source uncertainty",
            )
            require(row["experimental"]["uncertainty_unit"] == "percent", f"{row['row_id']}: uncertainty unit")
            require(row["inclusion"] == {"status": "scored", "reason": "scored"}, f"{row['row_id']}: inclusion")
            require(
                set(row["calculations"]) == {VARIANT_PRODUCTION, VARIANT_IRDFF},
                f"{row['row_id']}: variants",
            )
            production_hashes = result["input_sets"][
                row["calculations"][VARIANT_PRODUCTION]["input_set_id"]
            ]["input_hashes"]
            official_hashes = result["input_sets"][
                row["calculations"][VARIANT_IRDFF]["input_set_id"]
            ]["input_hashes"]
            require(
                production_hashes["measurement"] == EXPECTED_IRDFF["pdf"]
                and production_hashes["spectrum"]
                == EXPECTED_IRDFF["irdff_spectrum_archive"]
                and production_hashes["activation_data"] == EXPECTED_GLOBAL["library"]
                and production_hashes["activation_data_index"]
                == EXPECTED_GLOBAL["library_index"],
                f"{row['row_id']}: production input identities",
            )
            require(
                official_hashes["measurement"] == production_hashes["measurement"]
                and official_hashes["spectrum"] == production_hashes["spectrum"]
                and official_hashes["calculation_implementation"]
                == production_hashes["calculation_implementation"],
                f"{row['row_id']}: uncontrolled IRDFF substitution",
            )
            irdff_numbers[table].append(number)
        elif row["row_id"].startswith("fns:"):
            material = source["material"]
            experiment = source["experiment"]
            number = int(source["measurement_row"])
            require(
                row["row_id"] == f"fns:{material}:{experiment}:r{number:03d}",
                f"{row['row_id']}: row id",
            )
            require(row["family"] == f"fns:{material}/{experiment}", f"{row['row_id']}: family")
            require(
                row["source_id"] == f"CoNDERC-FNS:{material}/{experiment}.exp:row-{number}",
                f"{row['row_id']}: source id",
            )
            require(row["observable"] == "decay heat", f"{row['row_id']}: observable")
            require(row["unit"] == "uW/g", f"{row['row_id']}: unit")
            close(
                row["experimental"]["value"],
                source["measurement_heat_uW_g"],
                f"{row['row_id']}: source value",
            )
            close(
                row["experimental"]["uncertainty"],
                source["measurement_sigma_uW_g"],
                f"{row['row_id']}: source uncertainty",
            )
            require(row["experimental"]["uncertainty_unit"] == "uW/g", f"{row['row_id']}: uncertainty unit")
            time = float(source["measurement_time_raw"])
            value = float(source["measurement_heat_uW_g"])
            if time <= 0.0:
                expected_inclusion = {"status": "unscored", "reason": "nonpositive_measurement_time"}
            elif value <= 0.0:
                expected_inclusion = {"status": "unscored", "reason": "nonpositive_experimental_value"}
            elif source["cooling_step"] is None:
                expected_inclusion = {
                    "status": "unscored",
                    "reason": "no_cooling_step_within_2_percent",
                }
            else:
                expected_inclusion = {"status": "scored", "reason": "scored"}
                require(
                    0.0 <= source["alignment_relative_mismatch"] <= 0.02,
                    f"{row['row_id']}: alignment outside predicate",
                )
            require(row["inclusion"] == expected_inclusion, f"{row['row_id']}: inclusion predicate")
            require(
                set(row["calculations"])
                == {VARIANT_PRODUCTION, VARIANT_DECAY, VARIANT_FISPACT_CONTEXT},
                f"{row['row_id']}: variants",
            )
            production_hashes = result["input_sets"][
                row["calculations"][VARIANT_PRODUCTION]["input_set_id"]
            ]["input_hashes"]
            decay_hashes = result["input_sets"][
                row["calculations"][VARIANT_DECAY]["input_set_id"]
            ]["input_hashes"]
            require(
                production_hashes["measurement"] == source["measurement_file_sha256"]
                and production_hashes["activation_library"] == EXPECTED_GLOBAL["library"]
                and production_hashes["activation_index"] == EXPECTED_GLOBAL["library_index"]
                and production_hashes["decay_primary"] == EXPECTED_GLOBAL["decay_endf"]
                and production_hashes["decay_fallback"] == EXPECTED_GLOBAL["decay_jeff"]
                and production_hashes["runtime"] == EXPECTED_GLOBAL["actinv_module"],
                f"{row['row_id']}: production input identities",
            )
            require(
                {
                    role: value
                    for role, value in decay_hashes.items()
                    if role not in {"decay_primary", "decay_fallback"}
                }
                == {
                    role: value
                    for role, value in production_hashes.items()
                    if role not in {"decay_primary", "decay_fallback"}
                },
                f"{row['row_id']}: uncontrolled decay substitution",
            )
            fns_experiments.add((material, experiment))
        else:
            raise AssertionError(f"unknown row family: {row['row_id']}")
        require(
            math.isfinite(float(row["experimental"]["uncertainty"]))
            and float(row["experimental"]["uncertainty"]) >= 0.0,
            f"{row['row_id']}: invalid experimental uncertainty",
        )
        for variant, calculation in row["calculations"].items():
            validate_calculation(row, variant, calculation, result["input_sets"])
            if calculation["status"] == "scored" and calculation["material_mismatch"]:
                mismatches.append(f"{row['row_id']}::{variant}")
        families[row["family"]].append(row)
    require(irdff_numbers[18] == list(range(1, 45)), "Table 18 row sequence")
    require(irdff_numbers[19] == list(range(1, 27)), "Table 19 row sequence")
    require(irdff_numbers[20] == list(range(1, 55)), "Table 20 row sequence")
    require(len(fns_experiments) == 132, f"FNS experiment count changed: {len(fns_experiments)}")
    require(len(families) == 135, f"family count changed: {len(families)}")
    return families, sorted(mismatches)


def validate_family_metrics(result: dict[str, Any], families: dict[str, list[dict[str, Any]]]) -> None:
    require(set(result["family_metrics"]) == set(families), "family metric assignments changed")
    for family, rows in families.items():
        variants = {variant for row in rows for variant in row["calculations"]}
        require(
            set(result["family_metrics"][family]) == variants,
            f"{family}: metric variants changed",
        )
        for variant in variants:
            compare_metrics(
                result["family_metrics"][family][variant],
                metrics_for(rows, variant),
                f"{family}/{variant}",
            )


def distribution(values: list[float]) -> dict[str, Any]:
    absolute = [abs(value) for value in values]
    return {
        "rows": len(values),
        "median_abs_signed_log_change": percentile_linear(absolute, 0.5),
        "p90_abs_signed_log_change": percentile_linear(absolute, 0.9),
        "maximum_abs_signed_log_change": max(absolute),
    }


def validate_controlled_substitutions(result: dict[str, Any]) -> None:
    substitutions = result["controlled_substitutions"]
    require(
        set(substitutions)
        == {
            "numerical_solver",
            "chain_construction",
            "processor",
            "evaluation",
            "decay_yield",
            "measurement_definition",
        },
        "controlled substitution layers changed",
    )
    evaluation = []
    decay = []
    alignments = []
    for row in result["rows"]:
        calculations = row["calculations"]
        if VARIANT_IRDFF in calculations:
            left = calculations[VARIANT_PRODUCTION]
            right = calculations[VARIANT_IRDFF]
            if left["status"] == right["status"] == "scored":
                evaluation.append(right["signed_log_C_over_E"] - left["signed_log_C_over_E"])
        if VARIANT_DECAY in calculations:
            left = calculations[VARIANT_PRODUCTION]
            right = calculations[VARIANT_DECAY]
            if left["status"] == right["status"] == "scored":
                decay.append(right["signed_log_C_over_E"] - left["signed_log_C_over_E"])
                alignments.append(row["source_record"]["alignment_relative_mismatch"])
    for name, expected in (("evaluation", distribution(evaluation)), ("decay_yield", distribution(decay))):
        for key, value in expected.items():
            if key == "rows":
                require(substitutions[name][key] == value, f"{name}/{key}")
            else:
                close(substitutions[name][key], value, f"{name}/{key}")
    measurement = substitutions["measurement_definition"]
    require(measurement["rows"] == len(alignments), "measurement-definition row count")
    close(
        measurement["maximum_alignment_relative_mismatch"],
        max(alignments),
        "measurement-definition alignment bound",
    )
    require(measurement["calculation_effect"] == 0.0, "unsupported measurement transformation")


def validate_prior_evidence(result: dict[str, Any]) -> None:
    for name, evidence in result["prior_layer_evidence"].items():
        path = ROOT / evidence["path"]
        data = json.loads(path.read_text(encoding="utf-8"))
        require(evidence["sha256"] == sha256(path), f"{name}: evidence hash")
        require(data["pass"] is True and evidence["pass"] is True, f"{name}: evidence is not green")
    solver = result["controlled_substitutions"]["numerical_solver"]
    solver_data = json.loads((ROOT / solver["path"]).read_text(encoding="utf-8"))
    close(
        solver["maximum_meaningful_relative"],
        solver_data["worst"]["relative_above_tolerance_crossover"],
        "solver bound",
    )
    chain = result["controlled_substitutions"]["chain_construction"]
    chain_data = json.loads((ROOT / chain["path"]).read_text(encoding="utf-8"))
    close(chain["maximum_rate_relative"], chain_data["maximum_rate_relative"], "chain rate bound")
    processor = result["controlled_substitutions"]["processor"]
    processor_data = json.loads((ROOT / processor["path"]).read_text(encoding="utf-8"))
    close(
        processor["maximum_one_group_relative"],
        processor_data["summary"]["maximum_one_group_relative"],
        "processor one-group bound",
    )


def validate_source_diagnostics(
    result: dict[str, Any], families: dict[str, list[dict[str, Any]]]
) -> None:
    diagnostics = result["source_diagnostics"]
    ir = diagnostics["irdff"]
    require(ir["input_identities"] == EXPECTED_IRDFF, "IRDFF external identities changed")
    require(ir["row_counts"] == {"18": 44, "19": 26, "20": 54}, "IRDFF row counts")
    ir_rows = [row for row in result["rows"] if row["family"].startswith("irdff_")]
    require(ir["rows_total"] == len(ir_rows) == 124, "IRDFF total")
    require(
        ir["official_rows_folded"]
        == sum(row["calculations"][VARIANT_IRDFF]["status"] == "scored" for row in ir_rows)
        == 124,
        "IRDFF fold count",
    )
    require(
        ir["production_rows_folded"]
        == sum(row["calculations"][VARIANT_PRODUCTION]["status"] == "scored" for row in ir_rows),
        "production IRDFF fold count",
    )
    published = [
        relative(
            row["calculations"][VARIANT_IRDFF]["value"],
            row["source_record"]["published_calculated_mb"],
        )
        for row in ir_rows
    ]
    close(ir["maximum_official_vs_published_relative"], max(published), "published IRDFF fold check")

    fns = diagnostics["fns"]
    require(fns["experiments"] == 132, "FNS diagnostic experiment count")
    require(
        set(fns)
        == {
            "experiments",
            "cache_fingerprint",
            "calculation_implementation_sha256",
            "alignments",
            "run_summaries",
        },
        "non-reproducible FNS execution counters entered evidence",
    )
    require(set(fns["alignments"]) == {key for key in families if key.startswith("fns:")}, "FNS alignments")
    for family, alignment in fns["alignments"].items():
        rows = families[family]
        require(alignment["source_rows"] == len(rows), f"{family}: source row count")
        require(
            alignment["matched_rows"] == sum(row["inclusion"]["status"] == "scored" for row in rows),
            f"{family}: matched row count",
        )
        reasons = Counter(
            row["inclusion"]["reason"]
            for row in rows
            if row["inclusion"]["status"] == "unscored"
        )
        require(alignment["excluded_reasons"] == dict(sorted(reasons.items())), f"{family}: exclusions")


def relative(left: float, right: float) -> float:
    return abs(float(left) - float(right)) / max(abs(float(left)), abs(float(right)), 1.0e-300)


def validate_established_identity(result: dict[str, Any]) -> None:
    identity = result["established_fns_identity"]
    path = ROOT / identity["baseline_path"]
    require(identity["baseline_sha256"] == sha256(path), "CB1 baseline hash changed")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    generated: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result["rows"]:
        if row["family"].startswith("fns:") and row["inclusion"]["status"] == "scored":
            source = row["source_record"]
            generated[(source["material"], source["experiment"])].append(row)
    maximum = 0.0
    count = 0
    for record in baseline["records"]:
        current = generated[(record["material"], record["experiment"])]
        require(len(current) == len(record["pairs"]), "established FNS alignment changed")
        for row, pair in zip(current, record["pairs"], strict=True):
            maximum = max(
                maximum,
                relative(row["source_record"]["cooling_time_s"], pair["time_s"]),
                relative(row["experimental"]["value"], pair["measured_uW_g"]),
                relative(
                    row["calculations"][VARIANT_PRODUCTION]["value"],
                    pair["actinv_tendl2025_uW_g"],
                ),
            )
            count += 1
    require(count == identity["pairs_compared"] == 2360, "established FNS pair count")
    close(identity["maximum_relative_deviation"], maximum, "established FNS identity")
    require(identity["alignment_identity"] is True and identity["pass"] is True, "FNS identity verdict")


def expected_log_change(row: dict[str, Any], variant: str) -> float:
    if VARIANT_IRDFF in row["calculations"]:
        counterpart_name = VARIANT_IRDFF if variant == VARIANT_PRODUCTION else VARIANT_PRODUCTION
    elif VARIANT_DECAY in row["calculations"]:
        counterpart_name = VARIANT_DECAY if variant == VARIANT_PRODUCTION else VARIANT_PRODUCTION
    else:
        return 0.0
    counterpart = row["calculations"].get(counterpart_name)
    calculation = row["calculations"][variant]
    if counterpart is None or counterpart["status"] != "scored":
        return 0.0
    return counterpart["signed_log_C_over_E"] - calculation["signed_log_C_over_E"]


def validate_ledger(
    result: dict[str, Any], ledger: dict[str, Any], mismatches: list[str]
) -> None:
    require(ledger["schema"] == "actinv-p17-cause-ledger-1", "cause-ledger schema")
    require(ledger["protocol_sha256"] == PROTOCOL_SHA256, "cause-ledger protocol")
    require(ledger["append_only"] is True, "cause-ledger append-only marker")
    require(ledger["taxonomy"] == TAXONOMY, "cause taxonomy changed")
    require(ledger["entries_sha256"] == canonical_sha256(ledger["entries"]), "ledger entry-set hash")
    entries_by_key = {}
    row_by_id = {row["row_id"]: row for row in result["rows"]}
    for sequence, entry in enumerate(ledger["entries"], 1):
        require(entry["sequence"] == sequence, f"ledger sequence {sequence}")
        unhashed = {key: value for key, value in entry.items() if key != "entry_sha256"}
        require(entry["entry_sha256"] == canonical_sha256(unhashed), f"ledger entry hash {sequence}")
        require(entry["mismatch_key"] not in entries_by_key, f"duplicate cause {entry['mismatch_key']}")
        require(entry["primary_cause"] in CAUSES, f"invalid primary cause {sequence}")
        require(set(entry["secondary_causes"]) <= CAUSES, f"invalid secondary cause {sequence}")
        require(entry["primary_cause"] not in entry["secondary_causes"], f"duplicate cause class {sequence}")
        require(entry["confidence"] in {"demonstrated", "bounded", "unresolved"}, f"confidence {sequence}")
        if entry["primary_cause"] == "unresolved":
            require(entry["confidence"] == "unresolved", f"unresolved confidence {sequence}")
        row_id, variant = entry["mismatch_key"].rsplit("::", 1)
        require(row_id in row_by_id and variant in row_by_id[row_id]["calculations"], f"cause target {sequence}")
        close(
            entry["signed_log_change"],
            expected_log_change(row_by_id[row_id], variant),
            f"cause signed log change {sequence}",
        )
        require(
            f"input-set:{row_by_id[row_id]['calculations'][variant]['input_set_id']}" in entry["evidence"],
            f"cause input evidence {sequence}",
        )
        entries_by_key[entry["mismatch_key"]] = entry
    require(sorted(entries_by_key) == mismatches, "missing, duplicate, or relabeled material mismatch cause")
    require(result["cause_ledger"]["entries"] == len(ledger["entries"]), "cause-ledger count binding")
    require(
        result["cause_ledger"]["entries_sha256"] == ledger["entries_sha256"],
        "cause-ledger hash binding",
    )


def validate_counts_and_checks(
    result: dict[str, Any], families: dict[str, list[dict[str, Any]]], mismatches: list[str]
) -> None:
    rows = result["rows"]
    expected_counts = {
        "total": len(rows),
        "irdff": sum(row["family"].startswith("irdff_") for row in rows),
        "fns": sum(row["family"].startswith("fns:") for row in rows),
        "scored_source_rows": sum(row["inclusion"]["status"] == "scored" for row in rows),
        "unscored_source_rows": sum(row["inclusion"]["status"] == "unscored" for row in rows),
    }
    require(result["row_counts"] == expected_counts, "top-level row counts changed")
    material = result["material_mismatches"]
    require(material["g1"] == material["g2"] == material["g3"] == 0, "prior-gate mismatch count")
    require(material["g4"] == len(mismatches), "G4 mismatch count")
    require(material["keys_sha256"] == canonical_sha256(mismatches), "G4 mismatch key hash")
    expected_checks = {
        "only_open_irdff_tables": True,
        "all_124_irdff_rows": expected_counts["irdff"] == 124,
        "all_132_fns_experiments": len([key for key in families if key.startswith("fns:")]) == 132,
        "all_fns_source_rows_preserved": True,
        "all_irdff_official_rows_folded": all(
            row["calculations"].get(VARIANT_IRDFF, {}).get("status") == "scored"
            for row in rows
            if row["family"].startswith("irdff_")
        ),
        "official_folds_match_published_within_5_percent": result["source_diagnostics"]["irdff"][
            "maximum_official_vs_published_relative"
        ]
        <= 0.05,
        "established_fns_alignment_and_production_identical": result["established_fns_identity"]["pass"],
        "prior_layer_controls_green": all(item["pass"] for item in result["prior_layer_evidence"].values()),
        "every_material_mismatch_has_one_cause": True,
        "input_sets_are_referenced": True,
    }
    require(result["checks"] == expected_checks, "G4 check verdicts changed")
    require(result["pass"] is True and all(result["checks"].values()), "G4 result is not green")


def validate(result: dict[str, Any], ledger: dict[str, Any]) -> dict[str, int]:
    require(set(result) == TOP_LEVEL_REQUIRED, "top-level diagnostic fields changed")
    require(result["schema"] == "actinv-p17-diagnostic-1", "diagnostic schema")
    require(result["protocol_sha256"] == PROTOCOL_SHA256, "diagnostic protocol hash")
    require(result["opening_source_commit"] == OPENING_SOURCE, "opening source changed")
    require(result["global_input_identities"] == EXPECTED_GLOBAL, "global input identity changed")
    require(result["control_source_sha256"] == sha256(ROOT / "controls" / "g4_p17_diagnostics.py"), "generator hash")
    require(result["scoring_source_sha256"] == sha256(ROOT / "controls" / "p17_scoring.py"), "scorer hash")
    require(result["irdff_source_sha256"] == sha256(ROOT / "controls" / "p17_irdff.py"), "IRDFF helper hash")
    require(result["checker_source_sha256"] == sha256(Path(__file__)), "independent checker hash")
    require(
        result["evidence_sha256"]
        == canonical_sha256({key: value for key, value in result.items() if key != "evidence_sha256"}),
        "diagnostic evidence seal changed",
    )
    validate_schemas(result)
    validate_input_sets(result)
    families, mismatches = validate_rows(result)
    validate_family_metrics(result, families)
    validate_controlled_substitutions(result)
    validate_prior_evidence(result)
    validate_source_diagnostics(result, families)
    validate_established_identity(result)
    validate_ledger(result, ledger, mismatches)
    validate_counts_and_checks(result, families, mismatches)
    return {"rows": len(result["rows"]), "families": len(families), "mismatches": len(mismatches)}


def reseal(result: dict[str, Any]) -> None:
    result["evidence_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "evidence_sha256"}
    )


def mutation_plants(result: dict[str, Any], ledger: dict[str, Any]) -> dict[str, bool]:
    plants: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    value = deepcopy(result)
    value["rows"][0]["experimental"]["value"] *= 1.01
    reseal(value)
    plants["value"] = (value, deepcopy(ledger))

    unit = deepcopy(result)
    unit["rows"][0]["unit"] = "barn"
    reseal(unit)
    plants["unit"] = (unit, deepcopy(ledger))

    identity = deepcopy(result)
    identity["rows"][0]["row_id"] = "irdff:t18:r999"
    reseal(identity)
    plants["row_identity"] = (identity, deepcopy(ledger))

    inclusion = deepcopy(result)
    inclusion["rows"][0]["inclusion"]["reason"] = "nonpositive_experimental_value"
    reseal(inclusion)
    plants["inclusion_reason"] = (inclusion, deepcopy(ledger))

    family = deepcopy(result)
    family["rows"][0]["family"] = "irdff_table_20"
    reseal(family)
    plants["family_assignment"] = (family, deepcopy(ledger))

    identity_hash = deepcopy(result)
    identity_hash["global_input_identities"]["library"] = "0" * 64
    reseal(identity_hash)
    plants["hash"] = (identity_hash, deepcopy(ledger))

    metric = deepcopy(result)
    metric["family_metrics"]["irdff_table_18"][VARIANT_PRODUCTION][
        "geometric_mean_C_over_E"
    ] *= 1.01
    reseal(metric)
    plants["metric"] = (metric, deepcopy(ledger))

    cause_result = deepcopy(result)
    cause_ledger = deepcopy(ledger)
    cause_ledger["entries"].pop()
    cause_ledger["entries_sha256"] = canonical_sha256(cause_ledger["entries"])
    cause_result["cause_ledger"]["entries"] = len(cause_ledger["entries"])
    cause_result["cause_ledger"]["entries_sha256"] = cause_ledger["entries_sha256"]
    reseal(cause_result)
    plants["cause_removal"] = (cause_result, cause_ledger)

    caught = {}
    for name, (candidate_result, candidate_ledger) in plants.items():
        try:
            validate(candidate_result, candidate_ledger)
        except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError):
            caught[name] = True
        else:
            caught[name] = False
    return caught


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true", help="validate without rewriting check evidence")
    arguments = parser.parse_args()
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    summary = validate(result, ledger)
    plants = mutation_plants(result, ledger)
    checks = {
        "independent_validation": True,
        "all_eight_mutation_plants_caught": len(plants) == 8 and all(plants.values()),
    }
    output = {
        "schema": "actinv-p17-g4-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "diagnostic_sha256": sha256(RESULT),
        "cause_ledger_sha256": sha256(LEDGER),
        "checker_source_sha256": sha256(Path(__file__)),
        "schema_identities": {
            "diagnostic": sha256(DIAGNOSTIC_SCHEMA),
            "cause_ledger": sha256(LEDGER_SCHEMA),
        },
        "summary": summary,
        "mutation_plants": plants,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if not arguments.no_write:
        CHECK_RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
