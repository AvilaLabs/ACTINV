#!/usr/bin/env python3
"""P35 G1: re-execute the sealed checker battery on the current
artifact and run the reproduction leg."""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p35_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p35-work")
OUT = os.path.join(RES, "g1_p35_battery.json")

VOLATILE = {"ms", "wall_s", "elapsed_ms"}


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def sem(o):
    if isinstance(o, dict):
        return {k: sem(v) for k, v in o.items()
                if k not in VOLATILE}
    if isinstance(o, list):
        return [sem(v) for v in o]
    return o


def sem_sha(p):
    return hashlib.sha256(json.dumps(
        sem(json.load(open(p))), sort_keys=True).encode()).hexdigest()


def main():
    os.makedirs(WORK, exist_ok=True)
    battery = {}
    for checker in SEALS["battery"]["checkers"]:
        r = subprocess.run(
            cgroup([sys.executable, os.path.join(ROOT, checker)]),
            capture_output=True, text=True)
        # each checker writes its own check file; capture its verdict,
        # then restore the file — sealed check bytes must not be
        # overwritten by a re-run on a different artifact
        m = re.search(r"check_(g\d)_(p26b|p\d+)\.py", checker)
        if not m:
            battery[checker] = {"pass": False, "detail": "unparseable"}
            continue
        cj = os.path.join(RES, f"{m.group(1)}_{m.group(2)}_check.json")
        try:
            res = json.load(open(cj))
        except Exception as e:
            battery[checker] = {"pass": False,
                                "detail": f"no check file: {e}"}
            continue
        finally:
            subprocess.run(["git", "-C", ROOT, "checkout", "--", cj])
        mut = res.get("mutation_self_test", {})
        battery[checker] = {
            "pass": res.get("pass") is True
            and mut.get("planted") == mut.get("rejected"),
            "failures": res.get("failures", []),
            "mutations": f"{mut.get('rejected')}/{mut.get('planted')}",
            "exit": r.returncode,
        }

    # reproduction leg: two fresh runs of the frozen smoke study
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "g1c", os.path.join(ROOT, "controls", "g1_p31_campaign.py"))
    _g1 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_g1)
    repro = {}
    for i in (1, 2):
        d = os.path.join(WORK, f"repro_{i}")
        os.makedirs(d, exist_ok=True)
        sp = os.path.join(d, "study.json")
        json.dump(_g1.smoke_study("p31-smoke"), open(sp, "w"), indent=1)
        r = subprocess.run(
            cgroup([ACTINV, "study", "run", sp, d]),
            capture_output=True, text=True)
        rec = json.load(open(os.path.join(d, "study_record.json")))
        repro[f"run_{i}"] = {
            "exit": r.returncode,
            "executed": rec["population"]["executed"],
            "digests": {c["case_id"]: sem_sha(
                os.path.join(d, "cases", c["case_id"], "out.json"))
                for c in rec["cases"]},
        }
    repro["identical"] = (
        repro["run_1"]["digests"] == repro["run_2"]["digests"])

    out = {"gate": "G1", "phase": "P35", "battery": battery,
           "reproduction": repro}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    ok = all(b["pass"] for b in battery.values()) \
        and repro["identical"] \
        and all(r["exit"] == 0 for r in
                (repro["run_1"], repro["run_2"]))
    print(json.dumps({"battery_pass": ok,
                      "reproduction_identical": repro["identical"],
                      "n_checkers": len(battery)}, indent=1))


if __name__ == "__main__":
    main()
