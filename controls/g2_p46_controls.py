#!/usr/bin/env python3
"""P46 G2 frozen controls — synthetic-corpus fixtures re-derive table
cells; planted corpus-content changes shift eligible counts not
scores; evidence-less recommendations refused; alignment controls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p46_score as p46s  # noqa: E402
import p46_corpora as p46c  # noqa: E402

OUT = ROOT / "results/g2_p46_controls.json"


def check(name, ok, detail=None):
    d = {"control": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def synth_row(corpus, material, exp, heat, status="executed"):
    """heat: list of {t_s, heat_w_per_g} dicts (produce() shape)."""
    return {"corpus": corpus, "material": material,
            "experiment": exp, "status": status, "heat": heat}


def main() -> int:
    checks = []

    # c1: synthetic C/E set re-derives every table cell — plant an
    # exact experiment (Fe/1996exp_5min) with known computed heat
    # at known cooling steps, two corpora with known C/E
    _, _, matched, _excluded, _ = p46s.measured_points("Fe",
                                                     "1996exp_5min")
    cooling = p46s.fio.read_i(
        p46s.p44_bands.FNS / "Fe" /
        "TENDL-2017_1996exp_5min.i")["cooling_cum_s"]
    meas = p46s.fio.read_exp(
        p46s.p44_bands.FNS / "Fe" / "1996exp_5min.exp")
    heat_a = [{"t_s": float(cooling[s]),
               "heat_w_per_g":
                float(meas["heat_uW_g"][r]) * 1e-6 * 1.0}
              for r, s in matched]   # C/E exactly 1.0
    heat_b = [{"t_s": h["t_s"],
               "heat_w_per_g": h["heat_w_per_g"] * 2.0}
              for h in heat_a]       # C/E exactly 2.0
    rows = [synth_row("tendl-2017", "Fe", "1996exp_5min", heat_a),
            synth_row("eaf-2010", "Fe", "1996exp_5min", heat_b)]
    elem_sets = {"tendl-2017": {26}, "eaf-2010": {26},
                 "tendl-2025": set(), "tendl-2025-patched": set(),
                 "fendl-3.2c": set()}
    sc = p46s.score_ledger(rows, elem_sets)
    ta = sc["material_tables"]["tendl-2017|Fe"]
    tb = sc["material_tables"]["eaf-2010|Fe"]
    checks.append(check(
        "exact_ce_rederivation",
        abs(ta["median_ce"] - 1.0) < 1e-9
        and abs(tb["median_ce"] - 2.0) < 1e-9
        and ta["n_scored"] == len(matched)
        and tb["frac_within_2x"] == 1.0
        and tb["frac_within_20"] == 0.0,
        {"a": ta["median_ce"], "b": tb["median_ce"]}))

    # c2: uncovered accounting — corpus with no composition elements
    rows2 = [synth_row("fendl-3.2c", "Ag", "2000exp_5min", [])]
    elem2 = {"fendl-3.2c": {26, 29}}   # Fe, Cu only -> Ag uncovered
    sc2 = p46s.score_ledger(rows2, elem2)
    pts = sc2["points"]
    checks.append(check(
        "uncovered_semantics",
        all(p["outcome"] == "uncovered" for p in pts)
        and sc2["material_tables"]["fendl-3.2c|Ag"]["n_uncovered"] > 0
        and sc2["material_tables"]["fendl-3.2c|Ag"]["n_scored"] == 0,
        {"points": pts[:2],
         "cell": sc2["material_tables"]["fendl-3.2c|Ag"]}))

    # c3: failed accounting — executor failure counts, never scores
    rows3 = [{"corpus": "tendl-2017", "material": "Fe",
              "experiment": "1996exp_5min", "status": "failed",
              "error": "boom"}]
    sc3 = p46s.score_ledger(rows3, {"tendl-2017": {26}})
    tf = sc3["material_tables"]["tendl-2017|Fe"]
    checks.append(check("failed_semantics",
                        tf["n_failed"] == 1 and tf["n_scored"] == 0,
                        tf))

    # c4: recommendation refusal — a rec row missing evidence raises
    try:
        p46s.recommendation_row(
            "Fe", {"recommended": ["x"], "metric": "m",
                   "candidates": {"x": {"n_scored": 5,
                                        "median_ce": 1.0,
                                        "mean_abs_ln_ce": 0.1}}},
            "", "", {"x": {"sha256": "abc"}})
        refused = False
    except ValueError:
        refused = True
    checks.append(check("evidence_refusal", refused))

    # c5: planted corpus-content change -> uncovered experiment shifts
    # the eligible/uncovered count, not any score
    rows5 = [synth_row("eaf-2010", "Fe", "1996exp_5min", heat_a)]
    sc5a = p46s.score_ledger(rows5, {"eaf-2010": {26}})
    sc5b = p46s.score_ledger(rows5, {"eaf-2010": {29}})  # Fe removed
    checks.append(check(
        "coverage_shift_not_score",
        sc5a["material_tables"]["eaf-2010|Fe"]["n_scored"] > 0
        and sc5b["material_tables"]["eaf-2010|Fe"]["n_uncovered"] > 0
        and sc5b["material_tables"]["eaf-2010|Fe"]["n_scored"] == 0
        and all(p["outcome"] == "uncovered"
                for p in sc5b["points"]),
        None))

    # c6: family derivation — pure element vs alloy from composition
    f1, _ = p46s.composition_family({"FE": 100.0})
    f2, _ = p46s.composition_family({"FE": 64.0, "CR": 18.0,
                                     "NI": 12.0, "MO": 2.5})
    f3, _ = p46s.composition_family({"FE": 99.2, "MN": 0.8})
    checks.append(check("family_derivation",
                        f1 == "pure_element"
                        and f2 == "alloy_composition"
                        and f3 == "pure_element",
                        {"f1": f1, "f2": f2, "f3": f3}))

    # c7: unpopulated outcome — executed but zero computed heat
    heat0 = [{"t_s": h["t_s"], "heat_w_per_g": 0.0} for h in heat_a]
    rows7 = [synth_row("eaf-2010", "Fe", "1996exp_5min", heat0)]
    sc7 = p46s.score_ledger(rows7, {"eaf-2010": {26}})
    t7 = sc7["material_tables"]["eaf-2010|Fe"]
    checks.append(check("unpopulated_semantics",
                        t7["n_unpopulated"] == len(matched)
                        and t7["n_scored"] == 0,
                        t7["n_unpopulated"]))

    out = {"spec": "actinv-p46-g2-1",
           "controls": checks,
           "all_pass": all(c["pass"] for c in checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "controls": {c["control"]: c["pass"]
                                   for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
