#!/usr/bin/env python3
"""P29 G0 opening seal: protocol hash, opening commit, prior verdicts,
identity pins, frozen criteria control population, tolerances, sealed
partition."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p29_seals.json")


def sh(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


PRIOR = {}
for f in sorted(os.listdir(os.path.join(ROOT, "results"))):
    if f.startswith("verdict_p") and f.endswith(".json"):
        v = json.load(open(os.path.join(ROOT, "results", f)))
        PRIOR[f] = v["verdict"]

# Frozen criteria control population.
# - analytic chains: closed-form Bateman reference cases (pure isotope
#   feed + zero flux, or impulse production then decay), solved to
#   machine tolerance by the checker's own arithmetic.
# - stiff/long-history: 5 min pulse + 1 y cooling on the smoke Fe case —
#   long CRAM horizon with widely separated half-lives.
# - near-zero: a criterion whose target response is at the numerical
#   floor, requiring the absolute branch.
# - criteria cases: the P28 smoke population (8 cases) each carrying
#   per-(response, time) criteria at declared relative/absolute levels.
# - negative controls (G3): an unproducible response, a resource-limit
#   exhaustion probe, and an envelope-violating criterion.
POPULATION = {
    "criteria_cases": [
        "fe__fns_709__pulse_5min",
        "fe__fns_709__pulse_1d",
        "fe__irdff_sp_mat9861_709__pulse_5min",
        "fe__irdff_sp_mat9861_709__pulse_1d",
        "fe_co100wppm__fns_709__pulse_5min",
        "fe_co100wppm__fns_709__pulse_1d",
        "fe_co100wppm__irdff_sp_mat9861_709__pulse_5min",
        "fe_co100wppm__irdff_sp_mat9861_709__pulse_1d",
    ],
    "criteria_responses": [
        "total_activity_bq_per_g",
        "decay_heat_w_per_g",
        "inventory_per_nuclide",
    ],
    "criteria_times_s": [0.0, 86400.0],
    "reference_settings": {
        "prune": "none", "bmin_atoms_per_g": 0.0,
        "cram_order": 48, "mode": "coupled",
    },
    "analytic_controls": [
        {"id": "impulse_decay_mn56", "family": "analytic_chain",
         "feed": {"Mn56": 1e15}, "flux": 0.0, "hold_s": 86400.0,
         "note": "single decay; N(t) = N0 e^{-lam t}, t12=9284.04 s"},
        {"id": "chain_mo99_tc99m", "family": "analytic_chain",
         "feed": {"Mo99": 1e15}, "flux": 0.0, "hold_s": 86400.0,
         "note": "two-step Bateman: Mo-99 -> Tc-99m -> Tc-99; "
                 "closed form with tabulated half-lives"},
    ],
    "stiff_long_history": ["fe__fns_709__pulse_5min__cool_1y"],
    "near_zero": ["fe__fns_709__pulse_5min__near_zero_activity"],
    "negative_controls": [
        "unproducible_response", "resource_limit_exhaustion",
        "envelope_violating_criterion",
    ],
}

seals = {
    "schema": "actinv-p29-g0-seals-1",
    "phase": "P29",
    "recorded_at_utc": subprocess.run(
        ["date", "+%Y-%m-%dT%H:%M:%SZ", "-u"], capture_output=True,
        text=True).stdout.strip(),
    "protocol_sha256": sh(os.path.join(
        ROOT, "protocols", "ACTINV-P29_PROTOCOL.md")),
    "opening_commit": git("log", "--diff-filter=A", "-1",
                          "--format=%H", "--",
                          "protocols/ACTINV-P29_PROTOCOL.md"),
    "prior_verdicts": PRIOR,
    "identities": {
        "actinv_binary": {
            "path": "target/release/actinv",
            "sha256": sh(os.path.join(ROOT, "target", "release", "actinv")),
        },
        "actinv_version": subprocess.run(
            [os.path.join(ROOT, "target", "release", "actinv"),
             "--version"], capture_output=True, text=True).stdout.strip(),
        "activation_library": {
            "path": "target/p25c-release/"
                    "tendl-2025-patched-neutron-709g.npz",
            "sha256": sh(os.path.join(
                ROOT, "target", "p25c-release",
                "tendl-2025-patched-neutron-709g.npz")),
        },
        "activation_index": {
            "path": "target/p25c-release/"
                    "tendl-2025-patched-neutron-709g_index.json",
            "sha256": sh(os.path.join(
                ROOT, "target", "p25c-release",
                "tendl-2025-patched-neutron-709g_index.json")),
        },
        "decay_primary": {
            "path": "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat",
            "sha256": sh(os.path.join(
                ROOT, "actinv-data", "v1.0.0", "decay",
                "endf-b-viii-0_decay.dat")),
        },
        "decay_fallback": {
            "path": "actinv-data/v1.0.0/decay/jeff-3-3_decay.dat",
            "sha256": sh(os.path.join(
                ROOT, "actinv-data", "v1.0.0", "decay",
                "jeff-3-3_decay.dat")),
        },
    },
    "validation_population": POPULATION,
    "tolerances": {
        # criteria evaluation: abs branch below scale, rel elsewhere
        "abs_scale_atoms_per_g": 1e-6,
        "analytic_chain_rel": 1e-6,
        "reference_rel": 1e-9,
        "resource_limit_runs": 4,
    },
    "partition": {
        "qualifying": "p29_qualifying",
        "diagnostic": "p29_diagnostic",
        "rule": "outputs under p29_qualifying are consumed once, by the "
                "verdict gate; iteration uses p29_diagnostic",
    },
    "amendments": [],
}

json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
print("sealed:", OUT)
print("opening_commit:", seals["opening_commit"])
