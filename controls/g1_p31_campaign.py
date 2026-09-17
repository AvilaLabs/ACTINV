#!/usr/bin/env python3
"""P31 G1: run the frozen campaign — cold, warm-resume, and the
robustness workload — recording prepared-run counts and wall times."""
import hashlib
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p31_seals.json")))
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p31-work/g1")
OUT = os.path.join(ROOT, "results", "g1_p31_campaign.json")

COV = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.cov.npz")
COV_SHA = SEALS["identities"]["covariance_sidecar"]["sha256"]


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


P27_DIR = os.path.dirname(P27)


def smoke_study(study_id):
    st = json.load(open(P27))
    st["study_id"] = study_id
    for s in st["cases"]["spectra"]:
        ff = s.get("flux_file")
        if ff and not os.path.isabs(ff):
            s["flux_file"] = os.path.join(P27_DIR, ff)
    return st


def robustness_study(study_id):
    study = smoke_study(study_id)
    study["cases"]["spectra"] = [s for s in study["cases"]["spectra"]
                                 if s["name"] == "fns_709"]
    study["cases"]["schedules"] = [s for s in study["cases"]["schedules"]
                                  if s["name"] == "pulse_5min"]
    study.pop("comparison", None)
    study["robustness"] = {
        "samples": SEALS["validation_population"]["robustness_samples"],
        "seed": int(SEALS["validation_population"]["seed_hex"], 16),
        "channels": {
            "cross_section_mf33": True,
            "flux_rel_std": 0.05,
        },
        "covariance": {"path": COV, "sha256": COV_SHA},
        "responses": ["total_activity_bq_per_g"],
    }
    return study


def run(study, outdir):
    os.makedirs(outdir, exist_ok=True)
    sp = os.path.join(outdir, "study.json")
    json.dump(study, open(sp, "w"), indent=1)
    t0 = time.monotonic()
    r = subprocess.run(cgroup([ACTINV, "study", "run", sp, outdir]),
                       capture_output=True, text=True)
    wall = time.monotonic() - t0
    if r.returncode != 0:
        print("STDERR:", r.stderr[-500:])
        r.check_returncode()
    return json.load(open(os.path.join(outdir, "study_record.json"))), wall


def case_digests(rec, outdir):
    return {
        c["case_id"]: c.get("out_sha256")
        for c in rec["cases"]
    }


def main():
    os.makedirs(WORK, exist_ok=True)
    res = {}

    # cold smoke campaign
    cold_dir = os.path.join(WORK, "smoke_cold")
    rec, wall_cold = run(smoke_study("p31-smoke"), cold_dir)
    res["cold"] = {
        "wall_s": wall_cold,
        "prepared_runs": rec["prepared_runs"],
        "executed": rec["population"]["executed"],
        "verdict": rec["verdict"],
    }
    cold_digs = case_digests(rec, cold_dir)

    # warm resume — same invocation again
    rec2, wall_warm = run(smoke_study("p31-smoke"), cold_dir)
    res["warm_resume"] = {
        "wall_s": wall_warm,
        "prepared_runs": rec2["prepared_runs"],
        "resumed_cases": rec2.get("resumed_cases", []),
        "verdict": rec2["verdict"],
    }
    res["resume_digests_identical"] = (
        case_digests(rec2, cold_dir) == cold_digs)

    # robustness workload — samples share the nominal's prepared run;
    # the local-vs-nonlinear check adds one uncertainty signature/case
    rb_dir = os.path.join(WORK, "robust")
    rec3, wall_rb = run(robustness_study("p31-robust"), rb_dir)
    res["robustness_workload"] = {
        "wall_s": wall_rb,
        "prepared_runs": rec3["prepared_runs"],
        "executed": rec3["population"]["executed"],
    }

    record = {
        "gate": "G1", "phase": "P31",
        "workload": SEALS["validation_population"]["workload_cases"],
        "measured": res,
        "record_path": os.path.join(cold_dir, "study_record.json"),
        "robust_record_path": os.path.join(rb_dir, "study_record.json"),
    }
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
