#!/usr/bin/env python3
"""P43 verdict producer: assemble results/verdict_p43.json from the
gate evidence, then the closure checker independently re-derives it."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p43_seals.json")))
OUT = os.path.join(RES, "verdict_p43.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    g1 = json.load(open(os.path.join(RES, "g1_p43_mechanics.json")))
    g2 = json.load(open(os.path.join(RES, "g2_p43_controls.json")))
    g3p = json.load(open(os.path.join(RES, "g3_p43_conformance.json")))
    g3k = json.load(open(os.path.join(RES, "g3_p43_campaign.json")))
    camp = json.load(open(g3k["record_path"]))

    coverage = {}
    for c in camp["cases"]:
        ch = c["robustness"]["channels"]
        dec, fy = ch["decay_constants"], ch["fission_yields"]
        xs = ch["cross_section_mf33"]
        coverage[c["case_id"]] = {
            "decay_covered": len(dec["covered"]),
            "decay_uncovered": len(dec["uncovered"]),
            "yield_covered": len(fy["covered"]),
            "yield_uncovered": len(fy["uncovered"]),
            "xs_covered_rows": xs["covered_rows"],
            "xs_uncovered_rows": len(xs["uncovered_rows"]),
        }
    survival = {
        r["id"]: r.get("survival")
        for r in camp.get("comparison", {}).get("rules", [])
    }

    verdict = {
        "schema": "actinv-verdict-1",
        "phase": "P43",
        "qualified_family": "ACT-ROBUST-01",
        "verdict": "P43-CONDITIONAL",
        "conditional_reasons": [
            "amendments used and sealed at G0: fixture schema "
            "corrections, substrate-model decay trace, effective-set "
            "yield enumeration, full-index covariance build, "
            "restricted study outputs",
        ],
        "protocol_sha256": SEALS["protocol_sha256"],
        "opening_commit": SEALS["opening_commit"],
        "measured": {
            "coverage": coverage,
            "survival": survival,
            "campaign_wall_minutes": g3k["wall_minutes"],
            "envelope_minutes": g3k["envelope_minutes"],
            "controls": {k: v["status"]
                         for k, v in g2["controls"].items()},
            "conformance_probes": {
                k: {kk: vv for kk, vv in v.items() if kk != "reason"}
                for k, v in g3p["probes"].items()},
        },
        "evidence_sha256": {
            "g0_seals": sha(os.path.join(RES, "g0_p43_seals.json")),
            "g1_mechanics": sha(os.path.join(
                RES, "g1_p43_mechanics.json")),
            "g2_controls": sha(os.path.join(
                RES, "g2_p43_controls.json")),
            "g3_conformance": sha(os.path.join(
                RES, "g3_p43_conformance.json")),
            "g3_campaign": sha(os.path.join(
                RES, "g3_p43_campaign.json")),
        },
    }
    json.dump(verdict, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"verdict -> {OUT}")


if __name__ == "__main__":
    main()
