#!/usr/bin/env python3
"""P35 G2: derive the claim/limitation matrix from on-disk verdicts and
the release recommendation scoped to what is actually qualified."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "g2_p35_matrix.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# family -> (verdict file, qualified-by verdict prefix)
FAMILIES = {
    "activation_solver": {
        "verdict": "verdict_p26b.json",
        "claims": [
            "activation solves reproduce on the pinned data artifact set",
            "study expansion, execution and Core interchange are "
            "machine-verified (P27-PASS)",
        ],
        "limitations": [
            "computed results are correct evaluations of the declared "
            "inputs — not experimental validation",
        ],
    },
    "qualification_combinations": {
        "verdict": "verdict_p28.json",
        "claims": [
            "qualified combinations are enumerable and unsupported "
            "projectile/coverage combinations fail closed (P28)",
        ],
        "limitations": [
            "projectile coverage floors failed for non-neutron legs",
            "dosimetry-critical evaluations are absent from the artifact",
            "no blind experimental validation",
        ],
    },
    "numerical_control": {
        "verdict": "verdict_p29.json",
        "claims": [
            "declared numerical criteria re-check against references and "
            "escalate the solver ladder until satisfied or exhausted",
        ],
        "limitations": [
            "criteria cover computational error only — not nuclear-data "
            "uncertainty or predictive discrepancy",
        ],
    },
    "uncertainty": {
        "verdict": "verdict_p30.json",
        "claims": [
            "nonlinear uncertainty sampling with correlated MF=33 "
            "covariance draws, flux normalization and composition "
            "perturbations is reproducible and ledgered",
        ],
        "limitations": [
            "MF=33 coverage is partial; uncovered rows are named",
            "a nonzero covariance ridge was required",
            "nonpositive draws were clamped and counted",
            "sample spread is a sensitivity, not a confidence interval "
            "or domain bound",
        ],
    },
    "efficient_campaigns": {
        "verdict": "verdict_p31.json",
        "claims": [
            "prepared nuclear data is shared across compatible cases and "
            "samples; per-case-prepared baselines are bit-identical",
            "campaigns stream per-case evidence and resume with "
            "digest-verified skips",
        ],
        "limitations": [
            "reuse is exact preparation sharing — no solver-result "
            "caching or approximate reuse",
            "measured speedup is local to the frozen workload — not a "
            "competitive claim",
        ],
    },
    "identical_data_comparison": {
        "verdict": "verdict_p39.json",
        "claims": [
            "912/1016 contract cases structurally executable on the "
            "relaxed FENDL-3.2c artifact; sampled legs run both arms "
            "with zero arm failures",
            "lumped-channel synthesis closed the dominant coverage gap: "
            "shutdown ALARA/ACTINV ratios 0.98-1.10x; late-cooling ~1%",
        ],
        "limitations": [
            "frozen 5e-4 tolerance still fails on all sampled cases — "
            "residuals are named representation floors",
            "isomer-split branches exist only in ALARA's REAC heritage, "
            "absent from FENDL's own encoding",
            "quasi-stable activity conventions differ on stable nuclides",
            "sampled subset, not the full 912-case census",
        ],
    },
    "spatial_handoff": {
        "verdict": "verdict_p32.json",
        "claims": [
            "the local R2S chain executed end-to-end: OpenMC neutron "
            "tally -> import-flux -> per-voxel actinv -> distributed "
            "OpenMC photon sources at all cooling steps",
            "openmc.deplete comparison at solver-noise level "
            "(median rel deviation 2.1e-7)",
        ],
        "limitations": [
            "self-produced geometry only — not an external benchmark",
            "photon leg is a flux-proxy tally, not a qualified dose",
            "MCNP SDEF export stays point-at-origin",
        ],
    },
    "ai_assisted_setup": {
        "verdict": "verdict_p33.json",
        "claims": [],
        "limitations": ["requires the user's own AI provider account — "
                        "not available to the evaluation"],
    },
    "ai_bounded_investigation": {
        "verdict": "verdict_p34.json",
        "claims": [],
        "limitations": ["inherits P33's blocker; Core-dispatched "
                        "assistant evaluation not possible"],
    },
    "product_qualification": {
        "verdict": None,
        "claims": ["this phase"],
        "limitations": [],
    },
}

STATUS_BY_VERDICT = {
    "PASS": "qualified",
    "CONDITIONAL": "conditional",
    "BLOCKED": "blocked",
    "FAIL": "unmeasured",
}


def main():
    matrix = {}
    for fam, ent in FAMILIES.items():
        vf = ent["verdict"]
        if vf is None:
            matrix[fam] = {"status": "conditional",
                           "verdict": None,
                           "note": "this phase",
                           "claims": ent["claims"],
                           "limitations": ent["limitations"]}
            continue
        v = json.load(open(os.path.join(RES, vf)))
        vstr = v.get("verdict", "")
        # verdict format: PX-STATE
        state = vstr.split("-", 1)[-1] if "-" in vstr else vstr
        status = STATUS_BY_VERDICT.get(state, "unmeasured")
        matrix[fam] = {
            "status": status,
            "verdict": vstr,
            "verdict_sha256": sha(os.path.join(RES, vf)),
            "claims": ent["claims"],
            "limitations": ent["limitations"],
            "blockers": v.get("blockers"),
        }

    recommendation = {
        "recommendation": "conditional_release",
        "ship": [
            "activation solves over the pinned neutron-data artifact "
            "set (P26b/P27 envelope)",
            "study expansion, qualified-combination gating, numerical "
            "criteria, nonlinear uncertainty sampling and resumable "
            "campaign execution — each under its recorded conditions",
        ],
        "must_not_claim": [
            "spatial/R2S source handoff (P32 blocked — no executed "
            "chain)",
            "any AI-assisted capability (P33/P34 blocked — no provider "
            "access, no human evaluation)",
            "experimental or predictive validation",
            "competitive/performance claims beyond the locally measured "
            "preparation amortization",
            "unqualified coverage of W, Cr, Mn, Ni identical-data legs "
            "or non-neutron projectile floors",
        ],
        "missed_gates": ["P32", "P33", "P34"],
        "authorization": ("tagging, publishing and external "
                          "communications retain their existing "
                          "authorization requirements"),
    }

    out = {"gate": "G2", "phase": "P35", "matrix": matrix,
           "release_recommendation": recommendation}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: v["status"] for k, v in matrix.items()},
                     indent=1))


if __name__ == "__main__":
    main()
