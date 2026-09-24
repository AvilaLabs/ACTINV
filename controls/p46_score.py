#!/usr/bin/env python3
"""P46 frozen scorer — per-point C/E outcomes, score tables,
recommendation surface.

Frozen scoring rules (ACTINV-P46_PROTOCOL.md):
- alignment: frozen P44 align() — unit inference over {s, min, h, d,
  y}, nearest cooling step within max(2%, 1 s); nonpositive/zero
  measured rows excluded with named reasons;
- scored: computed heat > 0 -> C/E = computed / measured;
- uncovered: an experiment whose composition elements carry no targets
  in the corpus index is `uncovered` (counted, never scored zero);
- failed: executor failure; counted;
- tables: per (corpus, material) and per (corpus, family) — n_measured,
  n_scored, n_uncovered, n_failed, median C/E, mean |ln C/E|, fraction
  within [0.8, 1.25] and within 2x;
- family: pure_element (one composition element >= 99 wt%) vs
  alloy_composition — derived from parsed composition, not hand-lists;
- recommendation: per material with >= 3 scored points on >= 2 corpora,
  recommended corpus minimizes median |ln C/E| (ties within 1e-6 list
  all); every recommendation carries its evidence row — refusal at
  construction otherwise.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_band_coverage as p44bc  # noqa: E402  frozen align()
import p44_bands  # noqa: E402
import p46_corpora as p46c  # noqa: E402
from harness import fispact_io as fio  # noqa: E402

LEDGER = ROOT / "results/p46_run_ledger.jsonl"
OUT = ROOT / "results/p46_eval_tables.json"
SCHEMA = "actinv-p46-eval-1"
WITHIN20 = (0.8, 1.25)
WITHIN2X = (0.5, 2.0)
MIN_POINTS_FOR_REC = 3
MIN_CORPORA_FOR_REC = 2
TIE_TOL = 1e-6


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def corpus_elements(index_path: Path) -> set[int]:
    """element Z numbers with >= 1 target in the corpus index."""
    j = json.loads(index_path.read_text())
    out = set()
    for t in j.get("targets") or []:
        za = t.get("za")
        if za:
            out.add(int(za) // 1000)
    return out


INDEX_PATHS = {
    "tendl-2025": p46c.DATA / "tendl-2025/builds/full/"
                  "neutron.n.p10_index.json",
    "tendl-2025-patched": p46c.DATA / "tendl-2025-patched/build/"
                          "neutron.n.p10_index.json",
    "tendl-2017": p46c.DATA / "tendl-2017/build/"
                  "neutron.n.p10_index.json",
    "eaf-2010": p46c.DATA / "eaf-2010/"
                "actinv_eaf2010_709g_index.json",
    "fendl-3.2c": p46c.DATA / "p26b-work/g1-run/"
                  "actinv_fendl32c_709_index.json",
}

ELEMENT_Z = {
    "H": 1, "HE": 2, "LI": 3, "BE": 4, "B": 5, "C": 6, "N": 7, "O": 8,
    "F": 9, "NE": 10, "NA": 11, "MG": 12, "AL": 13, "SI": 14, "P": 15,
    "S": 16, "CL": 17, "AR": 18, "K": 19, "CA": 20, "SC": 21, "TI": 22,
    "V": 23, "CR": 24, "MN": 25, "FE": 26, "CO": 27, "NI": 28, "CU": 29,
    "ZN": 30, "GA": 31, "GE": 32, "AS": 33, "SE": 34, "BR": 35, "KR": 36,
    "RB": 37, "SR": 38, "Y": 39, "ZR": 40, "NB": 41, "MO": 42, "TC": 43,
    "RU": 44, "RH": 45, "PD": 46, "AG": 47, "CD": 48, "IN": 49, "SN": 50,
    "SB": 51, "TE": 52, "I": 53, "XE": 54, "CS": 55, "BA": 56, "LA": 57,
    "CE": 58, "PR": 59, "ND": 60, "PM": 61, "SM": 62, "EU": 63, "GD": 64,
    "TB": 65, "DY": 66, "HO": 67, "ER": 68, "TM": 69, "YB": 70, "LU": 71,
    "HF": 72, "TA": 73, "W": 74, "RE": 75, "OS": 76, "IR": 77, "PT": 78,
    "AU": 79, "HG": 80, "TL": 81, "PB": 82, "BI": 83,
}


def composition_family(composition: dict) -> tuple[str, set[int]]:
    """(family, element-Z set) from the experiment's composition."""
    els = {ELEMENT_Z[k]: v for k, v in composition.items()
           if k in ELEMENT_Z and v > 0}
    if not els:
        return "alloy_composition", set()
    top = max(els.values())
    total = sum(els.values())
    if len(els) == 1 or top / total >= 0.99:
        return "pure_element", set(els)
    return "alloy_composition", set(els)


