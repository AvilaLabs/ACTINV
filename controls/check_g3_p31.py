#!/usr/bin/env python3
"""P31 G3 independent checker: verifies every frozen negative control
was exercised against real machinery and rejected."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p31_seals.json")))
EV = os.path.join(RES, "g3_p31_conformance.json")
OUT = os.path.join(RES, "g3_p31_check.json")
WORK = os.path.expanduser("~/nuclear-data/p31-work/g3")


def check(ev, fs):
    if ev.get("phase") != "P31" or ev.get("gate") != "G3":
        fs.append("gate/phase wrong")
    probes = ev.get("probes", {})
    want = set(SEALS["validation_population"]["negative_controls"])
    if set(probes) != want:
        fs.append(f"probe set drifted: {set(probes) ^ want}")
        return
    for name, p in probes.items():
        if not p.get("rejected"):
            fs.append(f"probe {name} was not rejected")

    # re-derive from the artifacts, not just the flags
    mm = json.load(open(os.path.join(WORK, "mismatch",
                                     "study_record.json")))
    if mm["population"]["executed"] != 0:
        fs.append("mismatch: cases executed under wrong declared hash")
    if not any("SHA-256" in c.get("error", "") for c in mm["cases"]):
        fs.append("mismatch: rejection not attributed to the digest")

    dr = json.load(open(os.path.join(WORK, "drift",
                                     "study_record.json")))
    drifted = {c["case_id"] for c in dr["cases"]
               if "fe_co100wppm" in c["case_id"]}
    if drifted & set(dr.get("resumed_cases", [])):
        fs.append("drift: edited case was silently resumed")

    ph = json.load(open(os.path.join(WORK, "phantom",
                                     "study_record.json")))
    # the phantom case must have been re-executed (fresh evidence kind)
    phk = {c["case_id"] for c in ph["cases"]
           if c.get("evidence_kind") == "fresh"}
    if not phk:
        fs.append("phantom: deleted-artifact case not re-executed")

    po = json.load(open(os.path.join(WORK, "pollution",
                                     "study_record.json")))
    if any("evil" in c["case_id"] or "not_a_case" in c["case_id"]
           for c in po["cases"]):
        fs.append("pollution: foreign case entered the record")


def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0
    for label, mut in [
        ("accept", lambda v: v["probes"]["spec_mismatch_accepted"]
            .__setitem__("rejected", False)),
        ("drop", lambda v: v["probes"].pop("phantom_resume")),
        ("gate", lambda v: v.__setitem__("gate", "G2")),
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

    out = {"gate": "G3", "phase": "P31", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
