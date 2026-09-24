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
