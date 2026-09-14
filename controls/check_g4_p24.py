#!/usr/bin/env python3
"""P24 G4 independent checker.

Verifies the authorized fresh-partition read without importing
``p24_definitions``, ``p24_scorer`` or any production module, and without
re-parsing the sealed tables — the protocol's one-time read is spent, so this
checker works only on the produced record:

  - protocol / definitions / scorer / artifact identities re-hashed;
  - every ledger row appears exactly once (row_id, source_id), carries a
    canonical source-record digest that recomputes, and has an inclusion
    status/reason inside the frozen P24 vocabulary;
  - every scored calculation's ``ratio_C_over_E``, ``signed_log_C_over_E``
    and ``material_mismatch`` are recomputed from the row's own values;
  - family metrics (geomean, median, p90-linear, max, within-10/20/30%
    fractions, scored/unscored counts and reason histograms) are recomputed
    from the ledger with independent arithmetic;
  - the inclusion histogram and per-table row counts recount from the
    ledger; the cause-ledger keys equal the recomputed mismatch keys;
  - planted mutations of the record are rejected.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g4_p24_fresh.json"
LEDGER = RESULTS / "g4_p24_row_ledger.json"
CAUSES = RESULTS / "p24_cause_ledger.json"
G0_SEAL = RESULTS / "g0_p24_seals.json"
G2_AUDIT = RESULTS / "g2_p24_scorer_audit.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
DEF_MODULE = REPO / "controls" / "p24_definitions.py"
SCORER = REPO / "controls" / "p24_scorer.py"
CANDIDATE_NPZ = REPO / "target" / "p24-g0" / "candidate-neutron.npz"

P24_INCLUSION_REASONS = frozenset({
    "scored", "nonpositive_experimental_value", "nonfinite_experimental_value",
    "nonpositive_measurement_time", "no_cooling_step_within_2_percent",
    "non_neutron_incident_particle", "unmapped_target_reaction_product",
    "insufficient_spectrum_or_history", "unsupported_self_shielding",
    "undefined_state_alias", "undefined_eoi_history", "undefined_monitor",
    "internal_consistency_failure", "monitor_identity_not_predictive",
    "unsupported_observable_kind",
})
P24_CALCULATION_REASONS = frozenset({
    "scored", "nonpositive_calculated_value", "nonfinite_calculated_value",
    "variant_target_unavailable", "variant_reaction_unavailable",
    "different_data_context", "not_applicable",
    "undefined_monitor", "insufficient_spectrum_or_history",
    "non_neutron_incident_particle",
})
CAUSES_TAXONOMY = frozenset({
    "solver", "chain-construction", "processor", "evaluation", "decay-yield",
    "measurement-definition", "unsupported-model", "unresolved",
})
FOLD_VARIANTS = ("official", "candidate", "baseline")
WITHIN = {"10": (0.9, 1.1), "20": (0.8, 1.2), "30": (1.0 / 1.3, 1.3)}
LN_13 = math.log(1.3)

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(
        value, allow_nan=False, ensure_ascii=True,
        separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def percentile_linear(values: list[float], q: float) -> float:
    """NumPy 'linear' method, implemented independently."""
    s = sorted(values)
    n = len(s)
    rank = q * (n - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return s[lo] + frac * (s[hi] - s[lo])


def check_calculation(row: dict, variant: str, calc: dict,
                      out: list[str]) -> None:
    rid = row["row_id"]
    reason = calc.get("reason")
    if reason not in P24_CALCULATION_REASONS:
        out.append(f"{rid}::{variant}: reason {reason} outside vocabulary")
    if calc.get("status") == "scored":
        exp = row["experimental"]["value"]
        if not (isinstance(exp, (int, float)) and math.isfinite(exp) and exp > 0):
            out.append(f"{rid}::{variant}: scored with invalid experimental")
            return
        if reason != "scored" or not math.isfinite(calc.get("value", float("nan"))):
            out.append(f"{rid}::{variant}: scored status without value")
            return
        want_ratio = calc["value"] / exp
        got_ratio = calc.get("ratio_C_over_E")
        if got_ratio is None or not math.isclose(
                got_ratio, want_ratio, rel_tol=1e-12):
            out.append(f"{rid}::{variant}: C/E {got_ratio} != recomputed {want_ratio}")
        want_log = math.log(want_ratio)
        got_log = calc.get("signed_log_C_over_E")
        if got_log is None or not math.isclose(got_log, want_log, rel_tol=1e-12):
            out.append(f"{rid}::{variant}: log C/E mismatch")
        if calc.get("material_mismatch") != (abs(want_log) > LN_13):
            out.append(f"{rid}::{variant}: material_mismatch flag mismatch")
    else:
        if calc.get("value") is not None or calc.get("ratio_C_over_E") is not None:
            out.append(f"{rid}::{variant}: unscored row carries a value")
        if calc.get("material_mismatch"):
            out.append(f"{rid}::{variant}: unscored row flagged mismatch")
        if calc.get("status") == "context":
            if reason != "different_data_context":
                out.append(f"{rid}::{variant}: context row with wrong reason")


def family_metrics(rows: list[dict], variant: str) -> dict:
    """Independent copy of the frozen P17 metric semantics: an
    inclusion-unscored row counts its inclusion reason; an included row
    counts the per-variant calculation reason."""
    logs = []
    reasons = Counter()
    n_scored = 0
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            reasons[row["inclusion"]["reason"]] += 1
            continue
        calc = row["calculations"].get(variant)
        if calc is None:
            reasons["variant_reaction_unavailable"] += 1
            continue
        if calc["status"] != "scored":
            reasons[calc["reason"]] += 1
            continue
        n_scored += 1
        logs.append(abs(calc["signed_log_C_over_E"]))
    out = {"scored_rows": n_scored, "unscored_rows": len(rows) - n_scored,
           "unscored_reasons": dict(sorted(reasons.items()))}
    if logs:
        geo = math.exp(sum(
            math.log(row["calculations"][variant]["ratio_C_over_E"])
            for row in rows
            if row["calculations"][variant]["status"] == "scored") / len(logs))
        out.update({
            "geometric_mean_C_over_E": geo,
            "median_abs_log_C_over_E": percentile_linear(logs, 0.5),
            "p90_abs_log_C_over_E": percentile_linear(logs, 0.9),
            "maximum_abs_log_C_over_E": max(logs),
        })
        for band, (lo, hi) in WITHIN.items():
            n = sum(1 for row in rows
                    if row["calculations"][variant]["status"] == "scored"
                    and lo <= row["calculations"][variant]["ratio_C_over_E"] <= hi)
            out[f"fraction_within_{band}_percent"] = n / len(logs)
    else:
        out.update({"geometric_mean_C_over_E": None,
                    "median_abs_log_C_over_E": None,
                    "p90_abs_log_C_over_E": None,
                    "maximum_abs_log_C_over_E": None,
                    "fraction_within_10_percent": None,
                    "fraction_within_20_percent": None,
                    "fraction_within_30_percent": None})
    return out


def metric_equal(recorded: dict, recomputed: dict) -> bool:
    if set(recorded) != set(recomputed):
        return False
    for key, want in recomputed.items():
        got = recorded[key]
        if isinstance(want, float):
            if got is None or not math.isclose(got, want, rel_tol=1e-12):
                return False
        elif got != want:
            return False
    return True


def check_report(record: dict, rows: list[dict]) -> list[str]:
    local: list[str] = []
    if record.get("schema") != "actinv-p24-g4-fresh-1":
        local.append("schema")
    if record.get("gate") != "G4" or record.get("phase") != "P24":
        local.append("gate/phase")
    if not record.get("pass"):
        local.append("record pass is false")
    if record.get("protocol_sha256") != PROTOCOL_SHA256:
        local.append("protocol hash field")
    if record.get("row_count") != len(rows):
        local.append(f"row_count {record.get('row_count')} != {len(rows)}")
    if len({r["row_id"] for r in rows}) != len(rows):
        local.append("duplicate row_id")
    if len({r["source_id"] for r in rows}) != len(rows):
        local.append("duplicate source_id")

    histogram = Counter()
    per_table = Counter()
    mismatch_keys = set()
    for row in rows:
        rid = row["row_id"]
        inc = row.get("inclusion", {})
        if inc.get("status") not in ("scored", "unscored"):
            local.append(f"{rid}: bad inclusion status")
        if inc.get("reason") not in P24_INCLUSION_REASONS:
            local.append(f"{rid}: reason {inc.get('reason')} outside vocabulary")
        if inc.get("status") == "scored" and inc.get("reason") != "scored":
            local.append(f"{rid}: scored status with non-scored reason")
        histogram[inc.get("reason")] += 1
        per_table[str(row.get("source_record", {}).get("table"))] += 1
        if canonical_sha256(row["source_record"]) != row.get("source_record_sha256"):
            local.append(f"{rid}: source_record digest mismatch")
        for variant in FOLD_VARIANTS:
            calc = row["calculations"].get(variant)
            if calc is None:
                local.append(f"{rid}: missing {variant} calculation")
                continue
            check_calculation(row, variant, calc, local)
            if calc.get("status") == "scored" and calc.get("material_mismatch"):
                mismatch_keys.add(f"{rid}::{variant}")
        published = row["calculations"].get("published")
        if published is None or published.get("status") != "context":
            local.append(f"{rid}: missing published context column")

    if dict(record.get("inclusion_histogram") or {}) != dict(histogram):
        local.append("inclusion_histogram recount differs")
    for t, blk in (record.get("fresh_tables") or {}).items():
        if blk.get("rows") != per_table.get(t, 0):
            local.append(f"table {t}: recorded rows {blk.get('rows')} != {per_table.get(t,0)}")

    # family metrics recompute
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    rec_fam = record.get("family_metrics") or {}
    for family, fam_rows in by_family.items():
        for variant in FOLD_VARIANTS:
            want = family_metrics(fam_rows, variant)
            got = (rec_fam.get(family) or {}).get(variant)
            if got is None or not metric_equal(got, want):
                local.append(f"family {family}/{variant}: metrics differ")

    # cause ledger
    seg = json.loads(CAUSES.read_text())
    if seg.get("schema") != "actinv-p24-cause-ledger-segment-1":
        local.append("cause-ledger schema")
    keys = {e["mismatch_key"] for e in seg.get("entries", [])}
    if keys != mismatch_keys:
        local.append(f"cause keys {sorted(keys)} != mismatches {sorted(mismatch_keys)}")
    for e in seg.get("entries", []):
        if e.get("primary_cause") not in CAUSES_TAXONOMY:
            local.append(f"{e.get('mismatch_key')}: cause outside taxonomy")
        if not e.get("evidence"):
            local.append(f"{e.get('mismatch_key')}: no evidence")
        if any(s not in CAUSES_TAXONOMY for s in e.get("secondary_causes", [])):
            local.append(f"{e.get('mismatch_key')}: secondary outside taxonomy")
    cl = record.get("cause_ledger") or {}
    if set(cl.get("mismatch_keys") or []) != mismatch_keys:
        local.append("report cause_ledger keys != mismatches")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    record = json.loads(RECORD.read_text())
    rows = json.loads(LEDGER.read_text())["rows"]
    seal = json.loads(G0_SEAL.read_text())
    g2 = json.loads(G2_AUDIT.read_text())

    if record.get("definitions_sha256") != sha256(DEF_MODULE):
        failures.append("definitions module hash != live file")
    if record.get("scorer_sha256") != sha256(SCORER):
        failures.append("scorer hash != live file")
    if g2["freeze"]["scorer_sha256"] != sha256(SCORER):
        failures.append("scorer differs from the G2-audited file")

    seals = seal["artifacts"]
    cand = record.get("candidate") or {}
    base = record.get("baseline") or {}
    if cand.get("npz_sha256") != seals.get("candidate_npz_sha256"):
        failures.append("candidate NPZ identity differs from G0 seal")
    if cand.get("index_sha256") != seals.get("candidate_index_sha256"):
        failures.append("candidate index identity differs from G0 seal")
    if base.get("npz_sha256") != seals.get("baseline_npz_sha256"):
        failures.append("baseline NPZ identity differs from G0 seal")
    if base.get("index_sha256") != seals.get("baseline_index_sha256"):
        failures.append("baseline index identity differs from G0 seal")
    ai = record.get("input_identities", {})
    if ai.get("production_library") != seals.get("baseline_npz_sha256"):
        failures.append("production_library input identity != baseline seal")
    if CANDIDATE_NPZ.exists() and sha256(CANDIDATE_NPZ) != cand.get("npz_sha256"):
        failures.append("candidate NPZ live hash mismatch")

    failures.extend(check_report(record, rows))

    mutations = 0
    rejected = 0
    plants = [
        lambda r, w: w["rows"][0]["calculations"]["official"].update(
            {"ratio_C_over_E": 2.0}),
        lambda r, w: w["rows"][0].update({"inclusion": {"status": "unscored",
                                                      "reason": "bogus"}}),
        lambda r, w: w["rows"].pop(),
        lambda r, w: r["family_metrics"]["F-MOLBR1"]["official"].update(
            {"scored_rows": 999}),
        lambda r, w: r.update({"inclusion_histogram": {"scored": 1}}),
    ]
    for plant in plants:
        rep = copy.deepcopy(record)
        led = copy.deepcopy({"rows": rows})
        plant(rep, led)
        mutations += 1
        if check_report(rep, led["rows"]):
            rejected += 1
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p24-g4-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "recompute": {
            "rows": len(rows),
            "scored": histogram_n(rows),
        },
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g4_p24_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


def histogram_n(rows) -> int:
    return sum(1 for r in rows if r["inclusion"]["reason"] == "scored")


if __name__ == "__main__":
    sys.exit(main())
