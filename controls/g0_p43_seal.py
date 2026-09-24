#!/usr/bin/env python3
"""P43 G0 opening seal: protocol digest, tool/data identities, prior
verdicts, frozen population, fixtures, controls, conformance probes.

Every declared pin is recomputed; a mismatch aborts the seal instead of
recording a false identity.
"""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p43_seals.json")
WORK = os.path.expanduser("~/nuclear-data/p43-work")

PROTOCOL = "protocols/ACTINV-P43_PROTOCOL.md"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ident(path):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    return {"path": path, "sha256": sha(p)}


def pinned(path, declared):
    entry = ident(path)
    if entry["sha256"] != declared:
        raise SystemExit(
            f"pin mismatch for {path}: computed {entry['sha256']}, "
            f"declared {declared}"
        )
    return entry


def main():
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.strip()
    prior = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "results"))):
        if f.startswith("verdict_") and f.endswith(".json"):
            doc = json.load(open(os.path.join(ROOT, "results", f)))
            prior[f] = doc.get("verdict", doc.get("disposition"))
    seals = {
        "schema": "actinv-g0-seal-1",
        "phase": "P43",
        "recorded_at_utc":
            subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                           capture_output=True, text=True).stdout.strip(),
        "opening_commit": head,
        "protocol": PROTOCOL,
        "protocol_sha256": sha(os.path.join(ROOT, PROTOCOL)),
        "identities": {
            "actinv_binary": ident("target/release/actinv"),
            "activation_library": pinned(
                "actinv-data/v1.1.0/activation/"
                "tendl-2025-patched-neutron-709g.npz",
                "fb13c16c703c71a862ff82c78bc7fbd0761902264cf45efb97aa1a7e5e43b48d"),
            "activation_index": ident(
                "actinv-data/v1.1.0/activation/"
                "tendl-2025-patched-neutron-709g_index.json"),
            "covariance_sidecar": ident(
                os.path.join(WORK, "p43.cov.npz")),
            "decay_primary": pinned(
                "actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat",
                "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb"),
            "decay_fallback": pinned(
                "actinv-data/v1.1.0/decay/jeff-3-3_decay.dat",
                "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123"),
            "fission_yields": pinned(
                os.path.expanduser(
                    "~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf"),
                "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41"),
            "spectrum_source_spec": ident("examples/fns_fe_5min.json"),
        },
        "frozen_documents": {
            "mechanics_study": ident(os.path.join(WORK, "p43-mech.json")),
            "campaign_study": ident(os.path.join(WORK, "p43-campaign.json")),
            "decay_trace_nominal": ident(
                os.path.join(WORK, "p43_decay_trace_nominal.json")),
            "decay_trace_scaled": ident(
                os.path.join(WORK, "p43_decay_trace_scaled.json")),
            "yield_trace_nominal": ident(
                os.path.join(WORK, "p43_yield_trace_nominal.json")),
            "yield_trace_scaled": ident(
                os.path.join(WORK, "p43_yield_trace_scaled.json")),
        },
        "validation_population": {
            "mechanics_cases": [
                "fe__fns_709__pulse_5min",
                "fe_co100wppm__fns_709__pulse_5min",
                "u235__fns_709__pulse_5min",
            ],
            "campaign_cases": [
                f"{m}__fns_709__{s}"
                for s in ("pulse_5min", "cont_1d")
                for m in ("fe", "fe_co100wppm", "u235")
            ],
            "mechanics_samples": 8,
            "campaign_samples": 64,
            "seed": 43330177,
            "channels": ["cross_section_mf33", "flux_rel_std",
                         "composition_rel_std", "decay_constants",
                         "fission_yields"],
            "responses": ["total_activity_bq_per_g",
                          "decay_heat_w_per_g",
                          "total_atoms_per_g"],
            "decision_rules": ["r1", "r2"],
            "controls": ["decay_perturbation_trace",
                         "yield_perturbation_trace",
                         "channel_isolation",
                         "coverage_accounting",
                         "sample_resume",
                         "survival_accounting",
                         "zero_variance", "flux_linear",
                         "seed_reproducibility", "composition_sum"],
            "conformance_probes": [
                "decay_scale_absent_nuclide",
                "decay_scale_stable_nuclide",
                "yield_scale_absent_pair",
                "fission_channel_no_fissile_case",
                "scales_on_plain_spec",
                "all_scales_combined",
                "unknown_channel", "negative_std",
                "samples_out_of_range",
                "mutated_digest_fails_resume",
                "prepared_run_reuse",
            ],
        },
        "tolerances": {
            "decay_trace_rel": 1e-9,
            "yield_trace_rel": 1e-9,
            "envelope_minutes": 45,
        },
        "prior_verdicts": prior,
        "partition": {"campaign": "p43-campaign",
                      "mechanics": "p43-mech",
                      "fixtures": "p43-fixtures"},
        "amendments": [
            "fixture schema corrections: schedule is a bare Vec<Step>, "
            "outputs lives under options.outputs (frozen documents were "
            "invalid as first frozen; corrected before any gate "
            "evidence existed)",
            "yield_trace_scaled enumerates the effective pair set — the "
            "union of the two tables bracketing the spectrum-average "
            "energy (500 keV, 14 MeV), 1263 pairs — not the full-file "
            "union",
            "decay_perturbation_trace expectation corrected to the "
            "substrate model: N_daughter = N0·fλt and A = fλN0, not "
            "N0·exp(-fλt) — the declared composition is not a depleted "
            "chain state",
            "covariance sidecar built over the full index (1679 "
            "targets); build-covariance supports no subset selection",
            "study documents declare options.outputs "
            "[inventory,activity,heat,ledger,certificate]: the default "
            "null computes pathway analysis at ~90 s per step on the "
            "3873-state fission chain (measured: 248 s vs 1.4 s per "
            "u235 solve) which would breach the 45-minute envelope by "
            "~30x; the restriction changes no physics — same operator, "
            "schedule, and response extraction",
        ],
    }
    json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"sealed P43 at {head} -> {OUT}")


if __name__ == "__main__":
    main()
