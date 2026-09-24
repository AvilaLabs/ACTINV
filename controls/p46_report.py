#!/usr/bin/env python3
"""P46 report builder — machine-readable tables are already emitted by
p46_score.py; this renders the docs recommendation page. Every
recommendation row carries its evidence record; construction refuses a
recommendation missing any evidence field (protocol requirement).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "results/p46_eval_tables.json"
DOC = ROOT / "docs/P46_EVALUATION_RECOMMENDATIONS.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, default=TABLES)
    ap.add_argument("--doc", type=Path, default=DOC)
    args = ap.parse_args()
    t = json.loads(args.tables.read_text())
    recs = t["recommendations"]
    acct = t["corpus_accounting"]
    fam = t["family_tables"]
    mat = t["material_tables"]

    # construction-time evidence check (protocol refusal rule)
    for m, r in recs.items():
        ev = r.get("evidence") or {}
        for k in ("scorer_sha256", "ledger_sha256", "corpora",
                  "n_scored", "median_ce"):
            if not ev.get(k):
                raise SystemExit(
                    f"recommendation {m} lacks evidence.{k} — refused")
        for c in r["recommended"]:
            if c not in acct or not acct[c].get("sha256"):
                raise SystemExit(
                    f"recommendation {m} cites unaccounted corpus {c}")

    lines = []
    A = lines.append
    A("# ACTINV evaluation recommendations (P46)")
    A("")
    A(f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} "
      f"from `results/p46_eval_tables.json` "
      f"(ledger sha `{t['ledger_sha256'][:16]}…`, "
      f"scorer sha `{t['scorer_sha256'][:16]}…`).")
    A("")
    A("Every recommendation is computed on the identical "
      "spec→solve→C/E pipeline against the 132-experiment CoNDERC FNS "
      "decay-heat suite with decay data held fixed — the comparison "
      "isolates the activation cross-section corpus. Measurements are "
      "consumed C/E evidence (read before the protocol freeze; the "
      "scorer and partition froze before these scores were computed).")
    A("")
    A("## Corpus accounting")
    A("")
    A("| corpus | provenance | targets-covered elements | executed |"
      " failed | uncovered experiments | labels |")
    A("|--------|-----------|--------------------------|----------|"
      "---------|-----------------------|--------|")
    for c, a in sorted(acct.items()):
        A(f"| {c} | {a['provenance']} | {a['elements_covered']} |"
          f" {a['executed']} | {a['failed']} |"
          f" {a['uncovered_experiments']} |"
          f" {', '.join(a['expressibility'])} |")
    A("")
    A("## Per-family scores (median C/E, mean |ln C/E|, "
      "fraction within ±20%)")
    A("")
    A("| corpus | family | n_scored | median C/E | mean |ln C/E| |"
      " within ±20% | within 2× |")
    A("|--------|--------|----------|------------|--------------|"
      "---------------|------------|")
    for k, v in sorted(fam.items()):
        if not v["n_scored"]:
            continue
        c, f = k.split("|", 1)
        A(f"| {c} | {f} | {v['n_scored']} |"
          f" {v['median_ce']:.3f} | {v['mean_abs_ln_ce']:.3f} |"
          f" {v['frac_within_20']:.0%} | {v['frac_within_2x']:.0%} |")
    A("")
    A("## Recommendations (per material, ≥3 scored points, "
      "≥2 corpora)")
    A("")
    A("| material | recommended | mean |ln C/E| | n_scored |"
      " median C/E |")
    A("|----------|------------|--------------|----------|"
      "------------|")
    for m, r in sorted(recs.items()):
        for c in r["recommended"]:
            ev = r["evidence"]
            A(f"| {m} | {c} |"
              f" {r['candidates'][c]['mean_abs_ln_ce']:.3f} |"
              f" {ev['n_scored'][c]} | {ev['median_ce'][c]:.3f} |")
    A("")
    A("A recommendation is refused at construction unless it carries "
      "its corpus identity, scorer sha, ledger sha, n_scored and "
      "median C/E — the evidence fields above are the row it stands "
      "on.")
    A("")
    args.doc.write_text("\n".join(lines))
    print(json.dumps({"doc": str(args.doc),
                      "recommendations": len(recs)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
