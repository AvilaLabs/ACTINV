#!/usr/bin/env python3
"""P46 G1 mechanics — the frozen development subset (10 experiments)
scored through the sealed pipeline on all 5 corpora; per-corpus
accounting complete; expressibility labels assigned. Emits
results/g1_p46_mechanics.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p46_corpora as p46c  # noqa: E402

OUT = ROOT / "results/g1_p46_mechanics.json"
LEDGER = ROOT / "results/p46_dev_ledger.jsonl"
TABLES = ROOT / "results/p46_dev_tables.json"


def check(name, ok, detail=None):
    d = {"probe": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def main() -> int:
    t0 = time.monotonic()
    checks = []

    rc = subprocess.run(
        [sys.executable, str(ROOT / "controls/p46_corpora.py"),
         "--partition", "development",
         "--out", str(LEDGER)], capture_output=True, text=True)
    checks.append(check("development_run", rc.returncode == 0,
                        (rc.stdout or "")[-200:] +
                        (rc.stderr or "")[-200:]))

    rc = subprocess.run(
        [sys.executable, str(ROOT / "controls/p46_score.py"),
         "--ledger", str(LEDGER), "--out", str(TABLES)],
        capture_output=True, text=True)
    checks.append(check("development_scoring", rc.returncode == 0,
                        (rc.stdout or "")[-200:] +
                        (rc.stderr or "")[-200:]))
    t = json.loads(TABLES.read_text())

    # every corpus executed every development experiment
    acct = t["corpus_accounting"]
    checks.append(check(
        "accounting_complete",
        all(a["executed"] == 10 and a["failed"] == 0
            for a in acct.values()),
        {c: {"executed": a["executed"], "failed": a["failed"]}
         for c, a in acct.items()}))

    # expressibility labels present per corpus
    checks.append(check(
        "expressibility_assigned",
        all(a["expressibility"] and a["provenance"]
            for a in acct.values()),
        {c: a["expressibility"] for c, a in acct.items()}))

    # per-point outcomes are from the frozen vocabulary
    vocab = {"scored", "uncovered", "failed", "unpopulated"}
    bad = [p for p in t["points"] if p["outcome"] not in vocab]
    checks.append(check("outcome_vocabulary", not bad,
                        bad[:3]))

    # every recommendation carries its evidence row
    ev_bad = [m for m, r in t["recommendations"].items()
              if not (r.get("evidence") or {}).get("scorer_sha256")]
    checks.append(check("evidence_rows", not ev_bad, ev_bad[:3]))

    checks.append(check(
        "g1_complete",
        all(c["pass"] for c in checks), None))
    out = {"spec": "actinv-p46-g1-1",
           "probes": checks,
           "all_pass": all(c["pass"] for c in checks),
           "wall_s": time.monotonic() - t0}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "probes": {c["probe"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
