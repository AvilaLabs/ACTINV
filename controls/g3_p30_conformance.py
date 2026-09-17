#!/usr/bin/env python3
"""P30 G3: the five frozen negative controls, each driven against the
real binary and required to fail closed with a named error."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p30-work/g3")
OUT = os.path.join(ROOT, "results", "g3_p30_conformance.json")


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
    sp = os.path.join(WORK, "probe.json")
    os.makedirs(WORK, exist_ok=True)
    json.dump(study, open(sp, "w"))
    r = subprocess.run(cgroup([ACTINV, "study", "validate", sp]),
                       capture_output=True, text=True)
    return r


def try_spec_run(spec):
    os.makedirs(WORK, exist_ok=True)
    sp = os.path.join(WORK, "spec_probe.json")
    json.dump(spec, open(sp, "w"))
    r = subprocess.run(
        cgroup([ACTINV, "run", sp,
                os.path.join(WORK, "spec_probe.out.json")]),
        capture_output=True, text=True)
    return r


def main():
    res = {}

    # zero_samples
    s = base_study("neg-zeron")
    s["robustness"]["samples"] = 0
    r = try_validate(s)
    res["zero_samples"] = {
        "rejected": r.returncode != 0,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # negative_std
    s = base_study("neg-negstd")
    s["robustness"]["channels"]["flux_rel_std"] = -0.5
    r = try_validate(s)
    res["negative_std"] = {
        "rejected": r.returncode != 0
        and "flux_rel_std" in r.stderr + r.stdout,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # unknown_channel
    s = base_study("neg-chan")
    s["robustness"]["channels"]["bogus_channel"] = True
    r = try_validate(s)
    res["unknown_channel"] = {
        "rejected": r.returncode != 0,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # missing_covariance
    s = base_study("neg-cov")
    s["robustness"]["channels"]["cross_section_mf33"] = True
    r = try_validate(s)
    res["missing_covariance"] = {
        "rejected": r.returncode != 0
        and "covariance" in (r.stderr + r.stdout).lower(),
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # rate_scale_ledger: a single spec run with rate_scale exercises the
    # run-time enforcement (invalid row, nonpositive factor) and names
    # applied scales in the ledger
    g1_spec = json.load(open(os.path.join(
        ROOT, "..", "..", "nuclear-data", "p30-work", "g1", "run",
        "specs", "fe__fns_709__pulse_5min.json")))
    g1_spec = os.path.normpath(os.path.join(
        ROOT, "..", "..", "nuclear-data", "p30-work", "g1", "run",
        "specs", "fe__fns_709__pulse_5min.json"))
    spec = json.load(open(g1_spec))

    bad = json.loads(json.dumps(spec))
    bad["options"]["rate_scale"] = {"99999999": 1.5}
    r = try_spec_run(bad)
    out_of_range = r.returncode != 0 and "out of range" in (
        r.stderr + r.stdout)

    bad2 = json.loads(json.dumps(spec))
    bad2["options"]["rate_scale"] = {"0": -1.0}
    r2 = try_spec_run(bad2)
    nonpos = r2.returncode != 0

    good = json.loads(json.dumps(spec))
    good["options"]["rate_scale"] = {"0": 2.0}
    rp = os.path.join(WORK, "good.out.json")
    json.dump(good, open(os.path.join(WORK, "good_spec.json"), "w"))
    rg = subprocess.run(
        cgroup([ACTINV, "run", os.path.join(WORK, "good_spec.json"),
                rp]), capture_output=True, text=True)
    ledger_names = False
    if rg.returncode == 0 and os.path.isfile(rp):
        ledger = json.load(open(rp)).get("ledger", {})
        ledger_names = ledger.get("assembly", {}).get(
            "rate_scale") == 1
    res["rate_scale_ledger"] = {
        "rejected": out_of_range and nonpos,
        "ledger_names_applied": ledger_names,
        "reason": {
            "out_of_range": out_of_range,
            "nonpositive": nonpos,
        },
    }

    record = {"gate": "G3", "phase": "P30", "probes": res,
              "pass": all(v.get("rejected") for v in res.values())
              and res["rate_scale_ledger"]["ledger_names_applied"]}
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in res.items()}, indent=1)[:1500])


if __name__ == "__main__":
    main()
