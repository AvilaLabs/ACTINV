#!/usr/bin/env python3
"""P28 G0 opening seal: protocol hash, opening commit, prior verdicts,
identity pins, frozen regime axes, frozen validation population,
tolerances, sealed partition."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p28_seals.json")


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

seals = {
    "schema": "actinv-p28-g0-seals-1",
    "phase": "P28",
    "recorded_at_utc": subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True,
        text=True).stdout.strip(),
    "protocol_sha256": sh(os.path.join(
        ROOT, "protocols", "ACTINV-P28_PROTOCOL.md")),
    # the opening commit is the commit that registered this protocol
    "opening_commit": git("log", "--diff-filter=A", "-1",
                          "--format=%H", "--",
                          "protocols/ACTINV-P28_PROTOCOL.md"),
    "prior_verdicts": PRIOR,
    "identities": {
        "actinv_binary": {
            "path": "target/release/actinv",
            "sha256": sh(os.path.join(ROOT, "target", "release", "actinv")),
        },
        "actinv_version": subprocess.run(
            [os.path.join(ROOT, "target", "release", "actinv"), "--version"],
            capture_output=True, text=True).stdout.strip(),
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
        "shield_table": {
            "path": "results/g1_p19_shield_artifact.json",
            "sha256": sh(os.path.join(
                ROOT, "results", "g1_p19_shield_artifact.json")),
            "nuclides": ["Ag107", "Fe56", "Nb93", "Ta181", "U238", "W186"],
            "note": "P19 table; coverage per-nuclide; Fe-56 covered",
        },
        "alara_binary": {
            "path": os.path.expanduser(
                "~/nuclear-data/alara-2.9.2-build/src/alara"),
            "sha256": sh(os.path.expanduser(
                "~/nuclear-data/alara-2.9.2-build/src/alara")),
            "note": "P26b-pinned ALARA 2.9.2; reused as the independently "
                    "processed reference for FENDL-3.2c collapses",
        },
    },
    "selected_regime": {
        "projectile": {"qualified": ["neutron"],
                       "excluded": ["proton", "deuteron", "alpha"],
                       "reason": "P25 coverage floors failed for p/d/alpha"},
        "data": "tendl-2025-patched-neutron-709g.npz (v1.1.0 derived)",
        "group_structure": {"qualified": ["fispact-709"],
                            "excluded": ["all others"]},
        "shielding": {"qualified": ["infinite_dilution",
                                    "finite_dilution_on_table_covered"],
                      "note": "P19-PASS; uncovered nuclides in finite mode "
                              "must fail visibly, not silently dilute"},
        "temperature_K": {"qualified_range": [0.0, 1200.0],
                          "note": "shield-table grid 293.6-1200 K; 0 K is "
                                  "the explicit no-doppler boundary"},
        "responses": ["total_activity_bq_per_g", "decay_heat_w_per_g",
                      "photon_source_per_group", "inventory_per_nuclide",
                      "total_atoms_per_g"],
        "isomeric_identity": "liso {0,1} product states only "
                             "(P18/P18b FAIL stand)",
        "inherited_limitations": [
            "Ni-58, Nb-93, Ag-109, In-113, Au-197 unrecovered "
            "(dosimetry-critical set fails closed)",
            "53 sealed files carry non-signature defect classes",
            "geometry-dependent transport is an external input "
            "responsibility; dilution is not a substitute",
        ],
    },
    "validation_population": {
        "ordinary": {
            "study": "P27 frozen smoke population",
            "cases": 8,
            "source": "p27-work/study/smoke_study.json + run1",
        },
        "boundary": {
            "cases": [
                {"id": "fe__fns_709__pulse_5min__T0K",
                 "kind": "temperature_boundary", "temperature_K": 0.0},
                {"id": "fe__fns_709__pulse_5min__T1200K",
                 "kind": "temperature_boundary", "temperature_K": 1200.0},
                {"id": "fe__fns_709__pulse_5min__dilute_0p1b",
                 "kind": "dilution_boundary", "dilution": "fixed",
                 "sigma0_b": 0.1},
                {"id": "fe__fns_709__pulse_5min__dilute_1e10b",
                 "kind": "dilution_boundary", "dilution": "fixed",
                 "sigma0_b": 10000000000.0,
                 "note": "infinite-dilution limit must reproduce the "
                         "unshielded run"},
                {"id": "fe__single_group_g354__pulse_5min",
                 "kind": "spectrum_boundary",
                 "note": "unit flux in one mid group only"},
                {"id": "fe__fns_709__zero_flux",
                 "kind": "schedule_boundary",
                 "note": "zero flux -> zero production, decay only"},
                {"id": "ni__fns_709__pulse_5min",
                 "kind": "coverage_boundary",
                 "note": "Ni-58 unrecovered: qualified-or-gap, never "
                         "silent"},
                {"id": "fe__fns_709__pulse_5min__proton",
                 "kind": "projectile_boundary",
                 "note": "proton projectile: must fail with a named gap"},
            ],
        },
        "rate_traces": {
            "parents": ["Fe56", "Fe54", "Co59"],
            "channels": {"Fe56": ["n,p -> Mn56"],
                         "Fe54": ["n,p -> Mn54"],
                         "Co59": ["n,gamma -> Co60m1", "n,gamma -> Co60"]},
            "method": "single-group unit-flux probe per group: "
                      "sigma_g = N_daughter(t)/(N_parent * phi * t) in the "
                      "linear trace regime, vs artifact row bytes",
            "groups": 709,
        },
    },
    "tolerances": {
        "rate_trace_relative": 1e-6,
        "analytic_limit": "exact or < 1e-12 relative",
        "metric_reformation": "byte-identical digests",
        "dilution_infinite_limit_relative": 1e-9,
    },
    "evidence_partitions": {
        "diagnostic": {"consumers": ["probes, iteration"],
                       "consumption": "unlimited within P28"},
        "p28_qualifying": {"consumers": ["P28 G3 verdict"],
                           "consumption": "once, at G3",
                           "sealed_at": "G0"},
    },
    "amendments": [
        {"n": 1, "scope": "rate_traces.channels.Fe56",
         "was": ["n,gamma -> Fe57"],
         "is": ["n,p -> Mn56"],
         "rationale": "Fe-57 is a natural-iron constituent held in the "
                      "constant bulk reservoir; produced Fe-57 merges into "
                      "it and is not a distinguishable inventory state. "
                      "Fe-56(n,p)->Mn-56 is the dominant observable channel "
                      "(radioactive daughter, absent from the material). "
                      "Amended before any gate consumed the seal."},
    ],
}

json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
print("wrote", OUT)
print("population:", len(seals["validation_population"]["boundary"]["cases"]),
      "boundary +", seals["validation_population"]["ordinary"]["cases"],
      "ordinary +", len(seals["validation_population"]["rate_traces"]["parents"]),
      "x 709-group traces")
