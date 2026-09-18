#!/usr/bin/env python3
"""P36 G3 checker: the negative-control record is present and each
expected rejection carried a named reason."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
CTR = os.path.join(RES, "g3_p36_conformance.json")
OUT = os.path.join(RES, "g3_p36_check.json")


def check(ctr, fs):
    if ctr.get("gate") != "G3" or ctr.get("phase") != "P36":
        fs.append("gate/phase wrong")
        return
    cases = ctr.get("cases", [])
    if len(cases) < 6:
        fs.append(f"only {len(cases)} negative controls")
    for c in cases:
        if c.get("rejected") != c.get("expect_reject"):
            fs.append(f"{c['name']}: rejected={c['rejected']} "
                      f"expected {c['expect_reject']}")
        if c.get("expect_reject") and not c.get("named"):
            fs.append(f"{c['name']}: rejected without named reason")
    if not ctr.get("pass"):
        fs.append("conformance record itself reports failure")


def main():
    ctr = json.load(open(CTR))
    fs = []
    check(ctr, fs)
    planted = rejected = 0
    muts = [
        ("unreject", lambda v: v["cases"][0].__setitem__(
            "rejected", not v["cases"][0]["expect_reject"])),
        ("unnamed", lambda v: v["cases"][0].__setitem__(
            "named", False)),
        ("pass", lambda v: v.__setitem__("pass", False)),
        ("few", lambda v: v.__setitem__("cases", v["cases"][:2])),
    ]
    for label, mut in muts:
        planted += 1
        v = copy.deepcopy(ctr)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G3", "phase": "P36", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
