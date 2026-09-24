#!/usr/bin/env python3
"""P46 G4 independent checker — re-derives table cells and accounting
from raw point records + ledger, verifies recommendations carry
evidence, rejects planted mutations. Emits
results/check_g4_p46.json; exits nonzero on any failure.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/check_g4_p46.json"
LEDGER = ROOT / "results/p46_run_ledger.jsonl"
TABLES = ROOT / "results/p46_eval_tables.json"
VERDICT = ROOT / "results/verdict_p46.json"

sys.path.insert(0, str(ROOT / "controls"))
import p46_score as p46s  # noqa: E402


def check(name, ok, detail=None):
    d = {"check": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def mutate(rows, fn):
    m = copy.deepcopy(rows)
    fn(m)
    return m


def main() -> int:
    raw = [json.loads(l) for l in
           LEDGER.read_text().splitlines() if l.strip()]
    latest = {}
    for r in raw:
        latest[(r["corpus"], r["material"], r["experiment"])] = r
    rows = list(latest.values())
    t = json.loads(TABLES.read_text())
    verdict = json.loads(VERDICT.read_text())
    elem_sets = {c: p46s.corpus_elements(p)
                 for c, p in p46s.INDEX_PATHS.items()}
    checks = []

    # c1: coverage — every corpus ran every eligible experiment
    per = {}
    for r in rows:
        per.setdefault(r["corpus"], set()).add(
            f"{r['material']}/{r['experiment']}")
    bad = {c: 132 - len(v) for c, v in per.items() if len(v) != 132}
    checks.append(check("experiment_coverage",
                        not bad and len(per) == 5,
                        {"missing": bad}))

    # c2: re-derive one table cell exactly — pick the cell with the
    # most scored points; recompute median C/E from raw points
    cell_key = max(t["material_tables"],
                   key=lambda k: (t["material_tables"][k]["n_scored"],
                                  k))
    corpus, material = cell_key.split("|", 1)
    ces = sorted(p["ce"] for p in t["points"]
                 if p["corpus"] == corpus
                 and p["experiment"].startswith(f"{material}/")
                 and p["outcome"] == "scored")
    n = len(ces)
    red_med = ces[n // 2] if n else None
    red_ln = sum(abs(math.log(x)) for x in ces) / n if n else None
    pub = t["material_tables"][cell_key]
    checks.append(check(
        "table_cell_rederivation",
        n == pub["n_scored"]
        and red_med == pub["median_ce"]
        and red_ln == pub["mean_abs_ln_ce"],
        {"cell": cell_key, "n": n, "pub": pub["median_ce"],
         "red": red_med}))

    # c3: accounting re-derived — executed/failed/uncovered counts
    acct_ok = True
    for c, a in t["corpus_accounting"].items():
        cr = [r for r in rows if r["corpus"] == c]
        ex = sum(1 for r in cr if r.get("status") == "executed")
        fa = sum(1 for r in cr if r.get("status") == "failed")
        un = len({p["experiment"] for p in t["points"]
                  if p["corpus"] == c
                  and p["outcome"] == "uncovered"})
        if not (a["executed"] == ex and a["failed"] == fa
                and a["uncovered_experiments"] == un
                and a["eligible_experiments"] == 132):
            acct_ok = False
    checks.append(check("accounting_rederivation", acct_ok))

    # c4: every recommendation carries a complete evidence row and
    # cites only corpora present in the tables
    recs_ok = True
    for m, r in t["recommendations"].items():
        ev = r.get("evidence") or {}
        for k in ("scorer_sha256", "ledger_sha256", "corpora",
                  "n_scored", "median_ce"):
            if not ev.get(k):
                recs_ok = False
        for c in r.get("recommended", []):
            if c not in t["corpus_accounting"]:
                recs_ok = False
            if not ev.get("corpora", {}).get(c):
                recs_ok = False
    checks.append(check("recommendation_evidence", recs_ok))

    # c5: recommendation optimality — verify one material's winner
    # really minimizes mean_abs_ln_ce among its candidates
    m = next(iter(t["recommendations"]))
    cand = t["recommendations"][m]["candidates"]
    best = min(v["mean_abs_ln_ce"] for v in cand.values())
    rec_c = t["recommendations"][m]["recommended"]
    checks.append(check(
        "recommendation_optimal",
        all(abs(cand[c]["mean_abs_ln_ce"] - best) <= p46s.TIE_TOL
            for c in rec_c),
        {"material": m, "recommended": rec_c, "best": best}))

    # ---- planted mutations (each must be caught) ----
    # m1: drop all scored points for one corpus/material -> its table
    # cell must go to zero scored (score_ledger re-run)
    def m1(rs):
        for r in rs:
            if r["corpus"] == corpus and r["material"] == material:
                for h in r.get("heat") or []:
                    h["heat_w_per_g"] = 0.0
    sc1 = p46s.score_ledger(mutate(rows, m1), elem_sets)
    c1 = sc1["material_tables"].get(f"{corpus}|{material}") or {}
    checks.append(check(
        "m1_zeroed_heat_detected",
        c1.get("n_scored", 0) == 0
        and c1.get("n_unpopulated", 0) == n,
        {"scored": c1.get("n_scored")}))

    # m2: delete a corpus's rows -> its accounting cell must not
    # appear and its points must vanish
    sc2 = p46s.score_ledger(
        [r for r in rows if r["corpus"] != corpus], elem_sets)
    checks.append(check(
        "m2_deleted_corpus_detected",
        all(p["corpus"] != corpus for p in sc2["points"])
        and f"{corpus}|{material}" not in sc2["material_tables"],
        None))

    # m3: fabricate a recommendation without evidence -> construction
    # must refuse
    try:
        p46s.recommendation_row(
            material,
            {"recommended": ["ghost"], "metric": "x",
             "candidates": {"ghost": {"n_scored": 9, "median_ce": 1.0,
                                      "mean_abs_ln_ce": 0.0}}},
            "", "", {})
        refused = False
    except (ValueError, KeyError):
        refused = True
    checks.append(check("m3_evidence_refusal", refused))

    # m4: shrink a corpus's heat 100x -> its C/Es must move the table
    def m4(rs):
        for r in rs:
            if r["corpus"] == corpus:
                for h in r.get("heat") or []:
                    if h["heat_w_per_g"]:
                        h["heat_w_per_g"] /= 100.0
    sc4 = p46s.score_ledger(mutate(rows, m4), elem_sets)
    c4 = sc4["material_tables"].get(f"{corpus}|{material}") or {}
    checks.append(check(
        "m4_scaled_values_detected",
        c4.get("median_ce") is not None
        and abs(c4["median_ce"] - pub["median_ce"] / 100.0)
        < pub["median_ce"] * 0.01,
        {"pub": pub["median_ce"], "mut": c4.get("median_ce")}))

    # m5: envelope honored
    checks.append(check(
        "envelope",
        (verdict["campaign"].get("within_envelope") is True)
        and (verdict["campaign"].get("wall_s") or 1e18)
        < 90 * 60,
        {"wall_s": verdict["campaign"].get("wall_s")}))

    out = {"spec": "actinv-p46-g4-check-1",
           "checks": checks,
           "all_pass": all(c["pass"] for c in checks),
           "n_checks": len(checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "checks": {c["check"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
