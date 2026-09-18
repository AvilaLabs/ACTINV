#!/usr/bin/env python3
"""Post-P35 matrix refresh: re-derive the claim/limitation matrix from
the current on-disk verdicts and emit a dated addendum snapshot. The
sealed g2_p35_matrix.json is left untouched as the phase-time record.
Rows that changed since the seal are diffed explicitly."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALED = os.path.join(RES, "g2_p35_matrix.json")
OUT = os.path.join(RES, "p35_matrix_addendum_2026-09-17.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# family -> current verdict file + refreshed claim/limitation text
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
    "spatial_handoff": {
        "verdict": "verdict_p32.json",
        "claims": [
            "an executed OpenMC 0.15.3 neutron-to-photon R2S-style chain "
            "exists: mesh flux tallies import, per-voxel activation and "
            "photon sources, distributed spatial export, external photon "
            "transport, and openmc.deplete comparison at 2.1e-7 median "
            "relative deviation",
        ],
        "limitations": [
            "self-produced geometry only — not an external benchmark or "
            "validation",
            "photon leg is a flux-proxy tally, not a qualified dose "
            "prediction",
            "tally statistical error recorded but not propagated",
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
    "flagship_demonstration": {
        "verdict": "verdict_p36.json",
        "claims": [
            "the P26-selected W-MATCMP flagship executed end-to-end "
            "without AI: impurity-resolved RAFM comparison with "
            "activity, decay heat, photons, pathways and robustness; "
            "all declared decision rules pass at the scoped cooling "
            "time",
        ],
        "limitations": [
            "AI leg of the flagship not executed — inherits P33/P34 "
            "blockers",
            "not experimental validation",
        ],
    },
    "product_qualification": {
        "verdict": "verdict_p35.json",
        "claims": [
            "claim/limitation matrix and conditional_release "
            "recommendation exist and are machine-checked (P35)",
        ],
        "limitations": [
            "the sealed matrix's spatial_handoff row predates P32's "
            "close — this addendum is the current view",
        ],
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
        v = json.load(open(os.path.join(RES, vf)))
        vstr = v.get("verdict") or v.get("disposition", "")
        state = vstr.split("-", 1)[-1] if "-" in vstr else vstr
        matrix[fam] = {
            "status": STATUS_BY_VERDICT.get(state, "unmeasured"),
            "verdict": vstr,
            "verdict_sha256": sha(os.path.join(RES, vf)),
            "claims": ent["claims"],
            "limitations": ent["limitations"],
            "blockers": v.get("blockers"),
        }

    sealed = json.load(open(SEALED))["matrix"]
    diffs = {}
    for fam, row in matrix.items():
        old = sealed.get(fam)
        if old is None or old.get("status") != row["status"] \
                or old.get("verdict") != row["verdict"]:
            diffs[fam] = {
                "sealed_status": old["status"] if old else None,
                "sealed_verdict": old.get("verdict") if old else None,
                "current_status": row["status"],
                "current_verdict": row["verdict"],
            }

    out = {
        "schema": "actinv-matrix-addendum-1",
        "generated": "2026-09-17",
        "supersedes": "results/g2_p35_matrix.json "
                      "(sealed phase-time snapshot; rows marked stale "
                      "there are superseded here, not edited)",
        "matrix": matrix,
        "changed_rows_since_seal": diffs,
        "release_recommendation": {
            "recommendation": "conditional_release",
            "ship": [
                "activation solves over the pinned neutron-data "
                "artifact set (P26b/P27 envelope)",
                "study expansion, qualified-combination gating, "
                "numerical criteria, nonlinear uncertainty sampling "
                "and resumable campaign execution — each under its "
                "recorded conditions",
                "OpenMC mesh-flux activation + distributed photon "
                "source handoff under P32's recorded conditions",
            ],
            "must_not_claim": [
                "any AI-assisted capability (P33/P34 blocked — no "
                "provider access, no human evaluation)",
                "experimental or predictive validation",
                "competitive/performance claims beyond locally "
                "measured amortization",
                "external-benchmark or dose accuracy for the spatial "
                "handoff (P32 scope is self-produced, flux-proxy)",
                "unqualified coverage of W, Cr, Mn, Ni identical-data "
                "legs or non-neutron projectile floors",
            ],
            "missed_gates": ["P33", "P34"],
            "authorization": "tagging, publishing and external "
                             "communications retain their existing "
                             "authorization requirements",
        },
    }
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print("changed rows:", json.dumps(diffs, indent=1))


if __name__ == "__main__":
    main()
