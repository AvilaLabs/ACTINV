#!/usr/bin/env python3
"""P43 G3: execute the frozen 6-case x 64-sample campaign under the
bounded cgroup; record wall time against the 45-minute envelope and
publish the campaign evidence summary."""
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p43_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p43-work")
G3 = os.path.join(WORK, "g3", "campaign")
OUT = os.path.join(ROOT, "results", "g3_p43_campaign.json")
ENVELOPE_MIN = SEALS["tolerances"]["envelope_minutes"]


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            "RAYON_NUM_THREADS=2",
            *cmd]


def main():
    os.makedirs(G3, exist_ok=True)
    recp = os.path.join(G3, "study_record.json")
    t0 = time.monotonic()
    have_complete = (
        os.path.isfile(recp)
        and json.load(open(recp)).get("status") == "complete"
    )
    if not have_complete:
        # A timed campaign must run fresh: a resumed partial run would
        # measure only the remaining work, not the campaign.
        if os.path.isdir(G3):
            import shutil
            shutil.rmtree(G3)
        os.makedirs(G3, exist_ok=True)
        r = subprocess.run(
            cgroup([ACTINV, "study", "run",
                    os.path.join(WORK, "p43-campaign.json"), G3]),
            capture_output=True, text=True)
        print(r.stdout[-500:], r.stderr[-500:])
        r.check_returncode()
    wall_min = (time.monotonic() - t0) / 60.0

    rec = json.load(open(recp))
    pop = SEALS["validation_population"]
    cases = {}
    total_solves = 0
    all_ok = True
    for c in rec["cases"]:
        cid = c["case_id"]
        rb = c["robustness"]
        cases[cid] = {
            "status": c["status"],
            "samples": rb["samples"],
            "n_failed": rb["n_failed_samples"],
            "n_reused": rb["n_reused_samples"],
        }
        all_ok &= c["status"] == "executed" and rb["n_failed_samples"] == 0
        total_solves += rb["samples"]
    rules = rec.get("comparison", {}).get("rules", [])
    rule_view = {
        r["id"]: {"verdict": r["verdict"], "survival": r.get("survival")}
        for r in rules
    }
    record = {
        "gate": "G3", "phase": "P43",
        "record_path": recp,
        "study_sha256": rec.get("study_sha256"),
        "wall_minutes": wall_min,
        "envelope_minutes": ENVELOPE_MIN,
        "within_envelope": wall_min <= ENVELOPE_MIN,
        "cases": cases,
        "decision_rules": rule_view,
        "prepared_runs": rec.get("prepared_runs"),
        "population_match": sorted(cases) == sorted(
            pop["campaign_cases"]),
        "pass": all_ok and wall_min <= ENVELOPE_MIN,
    }
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in record.items()
                      if k != "cases"}, indent=1))


if __name__ == "__main__":
    main()
