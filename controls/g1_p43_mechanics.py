#!/usr/bin/env python3
"""P43 G1: run the four frozen trace fixtures and the three-case
mechanics study; publish the G1 record."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p43_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p43-work")
OUT = os.path.join(ROOT, "results", "g1_p43_mechanics.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            "RAYON_NUM_THREADS=2",
            *cmd]


def run(cmd, out_path):
    if os.path.isfile(out_path):
        return
    r = subprocess.run(cgroup(cmd), capture_output=True, text=True)
    print(r.stdout[-400:], r.stderr[-400:])
    r.check_returncode()


def main():
    g1 = os.path.join(WORK, "g1")
    os.makedirs(g1, exist_ok=True)

    fixtures = {}
    for name in ("decay_trace_nominal", "decay_trace_scaled",
                 "yield_trace_nominal", "yield_trace_scaled"):
        out = os.path.join(g1, f"{name}.out.json")
        run([ACTINV, "run", os.path.join(WORK, f"p43_{name}.json"), out],
            out)
        fixtures[name] = out

    mech_dir = os.path.join(g1, "mech")
    run([ACTINV, "study", "run",
         os.path.join(WORK, "p43-mech.json"), mech_dir],
        os.path.join(mech_dir, "study_record.json"))

    rec = json.load(open(os.path.join(mech_dir, "study_record.json")))
    cases = {}
    for c in rec["cases"]:
        cid = c["case_id"]
        rb = c.get("robustness")
        entry = {"status": c.get("status")}
        if rb:
            entry.update({
                "samples": rb["samples"],
                "n_failed": rb.get("n_failed_samples", 0),
                "prepared_runs": rec.get("prepared_runs"),
                "reused_samples": rb.get("n_reused_samples"),
                "channels": {
                    k: {"enabled": v.get("enabled")}
                    for k, v in rb["channels"].items()
                    if isinstance(v, dict) and "enabled" in v
                },
            })
        cases[cid] = entry
    record = {
        "gate": "G1", "phase": "P43",
        "study_sha256": rec.get("study_sha256"),
        "fixtures": fixtures,
        "record_path": os.path.join(mech_dir, "study_record.json"),
        "cases": cases,
    }
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(cases, indent=1))


if __name__ == "__main__":
    main()
