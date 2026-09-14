#!/usr/bin/env python3
"""P24 G5 — independent closure checker.

Imports no production, parsing or scoring module.  Rehashes all inputs,
repeats the definition-record arithmetic (EOI residuals), C/E arithmetic,
family metrics and exclusion accounting on the produced ledgers, verifies
the seal and unseal-authorization ordering via git ancestry, re-verifies
every prior verdict verbatim, and rejects planted mutations.

Writes ``results/verdict_p24.json``.  Under the frozen protocol a single
append-only repair amendment caps an otherwise passing closure at
``P24-CONDITIONAL``; a second repair round fails the phase.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUT = RESULTS / "verdict_p24.json"

PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
AMENDMENT = REPO / "protocols" / "ACTINV-P24_AMENDMENT_1.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
DEF_MODULE = REPO / "controls" / "p24_definitions.py"
SCORER = REPO / "controls" / "p24_scorer.py"
DRIVER = REPO / "controls" / "g4_p24_fresh.py"
DATA_ROOT = Path.home() / "nuclear-data" / "p17-irdff"
CANDIDATE_NPZ = REPO / "target" / "p24-g0" / "candidate-neutron.npz"
CANDIDATE_INDEX = REPO / "target" / "p24-g0" / "candidate-neutron_index.json"

OPENING_COMMIT = "122a1d360895368f37d7ccf00ffc8b6cabbbc5dc"
G0_COMMIT = "6883b6dd"
G3_COMMIT = "77f9484645d6e400f49c6a2202588305acb8ca5b"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p25.json": "P25-FAIL",
}

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
LN_13 = math.log(1.3)
WITHIN = {"10": (0.9, 1.1), "20": (0.8, 1.2), "30": (1.0 / 1.3, 1.3)}

GATE_FILES = {
    "g0_seals": "results/g0_p24_seals.json",
    "g0_check": "results/g0_p24_check.json",
    "g0_build": "results/g0_p24_candidate_build.json",
    "g1_definitions": "results/g1_p24_definitions.json",
    "g1_check": "results/g1_p24_check.json",
    "g2_audit": "results/g2_p24_scorer_audit.json",
    "g2_check": "results/g2_p24_check.json",
    "g3_diagnostic": "results/g3_p24_diagnostic.json",
    "g3_check": "results/g3_p24_check.json",
    "unseal_authorization": "results/p24_unseal_authorization.json",
    "g4_fresh": "results/g4_p24_fresh.json",
    "g4_ledger": "results/g4_p24_row_ledger.json",
    "g4_check": "results/g4_p24_check.json",
    "cause_ledger": "results/p24_cause_ledger.json",
}

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
    s = sorted(values)
    rank = q * (len(s) - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (rank - lo) * (s[hi] - s[lo])


def family_metrics(rows: list[dict], variant: str) -> dict:
    """Independent copy of the frozen P17 metric semantics."""
    scored, reasons = [], Counter()
    for row in rows:
        if row["inclusion"]["status"] != "scored":
            reasons[row["inclusion"]["reason"]] += 1
            continue
        calc = row["calculations"].get(variant)
        if calc is None:
            reasons["variant_reaction_unavailable"] += 1
        elif calc["status"] != "scored":
            reasons[calc["reason"]] += 1
        else:
            scored.append(calc)
    out = {"scored_rows": len(scored),
           "unscored_rows": len(rows) - len(scored),
           "unscored_reasons": dict(sorted(reasons.items()))}
    if scored:
        ratios = sorted(c["ratio_C_over_E"] for c in scored)
        logs = sorted(abs(c["signed_log_C_over_E"]) for c in scored)
        out.update({
            "geometric_mean_C_over_E": math.exp(
                sum(math.log(r) for r in ratios) / len(ratios)),
            "median_abs_log_C_over_E": percentile_linear(logs, 0.5),
            "p90_abs_log_C_over_E": percentile_linear(logs, 0.9),
            "maximum_abs_log_C_over_E": max(logs),
        })
        for band, (lo, hi) in WITHIN.items():
            out[f"fraction_within_{band}_percent"] = (
                sum(1 for r in ratios if lo <= r <= hi) / len(ratios))
    else:
        for key in ("geometric_mean_C_over_E", "median_abs_log_C_over_E",
                    "p90_abs_log_C_over_E", "maximum_abs_log_C_over_E",
                    "fraction_within_10_percent",
                    "fraction_within_20_percent",
                    "fraction_within_30_percent"):
            out[key] = None
    return out


def metric_equal(recorded, recomputed) -> bool:
    if recorded is None or set(recorded) != set(recomputed):
        return False
    for key, want in recomputed.items():
        got = recorded[key]
        if isinstance(want, float):
            if got is None or not math.isclose(got, want, rel_tol=1e-12):
                return False
        elif got != want:
            return False
    return True


def check_ledger(record: dict, rows: list[dict]) -> list[str]:
    """C/E arithmetic + exclusion accounting on the produced ledger."""
    local: list[str] = []
    if record.get("row_count") != len(rows):
        local.append("row_count mismatch")
    if len({r["row_id"] for r in rows}) != len(rows):
        local.append("duplicate row_id")
    if len({r["source_id"] for r in rows}) != len(rows):
        local.append("duplicate source_id")
    histogram = Counter()
    mismatch_keys = set()
    for row in rows:
        rid = row["row_id"]
        inc = row.get("inclusion", {})
        if inc.get("reason") not in P24_INCLUSION_REASONS:
            local.append(f"{rid}: inclusion reason outside vocabulary")
        if (inc.get("status") == "scored") != (inc.get("reason") == "scored"):
            local.append(f"{rid}: inclusion status/reason inconsistent")
        histogram[inc.get("reason")] += 1
        if canonical_sha256(row["source_record"]) != \
                row.get("source_record_sha256"):
            local.append(f"{rid}: source digest mismatch")
        exp = row["experimental"].get("value")
        for variant in FOLD_VARIANTS:
            calc = row["calculations"].get(variant)
            if calc is None:
                local.append(f"{rid}: missing {variant}")
                continue
            if calc.get("reason") not in P24_CALCULATION_REASONS:
                local.append(f"{rid}::{variant}: calc reason outside vocabulary")
            if calc.get("status") == "scored":
                if not (isinstance(exp, (int, float)) and math.isfinite(exp)
                        and exp > 0):
                    local.append(f"{rid}::{variant}: scored on invalid E")
                    continue
                ratio = calc["value"] / exp
                if not math.isclose(calc["ratio_C_over_E"], ratio, rel_tol=1e-12):
                    local.append(f"{rid}::{variant}: C/E recompute differs")
                if not math.isclose(
                        calc["signed_log_C_over_E"], math.log(ratio),
                        rel_tol=1e-12):
                    local.append(f"{rid}::{variant}: log C/E recompute differs")
                if calc["material_mismatch"] != (abs(math.log(ratio)) > LN_13):
                    local.append(f"{rid}::{variant}: mismatch flag differs")
                if calc["material_mismatch"]:
                    mismatch_keys.add(f"{rid}::{variant}")
    if dict(record.get("inclusion_histogram") or {}) != dict(histogram):
        local.append("inclusion_histogram recount differs")

    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    for family, fam_rows in by_family.items():
        for variant in FOLD_VARIANTS:
            if not metric_equal(
                    (record.get("family_metrics") or {}).get(family, {}).get(variant),
                    family_metrics(fam_rows, variant)):
                local.append(f"family {family}/{variant}: metrics differ")
    return local, mismatch_keys


def check_g1_arithmetic(g1: dict) -> list[str]:
    """Recompute the frozen-definition demonstration residuals."""
    local: list[str] = []
    d = g1.get("d1_demonstration_on_consumed_partition", {})
    per_row = d.get("per_row", [])
    n_recon = n_within = 0
    for e in per_row:
        recon = e.get("corrected_pulse_reconstruction")
        printed = e.get("published_inferred_si")
        if recon is None or printed is None:
            continue
        n_recon += 1
        want = abs(recon - printed) / printed
        if not math.isclose(e["residual_relative"], want, rel_tol=1e-9):
            local.append(f"{e['row_id']}: residual recompute differs")
        if e["within_inferred_observable_uncertainty"]:
            n_within += 1
    if n_recon != d.get("rows_reconstructed"):
        local.append("rows_reconstructed recount differs")
    if n_within != d.get("rows_within_inferred_observable_uncertainty"):
        local.append("rows_within_inferred recount differs")
    if g1.get("fresh_partition_values_read") is not False:
        local.append("G1 record does not assert no fresh values read")
    return local


def check_ordering() -> list[str]:
    local: list[str] = []

    def git(*args) -> str:
        return subprocess.run(["git", *args], cwd=REPO, text=True,
                              capture_output=True).stdout.strip()

    def is_ancestor(a: str, b: str) -> bool:
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", a, b],
            cwd=REPO).returncode == 0

    head = git("rev-parse", "HEAD")
    if not is_ancestor(OPENING_COMMIT, head):
        local.append("opening commit is not an ancestor of HEAD")
    if not is_ancestor(G0_COMMIT, head):
        local.append("G0 commit is not an ancestor of HEAD")
    if not is_ancestor(G3_COMMIT, head):
        local.append("G3 (authorization) commit is not an ancestor of HEAD")
    if not is_ancestor(OPENING_COMMIT, G3_COMMIT):
        local.append("G3 does not descend from the opening commit")

    amendments = sorted(p.name for p in (REPO / "protocols").glob(
        "ACTINV-P24_AMENDMENT_*.md"))
    if amendments != ["ACTINV-P24_AMENDMENT_1.md"]:
        local.append(f"unexpected P24 amendment set: {amendments}")

    auth = json.loads((RESULTS / "p24_unseal_authorization.json").read_text())
    if auth.get("checkpoint", {}).get("commit") != G3_COMMIT:
        local.append("authorization checkpoint != G3 commit")
    for wf in auth.get("workflows", []):
        if wf.get("head_sha") != G3_COMMIT or wf.get("conclusion") != "success":
            local.append(f"authorization workflow {wf.get('name')} not green")
    if len(auth.get("workflows", [])) < 2:
        local.append("fewer than two authorized workflow records")
    return local


def evaluate(records: dict) -> list[str]:
    local: list[str] = []
    for name, rec in records.items():
        if name.endswith("_check") and rec.get("pass") is not True:
            local.append(f"{name} does not carry pass")
    if records["g0_seals"].get("pass") is not True:
        local.append("G0 seal record not green")
    if records["g2_audit"].get("pass") is not True:
        local.append("G2 audit record not green")
    if records["g3_diagnostic"].get("pass") is not True:
        local.append("G3 diagnostic record not green")
    if records["g4_fresh"].get("pass") is not True:
        local.append("G4 record not green")

    g1 = records["g1_definitions"]
    if g1["frozen_definition_module"]["sha256"] != sha256(DEF_MODULE):
        local.append("definition module differs from the G1 freeze")
    if records["g2_audit"]["freeze"]["scorer_sha256"] != sha256(SCORER):
        local.append("scorer differs from the G2-audited freeze")
    if records["g4_fresh"].get("scorer_sha256") != sha256(SCORER):
        local.append("G4 scorer hash != live file")
    if records["g4_fresh"].get("definitions_sha256") != sha256(DEF_MODULE):
        local.append("G4 definitions hash != live file")

    seal = records["g0_seals"]
    for name, blk in seal.get("irdff", {}).get("files", {}).items():
        if blk.get("actual_sha256") != blk.get("expected_sha256"):
            local.append(f"irdff {name}: seal hash mismatch")
        path = DATA_ROOT / blk.get("filename", "")
        if path.is_file() and sha256(path) != blk.get("expected_sha256"):
            local.append(f"irdff {name}: live rehash mismatch")
    art = seal.get("artifacts", {})
    g4 = records["g4_fresh"]
    if g4.get("candidate", {}).get("npz_sha256") != art.get("candidate_npz_sha256"):
        local.append("G4 candidate identity != G0 seal")
    if g4.get("baseline", {}).get("npz_sha256") != art.get("baseline_npz_sha256"):
        local.append("G4 baseline identity != G0 seal")
    if CANDIDATE_NPZ.is_file() and \
            sha256(CANDIDATE_NPZ) != art.get("candidate_npz_sha256"):
        local.append("candidate NPZ live rehash mismatch")
    if CANDIDATE_INDEX.is_file() and \
            sha256(CANDIDATE_INDEX) != art.get("candidate_index_sha256"):
        local.append("candidate index live rehash mismatch")

    local.extend(check_g1_arithmetic(g1))
    ledger_failures, mismatch_keys = check_ledger(
        records["g4_fresh"], records["g4_ledger"]["rows"])
    local.extend(ledger_failures)

    seg = records["cause_ledger"]
    keys = {e["mismatch_key"] for e in seg.get("entries", [])}
    if keys != mismatch_keys:
        local.append("cause-ledger keys != recomputed mismatches")
    for e in seg.get("entries", []):
        if e.get("primary_cause") not in CAUSES_TAXONOMY or not e.get("evidence"):
            local.append(f"{e.get('mismatch_key')}: invalid cause entry")
    return local


def run() -> dict:
    local: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        local.append("protocol sha256 mismatch")
    if not AMENDMENT.is_file():
        local.append("Amendment 1 missing")

    records = {}
    for name, rel in GATE_FILES.items():
        path = REPO / rel
        if not path.is_file():
            local.append(f"missing evidence {rel}")
        else:
            records[name] = json.loads(path.read_text())

    for name, expected in EXPECTED_VERDICTS.items():
        vp = RESULTS / name
        if not vp.exists():
            local.append(f"missing verdict {name}")
            continue
        if json.loads(vp.read_text()).get("verdict") != expected:
            local.append(f"{name} verdict != {expected}")

    if not local:
        local.extend(evaluate(records))
        local.extend(check_ordering())

    verdict = "P24-CONDITIONAL" if not local else "P24-FAIL"
    return {
        "schema": "actinv-p24-verdict-1",
        "phase": "P24",
        "verdict": verdict,
        "failures": local[:100],
        "failure_count": len(local),
        "evidence_sha256": {
            name: sha256(REPO / rel)
            for name, rel in GATE_FILES.items() if (REPO / rel).is_file()},
        "protocol_sha256": sha256(PROTOCOL),
        "amendment_1_sha256": sha256(AMENDMENT) if AMENDMENT.is_file() else None,
        "conditional_basis": (
            "One append-only repair round (Amendment 1, R1-R5) was used for "
            "mechanical input/scoring-path defects and report completeness; "
            "no definition, mapping, metric or exclusion changed after the "
            "authorized read and no scored value was produced before it."),
        "release_note": (
            "P24-CONDITIONAL does not certify TENDL-2025, assert an accuracy "
            "figure, or lift the 1.1.0 release hold; the release decision "
            "remains the maintainer's and consumes this report beside the "
            "P25-FAIL record."),
    }


def self_test() -> int:
    base = {n: json.loads((REPO / rel).read_text())
            for n, rel in GATE_FILES.items()}

    def mut_ce(r):
        r["g4_ledger"]["rows"][0]["calculations"]["official"]["ratio_C_over_E"] = 9.0

    def mut_reason(r):
        r["g4_ledger"]["rows"][0]["inclusion"]["reason"] = "bogus_reason"

    def mut_metric(r):
        r["g4_fresh"]["family_metrics"]["F-MOLBR1"]["official"]["geometric_mean_C_over_E"] = 2.0

    def mut_drop(r):
        r["g4_ledger"]["rows"].pop()

    def mut_residual(r):
        r["g1_definitions"]["d1_demonstration_on_consumed_partition"]["per_row"][0]["residual_relative"] = 0.5

    rejected = 0
    for plant in (mut_ce, mut_reason, mut_metric, mut_drop, mut_residual):
        m = copy.deepcopy(base)
        plant(m)
        f = evaluate(m)
        if f:
            rejected += 1
        else:
            print("MUTATION NOT REJECTED:", plant.__name__)
    print(f"self-test rejected {rejected}/5 mutations")
    return 0 if rejected == 5 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    result = run()
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items()
                      if k != "evidence_sha256"}, indent=1))
    return 0 if result["verdict"].startswith("P24-CONDITIONAL") or \
        result["verdict"] == "P24-PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
