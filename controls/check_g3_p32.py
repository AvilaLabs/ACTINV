#!/usr/bin/env python3
"""P32 G3 checker: every negative control failed closed, named."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
G3 = os.path.join(RES, "g3_p32.json")
OUT = os.path.join(RES, "g3_p32_check.json")
NEED = {"missing_bounds", "missing_step", "no_photon_source",
        "zero_volume", "negative_volume", "degenerate_bounds",
        "omitted_photon_strength", "wrong_schema"}


def check(rec, fs):
    if rec.get("schema") != "actinv-p32-g3-1" or rec.get("phase") != "P32":
        fs.append("schema/phase wrong")
    c = rec.get("controls", {})
    if set(c) != NEED:
        fs.append(f"control set {set(c)} != {NEED}")
    for n, r in c.items():
        if not (r.get("fail_closed") and r.get("named_reason")
                and r.get("exit", 0) != 0):
            fs.append(f"{n} did not fail closed with a named reason")


def main():
    rec = json.load(open(G3))
    fs = []
    check(rec, fs)
    planted = rejected = 0
    for label, mut in [
        ("schema", lambda v: v.__setitem__("schema", "x")),
        ("drop", lambda v: v["controls"].pop("zero_volume")),
        ("unclose", lambda v: v["controls"]["missing_bounds"]
            .__setitem__("fail_closed", True)),
    ]:
        planted += 1
        v = copy.deepcopy(rec)
        if label == "unclose":
            v["controls"]["missing_bounds"]["fail_closed"] = False
        else:
            mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G3", "phase": "P32", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
