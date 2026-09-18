#!/usr/bin/env python3
"""P36 G4 checker: the verdict names a valid disposition, cites only
passing gates, names its blockers and limitations, and does not
overclaim beyond the declared scope."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
V = os.path.join(RES, "verdict_p36.json")
OUT = os.path.join(RES, "g4_p36_check.json")

REQUIRED_BLOCKERS = ("AI", "P33", "P34")
FORBIDDEN = ("experimental validation established", "validated",
             "regulatory qualification", "production-ready",
             "superior to", "outperforms")


def check(v, fs):
    if v.get("schema") != "actinv-verdict-1" or v.get("phase") != "P36":
        fs.append("schema/phase wrong")
        return
    disp = v.get("disposition", "")
    if not disp.startswith("P36-"):
        fs.append(f"bad disposition {disp}")
        return
    unconditional = disp.endswith("PASS")
    # gates
    gates = v.get("gates", {})
    for g in ("G0", "G1", "G2", "G3"):
        if gates.get(g) != "pass":
            fs.append(f"{g} not pass")
            continue
        p = os.path.join(RES, f"{g.lower()}_p36_check.json")
        if not os.path.isfile(p):
            fs.append(f"{g} check file missing")
            continue
        if not json.load(open(p)).get("pass"):
            fs.append(f"{g} check file reports failure")
    # unconditional pass must carry no conditions
    if unconditional and v.get("conditions"):
        fs.append("PASS verdict carries conditions")
    # blockers must name the AI leg
    blob = json.dumps(v.get("conditions", [])) + json.dumps(
        v.get("headline_results", {}))
    for b in REQUIRED_BLOCKERS:
        if b not in blob:
            fs.append(f"blocker '{b}' not named in conditions")
    # limitations must be present
    if len(v.get("conditions", [])) < 4:
        fs.append("too few conditions/limitations")
    # machinery changes ledgered
    if not v.get("machinery_changes_forced_by_flagship"):
        fs.append("machinery changes not ledgered")
    # no overclaim in the *claims* (headline results + machinery
    # ledger); the conditions list may legitimately negate these
    # phrases
    claims = json.dumps(v.get("headline_results", {})).lower() + \
        json.dumps(v.get("machinery_changes_forced_by_flagship",
                         [])).lower()
    for phrase in FORBIDDEN:
        if phrase in claims:
            fs.append(f"forbidden claim: {phrase}")
    # headline results present
    hr = v.get("headline_results", {})
    for k in ("decision_rules_100y", "dominant_finding",
              "robustness_survival", "pathway_attribution"):
        if k not in hr:
            fs.append(f"headline missing {k}")


def main():
    v = json.load(open(V))
    fs = []
    check(v, fs)
    planted = rejected = 0
    muts = [
        ("uncond", lambda v: v.__setitem__("disposition", "P36-PASS")),
        ("gate", lambda v: v["gates"].__setitem__("G2", "fail")),
        ("nolim", lambda v: v.__setitem__("conditions",
                                          ["shipped, all good"])),
        ("noblock", lambda v: v.__setitem__("conditions", [])),
        ("overclaim", lambda v: v["headline_results"].__setitem__(
            "status", "validated against experiment")),
    ]
    for label, mut in muts:
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
    out = {"gate": "G4", "phase": "P36", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
