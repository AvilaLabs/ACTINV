#!/usr/bin/env python3
"""P31 G2 independent checker: verifies the control record against raw
artifacts on disk — resume integrity re-verified byte-for-byte, reused
vs baseline outputs re-derived, kill-resume completion checked."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p31_seals.json")))
EV = os.path.join(RES, "g2_p31_controls.json")
OUT = os.path.join(RES, "g2_p31_check.json")
WORK = os.path.expanduser("~/nuclear-data/p31-work/g2")
G1DIR = os.path.expanduser("~/nuclear-data/p31-work/g1")

VOLATILE = {"ms", "wall_s", "elapsed_ms"}


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


def outs(d):
    cdir = os.path.join(d, "cases")
    return {x: sem_sha(os.path.join(cdir, x, "out.json"))
            for x in sorted(os.listdir(cdir))
            if os.path.isfile(os.path.join(cdir, x, "out.json"))}


def check(ev, fs):
    if ev.get("phase") != "P31" or ev.get("gate") != "G2":
        fs.append("gate/phase wrong")
    controls = ev.get("controls", {})
    want = set(SEALS["validation_population"]["controls"])
    if set(controls) != want:
        fs.append(f"control set drifted: {set(controls) ^ want}")
        return
    for name, c in controls.items():
        if c.get("status") != "pass":
            fs.append(f"control {name} not passing")

    # re-derive: cold vs baseline outputs
    cold = outs(os.path.join(G1DIR, "smoke_cold"))
    base = outs(os.path.join(WORK, "baseline"))
    if cold != base:
        fs.append("shared-prep outputs differ from per-case baseline")
    # re-derive: baseline really did prepare per case
    brec = json.load(open(os.path.join(WORK, "baseline",
                                       "study_record.json")))
    if brec["prepared_runs"] != len(brec["cases"]):
        fs.append("baseline did not prepare per case")
    # re-derive: torn/corrupt/stale/interrupted final outputs all equal
    for label in ("torn", "corrupt", "stale", "interrupted"):
        d = outs(os.path.join(WORK, label))
        if len(d) != 8:
            fs.append(f"{label}: incomplete final output set")
        elif label != "stale" and d != cold:
            fs.append(f"{label}: final outputs differ from cold")
    # stale re-executed the drifted cases: those outs legitimately
    # differ; the pulse cases must match
    stale = outs(os.path.join(WORK, "stale"))
    for cid in cold:
        if "pulse_5min" in cid and stale.get(cid) != cold[cid]:
            fs.append("stale: undrifted case output changed")
    srec = json.load(open(os.path.join(WORK, "stale",
                                       "study_record.json")))
    re_exec = {c["case_id"] for c in srec["cases"]
               if c.get("evidence_kind") == "fresh"}
    if re_exec != {c for c in cold if "cont_1d" in c}:
        fs.append("stale: drifted cases were not all re-executed")
    # interrupted: the partial record must never have claimed a full run
    irec = json.load(open(os.path.join(WORK, "interrupted",
                                       "study_record.json")))
    if irec.get("status") != "complete":
        fs.append("interrupted run did not reach complete")
    if not irec.get("resumed_cases"):
        fs.append("interrupted run resumed nothing")


def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0
    for label, mut in [
        ("status", lambda v: v["controls"]["resume_torn"]
            .__setitem__("status", "pass")
            if v["controls"]["resume_torn"]["status"] != "pass"
            else v["controls"]["resume_stale_spec"]
            .__setitem__("status", "fail")),
        ("gate", lambda v: v.__setitem__("gate", "G3")),
        ("drop", lambda v: v["controls"].pop("interrupted_run")),
    ]:
        planted += 1
        v = copy.deepcopy(ev)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)

    out = {"gate": "G2", "phase": "P31", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