def measured_points(material: str, experiment: str):
    """aligned measured points via the frozen P44 machinery."""
    d = p44_bands.FNS / material
    meas = fio.read_exp(d / f"{experiment}.exp")
    inp = fio.read_i(d / f"TENDL-2017_{experiment}.i")
    cooling = [float(t) for t in inp["cooling_cum_s"]]
    matched, excluded, unit = p44bc.align(
        cooling, list(meas["t_raw"]), list(meas["heat_uW_g"]))
    return meas, cooling, matched, excluded, unit


def computed_at(heat: list[dict], t_s: float) -> float | None:
    """computed heat at a cooling step (exact t match within 1e-6
    relative); None if absent."""
    for h in heat:
        if abs(h["t_s"] - t_s) <= max(1e-6 * t_s, 1e-9):
            return h["heat_w_per_g"]
    return None


def score_ledger(rows: list[dict], corpus_elem_sets: dict) -> dict:
    """Score ledger rows -> per-point outcomes + tables + recs."""
    # latest row per (corpus, experiment) — appended ledgers may carry
    # duplicate rows from re-runs
    latest = {}
    for r in rows:
        latest[(r["corpus"], r["material"], r["experiment"])] = r
    rows = list(latest.values())

    meas_cache = {}
    points = []
    per_cm = defaultdict(lambda: {"n_measured": 0, "n_scored": 0,
                                  "n_uncovered": 0, "n_failed": 0,
                                  "n_unpopulated": 0, "n_excluded": 0,
                                  "ces": []})
    per_cf = defaultdict(lambda: {"n_measured": 0, "n_scored": 0,
                                  "n_uncovered": 0, "n_failed": 0,
                                  "n_unpopulated": 0, "n_excluded": 0,
                                  "ces": []})
    families = {}
    for r in rows:
        corpus, material, exp = r["corpus"], r["material"], \
            r["experiment"]
        eid = f"{material}/{exp}"
        key = (corpus, material)
        if eid not in meas_cache:
            inp = fio.read_i(p44_bands.FNS / material /
                             f"TENDL-2017_{exp}.i")
            fam, els = composition_family(inp["elements"])
            meas, cooling, matched, excluded, unit = \
                measured_points(material, exp)
            meas_cache[eid] = {
                "meas": meas, "cooling": cooling,
                "matched": matched, "excluded": excluded,
                "unit": unit,
                "family": fam, "elements": els}
            families[material] = fam
        mc = meas_cache[eid]
        fam = mc["family"]
        famkey = (corpus, fam)

        # uncovered: no composition element has a corpus target
        covered_els = mc["elements"] & corpus_elem_sets.get(corpus, set())
        if not covered_els:
            per_cm[key]["n_uncovered"] += len(mc["meas"]["heat_uW_g"])
            per_cf[famkey]["n_uncovered"] += \
                len(mc["meas"]["heat_uW_g"])
            points.append({"corpus": corpus, "experiment": eid,
                           "outcome": "uncovered"})
            continue
        if r.get("status") != "executed":
            per_cm[key]["n_failed"] += 1
            per_cf[famkey]["n_failed"] += 1
            points.append({"corpus": corpus, "experiment": eid,
                           "outcome": "failed",
                           "error": r.get("error")})
            continue
        heat = r.get("heat") or []
        per_cm[key]["n_measured"] += len(mc["meas"]["heat_uW_g"])
        per_cf[famkey]["n_measured"] += len(mc["meas"]["heat_uW_g"])
        per_cm[key]["n_excluded"] += len(mc["excluded"])
        per_cf[famkey]["n_excluded"] += len(mc["excluded"])
        for (row_i, step_i) in mc["matched"]:
            m_w = mc["meas"]["heat_uW_g"][row_i] * 1e-6
            c_w = computed_at(heat, mc["cooling"][step_i])
            if c_w is not None and c_w > 0:
                ce = float(c_w) / float(m_w)
                per_cm[key]["n_scored"] += 1
                per_cm[key]["ces"].append(ce)
                per_cf[famkey]["n_scored"] += 1
                per_cf[famkey]["ces"].append(ce)
                points.append({"corpus": corpus, "experiment": eid,
                               "t_s": mc["cooling"][step_i],
                               "outcome": "scored", "ce": ce})
            else:
                per_cm[key]["n_unpopulated"] += 1
                per_cf[famkey]["n_unpopulated"] += 1
                points.append({"corpus": corpus, "experiment": eid,
                               "t_s": mc["cooling"][step_i],
                               "outcome": "unpopulated"})

    def agg(g):
        ces = [x for x in g["ces"] if x is not None]
        n = len(ces)
        return {"n_measured": g["n_measured"],
                "n_scored": n,
                "n_uncovered": g["n_uncovered"],
                "n_failed": g["n_failed"],
                "n_unpopulated": g["n_unpopulated"],
                "n_excluded": g["n_excluded"],
                "median_ce": sorted(ces)[n // 2] if n else None,
                "mean_abs_ln_ce": (sum(abs(math.log(c)) for c in ces) / n
                                   if n else None),
                "frac_within_20": (sum(1 for c in ces
                                       if WITHIN20[0] <= c <= WITHIN20[1])
                                   / n if n else None),
                "frac_within_2x": (sum(1 for c in ces
                                       if WITHIN2X[0] <= c <= WITHIN2X[1])
                                   / n if n else None)}

    material_tables = {f"{c}|{m}": agg(g)
                       for (c, m), g in sorted(per_cm.items())}
    family_tables = {f"{c}|{f}": agg(g)
                     for (c, f), g in sorted(per_cf.items())}
    return {"points": points, "material_tables": material_tables,
            "family_tables": family_tables, "families": families}


def build_recommendations(points: list[dict], material_tables: dict,
                          scorer_sha: str, ledger_sha: str,
                          corpus_meta: dict) -> dict:
    """recommendation rows per material + per family."""
    # per-material ranked candidates
    recs = {}
    by_mat = defaultdict(dict)
    for k, t in material_tables.items():
        c, m = k.split("|", 1)
        if t["n_scored"] >= MIN_POINTS_FOR_REC and \
                t["mean_abs_ln_ce"] is not None:
            by_mat[m][c] = t
    for m, cand in by_mat.items():
        if len(cand) < MIN_CORPORA_FOR_REC:
            continue
        best = min(v["mean_abs_ln_ce"] for v in cand.values())
        winners = sorted(c for c, v in cand.items()
                         if abs(v["mean_abs_ln_ce"] - best) <= TIE_TOL)
        recs[m] = {"recommended": winners,
                   "metric": "mean_abs_ln_ce",
                   "candidates": {c: {"n_scored": v["n_scored"],
                                      "median_ce": v["median_ce"],
                                      "mean_abs_ln_ce":
                                          v["mean_abs_ln_ce"]}
                                  for c, v in sorted(cand.items())}}
    return recs


def recommendation_row(material: str, rec: dict, scorer_sha: str,
                       ledger_sha: str, corpus_meta: dict) -> dict:
    """An evidence-carrying row; construction refuses missing fields."""
    row = {"material": material,
           "recommended": rec["recommended"],
           "metric": rec["metric"],
           "evidence": {"scorer_sha256": scorer_sha,
                        "ledger_sha256": ledger_sha,
                        "corpora": {c: corpus_meta[c]["sha256"]
                                    for c in rec["recommended"]},
                        "n_scored": {c: rec["candidates"][c]["n_scored"]
                                     for c in rec["recommended"]},
                        "median_ce": {c: rec["candidates"][c]["median_ce"]
                                      for c in rec["recommended"]},
                        "mean_abs_ln_ce": {
                            c: rec["candidates"][c]["mean_abs_ln_ce"]
                            for c in rec["recommended"]}}}
    for k in ("scorer_sha256", "ledger_sha256", "corpora",
              "n_scored", "median_ce", "mean_abs_ln_ce"):
        if not row["evidence"].get(k):
            raise ValueError(
                f"recommendation for {material} lacks evidence.{k}")
    return row


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    raw = [json.loads(l) for l in
           args.ledger.read_text().splitlines() if l.strip()]
    latest = {}
    for r in raw:
        latest[(r["corpus"], r["material"], r["experiment"])] = r
    rows = list(latest.values())
    elem_sets = {c: corpus_elements(p)
                 for c, p in INDEX_PATHS.items()}
    scored = score_ledger(rows, elem_sets)
    meta = {c: p46c.corpus_meta(c) for c in p46c.CORPORA}
    recs = build_recommendations(scored["points"],
                                 scored["material_tables"],
                                 sha256(Path(__file__)),
                                 sha256(args.ledger), meta)
    rec_rows = {m: recommendation_row(m, r, sha256(Path(__file__)),
                                      sha256(args.ledger), meta)
                for m, r in recs.items()}

    # per-corpus accounting
    accounting = {}
    for c in p46c.CORPORA:
        cr = [r for r in rows if r["corpus"] == c]
        uncovered_exps = {p["experiment"] for p in scored["points"]
                          if p["corpus"] == c
                          and p["outcome"] == "uncovered"}
        accounting[c] = {
            "eligible_experiments": 132,
            "executed": sum(1 for r in cr
                            if r.get("status") == "executed"),
            "failed": sum(1 for r in cr
                          if r.get("status") == "failed"),
            "uncovered_experiments": len(uncovered_exps),
            "provenance": meta[c]["provenance"],
            "expressibility": meta[c]["expressibility"],
            "sha256": meta[c]["sha256"],
            "elements_covered": len(elem_sets[c])}

    out = {"schema": SCHEMA,
           "scorer_sha256": sha256(Path(__file__)),
           "ledger_sha256": sha256(args.ledger),
           "corpus_accounting": accounting,
           "material_tables": scored["material_tables"],
           "family_tables": scored["family_tables"],
           "families": scored["families"],
           "recommendations": rec_rows,
           "points": scored["points"]}
    args.out.write_text(json.dumps(out, indent=1))
    print(json.dumps({
        "points": len(scored["points"]),
        "material_cells": len(scored["material_tables"]),
        "recommendations": len(rec_rows),
        "per_corpus": {c: accounting[c]["executed"]
                       for c in accounting}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
