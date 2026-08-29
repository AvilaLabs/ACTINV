#!/usr/bin/env python3
"""Independent compact-evidence checker for the expected P17 G5 failure.

This module imports neither the held-out parser, the generator, nor the frozen
P17 scoring implementation.  It independently repeats row conservation,
source hashing, EOI arithmetic, C/E metrics, mismatch coverage, ledger hashes,
and failure-condition logic from committed compact evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g5_p17_heldout.json"
LEDGER = ROOT / "results" / "p17_cause_ledger_g5.json"
CHECK_RESULT = ROOT / "results" / "g5_p17_check.json"
PREDECESSOR = ROOT / "results" / "p17_cause_ledger.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
AMENDMENT = ROOT / "protocols" / "ACTINV-P17_AMENDMENT_1.md"

PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
AMENDMENT_SHA256 = "e78c84d9f80c9bc6b7f0e79050206991515d283f43deeabd32f42c325f53581e"
UNSEAL_COMMIT = "b7039e2bff4346d9c39c283fcd2aabd768a8e628"

VARIANT_PRODUCTION = "actinv_tendl2025_post_failure_diagnostic"
VARIANT_CURRENT_IRDFF = "irdff_ii_current_archive_post_failure_diagnostic"
VARIANT_PUBLISHED = "irdff_ii_published_context"
VARIANTS = {VARIANT_PRODUCTION, VARIANT_CURRENT_IRDFF, VARIANT_PUBLISHED}

FAMILY_H1 = "H1_SPR_III_table_23"
FAMILY_H2 = "H2_ACRR_table_25"
FAMILY_H3 = "H3_Maxwellian_table_36"

EXPECTED_LABELS = {
    FAMILY_H1: [
        "Sc45g-Cd", "Mn55g-Cd", "Au197g-bare", "Au197g-Cd", "Sc45g-Cdtk/B4C",
        "Na23g-bare", "Fe58g-Cd", "Na23g-Cdna", "Mn55g-Cdtk/B4C", "W186g-bare",
        "Cu63g-Cd", "Na23g-Cdtk/B4C", "U235f-Cdtk", "U235f-Cdtk/B4C", "Pu239f-Cdtk",
        "Pu239f-Cdtk/B4C", "Np237f-Cdtk", "Np237f-Cdtk/B4C", "In115nm-bare", "U238f-Cd",
        "U238f-Cdtk/B4C", "Ti47p-Cd", "S32p-bare", "Ni58p-Cd", "Zn64p-Cd", "Fe54p-Cd",
        "Al27p-Cd", "Co59p-bare", "Ti46p-Cd", "Cu63a-bare", "Fe56p-Cd", "Ti48p-Cd",
        "Mg24p-Cd", "Al27a-Cd", "Nb932-bare", "Co592-bare", "Mn552-bare",
        "Mn552-Cdtk/B4C", "Zr902-Cd", "Ni582-bare",
    ],
    FAMILY_H2: [
        "Sc45g-bare", "Na23g-bare", "Mn55g-bare", "Fe58g-bare", "Co59g-bare", "Sc45g-Cd",
        "Au197g-bare", "Au197g-Cd", "Ag109g-bare", "Ag109g-Cd", "Na23g-Cd", "W186g-bare",
        "Co59g-Cd", "Mn55g-Cd", "Fe58g-Cd", "rmleu-Cdtk/B4C", "rmlpu-Cdtk/B4C",
        "Np237f-Cdtk/B4C", "rmldu-Cdtk/B4C", "Ti47p-bare", "S32p-bare", "Ni58p-bare",
        "Zn64p-bare", "Fe54p-bare", "Co59p-Cd", "Ti46p-bare", "Ni60p-bare", "Fe56p-bare",
        "Ti48p-bare", "Mg24p-bare", "Al27a-bare", "Nb932-bare", "Zr902-bare",
    ],
    FAMILY_H3: [
        "Mn55g", "Cu63g", "Au197g", "Co59g", "U238g", "Nb93g", "La139g", "Au197g",
        "Au197g", "Ta181g", "Sc45g", "Au197g", "In113gm", "In115gm", "In113gg", "Na23g",
        "In113g", "Fe58g", "W186g", "Ag109g", "U238g",
    ],
}
EXPECTED_INPUTS = {
    "pdf": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    "irdff_group_archive": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
    "irdff_spectrum_archive": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
    "irdff_pointwise_archive": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
    "irdff_decay_archive": "397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7",
    "production_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "production_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(left: Any, right: Any, message: str, *, relative: float = 2.0e-13) -> None:
    if left is None or right is None:
        require(left is right, f"{message}: {left!r} != {right!r}")
        return
    require(
        math.isclose(float(left), float(right), rel_tol=relative, abs_tol=2.0e-15),
        f"{message}: {left!r} != {right!r}",
    )


def percentile_linear(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metrics(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    ratios = []
    reasons: Counter[str] = Counter()
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            reasons[row["inclusion"]["reason"]] += 1
            continue
        calculation = row["calculations"][variant]
        if calculation["status"] != "scored":
            reasons[calculation["reason"]] += 1
            continue
        ratios.append(float(calculation["ratio_C_over_E"]))
    if ratios:
        logs = [math.log(value) for value in ratios]
        absolute = [abs(value) for value in logs]
        summary = {
            "geometric_mean_C_over_E": math.exp(math.fsum(logs) / len(logs)),
            "median_abs_log_C_over_E": percentile_linear(absolute, 0.5),
            "p90_abs_log_C_over_E": percentile_linear(absolute, 0.9),
            "maximum_abs_log_C_over_E": max(absolute),
            "fraction_within_10_percent": sum(0.9 <= value <= 1.1 for value in ratios) / len(ratios),
            "fraction_within_20_percent": sum(0.8 <= value <= 1.2 for value in ratios) / len(ratios),
            "fraction_within_30_percent": sum((1.0 / 1.3) <= value <= 1.3 for value in ratios) / len(ratios),
        }
    else:
        summary = {
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
        **summary,
    }


def compare_metric(actual: dict[str, Any], expected: dict[str, Any], message: str) -> None:
    require(set(actual) == set(expected), f"{message}: keys")
    require(actual["scored_rows"] == expected["scored_rows"], f"{message}: scored")
    require(actual["unscored_rows"] == expected["unscored_rows"], f"{message}: unscored")
    require(actual["unscored_reasons"] == expected["unscored_reasons"], f"{message}: reasons")
    for key in set(actual) - {"scored_rows", "unscored_rows", "unscored_reasons"}:
        close(actual[key], expected[key], f"{message}: {key}")


def validate_input_sets(result: dict[str, Any]) -> None:
    require(result["input_identities"] == EXPECTED_INPUTS, "external input identities")
    require(set(result["input_sets"]) == {"production", "current_irdff", "published"}, "input sets")
    for identifier, value in result["input_sets"].items():
        hashes = value["input_hashes"]
        require(hashes and all(len(item) == 64 for item in hashes.values()), f"{identifier}: hashes")
        identical = set(value["identical_input_roles"])
        changed = set(value["changed_input_roles"])
        require(not identical & changed and identical | changed == set(hashes), f"{identifier}: role partition")
        require(hashes["protocol"] == PROTOCOL_SHA256, f"{identifier}: protocol")
        require(hashes["amendment"] == AMENDMENT_SHA256, f"{identifier}: amendment")


def validate_rows(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows = result["rows"]
    require(len(rows) == 94, "held-out source-row count")
    require(len({row["row_id"] for row in rows}) == 94, "row ids unique")
    require(len({row["source_id"] for row in rows}) == 94, "source ids unique")
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        require(
            set(row)
            == {
                "row_id", "family", "source_id", "source_record", "source_record_sha256",
                "observable", "unit", "experimental", "inclusion", "calculations", "heldout_evidence",
            },
            f"{row['row_id']}: row shape",
        )
        require(row["source_record_sha256"] == canonical_sha256(row["source_record"]), f"{row['row_id']}: source hash")
        require(set(row["calculations"]) == VARIANTS, f"{row['row_id']}: variants")
        require(row["unit"] in {"dimensionless", "mb"}, f"{row['row_id']}: unit")
        experimental = float(row["experimental"]["value"])
        require(math.isfinite(experimental) and experimental > 0.0, f"{row['row_id']}: experimental")
        for variant, calculation in row["calculations"].items():
            require(
                set(calculation)
                == {
                    "status", "reason", "value", "ratio_C_over_E", "signed_log_C_over_E",
                    "material_mismatch", "input_set_id", "interpretation",
                },
                f"{row['row_id']}/{variant}: calculation shape",
            )
            if calculation["status"] == "scored":
                value = float(calculation["value"])
                ratio = value / experimental
                close(calculation["ratio_C_over_E"], ratio, f"{row['row_id']}/{variant}: ratio")
                close(calculation["signed_log_C_over_E"], math.log(ratio), f"{row['row_id']}/{variant}: log")
                require(
                    calculation["material_mismatch"]
                    == (not ((1.0 / 1.3) <= ratio <= 1.3)),
                    f"{row['row_id']}/{variant}: mismatch",
                )
            else:
                require(calculation["ratio_C_over_E"] is None, f"{row['row_id']}/{variant}: unscored ratio")
                require(calculation["signed_log_C_over_E"] is None, f"{row['row_id']}/{variant}: unscored log")
                require(not calculation["material_mismatch"], f"{row['row_id']}/{variant}: unscored mismatch")
        families[row["family"]].append(row)
    require(set(families) == set(EXPECTED_LABELS), "family identities")
    for family, expected in EXPECTED_LABELS.items():
        ordered = sorted(families[family], key=lambda row: int(row["source_record"]["table_row"]))
        require([row["source_record"]["label"] for row in ordered] == expected, f"{family}: labels")
        families[family] = ordered
    return families


def validate_eoi(families: dict[str, list[dict[str, Any]]], result: dict[str, Any]) -> None:
    uniform_relatives = []
    pulse_relatives = []
    for family in (FAMILY_H1, FAMILY_H2):
        rows = families[family]
        monitor_label = "Ni58p-Cd" if family == FAMILY_H1 else "Ni58p-bare"
        monitor = next(row for row in rows if row["source_record"]["label"] == monitor_label)
        monitor_source = monitor["source_record"]
        for row in rows:
            source = row["source_record"]
            frozen = row["heldout_evidence"]["frozen_uniform_960s_reconstruction"]
            pulse = row["heldout_evidence"]["post_failure_pulse_reconstruction"]
            published_inferred = float(source["published_spectral_index"]) / float(source["published_SI_C_over_E"])
            monitor_lambda = math.log(2.0) / float(frozen["monitor_half_life_s"])
            monitor_saturation = -math.expm1(-monitor_lambda * 960.0)
            eoi_ratio = float(source["measured_EOI_per_atom"]) / float(monitor_source["measured_EOI_per_atom"])
            if frozen["observable_kind"] == "fissions_per_atom":
                uniform_value = eoi_ratio * monitor_saturation / 960.0
            else:
                product_lambda = math.log(2.0) / float(frozen["product_half_life_s"])
                product_saturation = -math.expm1(-product_lambda * 960.0)
                uniform_value = eoi_ratio * monitor_saturation / product_saturation
            close(frozen["value"], uniform_value, f"{row['row_id']}: uniform EOI")
            close(frozen["published_inferred_value"], published_inferred, f"{row['row_id']}: frozen inferred")
            uniform_relative = abs(uniform_value - published_inferred) / published_inferred
            close(frozen["relative_to_published_inferred"], uniform_relative, f"{row['row_id']}: uniform relative")
            uniform_relatives.append(uniform_relative)

            pulse_monitor_lambda = math.log(2.0) / float(pulse["monitor_half_life_s"])
            if pulse["product_half_life_s"] is None:
                pulse_value = eoi_ratio * pulse_monitor_lambda
            else:
                pulse_product_lambda = math.log(2.0) / float(pulse["product_half_life_s"])
                pulse_value = eoi_ratio * pulse_monitor_lambda / pulse_product_lambda
            close(pulse["value"], pulse_value, f"{row['row_id']}: pulse EOI")
            close(pulse["published_inferred_value"], published_inferred, f"{row['row_id']}: pulse inferred")
            pulse_relative = abs(pulse_value - published_inferred) / published_inferred
            close(pulse["relative_to_published_inferred"], pulse_relative, f"{row['row_id']}: pulse relative")
            pulse_relatives.append(pulse_relative)
    frozen = result["frozen_gate_evidence"]
    close(frozen["uniform_960s_maximum_reconstruction_relative"], max(uniform_relatives), "uniform maximum")
    close(frozen["post_failure_pulse_maximum_reconstruction_relative"], max(pulse_relatives), "pulse maximum")
    require(max(uniform_relatives) > 0.005, "uniform failure must remain demonstrated")
    require(max(pulse_relatives) <= 0.04, "pulse diagnostic bound")


def validate_exclusions(families: dict[str, list[dict[str, Any]]]) -> None:
    require(
        all(row["inclusion"] == {"status": "unscored", "reason": "unsupported_self_shielding"} for row in families[FAMILY_H1]),
        "H1 cover exclusions",
    )
    expected_h2_scored = {
        "Ti47p-bare", "S32p-bare", "Ni58p-bare", "Zn64p-bare", "Fe54p-bare", "Ti46p-bare",
        "Ni60p-bare", "Fe56p-bare", "Ti48p-bare", "Mg24p-bare", "Al27a-bare", "Nb932-bare", "Zr902-bare",
    }
    actual_h2_scored = {
        row["source_record"]["label"] for row in families[FAMILY_H2] if row["inclusion"]["status"] == "scored"
    }
    require(actual_h2_scored == expected_h2_scored, "H2 supported observable set")
    require(all(row["inclusion"]["status"] == "scored" for row in families[FAMILY_H3]), "H3 inclusion")


def validate_h3(families: dict[str, list[dict[str, Any]]], result: dict[str, Any]) -> None:
    relatives = []
    inconsistent = []
    for row in families[FAMILY_H3]:
        evidence = row["heldout_evidence"]
        group = float(evidence["current_irdff_groupwise_mb"])
        point = float(evidence["current_irdff_pointwise_mb"])
        relative = abs(group - point) / point
        close(evidence["groupwise_vs_pointwise_relative"], relative, f"{row['row_id']}: fold differential")
        close(row["calculations"][VARIANT_CURRENT_IRDFF]["value"], point, f"{row['row_id']}: point calculation")
        source = row["source_record"]
        literal_ratio = float(source["published_calculated_mb"]) / float(source["measured_mb"])
        close(evidence["published_literal_ratio"], literal_ratio, f"{row['row_id']}: literal ratio")
        close(
            evidence["published_ratio_minus_printed_C_over_E"],
            literal_ratio - float(source["published_C_over_E"]),
            f"{row['row_id']}: source discrepancy",
        )
        if evidence["source_internal_inconsistency"]:
            inconsistent.append(row)
        relatives.append(relative)
    require(len(inconsistent) == 1 and inconsistent[0]["source_record"]["table_row"] == 21, "Table 36 anomaly identity")
    anomaly = inconsistent[0]["source_record"]
    require(anomaly["measured_mb"] == 108.0 and anomaly["published_calculated_mb"] == 389.9, "Table 36 literals")
    require(anomaly["published_C_over_E"] == 1.011, "Table 36 printed C/E")
    close(result["h3_fold_diagnostics"]["maximum_groupwise_vs_pointwise_relative"], max(relatives), "H3 fold max")
    require(max(relatives) <= 0.005, "H3 pointwise differential threshold")


def validate_metrics(families: dict[str, list[dict[str, Any]]], result: dict[str, Any]) -> None:
    require(set(result["family_metrics"]) == set(families), "metric families")
    for family, rows in families.items():
        require(set(result["family_metrics"][family]) == VARIANTS, f"{family}: metric variants")
        for variant in VARIANTS:
            compare_metric(
                result["family_metrics"][family][variant],
                metrics(rows, variant),
                f"{family}/{variant}",
            )


def validate_failure(result: dict[str, Any]) -> None:
    evidence = result["frozen_gate_evidence"]
    counterfactuals = evidence["bare_capture_counterfactuals"]
    for row in counterfactuals:
        expected = abs(
            float(row["unshielded_current_archive_spectral_index"])
            - float(row["published_spectral_index"])
        ) / float(row["published_spectral_index"])
        close(row["relative"], expected, f"{row['label']}: bare counterfactual")
    bare_max = max(float(row["relative"]) for row in counterfactuals)
    close(evidence["bare_capture_unshielded_maximum_relative_to_published"], bare_max, "bare maximum")
    expected_failed = {
        "amendment_1_uniform_960s_EOI_reproduces_observable": evidence[
            "uniform_960s_maximum_reconstruction_relative"
        ]
        <= 0.005,
        "amendment_1_mapping_covers_publication_Ag109gm_alias": False,
        "amendment_1_bare_means_unshielded_for_capture_foils": bare_max <= 0.05,
        "no_second_post_unseal_repair_needed": False,
    }
    require(evidence["failed_conditions"] == expected_failed, "frozen failure conditions")
    require(not any(expected_failed.values()), "all four frozen conditions must fail")
    require(result["verdict"] == "P17-FAIL" and result["pass"] is False, "P17 verdict")


def validate_ledger(result: dict[str, Any], ledger: dict[str, Any]) -> None:
    require(result["cause_ledger_segment"]["sha256"] == sha256(LEDGER), "ledger file identity")
    require(ledger["predecessor"] == {"path": "results/p17_cause_ledger.json", "sha256": sha256(PREDECESSOR)}, "ledger predecessor")
    require(ledger["append_only"] is True, "append-only ledger")
    require(ledger["entries_sha256"] == canonical_sha256(ledger["entries"]), "ledger entries hash")
    require(result["cause_ledger_segment"]["entries_sha256"] == ledger["entries_sha256"], "result ledger binding")
    for sequence, entry in enumerate(ledger["entries"], 1):
        require(entry["sequence"] == sequence, "ledger sequence")
        unhashed = {key: value for key, value in entry.items() if key != "entry_sha256"}
        require(entry["entry_sha256"] == canonical_sha256(unhashed), f"ledger entry {sequence} hash")
    rows = result["rows"]
    mismatches = sorted(
        f"{row['row_id']}::{VARIANT_PRODUCTION}"
        for row in rows
        if row["calculations"][VARIANT_PRODUCTION]["status"] == "scored"
        and row["calculations"][VARIANT_PRODUCTION]["material_mismatch"]
    )
    require(result["post_failure_material_mismatch_keys"] == mismatches, "result mismatch keys")
    require(sorted(entry["mismatch_key"] for entry in ledger["entries"]) == mismatches, "ledger mismatch coverage")


def validate(result: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    require(result["schema"] == "actinv-p17-heldout-failure-1", "result schema")
    require(result["protocol_sha256"] == PROTOCOL_SHA256 == sha256(PROTOCOL), "protocol identity")
    require(result["amendment_sha256"] == AMENDMENT_SHA256 == sha256(AMENDMENT), "amendment identity")
    require(result["unseal_commit"] == UNSEAL_COMMIT, "unseal identity")
    require(result["control_source_sha256"] == sha256(ROOT / "controls" / "g5_p17_heldout.py"), "generator identity")
    require(result["checker_source_sha256"] == sha256(Path(__file__)), "checker identity")
    require(result["helper_source_sha256"] == sha256(ROOT / "controls" / "p17_heldout.py"), "helper identity")
    require(result["scoring_source_sha256"] == sha256(ROOT / "controls" / "p17_scoring.py"), "scoring identity")
    require(
        result["evidence_sha256"]
        == canonical_sha256({key: value for key, value in result.items() if key != "evidence_sha256"}),
        "result evidence hash",
    )
    validate_input_sets(result)
    families = validate_rows(result)
    validate_eoi(families, result)
    validate_exclusions(families)
    validate_h3(families, result)
    validate_metrics(families, result)
    validate_failure(result)
    validate_ledger(result, ledger)
    expected_checks = {
        "all_heldout_rows_preserved": True,
        "post_failure_pulse_limit_reconstructs_printed_SI_within_4_percent": True,
        "current_irdff_groupwise_matches_pointwise_within_0_5_percent": True,
        "literal_table36_source_anomaly_preserved": True,
        "post_failure_mismatches_ledgered_exactly_once": True,
    }
    require(result["post_failure_checks"] == expected_checks, "post-failure checks")
    require(result["post_failure_diagnostics_complete"] is True, "post-failure completeness")
    return {
        "rows": len(result["rows"]),
        "families": {family: len(rows) for family, rows in families.items()},
        "production_mismatches": len(result["post_failure_material_mismatch_keys"]),
        "verdict": result["verdict"],
    }


def mutations_pass(result: dict[str, Any], ledger: dict[str, Any]) -> dict[str, bool]:
    plants: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]] = [
        ("value", lambda value, _ledger: value["rows"][73]["calculations"][VARIANT_PRODUCTION].__setitem__("value", 1.0)),
        ("unit", lambda value, _ledger: value["rows"][0].__setitem__("unit", "barn")),
        ("row_identity", lambda value, _ledger: value["rows"][0].__setitem__("source_id", "changed")),
        ("inclusion", lambda value, _ledger: value["rows"][0].__setitem__("inclusion", {"status": "scored", "reason": "scored"})),
        ("family", lambda value, _ledger: value["rows"][0].__setitem__("family", FAMILY_H2)),
        ("hash", lambda value, _ledger: value["input_identities"].__setitem__("pdf", "0" * 64)),
        ("metric", lambda value, _ledger: value["family_metrics"][FAMILY_H3][VARIANT_PRODUCTION].__setitem__("geometric_mean_C_over_E", 1.0)),
        ("cause_removal", lambda _value, value_ledger: value_ledger["entries"].pop()),
        ("failure_flip", lambda value, _ledger: value["frozen_gate_evidence"]["failed_conditions"].__setitem__("no_second_post_unseal_repair_needed", True)),
        ("source_anomaly", lambda value, _ledger: value["rows"][-1]["source_record"].__setitem__("published_calculated_mb", 109.2)),
    ]
    output = {}
    for name, plant in plants:
        changed_result = deepcopy(result)
        changed_ledger = deepcopy(ledger)
        plant(changed_result, changed_ledger)
        try:
            validate(changed_result, changed_ledger)
        except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError):
            output[name] = True
        else:
            output[name] = False
    return output


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    summary = validate(result, ledger)
    plants = mutations_pass(result, ledger)
    output = {
        "schema": "actinv-p17-heldout-check-1",
        "result_sha256": sha256(RESULT),
        "ledger_sha256": sha256(LEDGER),
        "checker_source_sha256": sha256(Path(__file__)),
        "summary": summary,
        "mutation_plants": plants,
        "pass": all(plants.values()),
    }
    if not arguments.no_write:
        write_json(CHECK_RESULT, output)
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
