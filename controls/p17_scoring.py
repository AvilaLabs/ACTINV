#!/usr/bin/env python3
"""Frozen P17 row scoring and family-metric implementation.

This module contains no ACTINV imports and performs no data acquisition.  The
G4 generator supplies source rows and calculation variants; this module checks
their shape, derives C/E values, and computes the protocol metrics.  G4's
independent checker deliberately does not import this file.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Any, Iterable

import numpy as np


SCHEMA = "actinv-p17-diagnostic-1"
CAUSE_LEDGER_SCHEMA = "actinv-p17-cause-ledger-1"
PROTOCOL_SHA256 = "c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f"
WITHIN_10 = (0.9, 1.1)
WITHIN_20 = (0.8, 1.2)
WITHIN_30 = (1.0 / 1.3, 1.3)

INCLUSION_REASONS = frozenset(
    {
        "scored",
        "nonpositive_experimental_value",
        "nonfinite_experimental_value",
        "nonpositive_measurement_time",
        "no_cooling_step_within_2_percent",
        "non_neutron_incident_particle",
        "unmapped_target_reaction_product",
        "insufficient_spectrum_or_history",
        "unsupported_self_shielding",
    }
)
CALCULATION_REASONS = frozenset(
    {
        "scored",
        "nonpositive_calculated_value",
        "nonfinite_calculated_value",
        "variant_target_unavailable",
        "variant_reaction_unavailable",
        "different_data_context",
        "not_applicable",
    }
)
CAUSES = frozenset(
    {
        "solver",
        "chain-construction",
        "processor",
        "evaluation",
        "decay-yield",
        "measurement-definition",
        "unsupported-model",
        "unresolved",
    }
)


def canonical_bytes(value: Any) -> bytes:
    """Return the single canonical JSON representation used by P17."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0.0


def source_digest(source_record: dict[str, Any]) -> str:
    return canonical_sha256(source_record)


def make_input_set(
    *,
    input_hashes: dict[str, str],
    identical_input_roles: Iterable[str],
    changed_input_roles: Iterable[str],
) -> dict[str, Any]:
    """Build the deduplicated identity record referenced by row calculations."""

    hashes = dict(sorted(input_hashes.items()))
    if not hashes or any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hashes.values()
    ):
        raise ValueError("every input set needs named lowercase SHA-256 identities")
    identical = sorted(set(identical_input_roles))
    changed = sorted(set(changed_input_roles))
    if set(identical) & set(changed):
        raise ValueError("input roles cannot be both identical and changed")
    if set(identical) | set(changed) != set(hashes):
        raise ValueError("identical/changed roles must partition input_hashes")
    return {
        "input_hashes": hashes,
        "identical_input_roles": identical,
        "changed_input_roles": changed,
    }


def score_calculation(
    experimental_value: float,
    calculated_value: float | None,
    *,
    status: str = "scored",
    reason: str = "scored",
    input_set_id: str,
    interpretation: str,
) -> dict[str, Any]:
    """Create one calculation record and derive its frozen C/E quantities."""

    if status not in {"scored", "unscored", "context"}:
        raise ValueError(f"invalid calculation status {status!r}")
    if reason not in CALCULATION_REASONS:
        raise ValueError(f"invalid calculation reason {reason!r}")
    if status == "scored" and reason != "scored":
        raise ValueError("a scored calculation must use reason='scored'")
    if status != "scored" and reason == "scored":
        raise ValueError("an unscored/context calculation needs a non-scored reason")
    if not finite_positive(experimental_value):
        raise ValueError("calculation scoring requires a finite positive experimental value")

    ratio = None
    signed_log = None
    mismatch = False
    if status == "scored":
        if calculated_value is None or not math.isfinite(float(calculated_value)):
            status = "unscored"
            reason = "nonfinite_calculated_value"
        elif float(calculated_value) <= 0.0:
            status = "unscored"
            reason = "nonpositive_calculated_value"
        else:
            ratio = float(calculated_value) / float(experimental_value)
            signed_log = math.log(ratio)
            mismatch = not (WITHIN_30[0] <= ratio <= WITHIN_30[1])

    if not input_set_id:
        raise ValueError("every calculation must reference an input set")

    return {
        "status": status,
        "reason": reason,
        "value": float(calculated_value) if calculated_value is not None else None,
        "ratio_C_over_E": ratio,
        "signed_log_C_over_E": signed_log,
        "material_mismatch": mismatch,
        "input_set_id": input_set_id,
        "interpretation": interpretation,
    }


def unscored_calculation(
    *, input_set_id: str, reason: str, interpretation: str, value: float | None = None
) -> dict[str, Any]:
    if not input_set_id:
        raise ValueError("every calculation must reference an input set")
    if reason not in CALCULATION_REASONS or reason == "scored":
        raise ValueError(f"invalid unscored calculation reason {reason!r}")
    if value is not None and not math.isfinite(float(value)):
        raise ValueError("preserved unscored values must be finite")
    return {
        "status": "context" if reason == "different_data_context" else "unscored",
        "reason": reason,
        "value": float(value) if value is not None else None,
        "ratio_C_over_E": None,
        "signed_log_C_over_E": None,
        "material_mismatch": False,
        "input_set_id": input_set_id,
        "interpretation": interpretation,
    }


