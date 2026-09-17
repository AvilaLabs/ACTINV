#!/usr/bin/env python3
"""P29 G1: run the frozen criteria population (8 cases) through the
ACT-REFINE-01 mechanism and publish the criteria verdicts plus per-
component error accounting."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p29-work/g1")
OUT = os.path.join(ROOT, "results", "g1_p29_refinement.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.join(WORK, "assets"), exist_ok=True)
    # stage the IRDFF flux asset the study references
    import shutil
    src = os.path.expanduser(
        "~/nuclear-data/p27-work/study/assets/irdff_709.flux")
    dst = os.path.join(WORK, "assets", "irdff_709.flux")
    if not os.path.exists(dst):
        shutil.copy2(src, dst)

    study = json.load(open(P27))
    study["study_id"] = "p29-criteria"
    study["title"] = ("P29 criteria population: 8 cases x "
                      "(response, time) criteria at 1e-6/1e-4")
    criteria = []
    for resp in SEALS["validation_population"]["criteria_responses"]:
        for t in SEALS["validation_population"]["criteria_times_s"]:
            criteria.append({
                "response": resp, "time_s": t,
                "rel": 1e-6, "abs": 1e-24,
            })
    study["refinement"] = {
        "criteria": criteria,
        "resource_limit_runs": SEALS["tolerances"]["resource_limit_runs"],
    }
    sp = os.path.join(WORK, "criteria_study.json")
    json.dump(study, open(sp, "w"), indent=1)

    outdir = os.path.join(WORK, "run")
    if not os.path.isfile(os.path.join(outdir, "study_record.json")):
        r = subprocess.run(cgroup([ACTINV, "study", "run", sp, outdir]),
                           capture_output=True, text=True)
        print(r.stdout[-400:], r.stderr[-400:])
        r.check_returncode()

    rec = json.load(open(os.path.join(outdir, "study_record.json")))
    got = {c["case_id"] for c in rec["cases"]}
    want = set(SEALS["validation_population"]["criteria_cases"])
    if got != want:
        raise SystemExit(f"population drift: {got ^ want}")

    # evidence: per-case refinement block verbatim + run digests
    cases = []
    for c in rec["cases"]:
        entry = {
            "case_id": c["case_id"],
            "status": c["status"],
            "spec_sha256": c["spec_sha256"],
            "out_sha256": c.get("out_sha256"),
            "refinement": c.get("refinement"),
        }
        for tag in ("cram48_variant", "reference"):
            rp = os.path.join(outdir, "cases", c["case_id"],
                              f"ref_{tag}.json")
            if os.path.isfile(rp):
                import hashlib
                entry[f"{tag}_sha256"] = hashlib.sha256(
                    open(rp, "rb").read()).hexdigest()
        cases.append(entry)

    out = {
        "schema": "actinv-p29-g1-1", "gate": "G1", "phase": "P29",
        "partition": "p29_qualifying",
        "study_sha256": rec["study_sha256"],
        "manifest_sha256": rec.get("manifest_sha256"),
        "population": rec["population"],
        "cases": cases,
        "criteria_verdicts": {
            c["case_id"]: c["refinement"]["verdict"]
            if c.get("refinement") else c["status"]
            for c in cases},
        "component_classes": {
            "solver_time_integration": "empirically_estimated",
            "population_pruning_and_mode": "empirically_estimated",
            "processing_collapse": "bounded",
        },
    }
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out["criteria_verdicts"], indent=1))


if __name__ == "__main__":
    main()
