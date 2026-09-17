#!/usr/bin/env python3
"""P30 G3 independent checker: replays the frozen negative controls
against the real binary; planted mutations."""
import copy
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p30_seals.json")))
RES = os.path.join(ROOT, "results", "g3_p30_conformance.json")
WORK = os.path.expanduser("~/nuclear-data/p30-work/g3")
OUT = os.path.join(ROOT, "results", "g3_p30_check.json")
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")

NEGS = SEALS["validation_population"]["negative_controls"]


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def base_study(study_id):
    study = json.load(open(P27))
    study["study_id"] = study_id
    study["cases"]["spectra"] = [s for s in study["cases"]["spectra"]
                                 if s["name"] == "fns_709"]
    study["cases"]["schedules"] = [s for s in study["cases"]["schedules"]
                                  if s["name"] == "pulse_5min"]
    study["cases"]["materials"] = [study["cases"]["materials"][0]]
    study.pop("comparison", None)
    study["robustness"] = {
        "samples": 4, "seed": 1,
        "channels": {"flux_rel_std": 0.05},
        "responses": ["total_activity_bq_per_g"],
    }
    return study


def try_validate(study):
    sp = os.path.join(WORK, "chk_probe.json")
    os.makedirs(WORK, exist_ok=True)
    json.dump(study, open(sp, "w"))
    return subprocess.run(cgroup([ACTINV, "study", "validate", sp]),
                          capture_output=True, text=True)


def check(res, fs):
    probes = res.get("probes", {})
    if set(probes) != set(NEGS):
        fs.append(f"negative-control population drifted: "
                  f"{set(probes) ^ set(NEGS)}")
        return
    for k, v in probes.items():
        if not v.get("rejected"):
            fs.append(f"probe {k} not rejected")
    if not probes.get("rate_scale_ledger", {}) \
            .get("ledger_names_applied"):
        fs.append("rate_scale ledger naming not demonstrated")

    # independent replays (real binary, fresh study docs)
    s = base_study("chk-zeron")
    s["robustness"]["samples"] = 0
    if try_validate(s).returncode == 0:
        fs.append("replay: samples=0 accepted")

    s = base_study("chk-negstd")
    s["robustness"]["channels"]["flux_rel_std"] = -0.5
    if try_validate(s).returncode == 0:
        fs.append("replay: negative flux_rel_std accepted")

    s = base_study("chk-chan")
    s["robustness"]["channels"]["bogus"] = 1
    if try_validate(s).returncode == 0:
        fs.append("replay: unknown channel accepted")

    s = base_study("chk-cov")
    s["robustness"]["channels"]["cross_section_mf33"] = True
    if try_validate(s).returncode == 0:
        fs.append("replay: missing covariance accepted")

    spec_path = os.path.join(
        ROOT, "..", "..", "nuclear-data", "p30-work", "g1", "run",
        "specs", "fe__fns_709__pulse_5min.json")
    spec = json.load(open(os.path.normpath(spec_path)))
    spec["options"]["rate_scale"] = {"99999999": 1.5}
    sp = os.path.join(WORK, "chk_spec.json")
    json.dump(spec, open(sp, "w"))
    r = subprocess.run(
        cgroup([ACTINV, "run", sp, os.path.join(WORK, "chk.out.json")]),
        capture_output=True, text=True)
    if r.returncode == 0:
        fs.append("replay: out-of-range rate_scale row accepted")


def main():
    res = json.load(open(RES))
    fs = []
    check(res, fs)

    planted = rejected = 0

    def mut_rejected(r):
        r["probes"]["zero_samples"]["rejected"] = False

    def mut_pop(r):
        r["probes"].pop("unknown_channel")

    def mut_ledger(r):
        r["probes"]["rate_scale_ledger"]["ledger_names_applied"] = False

    def mut_pass(r):
        r["pass"] = not r["pass"]

    for mut in [mut_rejected, mut_pop, mut_ledger]:
        planted += 1
        v = copy.deepcopy(res)
        mut(v)
        mfs = []
        check(v, mfs)
        if mfs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(res, fs)
    out = {"gate": "G3", "phase": "P30", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
