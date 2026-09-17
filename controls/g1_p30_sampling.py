#!/usr/bin/env python3
"""P30 G1: run the frozen 2-case sampling population through the
ACT-ROBUST-01 mechanism and publish the sampling records."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p30_seals.json")))
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p30-work/g1")
OUT = os.path.join(ROOT, "results", "g1_p30_sampling.json")

COV = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.cov.npz")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def main():
    os.makedirs(WORK, exist_ok=True)
    study = json.load(open(P27))
    study["study_id"] = "p30-robust"
    study["title"] = ("P30 sampling population: 2 cases x 16 samples, "
                      "MF33 + flux + composition channels")
    # restrict the grid to the frozen population
    study["cases"]["spectra"] = [s for s in study["cases"]["spectra"]
                                 if s["name"] == "fns_709"]
    study["cases"]["schedules"] = [s for s in study["cases"]["schedules"]
                                  if s["name"] == "pulse_5min"]
    study.pop("comparison", None)
    study["robustness"] = {
        "samples": SEALS["validation_population"]["samples"],
        "seed": int(SEALS["validation_population"]["seed_hex"], 16),
        "channels": {
            "cross_section_mf33": True,
            "flux_rel_std": 0.05,
            "composition_rel_std": {"Co": 0.20},
        },
        "covariance": {
            "path": COV,
            "sha256": SEALS["identities"]["covariance_sidecar"]["sha256"],
        },
        "responses": SEALS["validation_population"]["responses"],
    }
    sp = os.path.join(WORK, "sampling_study.json")
    json.dump(study, open(sp, "w"), indent=1)

    outdir = os.path.join(WORK, "run")
    if not os.path.isfile(os.path.join(outdir, "study_record.json")):
        r = subprocess.run(cgroup([ACTINV, "study", "run", sp, outdir]),
                           capture_output=True, text=True)
        print(r.stdout[-400:], r.stderr[-400:])
        r.check_returncode()

    rec = json.load(open(os.path.join(outdir, "study_record.json")))
    summary = {}
    for c in rec["cases"]:
        cid = c["case_id"]
        rb = c.get("robustness")
        if not rb:
            summary[cid] = {"status": "missing"}
            continue
        summary[cid] = {
            "status": rb["status"],
            "samples": rb["samples"],
            "n_failed": rb["n_failed_samples"],
            "covered_rows": rb["channels"]["cross_section_mf33"]
            ["covered_rows"],
            "correlated": rb["channels"]["cross_section_mf33"]
            ["correlated"],
        }
    record = {"gate": "G1", "phase": "P30", "study_sha256":
              rec["study_sha256"], "cases": summary,
              "record_path": os.path.join(outdir, "study_record.json")}
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
