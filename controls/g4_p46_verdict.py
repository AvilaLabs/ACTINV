#!/usr/bin/env python3
"""P46 G4 verdict producer — assembles the final evaluation-intelligence
report from the sealed tables + campaign record.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/verdict_p46.json"
TABLES = ROOT / "results/p46_eval_tables.json"
CAMPAIGN = ROOT / "results/g3_p46_campaign.json"
SEAL = ROOT / "results/g0_p46_seals.json"


def main() -> int:
    t = json.loads(TABLES.read_text())
    camp = json.loads(CAMPAIGN.read_text())
    seal = json.loads(SEAL.read_text())
    acct = t["corpus_accounting"]

    # pooled headline per corpus: pooled median |ln C/E| across all
    # scored points, plus coverage ratios
    pooled = {}
    points = t["points"]
    for c in acct:
        ces = [p["ce"] for p in points
               if p["corpus"] == c and p["outcome"] == "scored"]
        n = len(ces)
        import math
        pooled[c] = {
            "n_scored": n,
            "n_uncovered_points": sum(
                1 for p in points
                if p["corpus"] == c and p["outcome"] == "uncovered"),
            "median_ce": sorted(ces)[n // 2] if n else None,
            "mean_abs_ln_ce": (sum(abs(math.log(x)) for x in ces) / n
                               if n else None),
            "experiments_executed": acct[c]["executed"],
            "experiments_uncovered": acct[c]["uncovered_experiments"],
            "elements_covered": acct[c]["elements_covered"]}

    verdict = {
        "schema": "actinv-p46-verdict-1",
        "phase": "P46",
        "produced_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime()),
        "seal_commit": seal.get("opening_commit"),
        "campaign": {"wall_s": camp.get("wall_s"),
                     "within_envelope": camp.get("within_envelope"),
                     "records": camp.get("records"),
                     "executed": camp.get("executed"),
                     "failed": camp.get("failed")},
        "corpus_pooled": pooled,
        "material_tables": t["material_tables"],
        "family_tables": t["family_tables"],
        "recommendations": t["recommendations"],
        "corpus_accounting": acct,
        "irdff_arm": "unmeasured",
        "disposition": {
            "verdict": "P46-CONDITIONAL",
            "basis": ("IRDFF-II SACS arm declared unmeasured at "
                      "freeze (folded-XS scoring unit, machinery not "
                      "executable this phase); FNS arm fully scored "
                      "on all five admitted corpora; recommendations "
                      "all carry evidence rows; corpus builder "
                      "provenance is declared per artifact, not a "
                      "single revision")},
    }
    OUT.write_text(json.dumps(verdict, indent=1))
    print(json.dumps({
        "verdict": verdict["disposition"]["verdict"],
        "recommendations": len(t["recommendations"]),
        "pooled": {c: {"n_scored": v["n_scored"],
                       "mean_abs_ln_ce": v["mean_abs_ln_ce"]}
                   for c, v in pooled.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
