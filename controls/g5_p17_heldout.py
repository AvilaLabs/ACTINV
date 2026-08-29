#!/usr/bin/env python3
"""P17 G5 held-out score, frozen-protocol failure, and P18 diagnostic evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from p17_heldout import (
    FIELD_SPECTRUM_MAT,
    PRODUCTION_INDEX,
    PRODUCTION_LIBRARY,
    ProductionLibrary,
    checked_inputs,
    decay_by_identity,
    derived_si_rows,
    extracted_inputs,
    fold_group_response,
    parse_maxwellian_table,
    pointwise_response_macs,
    post_failure_publication_mapping,
    production_macs,
    production_spectrum_response,
    selected_endf_catalog,
    sha256,
    spectrum_catalog,
    split_reaction,
    validate_support_tables,
)
from p17_scoring import (
    all_family_metrics,
    canonical_sha256,
    cause_entry,
    make_input_set,
    make_row,
    mismatch_keys,
    score_calculation,
    unscored_calculation,
)


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g5_p17_heldout.json"
LEDGER_SEGMENT = ROOT / "results" / "p17_cause_ledger_g5.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P17_PROTOCOL.md"
AMENDMENT = ROOT / "protocols" / "ACTINV-P17_AMENDMENT_1.md"
PREDECESSOR_LEDGER = ROOT / "results" / "p17_cause_ledger.json"

SCHEMA = "actinv-p17-heldout-failure-1"
LEDGER_SCHEMA = "actinv-p17-cause-ledger-segment-1"
PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
AMENDMENT_SHA256 = "e78c84d9f80c9bc6b7f0e79050206991515d283f43deeabd32f42c325f53581e"
UNSEAL_COMMIT = "b7039e2bff4346d9c39c283fcd2aabd768a8e628"

VARIANT_PRODUCTION = "actinv_tendl2025_post_failure_diagnostic"
VARIANT_CURRENT_IRDFF = "irdff_ii_current_archive_post_failure_diagnostic"
VARIANT_PUBLISHED = "irdff_ii_published_context"
VARIANTS = (VARIANT_PRODUCTION, VARIANT_CURRENT_IRDFF, VARIANT_PUBLISHED)

FAMILY_H1 = "H1_SPR_III_table_23"
FAMILY_H2 = "H2_ACRR_table_25"
FAMILY_H3 = "H3_Maxwellian_table_36"


def rendered_json(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=1, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(rendered_json(value))


def raw_si_source(source: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "table",
        "table_row",
        "label",
        "reaction_label",
        "cover",
        "E50_MeV",
        "measured_EOI_per_atom",
        "experimental_uncertainty_percent",
        "published_SACS_uncertainty_percent",
        "published_spectral_index",
        "published_spectral_index_uncertainty_percent",
        "published_SI_C_over_E",
        "published_SI_C_over_E_uncertainty_percent",
        "source_line",
    }
    return {key: source[key] for key in sorted(keys)}


def raw_h3_source(source: dict[str, Any]) -> dict[str, Any]:
    return dict(sorted(source.items()))


def input_sets(identities: dict[str, str]) -> dict[str, dict[str, Any]]:
    common = {
        "primary_reference_pdf": identities["pdf"],
        "heldout_helper": sha256(ROOT / "controls" / "p17_heldout.py"),
        "protocol": PROTOCOL_SHA256,
        "amendment": AMENDMENT_SHA256,
    }
    return {
        "production": make_input_set(
            input_hashes={
                **common,
                "activation_library": identities["production_library"],
                "activation_index": identities["production_index"],
                "benchmark_spectra": identities["irdff_spectrum_archive"],
                "decay_data": identities["irdff_decay_archive"],
            },
            identical_input_roles=common,
            changed_input_roles={
                "activation_library",
                "activation_index",
                "benchmark_spectra",
                "decay_data",
            },
        ),
        "current_irdff": make_input_set(
            input_hashes={
                **common,
                "groupwise_evaluation": identities["irdff_group_archive"],
                "pointwise_evaluation": identities["irdff_pointwise_archive"],
                "benchmark_spectra": identities["irdff_spectrum_archive"],
                "decay_data": identities["irdff_decay_archive"],
            },
            identical_input_roles=common,
            changed_input_roles={
                "groupwise_evaluation",
                "pointwise_evaluation",
                "benchmark_spectra",
                "decay_data",
            },
        ),
        "published": make_input_set(
            input_hashes=common,
            identical_input_roles=common,
            changed_input_roles=set(),
        ),
    }


def excluded_calculations(
    production_value: float | None = None,
    current_irdff_value: float | None = None,
    published_value: float | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        VARIANT_PRODUCTION: unscored_calculation(
            input_set_id="production",
            reason="not_applicable",
            interpretation="Post-failure diagnostic only; this source row is outside the supported observable set.",
            value=production_value,
        ),
        VARIANT_CURRENT_IRDFF: unscored_calculation(
            input_set_id="current_irdff",
            reason="not_applicable",
            interpretation="Current-archive counterfactual is preserved but not scored for an unsupported observable.",
            value=current_irdff_value,
        ),
        VARIANT_PUBLISHED: unscored_calculation(
            input_set_id="published",
            reason="different_data_context",
            interpretation="Published IRDFF-II validation context; not an ACTINV blind prediction.",
            value=published_value,
        ),
    }


def build_si_rows(
    sources: list[dict[str, Any]],
    group_mf3: dict[tuple[int, int], tuple[Any, ...]],
    group_mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
    spectra: dict[int, tuple[Any, ...]],
    production: ProductionLibrary,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output = []
    bare_capture_counterfactuals = []
    for table in (23, 25):
        table_sources = [source for source in sources if source["table"] == table]
        monitor_label = "Ni58p-Cd" if table == 23 else "Ni58p-bare"
        monitor = next(source for source in table_sources if source["label"] == monitor_label)
        spectrum = spectra[FIELD_SPECTRUM_MAT[table]]
        monitor_mapping = monitor["post_failure_mapping"]
        official_monitor, official_monitor_keys = fold_group_response(
            monitor_mapping, group_mf3, group_mf10, spectrum
        )
        production_monitor, production_monitor_reason, production_monitor_evidence = (
            production_spectrum_response(production, monitor_mapping, spectrum)
        )
        for source in table_sources:
            mapping = source["post_failure_mapping"]
            pulse = source["post_failure_pulse_spectral_index"]
            published = float(source["published_spectral_index"])
            production_si = None
            official_si = None
            production_evidence: list[dict[str, Any]] = []
            official_keys: list[list[int]] = []
            production_reason = "not_applicable"
            if source["cover"] == "bare" and table == 25:
                official_value, official_keys = fold_group_response(
                    mapping, group_mf3, group_mf10, spectrum
                )
                production_value, production_reason, production_evidence = (
                    production_spectrum_response(production, mapping, spectrum)
                )
                if official_value is not None and official_monitor is not None:
                    official_si = official_value / official_monitor
                if production_value is not None and production_monitor is not None:
                    production_si = production_value / production_monitor

            if mapping["kind"] == "composite_fission_foil":
                suffix = "f"
            else:
                _element, _mass, suffix = split_reaction(source["reaction_label"])
            monitor_identity = source["label"] == monitor_label
            unsupported_cover = table == 23 or source["cover"] != "bare"
            unsupported_bare_capture = table == 25 and source["cover"] == "bare" and suffix == "g"
            if unsupported_bare_capture and official_si is not None:
                bare_capture_counterfactuals.append(
                    {
                        "row_id": f"p17-h2-t25-r{source['table_row']:03d}",
                        "label": source["label"],
                        "published_spectral_index": published,
                        "unshielded_current_archive_spectral_index": official_si,
                        "relative": abs(official_si - published) / published,
                    }
                )

            if unsupported_cover or unsupported_bare_capture:
                inclusion_status = "unscored"
                inclusion_reason = "unsupported_self_shielding"
                calculations = excluded_calculations(production_si, official_si, published)
            elif monitor_identity:
                inclusion_status = "scored"
                inclusion_reason = "scored"
                calculations = excluded_calculations(production_si, official_si, published)
            else:
                inclusion_status = "scored"
                inclusion_reason = "scored"
                calculations = {
                    VARIANT_PRODUCTION: score_calculation(
                        pulse["value"],
                        production_si,
                        status="scored" if production_si is not None else "unscored",
                        reason="scored" if production_si is not None else production_reason,
                        input_set_id="production",
                        interpretation="Post-P17-failure ACTINV/TENDL-2025 diagnostic; not a repaired G5 score.",
                    ),
                    VARIANT_CURRENT_IRDFF: score_calculation(
                        pulse["value"],
                        official_si,
                        status="scored" if official_si is not None else "unscored",
                        reason="scored" if official_si is not None else "variant_reaction_unavailable",
                        input_set_id="current_irdff",
                        interpretation="Current IRDFF-II groupwise archive folded independently after P17 failed.",
                    ),
                    VARIANT_PUBLISHED: score_calculation(
                        pulse["value"],
                        published,
                        input_set_id="published",
                        interpretation="Published IRDFF-II context reconstructed from printed EOI values.",
                    ),
                }

            family = FAMILY_H1 if table == 23 else FAMILY_H2
            row_id = f"p17-h{1 if table == 23 else 2}-t{table}-r{source['table_row']:03d}-{source['label']}"
            uncertainty = math.hypot(
                float(source["experimental_uncertainty_percent"]),
                float(monitor["experimental_uncertainty_percent"]),
            )
            row = make_row(
                row_id=row_id,
                family=family,
                source_id=f"IRDFF-II:Table-{table}:row-{source['table_row']:03d}",
                source_record=raw_si_source(source),
                observable="spectral_index_relative_to_Ni58p",
                unit="dimensionless",
                experimental_value=float(pulse["value"]),
                experimental_uncertainty=uncertainty,
                experimental_uncertainty_unit="percent; RSS of printed EOI activity uncertainties",
                inclusion_status=inclusion_status,
                inclusion_reason=inclusion_reason,
                calculations=calculations,
            )
            row["heldout_evidence"] = {
                "frozen_amendment_1_mapping": source["mapping"],
                "post_failure_mapping": mapping,
                "frozen_uniform_960s_reconstruction": source["experimental_spectral_index"],
                "post_failure_pulse_reconstruction": pulse,
                "production_eligibility_before_failure": source["production_eligibility"],
                "production_response_reason": production_reason,
                "production_response_evidence": production_evidence,
                "production_monitor_reason": production_monitor_reason,
                "production_monitor_evidence": production_monitor_evidence,
                "current_irdff_group_keys": official_keys,
                "current_irdff_monitor_group_keys": official_monitor_keys,
                "post_failure_exclusion_detail": (
                    "covered numerator or monitor requires unsupplied transport"
                    if unsupported_cover
                    else (
                        "finite bare capture foil requires unsupplied self-shielding correction"
                        if unsupported_bare_capture
                        else ("monitor identity is not predictive accuracy" if monitor_identity else None)
                    )
                ),
            }
            output.append(row)
    return output, bare_capture_counterfactuals


def build_h3_rows(
    sources: list[dict[str, Any]],
    group_mf3: dict[tuple[int, int], tuple[Any, ...]],
    group_mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
    point_mf3: dict[tuple[int, int], tuple[Any, ...]],
    point_mf10: dict[tuple[int, int, int, int], tuple[Any, ...]],
    production: ProductionLibrary,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = []
    fold_relatives = []
    for source in sources:
        mapping = post_failure_publication_mapping(source["label"])
        # Wallner's U-238 measurements are laboratory SACS under a unit-area
        # Maxwellian.  All other rows use the stellar 2/sqrt(pi) convention.
        stellar = source["reference"] != "[210]"
        group_value, group_keys = pointwise_response_macs(
            mapping,
            group_mf3,
            group_mf10,
            float(source["kT_lab_keV"]),
            stellar_normalization=stellar,
        )
        point_value, point_keys = pointwise_response_macs(
            mapping,
            point_mf3,
            point_mf10,
            float(source["kT_lab_keV"]),
            stellar_normalization=stellar,
        )
        production_value, production_reason, production_evidence = production_macs(
            production,
            mapping,
            float(source["kT_lab_keV"]),
            stellar_normalization=stellar,
        )
        group_mb = None if group_value is None else 1000.0 * group_value
        point_mb = None if point_value is None else 1000.0 * point_value
        production_mb = None if production_value is None else 1000.0 * production_value
        if group_mb is not None and point_mb is not None:
            fold_relatives.append(abs(group_mb - point_mb) / point_mb)
        experimental = float(source["measured_mb"])
        calculations = {
            VARIANT_PRODUCTION: score_calculation(
                experimental,
                production_mb,
                status="scored" if production_mb is not None else "unscored",
                reason="scored" if production_mb is not None else production_reason,
                input_set_id="production",
                interpretation="ACTINV shipped TENDL-2025 groups; post-P17-failure diagnostic only.",
            ),
            VARIANT_CURRENT_IRDFF: score_calculation(
                experimental,
                point_mb,
                status="scored" if point_mb is not None else "unscored",
                reason="scored" if point_mb is not None else "variant_reaction_unavailable",
                input_set_id="current_irdff",
                interpretation="Independent pointwise fold of the current hash-pinned IRDFF-II archive.",
            ),
        }
        source_inconsistent = source["table_row"] == 21
        if source_inconsistent:
            calculations[VARIANT_PUBLISHED] = unscored_calculation(
                input_set_id="published",
                reason="different_data_context",
                interpretation="Literal published calculated cell conflicts with its printed C/E; no value inferred.",
                value=float(source["published_calculated_mb"]),
            )
        else:
            calculations[VARIANT_PUBLISHED] = score_calculation(
                experimental,
                float(source["published_calculated_mb"]),
                input_set_id="published",
                interpretation="Literal published IRDFF-II calculation context.",
            )
        row = make_row(
            row_id=f"p17-h3-t36-r{source['table_row']:03d}-{source['label']}",
            family=FAMILY_H3,
            source_id=f"IRDFF-II:Table-36:row-{source['table_row']:03d}",
            source_record=raw_h3_source(source),
            observable="Maxwellian_spectrum_averaged_cross_section",
            unit="mb",
            experimental_value=experimental,
            experimental_uncertainty=float(source["experimental_uncertainty_percent"]),
            experimental_uncertainty_unit="percent",
            inclusion_status="scored",
            inclusion_reason="scored",
            calculations=calculations,
        )
        literal_ratio = float(source["published_calculated_mb"]) / experimental
        row["heldout_evidence"] = {
            "post_failure_mapping": mapping,
            "maxwellian_normalization": (
                "stellar_2_over_sqrt_pi" if stellar else "unit_area_laboratory"
            ),
            "current_irdff_groupwise_mb": group_mb,
            "current_irdff_pointwise_mb": point_mb,
            "groupwise_vs_pointwise_relative": (
                None
                if group_mb is None or point_mb is None
                else abs(group_mb - point_mb) / point_mb
            ),
            "current_irdff_group_keys": group_keys,
            "current_irdff_pointwise_keys": point_keys,
            "production_reason": production_reason,
            "production_evidence": production_evidence,
            "published_literal_ratio": literal_ratio,
            "published_ratio_minus_printed_C_over_E": literal_ratio
            - float(source["published_C_over_E"]),
            "source_internal_inconsistency": source_inconsistent,
        }
        output.append(row)
    return output, {
        "maximum_groupwise_vs_pointwise_relative": max(fold_relatives),
        "folded_rows": len(fold_relatives),
    }


def ledger_segment(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    by_id = {row["row_id"]: row for row in rows}
    production_mismatches = [
        key for key in mismatch_keys(rows) if key.endswith(f"::{VARIANT_PRODUCTION}")
    ]
    for mismatch_key in production_mismatches:
        row_id = mismatch_key.rsplit("::", 1)[0]
        row = by_id[row_id]
        production = row["calculations"][VARIANT_PRODUCTION]
        current = row["calculations"][VARIANT_CURRENT_IRDFF]
        if current["status"] == "scored":
            signed_change = math.log(float(current["value"]) / float(production["value"]))
        else:
            signed_change = 0.0
        label = row["source_record"]["label"]
        if label in {"In113gm", "In113gg"}:
            entry = cause_entry(
                mismatch_key,
                primary_cause="evaluation",
                secondary_causes=["measurement-definition"],
                controlled_substitution="TENDL-2025 to current IRDFF-II pointwise branch response",
                signed_log_change=signed_change,
                evidence=[
                    f"{row_id}#heldout_evidence.post_failure_mapping",
                    f"{row_id}#calculations.{VARIANT_CURRENT_IRDFF}",
                    "Current IRDFF-II branch fold also disagrees materially while its total In-113 capture is near the measurement.",
                ],
                confidence="bounded",
            )
        else:
            entry = cause_entry(
                mismatch_key,
                primary_cause="unresolved",
                secondary_causes=["evaluation", "measurement-definition"],
                controlled_substitution="TENDL-2025 to current IRDFF-II pointwise response",
                signed_log_change=signed_change,
                evidence=[
                    f"{row_id}#calculations.{VARIANT_CURRENT_IRDFF}",
                    "The primary paper explicitly leaves the Ag-109 evaluation-versus-experiment cause unresolved.",
                ],
                confidence="unresolved",
            )
        entry["sequence"] = len(entries) + 1
        entry["entry_sha256"] = canonical_sha256(entry)
        entries.append(entry)
    return {
        "schema": LEDGER_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "append_only": True,
        "predecessor": {
            "path": "results/p17_cause_ledger.json",
            "sha256": sha256(PREDECESSOR_LEDGER),
        },
        "scope": "post-P17-failure diagnostic mismatches; not accepted G5 evidence",
        "taxonomy": [
            "solver",
            "chain-construction",
            "processor",
            "evaluation",
            "decay-yield",
            "measurement-definition",
            "unsupported-model",
            "unresolved",
        ],
        "entries": entries,
        "entries_sha256": canonical_sha256(entries),
    }


def generate() -> tuple[dict[str, Any], dict[str, Any]]:
    if sha256(PROTOCOL) != PROTOCOL_SHA256 or sha256(AMENDMENT) != AMENDMENT_SHA256:
        raise RuntimeError("P17 protocol or Amendment 1 identity changed")
    identities = checked_inputs()
    support = validate_support_tables()
    with extracted_inputs() as paths:
        si_sources, si_diagnostics = derived_si_rows(decay_by_identity(paths["decay"]))
        h3_sources = parse_maxwellian_table()
        post_failure_mappings = [source["post_failure_mapping"] for source in si_sources]
        post_failure_mappings.extend(
            post_failure_publication_mapping(source["label"]) for source in h3_sources
        )
        group_mf3, group_mf10 = selected_endf_catalog(paths["group"], post_failure_mappings)
        point_mf3, point_mf10 = selected_endf_catalog(
            paths["pointwise"],
            [post_failure_publication_mapping(source["label"]) for source in h3_sources],
        )
        spectra = spectrum_catalog(paths["spectrum"])
        production = ProductionLibrary(PRODUCTION_LIBRARY, PRODUCTION_INDEX)
        si_rows, bare_capture_counterfactuals = build_si_rows(
            si_sources, group_mf3, group_mf10, spectra, production
        )
        h3_rows, h3_fold_diagnostics = build_h3_rows(
            h3_sources,
            group_mf3,
            group_mf10,
            point_mf3,
            point_mf10,
            production,
        )
    rows = si_rows + h3_rows
    ledger = ledger_segment(rows)
    production_mismatch_keys = sorted(
        key for key in mismatch_keys(rows) if key.endswith(f"::{VARIANT_PRODUCTION}")
    )
    uniform_max = float(si_diagnostics["maximum_EOI_reconstruction_relative"])
    pulse_max = float(si_diagnostics["maximum_post_failure_pulse_reconstruction_relative"])
    bare_capture_max = max(
        float(row["relative"]) for row in bare_capture_counterfactuals
    )
    source_inconsistent = next(
        row for row in h3_rows if row["heldout_evidence"]["source_internal_inconsistency"]
    )
    failed_conditions = {
        "amendment_1_uniform_960s_EOI_reproduces_observable": uniform_max <= 0.005,
        "amendment_1_mapping_covers_publication_Ag109gm_alias": False,
        "amendment_1_bare_means_unshielded_for_capture_foils": bare_capture_max <= 0.05,
        "no_second_post_unseal_repair_needed": False,
    }
    post_failure_checks = {
        "all_heldout_rows_preserved": len(rows) == 94
        and len({row["source_id"] for row in rows}) == 94,
        "post_failure_pulse_limit_reconstructs_printed_SI_within_4_percent": pulse_max <= 0.04,
        "current_irdff_groupwise_matches_pointwise_within_0_5_percent": h3_fold_diagnostics[
            "maximum_groupwise_vs_pointwise_relative"
        ]
        <= 0.005,
        "literal_table36_source_anomaly_preserved": source_inconsistent["source_record"]
        ["published_calculated_mb"]
        == 389.9
        and source_inconsistent["source_record"]["published_C_over_E"] == 1.011,
        "post_failure_mismatches_ledgered_exactly_once": sorted(
            entry["mismatch_key"] for entry in ledger["entries"]
        )
        == production_mismatch_keys,
    }
    result = {
        "schema": SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "unseal_commit": UNSEAL_COMMIT,
        "control_source_sha256": sha256(Path(__file__)),
        "checker_source_sha256": sha256(ROOT / "controls" / "check_g5_p17.py"),
        "helper_source_sha256": sha256(ROOT / "controls" / "p17_heldout.py"),
        "scoring_source_sha256": sha256(ROOT / "controls" / "p17_scoring.py"),
        "input_identities": identities,
        "input_sets": input_sets(identities),
        "support_tables": support,
        "variant_definitions": {
            VARIANT_PRODUCTION: "Post-failure ACTINV v1.0.1/TENDL-2025 diagnostic; not a repaired held-out score.",
            VARIANT_CURRENT_IRDFF: "Current hash-pinned IRDFF-II archive independently folded after P17 failed.",
            VARIANT_PUBLISHED: "Literal published IRDFF-II calculation context; never an ACTINV competitor score.",
        },
        "row_counts": {
            "source_total": 94,
            "H1": 40,
            "H2": 33,
            "H3": 21,
            "post_failure_production_material_mismatches": len(production_mismatch_keys),
        },
        "rows": rows,
        "family_metrics": all_family_metrics(rows),
        "frozen_gate_evidence": {
            "uniform_960s_maximum_reconstruction_relative": uniform_max,
            "post_failure_pulse_maximum_reconstruction_relative": pulse_max,
            "bare_capture_unshielded_maximum_relative_to_published": bare_capture_max,
            "bare_capture_counterfactuals": bare_capture_counterfactuals,
            "publication_alias_conflicts": [
                "IRDFF-II:Table-25:row-009 Ag109g-bare",
                "IRDFF-II:Table-25:row-010 Ag109g-Cd",
                "IRDFF-II:Table-36:row-020 Ag109g",
            ],
            "failed_conditions": failed_conditions,
        },
        "h3_fold_diagnostics": h3_fold_diagnostics,
        "post_failure_material_mismatch_keys": production_mismatch_keys,
        "cause_ledger_segment": {
            "path": "results/p17_cause_ledger_g5.json",
            "sha256": hashlib.sha256(rendered_json(ledger)).hexdigest(),
            "entries_sha256": ledger["entries_sha256"],
        },
        "post_failure_checks": post_failure_checks,
        "post_failure_diagnostics_complete": all(post_failure_checks.values()),
        "verdict": "P17-FAIL",
        "pass": False,
        "failure_reason": (
            "The committed Amendment 1 assumptions fail held-out EOI and self-shielding arithmetic; "
            "a second post-unseal mapping/measurement repair would be required."
        ),
    }
    result["evidence_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "evidence_sha256"}
    )
    return result, ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    result, ledger = generate()
    if not arguments.no_write:
        write_json(LEDGER_SEGMENT, ledger)
        if result["cause_ledger_segment"]["sha256"] != sha256(LEDGER_SEGMENT):
            raise RuntimeError("rendered cause-ledger segment identity changed while writing")
        write_json(RESULT, result)
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "rows": result["row_counts"],
                "failed_conditions": result["frozen_gate_evidence"]["failed_conditions"],
                "post_failure_checks": result["post_failure_checks"],
                "metrics": result["family_metrics"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    # A scientifically expected P17-FAIL is represented in the result rather
    # than as a process failure so closure and successor controls can replay it.
    return 0 if result["post_failure_diagnostics_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
