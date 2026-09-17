#!/usr/bin/env python3
"""P29 G3: conformance — criteria that must NOT pass.

- unproducible_response: a criterion on a response the case cannot
  produce must record unestablished, not pass;
- resource_limit_exhaustion: an unattainable rel bound with
  resource_limit_runs=0 must record unmet with no escalation;
- envelope_violating_criterion: a criterion referencing an unqualified
  response must be refused at study validation.
"""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p29-work/g3")
OUT = os.path.join(ROOT, "results", "g3_p29_conformance.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def one_case_study(sid, refine):
    s = json.load(open(P27))
    s["study_id"] = sid
    # trim to a single case: one material x spectrum x schedule
    s["cases"]["materials"] = s["cases"]["materials"][:1]
    s["cases"]["spectra"] = s["cases"]["spectra"][:1]
    s["cases"]["schedules"] = s["cases"]["schedules"][:1]
    s.pop("comparison", None)
    if refine is not None:
        s["refinement"] = refine
    return s


def main():
    os.makedirs(WORK, exist_ok=True)
    results = []

    # 1. unproducible response: a criterion at a cooling time the case
    # never produces (t=3600 s is not in cooling_times_s) — the extractor
    # cannot find the value -> unestablished, never a silent pass.
    s = one_case_study("p29-unprod", {"criteria": [
        {"response": "total_activity_bq_per_g", "time_s": 3600.0,
         "rel": 1e-6}], "resource_limit_runs": 2})
    sp = os.path.join(WORK, "unproducible_response.json")
    json.dump(s, open(sp, "w"))
    r = subprocess.run(
        cgroup([ACTINV, "study", "run", sp,
                os.path.join(WORK, "unprod_run")]),
        capture_output=True, text=True)
    rec = {"id": "unproducible_response",
           "spec_sha256": hashlib.sha256(open(sp, "rb").read()).hexdigest()}
    rp = os.path.join(WORK, "unprod_run", "study_record.json")
    if os.path.isfile(rp):
        rr = json.load(open(rp))
        crit = rr["cases"][0].get("refinement", {}).get("criteria", [])
        rec["status"] = "executed"
        rec["criteria_verdicts"] = [c["verdict"] for c in crit]
    else:
        rec.update(status="gap", error=(r.stderr or r.stdout)[-300:])
    results.append(rec)

    # 2. resource-limit exhaustion: absurdly tight rel (1e-30) with
    # resource_limit_runs 0 — unmet, zero escalation.
    s = one_case_study("p29-exhaust", {"criteria": [
        {"response": "total_activity_bq_per_g", "time_s": 0.0,
         "rel": 1e-30}], "resource_limit_runs": 0})
    sp = os.path.join(WORK, "resource_limit_exhaustion.json")
    json.dump(s, open(sp, "w"))
    r = subprocess.run(
        cgroup([ACTINV, "study", "run", sp,
                os.path.join(WORK, "exhaust_run")]),
        capture_output=True, text=True)
    rec = {"id": "resource_limit_exhaustion",
           "spec_sha256": hashlib.sha256(open(sp, "rb").read()).hexdigest()}
    rp = os.path.join(WORK, "exhaust_run", "study_record.json")
    if os.path.isfile(rp):
        rr = json.load(open(rp))
        crit = rr["cases"][0].get("refinement", {}).get("criteria", [])
        rec["status"] = "executed"
        rec["criteria_verdicts"] = [c["verdict"] for c in crit]
        rec["escalation_runs"] = [c["escalation_runs"] for c in crit]
        rec["runs_used"] = rr["cases"][0]["refinement"]["runs_used"]
    else:
        rec.update(status="gap", error=(r.stderr or r.stdout)[-300:])
    results.append(rec)

    # 3. envelope-violating criterion: unqualified response must fail at
    # validation before any run.
    s = one_case_study("p29-envelope", {"criteria": [
        {"response": "dose_rate_msv_h", "time_s": 0.0,
         "rel": 1e-6}]})
    sp = os.path.join(WORK, "envelope_violating_criterion.json")
    json.dump(s, open(sp, "w"))
    r = subprocess.run(
        cgroup([ACTINV, "study", "run", sp,
                os.path.join(WORK, "env_run")]),
        capture_output=True, text=True)
    rec = {"id": "envelope_violating_criterion",
           "spec_sha256": hashlib.sha256(open(sp, "rb").read()).hexdigest(),
           "status": "gap" if r.returncode != 0 else "executed",
           "error": (r.stderr or r.stdout).strip()[-300:]}
    results.append(rec)

    for x in results:
        print(x["id"], x["status"],
              x.get("criteria_verdicts") or x.get("error", "")[:80],
              flush=True)

    out = {"schema": "actinv-p29-g3-1", "gate": "G3", "phase": "P29",
           "partition": "p29_qualifying", "probes": results}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
