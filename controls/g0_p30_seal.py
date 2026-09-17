#!/usr/bin/env python3
"""P30 G0 opening seal: protocol digest, tool/data identities, prior
verdicts, frozen population + controls + conformance probes, partition."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p30_seals.json")

PROTOCOL = "protocols/ACTINV-P30_PROTOCOL.md"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ident(path):
    return {"path": path, "sha256": sha(os.path.join(ROOT, path))}


def main():
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.strip()
    prior = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "results"))):
        if f.startswith("verdict_") and f.endswith(".json"):
            prior[f] = json.load(open(
                os.path.join(ROOT, "results", f)))["verdict"]
    seals = {
        "schema": "actinv-g0-seal-1",
        "phase": "P30",
        "recorded_at_utc":
            subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                           capture_output=True, text=True).stdout.strip(),
        "opening_commit": head,
        "protocol": PROTOCOL,
        "protocol_sha256": sha(os.path.join(ROOT, PROTOCOL)),
        "identities": {
            "actinv_binary": ident("target/release/actinv"),
            "activation_library": ident(
                "target/p25c-release/"
                "tendl-2025-patched-neutron-709g.npz"),
            "activation_index": ident(
                "target/p25c-release/"
                "tendl-2025-patched-neutron-709g_index.json"),
            "covariance_sidecar": ident(
                "target/p25c-release/"
                "tendl-2025-patched-neutron-709g.cov.npz"),
            "decay_primary": ident(
                "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat"),
            "decay_fallback": ident(
                "actinv-data/v1.0.0/decay/jeff-3-3_decay.dat"),
        },
        "validation_population": {
            "sampling_cases": [
                "fe__fns_709__pulse_5min",
                "fe_co100wppm__fns_709__pulse_5min",
            ],
            "samples": 16,
            "seed_hex": "0x5EED30",
            "channels": ["cross_section_mf33", "flux_rel_std",
                         "composition_rel_std"],
            "responses": ["total_activity_bq_per_g",
                          "decay_heat_w_per_g"],
            "controls": ["zero_variance", "flux_linear",
                         "seed_reproducibility", "coverage_accounting",
                         "composition_sum"],
            "negative_controls": ["zero_samples", "negative_std",
                                  "unknown_channel",
                                  "missing_covariance",
                                  "rate_scale_ledger"],
        },
        "tolerances": {
            "analytic_rel": 1e-6,
            "sampling_ci_z": 1.96,
        },
        "prior_verdicts": prior,
        "partition": {"qualifying": "p30_qualifying"},
        "amendments": [],
    }
    json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"sealed P30 at {head} -> {OUT}")


if __name__ == "__main__":
    main()
