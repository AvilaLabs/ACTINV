#!/usr/bin/env python3
"""P44 verdict producer: assemble results/verdict_p44.json from gate
evidence; the closure checker independently re-derives it."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p44_seals.json")))
OUT = os.path.join(RES, "verdict_p44.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    g1 = json.load(open(os.path.join(RES, "g1_p44_mechanics.json")))
    g2 = json.load(open(os.path.join(RES, "g2_p44_controls.json")))
    g3 = json.load(open(os.path.join(RES, "g3_p44_conformance.json")))
    g4s = json.load(open(os.path.join(RES, "g4_p44_scoring.json")))
    rep = json.load(open(os.path.join(RES, "p44_sealed_coverage.json")))

    coverage = {}
    for bt_met, groups in rep["aggregates"].items():
        coverage[bt_met] = {
            "pooled": groups["pooled"]["all"],
            "per_material": {m: g["coverage"]
                             for m, g in groups["material"].items()},
            "per_experiment_type": {
                t: g for t, g in groups["experiment_type"].items()},
        }
    n_low = sum(1 for bt_met, groups in rep["aggregates"].items()
                for m, g in groups["material"].items()
                if g["coverage"] is not None and g["coverage"] < 0.5)

    # diagnostic decomposition (descriptive, post-hoc reporting — not a
    # band or metric change): the covariance-bound patched library is a
    # 1679-target subset of full TENDL-2025 (2850); materials whose
    # composition elements lack natural-isotope targets in that subset
    # show zero/partial nominals. Elements flagged: every natural
    # isotope missing for Ag, Au, Nb, Ta, Ir; partial loss for Ba, Bi,
    # Br, Cd, Cs, Dy, Er, Eu, Ge, Hf, Hg, I, K, La, Ni, Os, Pd, Te, W,
    # Yb (plus alloy constituents).
    isotope_incomplete = {
        "Ag", "Au", "Nb", "Ta", "Ir", "Ba", "Bi", "Br", "Cd", "Cs",
        "Dy", "Er", "Eu", "Ge", "Hf", "Hg", "I", "K", "La", "Os",
        "Pd", "Te", "W", "Yb", "Ni", "Cu",
    }
    fo_mat = rep["aggregates"]["first_order.band_only"]["material"]
    comp = inc = {"covered": 0, "denominator": 0}
    comp, inc = dict(comp), dict(inc)
    for m, g in fo_mat.items():
        (inc if m in isotope_incomplete else comp)[
            "covered"] += g["covered"]
        (inc if m in isotope_incomplete else comp)[
            "denominator"] += g["denominator"]
    isotope_split = {
        "isotope_complete": {**comp,
                            "coverage": comp["covered"] / comp["denominator"]
                            if comp["denominator"] else None},
        "isotope_incomplete": {**inc,
                              "coverage": inc["covered"] / inc["denominator"]
                              if inc["denominator"] else None},
        "basis": "first_order.band_only; element-level natural-isotope "
                 "completeness vs the 1679-target patched index "
                 "(approximate — alloys by flagged constituents)",
    }
    zero_nominal = sorted({
        r["material"] for r in rep["experiments"]
        if r["points"] and all(
            (p["bands"]["first_order"].get("band") or {}).get("nominal", 1)
            == 0 for p in r["points"])})

    verdict = {
        "schema": "actinv-verdict-1",
        "phase": "P44",
        "qualified_family": "ACT-ROBUST-01",
        "verdict": "P44-CONDITIONAL",
        "conditional_reasons": [
            "amendments used and sealed at G0: numeric step-time "
            "matching in sampled-band extraction",
            "corpus caveat applies as declared: the FNS corpus is "
            "consumed C/E evidence — the seal binds band definitions, "
            "scoring code, partition and metrics, not blind data",
            "pooled coverage is materially below the declared 0.6827 "
            "level: the measured result names two causes — missing "
            "natural-isotope targets in the covariance-bound patched "
            "library (2.9% coverage on affected materials, five "
            "zero-nominal) and genuinely narrow declared bands on "
            "isotope-complete materials (39%)",
        ],
        "protocol_sha256": SEALS["protocol_sha256"],
        "opening_commit": SEALS["opening_commit"],
        "measured": {
            "sealed_experiments": g4s["sealed_experiments"],
            "sealed_points": g4s["n_points"],
            "excluded_nonpoints": g4s["n_excluded_nonpoints"],
            "pooled": g4s["pooled"],
            "per_material": {k: v["per_material"]
                             for k, v in coverage.items()},
            "per_experiment_type": {k: v["per_experiment_type"]
                                    for k, v in coverage.items()},
            "materials_below_50pct_band_only": n_low,
            "isotope_split": isotope_split,
            "zero_nominal_materials": zero_nominal,
            "library_subset_note":
                "covariance-bound patched library holds 1679/2850 "
                "full-TENDL-2025 targets; missing natural isotopes "
                "drive zero/partial nominals on 26 flagged materials",
            "wall_minutes": g4s["total_wall_minutes"],
            "envelope_minutes": g4s["envelope_minutes"],
            "controls": {k: v["status"]
                         for k, v in g2["controls"].items()},
            "conformance_probes": g3["probes"],
        },
        "evidence_sha256": {
            "g0_seals": sha(os.path.join(RES, "g0_p44_seals.json")),
            "g1_mechanics": sha(os.path.join(RES, "g1_p44_mechanics.json")),
            "g2_controls": sha(os.path.join(RES, "g2_p44_controls.json")),
            "g3_conformance": sha(
                os.path.join(RES, "g3_p44_conformance.json")),
            "g4_scoring": sha(os.path.join(RES, "g4_p44_scoring.json")),
            "sealed_coverage": sha(
                os.path.join(RES, "p44_sealed_coverage.json")),
        },
    }
    json.dump(verdict, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"verdict -> {OUT}")


if __name__ == "__main__":
    main()
