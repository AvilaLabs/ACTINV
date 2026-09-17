#!/usr/bin/env python3
"""P31 G0 seal: baseline commit, protocol hash, binary identity,
prior verdicts, frozen workload + targets + controls."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def main():
    opening = git("rev-parse", "HEAD")
    binary = os.path.join(ROOT, "target", "release", "actinv")
    protocol = os.path.join(ROOT, "protocols", "ACTINV-P31_PROTOCOL.md")

    seals = {
        "gate": "G0",
        "phase": "P31",
        "protocol_sha256": sha(protocol),
        "opening_commit": opening,
        "baseline_binary": {
            "path": "target/release/actinv",
            "sha256": sha(binary),
            "note": ("baseline-recorded: P31 rebuilds the binary; "
                     "data artifacts stay pinned"),
        },
        "prior_verdicts": {
            f: json.load(open(os.path.join(RES, f)))["verdict"]
            for f in ("verdict_p27.json", "verdict_p28.json",
                      "verdict_p29.json", "verdict_p30.json")
        },
        "validation_population": {
            "workload_cases": [
                f"{m}__{s}__{p}"
                for m in ("fe", "fe_co100wppm")
                for s in ("fns_709", "irdff_sp_mat9861_709")
                for p in ("pulse_5min", "cont_1d")
            ],
            "robustness_cases": [
                "fe__fns_709__pulse_5min",
                "fe_co100wppm__fns_709__pulse_5min",
            ],
            "robustness_samples": 4,
            "seed_hex": "0x5EED31",
            "targets": {
                "smoke_prepared_runs": 1,
                "resume_skips_verify": True,
                "resume_outputs_identical": True,
            },
            "controls": [
                "reuse_correctness",
                "resume_torn",
                "resume_corrupt",
                "resume_stale_spec",
                "distinct_signatures",
                "interrupted_run",
            ],
            "negative_controls": [
                "spec_mismatch_accepted",
                "corrupt_record_swallowed",
                "phantom_resume",
                "option_drift",
                "directory_pollution",
            ],
        },
        "tolerances": {
            "analytic_rel": 1e-6,
            "reuse_equality": "bit-identical (tolerance unused)",
        },
        "identities": {
            "activation_library": {
                "path": "target/p25c-release/"
                        "tendl-2025-patched-neutron-709g.npz",
                "sha256": sha(os.path.join(
                    ROOT, "target/p25c-release/"
                          "tendl-2025-patched-neutron-709g.npz")),
            },
            "covariance_sidecar": {
                "path": "target/p25c-release/"
                        "tendl-2025-patched-neutron-709g.cov.npz",
                "sha256": sha(os.path.join(
                    ROOT, "target/p25c-release/"
                          "tendl-2025-patched-neutron-709g.cov.npz")),
            },
        },
    }
    out = os.path.join(RES, "g0_p31_seals.json")
    json.dump(seals, open(out, "w"), indent=2, sort_keys=True)
    print(json.dumps({"sealed": out, "opening_commit": opening},
                     indent=1))


if __name__ == "__main__":
    main()
