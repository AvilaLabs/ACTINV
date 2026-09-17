#!/usr/bin/env python3
"""P35 G3 independent checker: verifies every frozen negative control
was exercised against real machinery and rejected."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p35_seals.json")))
EV = os.path.join(RES, "g3_p35_conformance.json")
OUT = os.path.join(RES, "g3_p35_check.json")
WORK = os.path.expanduser("~/nuclear-data/p35-work/g3")


def check(ev, fs):
    if ev.get("phase") != "P35" or ev.get("gate") != "G3":
        fs.append("gate/phase wrong")
    probes = ev.get("probes", {})
    want = set(SEALS["negative_controls"])
    if set(probes) != want:
        fs.append(f"probe set drifted: {set(probes) ^ want}")
        return
    for name, p in probes.items():
        if not p.get("rejected"):
            fs.append(f"probe {name} was not rejected")

    # each probe must carry its recorded failures — a bare flag is
    # not evidence of exercising the real check
    for name, p in probes.items():
        if not p.get("failures"):
            fs.append(f"probe {name} recorded no rejection detail")

def main():
    ev = json.load(open(EV))
    fs = []
    check(ev, fs)

    planted = rejected = 0
    for label, mut in [
        ("accept", lambda v: v["probes"]["upgraded_verdict"]
            .__setitem__("rejected", False)),
        ("drop", lambda v: v["probes"].pop("unsupported_claim")),
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

    out = {"gate": "G3", "phase": "P35", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
