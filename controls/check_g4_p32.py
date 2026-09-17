#!/usr/bin/env python3
"""P32 G4 closure: verdict consistent with executed evidence."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p32.json")
OUT = os.path.join(RES, "g4_p32_check.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(v, fs):
    if v.get("phase") != "P32":
        fs.append("verdict phase wrong")
    if v.get("verdict") not in ("P32-PASS", "P32-CONDITIONAL",
                                "P32-BLOCKED", "P32-FAIL"):
        fs.append(f"unexpected verdict {v.get('verdict')}")
    ev = v.get("evidence_sha256", {})
    for key in ("g0_seals", "g0_check", "g1_chain", "g1_check",
                "g2_controls", "g2_check", "g3_controls", "g3_check"):
        f = {"g0_seals": "g0_p32_seals.json",
             "g0_check": "g0_p32_check.json",
             "g1_chain": "g1_p32.json",
             "g1_check": "g1_p32_check.json",
             "g2_controls": "g2_p32.json",
             "g2_check": "g2_p32_check.json",
             "g3_controls": "g3_p32.json",
             "g3_check": "g3_p32_check.json"}[key]
        p = os.path.join(RES, f)
        if not os.path.isfile(p):
            fs.append(f"missing evidence file {f}")
        elif sha(p) != ev.get(key):
            fs.append(f"evidence digest drifted: {key}")
    # every gate check must have passed
    for key in ("g0_check", "g1_check", "g2_check", "g3_check"):
        f = {"g0_check": "g0_p32_check.json",
             "g1_check": "g1_p32_check.json",
             "g2_check": "g2_p32_check.json",
             "g3_check": "g3_p32_check.json"}[key]
        p = os.path.join(RES, f)
        if os.path.isfile(p):
            if not json.load(open(p)).get("pass"):
                fs.append(f"{key} did not pass")
    if v.get("protocol_sha256") != sha(
            os.path.join(ROOT, "protocols", "ACTINV-P32_PROTOCOL.md")):
        fs.append("protocol digest drifted")
    # verdict must not overclaim
    basis = json.dumps(v).lower()
    for bad in ("experimental validation", "superior", "validated dose"):
        if bad in basis and "not " not in basis.split(bad)[0][-30:]:
            fs.append(f"verdict may overclaim: '{bad}'")
    if v.get("verdict") == "P32-PASS" and not v.get("conditions"):
        fs.append("unconditional verdict without evidence of conditions")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)
    planted = rejected = 0
    for label, mut in [
        ("phase", lambda x: x.__setitem__("phase", "P31")),
        ("verdict", lambda x: (x.__setitem__("verdict", "P32-PASS"),
                               x.pop("conditions", None))),
        ("digest", lambda x: x["evidence_sha256"]
            .__setitem__("g1_chain", "0" * 64)),
        ("protocol", lambda x: x.__setitem__("protocol_sha256", "0" * 64)),
    ]:
        planted += 1
        vv = copy.deepcopy(v)
        mut(vv)
        mfs = []
        try:
            check(vv, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G4", "phase": "P32", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