def make_row(
    *,
    row_id: str,
    family: str,
    source_id: str,
    source_record: dict[str, Any],
    observable: str,
    unit: str,
    experimental_value: float | None,
    experimental_uncertainty: float | None,
    experimental_uncertainty_unit: str,
    inclusion_status: str,
    inclusion_reason: str,
    calculations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not row_id or not family or not source_id or not observable or not unit:
        raise ValueError("row identity, family, source, observable and unit are required")
    if inclusion_status not in {"scored", "unscored"}:
        raise ValueError(f"invalid inclusion status {inclusion_status!r}")
    if inclusion_reason not in INCLUSION_REASONS:
        raise ValueError(f"invalid inclusion reason {inclusion_reason!r}")
    if (inclusion_status == "scored") != (inclusion_reason == "scored"):
        raise ValueError("row inclusion status and reason disagree")
    if inclusion_status == "scored" and not finite_positive(experimental_value):
        raise ValueError("scored rows require a finite positive experimental value")
    if experimental_uncertainty is not None and (
        not math.isfinite(float(experimental_uncertainty))
        or float(experimental_uncertainty) < 0.0
    ):
        raise ValueError("experimental uncertainty must be finite and nonnegative")
    if not calculations:
        raise ValueError("every row must preserve at least one calculation variant")

    return {
        "row_id": row_id,
        "family": family,
        "source_id": source_id,
        "source_record": source_record,
        "source_record_sha256": source_digest(source_record),
        "observable": observable,
        "unit": unit,
        "experimental": {
            "value": float(experimental_value) if experimental_value is not None else None,
            "uncertainty": (
                float(experimental_uncertainty)
                if experimental_uncertainty is not None
                else None
            ),
            "uncertainty_unit": experimental_uncertainty_unit,
        },
        "inclusion": {"status": inclusion_status, "reason": inclusion_reason},
        "calculations": dict(sorted(calculations.items())),
    }


def family_metrics(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            reasons[row["inclusion"]["reason"]] += 1
            continue
        calculation = row["calculations"].get(variant)
        if calculation is None:
            reasons["variant_reaction_unavailable"] += 1
            continue
        if calculation["status"] != "scored":
            reasons[calculation["reason"]] += 1
            continue
        scored.append(calculation)

    ratios = np.asarray([entry["ratio_C_over_E"] for entry in scored], dtype=float)
    if len(ratios):
        absolute_log = np.abs(np.log(ratios))
        metrics: dict[str, Any] = {
            "geometric_mean_C_over_E": float(np.exp(np.mean(np.log(ratios)))),
            "median_abs_log_C_over_E": float(np.quantile(absolute_log, 0.5, method="linear")),
            "p90_abs_log_C_over_E": float(np.quantile(absolute_log, 0.9, method="linear")),
            "maximum_abs_log_C_over_E": float(np.max(absolute_log)),
            "fraction_within_10_percent": float(
                np.mean((ratios >= WITHIN_10[0]) & (ratios <= WITHIN_10[1]))
            ),
            "fraction_within_20_percent": float(
                np.mean((ratios >= WITHIN_20[0]) & (ratios <= WITHIN_20[1]))
            ),
            "fraction_within_30_percent": float(
                np.mean((ratios >= WITHIN_30[0]) & (ratios <= WITHIN_30[1]))
            ),
        }
    else:
        metrics = {
            "geometric_mean_C_over_E": None,
            "median_abs_log_C_over_E": None,
            "p90_abs_log_C_over_E": None,
            "maximum_abs_log_C_over_E": None,
            "fraction_within_10_percent": None,
            "fraction_within_20_percent": None,
            "fraction_within_30_percent": None,
        }
    return {
        "scored_rows": len(scored),
        "unscored_rows": len(rows) - len(scored),
        "unscored_reasons": dict(sorted(reasons.items())),
        **metrics,
    }


def all_family_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        families[row["family"]].append(row)
    output: dict[str, dict[str, Any]] = {}
    for family, family_rows in sorted(families.items()):
        variants = sorted(
            {variant for row in family_rows for variant in row["calculations"]}
        )
        output[family] = {
            variant: family_metrics(family_rows, variant) for variant in variants
        }
    return output


def mismatch_keys(rows: list[dict[str, Any]]) -> list[str]:
    keys = []
    for row in rows:
        for variant, calculation in row["calculations"].items():
            if calculation["status"] == "scored" and calculation["material_mismatch"]:
                keys.append(f"{row['row_id']}::{variant}")
    return sorted(keys)


def cause_entry(
    mismatch_key: str,
    *,
    primary_cause: str,
    secondary_causes: Iterable[str] = (),
    controlled_substitution: str,
    signed_log_change: float,
    evidence: list[str],
    confidence: str,
) -> dict[str, Any]:
    secondary = sorted(set(secondary_causes))
    if primary_cause not in CAUSES or any(cause not in CAUSES for cause in secondary):
        raise ValueError("cause entry uses a cause outside the frozen taxonomy")
    if primary_cause in secondary:
        raise ValueError("primary cause cannot also be secondary")
    if confidence not in {"demonstrated", "bounded", "unresolved"}:
        raise ValueError(f"invalid attribution confidence {confidence!r}")
    if not math.isfinite(signed_log_change):
        raise ValueError("signed log change must be finite")
    if not evidence:
        raise ValueError("cause entries require supporting evidence")
    return {
        "mismatch_key": mismatch_key,
        "primary_cause": primary_cause,
        "secondary_causes": secondary,
        "controlled_substitution": controlled_substitution,
        "signed_log_change": float(signed_log_change),
        "evidence": evidence,
        "confidence": confidence,
    }
