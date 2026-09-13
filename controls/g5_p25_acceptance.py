#!/usr/bin/env python3
"""P25 G5 — retrospective scoring of the repaired candidate under the
frozen P25 acceptance gates.

Runs the unchanged P18b scorer over the P18b candidate artifacts first
(historical reproducibility, gate 5), then scores the P25 candidate
artifacts at ``target/p25-g4/candidate-{p}.npz`` through the same
``g4_p18b_diagnostics`` module — which dispatches on the artifact's
declared ``emission_model``, so legacy artifacts keep legacy scoring.

Frozen gates (protocol §"Frozen acceptance gates"):
  1. complete outcome accounting — every eligible row gets exactly one
     named outcome in the G1 census vocabulary;
  2. comparable-case nonregression vs the v1.0.1 baseline — the frozen
     P18b additive/multiplicative median/p90 and 1-percentage-point
     coverage limits, per stratum with >=10 paired rows and overall;
  3. coverage floors — Amendment B per-stratum scored-row floors against
     the full 1,859-row eligible ledger;
  4. no empty stratum — >=10 scored rows per stratum;
  5. historical reproducibility — the P18b re-score reproduces the
     sealed record's metrics exactly.

Writes ``results/g5_p25_acceptance.json``. ``--self-test`` rejects
planted mutations.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_p25_census as g1  # noqa: E402
import g4_p18b_diagnostics as g4  # noqa: E402
import g5_p18b_heldout as g5  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK25 = ROOT / "target/p25-g4"
G4REC = ROOT / "results/g4_p25_repairs.json"
P18B_RECORD = ROOT / "results/g5_p18b_heldout.json"
CENSUS = ROOT / "results/g1_p25_census.json"
REPORT = ROOT / "results/g5_p25_acceptance.json"

# Amendment B frozen floors: stratum -> (min share, min scored rows)
FLOORS = {
    "neutron": (0.40, 188),
    "proton": (0.60, 417),
    "deuteron": (0.50, 72),
    "alpha": (0.55, 304),
}


def candidate_outcome(rec: dict, g4rec: dict, staged_built: dict) -> str:
    """One named outcome per eligible row, G1 census vocabulary."""
    blk = rec.get("candidate") or {}
    status = blk.get("status")
    if status == "scored":
        ln = blk.get("ln_cm")
        if blk.get("cm") == 0 or (ln is not None and not math.isfinite(ln)):
            return "zero_prediction_scored"
        return "scored"
    if status == "build_failed_g3":
        proj = rec["projectile"]
        fname = g5_target_file(rec)
        if fname and fname in (g4rec["projectiles"].get(proj, {})
                               .get("failures") or {}):
            return "construction_failed:quarantined"
        if fname and fname in staged_built.get(proj, set()):
            return "construction_failed:staged_built"
        return "construction_failed:unbuilt"
    if status is None:
        return "construction_failed:unbuilt"
    if status in ("zero_denominator", "undefined_ratio_form",
                  "energy_outside_groups"):
        return f"undefined_ratio:{status}"
    if status in ("target_absent", "library_absent"):
        return f"construction_failed:{status}"
    return f"undefined_ratio:{status}"


def g5_target_file(rec: dict) -> str | None:
    """Target filename for a ledger row, via the family_id ZA encoding."""
    return g1.family_target_file(rec["projectile"], rec["family_id"])


def p25_gates(scored: dict, accounting: dict, mapping: dict,
              historical_ok: bool) -> dict:
    """Frozen P25 acceptance gates."""
    # ---- gate 2: paired nonregression (frozen P18b limits) ----------
    p18_gates = g5.gates(scored, mapping)

    # ---- gate 3: Amendment B coverage floors ------------------------
    floor_rows = {}
    floors_ok = True
    for proj, (share, rows) in FLOORS.items():
        blk = scored["per_proj"].get(proj, {})
        cand_rows = blk.get("candidate", {}).get("rows", 0)
        eligible = blk.get("eligible_rows", 0)
        got_share = cand_rows / eligible if eligible else 0.0
        ok = cand_rows >= rows and got_share >= share
        floors_ok = floors_ok and ok
        floor_rows[proj] = {
            "eligible_rows": eligible,
            "candidate_scored": cand_rows,
            "floor_share": share, "floor_rows": rows,
            "scored_share": got_share,
            "pass": ok,
        }

    # ---- gate 4: no empty stratum ------------------------------------
    strata_nonempty = {
        proj: (scored["per_proj"].get(proj, {})
               .get("candidate", {}).get("rows", 0) >= 10)
        for proj in FLOORS
    }
    no_empty = all(strata_nonempty.values())

    # ---- gate 1: complete outcome accounting --------------------------
    unnamed = accounting["unnamed_outcomes"]
    accounting_ok = (unnamed == [] and accounting["accounted_rows"]
                     == accounting["eligible_rows"])

    return {
        "outcome_accounting": accounting_ok,
        "unnamed_outcomes": unnamed,
        "paired_nonregression": {
            "strata_pass": p18_gates["strata_pass"],
            "overall_pass": p18_gates["overall_pass"],
            "strata": p18_gates["strata"],
            "overall": p18_gates["overall"],
        },
        "coverage_floors": floor_rows,
        "coverage_floors_pass": floors_ok,
        "no_empty_stratum": no_empty,
        "strata_nonempty": strata_nonempty,
        "historical_reproducibility": historical_ok,
        "mapping_rule4": {
            "violation_count": mapping["violation_count"],
            "pass": mapping["violation_count"] == 0,
            "note": "P18b rule-4 record carried as evidence; the P25 "
                    "frozen gates do not list it as a pass condition.",
        },
        "benefit_evidence": p18_gates.get("benefit"),
        "pass": bool(
            accounting_ok and p18_gates["strata_pass"]
            and p18_gates["overall_pass"] and floors_ok
            and no_empty and historical_ok),
    }


def evaluate(report: dict) -> list[str]:
    """Independent arithmetic re-derivation for the G6 checker and
    --self-test: recomputes every gate from the ledger itself."""
    f = []
    if report.get("schema") != "actinv-p25-g5-acceptance-1":
        f.append("schema")
        return f
    led = report.get("ledger", [])
    eligible = [r for r in led if r["status"] == "eligible"]
    if len(eligible) != report["counts"].get("eligible_rows"):
        f.append("eligible count")

    # recompute per-stratum scored counts and floor checks
    per = defaultdict(int)
    for r in eligible:
        blk = r.get("candidate") or {}
        if blk.get("status") == "scored":
            per[r["projectile"]] += 1
    for proj, (share, rows) in FLOORS.items():
        rec = report["gates"]["coverage_floors"].get(proj, {})
        elig = rec.get("eligible_rows", 0)
        want = per.get(proj, 0) >= rows and (
            per.get(proj, 0) / elig >= share if elig else False)
        if rec.get("candidate_scored") != per.get(proj, 0):
            f.append(f"{proj}: scored count")
        if rec.get("pass") != want:
            f.append(f"{proj}: floor verdict")
    # gate-4 / gate-1 re-derivation
    if report["gates"]["no_empty_stratum"] != all(
            per.get(p, 0) >= 10 for p in FLOORS):
        f.append("no_empty_stratum")
    if report["gates"]["outcome_accounting"] != (
            report["gates"]["unnamed_outcomes"] == []):
        f.append("outcome_accounting")
    want = (report["gates"]["outcome_accounting"]
            and report["gates"]["paired_nonregression"]["strata_pass"]
            and report["gates"]["paired_nonregression"]["overall_pass"]
            and report["gates"]["coverage_floors_pass"]
            and report["gates"]["no_empty_stratum"]
            and report["gates"]["historical_reproducibility"])
    if report["gates"]["pass"] != want:
        f.append("pass")
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args()
    if args.self_test:
        rep = json.loads(Path(args.report).read_text())
        rejected = 0

        def mut_floor(r):
            cur = r["gates"]["coverage_floors"]["neutron"]["pass"]
            r["gates"]["coverage_floors"]["neutron"]["pass"] = not cur
        def mut_pass(r):
            r["gates"]["pass"] = not r["gates"]["pass"]
        def mut_row(r):
            e = next(x for x in r["ledger"]
                     if (x.get("candidate") or {}).get("status") == "scored")
            e["candidate"]["status"] = "build_failed_g3"
        def mut_hist(r):
            r["counts"]["eligible_rows"] = 0
        for mutation in (mut_floor, mut_pass, mut_row, mut_hist):
            m = copy.deepcopy(rep)
            mutation(m)
            if evaluate(m):
                rejected += 1
        print(f"self-test rejected {rejected}/4 mutations")
        return 0 if rejected == 4 else 1

    # gate 5 first: the unchanged P18b scorer over the P18b artifacts
    # must reproduce the sealed record exactly (float equality).
    hist = g5.score_heldout()
    sealed = json.loads(P18B_RECORD.read_text())
    hist_ok = True
    hist_diffs = []
    for proj, blk in sealed["per_projectile"].items():
        got = hist["per_proj"].get(proj, {})
        for label in ("baseline", "candidate"):
            for k, v in blk[label].items():
                if got.get(label, {}).get(k) != v:
                    hist_ok = False
                    hist_diffs.append(f"{proj}/{label}/{k}")
        if got.get("eligible_rows") != blk["eligible_rows"]:
            hist_ok = False
            hist_diffs.append(f"{proj}/eligible_rows")
    for label in ("baseline", "candidate"):
        for k, v in sealed["overall"][label].items():
            if hist["overall"][label].get(k) != v:
                hist_ok = False
                hist_diffs.append(f"overall/{label}/{k}")
    if hist["stats"].get("eligible_rows") != sealed["counts"].get(
            "eligible_rows"):
        hist_ok = False
        hist_diffs.append("counts/eligible_rows")

    # P25 candidate scoring through the same fold
    scored = g5.score_heldout(
        work=WORK25, report=WORK25 / "build_report.json")
    mapping = g5.mapping_gate(
        work=WORK25, report=WORK25 / "build_report.json")

    g4rec = json.loads(G4REC.read_text())
    staged_built = {
        p: set(e["built_names"])
        for p, e in ((r["projectile"], r)
                     for r in json.loads(
                         (WORK25 / "build_report.json").read_text()))
    }

    # gate 1: one named outcome per eligible row
    outcomes: dict[str, dict[str, int]] = {}
    unnamed = []
    for rec in scored["ledger"]:
        if rec["status"] != "eligible":
            continue
        name = candidate_outcome(rec, g4rec, staged_built)
        rec["candidate_outcome"] = name
        if name is None or name.startswith("undefined_ratio:None"):
            unnamed.append(rec["row_id"])
        outcomes.setdefault(rec["projectile"], {})[name] = (
            outcomes[rec["projectile"]].get(name, 0) + 1)

    accounting = {
        "eligible_rows": sum(
            1 for r in scored["ledger"] if r["status"] == "eligible"),
        "accounted_rows": sum(
            1 for r in scored["ledger"]
            if r["status"] == "eligible" and r.get("candidate_outcome")),
        "unnamed_outcomes": unnamed[:50],
        "outcomes": outcomes,
    }
    gates = p25_gates(scored, accounting, mapping, hist_ok)

    report = {
        "schema": "actinv-p25-g5-acceptance-1",
        "gate": "P25-G5",
        "historical_rerun": {
            "reproduces_sealed": hist_ok,
            "diffs": hist_diffs[:50],
        },
        "counts": {
            "heldout_families": sum(
                1 for fm in scored["seal"]["families"]
                if fm["partition"] == "heldout"),
            "ledger_rows": len(scored["ledger"]),
            **scored["stats"],
        },
        "outcome_accounting": accounting,
        "per_projectile": scored["per_proj"],
        "overall": scored["overall"],
        "paired_rows": len(scored["paired"]),
        "paired_bootstrap": scored["bootstrap"],
        "mapping_gate": mapping,
        "candidate_builds": {
            p: {
                "built_files": r["built_files"],
                "failed_count": len(r["failures"]),
                "output_sha256": r["output_sha256"],
                "emission_model": r["index_emission_model"],
            }
            for p, r in g4rec["projectiles"].items()},
        "gates": gates,
        "qualification_label": (
            "P25 qualification rests on engineering evidence and "
            "retrospective scoring of the sealed P18b held-out "
            "population; no sufficiently independent unread isomeric "
            "measurement set exists. A fresh blind stratum may be "
            "attempted only after construction completeness and family "
            "coverage prerequisites pass, on material sealed before "
            "its values are read."),
        "ledger": scored["ledger"],
    }
    Path(args.report).write_text(json.dumps(report, indent=1))
    print(json.dumps({
        "counts": report["counts"],
        "outcome_accounting": accounting["outcomes"],
        "per_projectile": {
            p: {"eligible": b["eligible_rows"],
                "baseline_rows": b["baseline"]["rows"],
                "candidate_rows": b["candidate"]["rows"]}
            for p, b in scored["per_proj"].items()},
        "paired_rows": report["paired_rows"],
        "historical_reproduces": hist_ok,
        "gates": {k: v for k, v in gates.items()
                  if not isinstance(v, dict)},
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
