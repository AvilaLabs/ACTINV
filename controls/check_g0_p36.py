#!/usr/bin/env python3
"""P36 G0 checker: seals exist, digests verify, frozen study parses."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS_F = os.path.join(RES, "g0_p36_seals.json")
OUT = os.path.join(RES, "g0_p36_check.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(seals, fs):
    if seals.get("schema") != "actinv-p36-g0-1" or seals.get("phase") != "P36":
        fs.append("schema/phase wrong")
    if seals.get("flagship") != "W-MATCMP" or seals.get("leg") != "non-AI":
        fs.append("flagship/leg wrong")
    if sha(os.path.join(ROOT, "protocols", "ACTINV-P36_PROTOCOL.md")) \
            != seals.get("protocol_sha256"):
        fs.append("protocol digest drifted")
    for name, e in seals.get("identities", {}).items():
        p = os.path.join(ROOT, e["path"])
        if not os.path.isfile(p):
            fs.append(f"missing sealed input {e['path']}")
        elif sha(p) != e["sha256"]:
            fs.append(f"digest drift: {name}")
    study_p = os.path.join(ROOT, seals["study"]["path"])
    if not os.path.isfile(study_p):
        fs.append("frozen study missing")
    elif sha(study_p) != seals["study"]["sha256"]:
        fs.append("frozen study digest drifted")
    else:
        s = json.load(open(study_p))
        if s.get("study") != "actinv-study-1":
            fs.append("study schema marker wrong")
        mats = s.get("cases", {}).get("materials", [])
        if len(mats) != seals.get("expected_case_count"):
            fs.append(f"materials {len(mats)} != sealed count")
        for m in mats:
            if abs(sum(m["composition"].values()) - 100.0) > 1e-6:
                fs.append(f"material {m['name']} does not sum to 100 wt%")
        rules = s.get("comparison", {}).get("decision_rules", [])
        if len(rules) < 3:
            fs.append("decision rules missing")
        rob = s.get("robustness", {})
        if not (rob.get("samples", 0) >= 2
                and "composition_rel_std" in rob.get("channels", {})):
            fs.append("robustness block missing composition channel")
    if seals.get("prior_verdicts", {}).get("verdict_p32.json") \
            != "P32-CONDITIONAL":
        fs.append("P32 not closed as prerequisite")
    if seals.get("prior_verdicts", {}).get("verdict_p33.json") \
            != "P33-BLOCKED":
        fs.append("P33 blocker assumption changed — review leg claim")


def main():
    seals = json.load(open(SEALS_F))
    fs = []
    check(seals, fs)
    planted = rejected = 0
    for label, mut in [
        ("phase", lambda v: v.__setitem__("phase", "P35")),
        ("leg", lambda v: v.__setitem__("leg", "with-AI")),
        ("digest",
         lambda v: v["identities"]["actinv_binary"]
         .__setitem__("sha256", "0" * 64)),
        ("study", lambda v: v["study"].__setitem__("sha256", "0" * 64)),
        ("p33", lambda v: v["prior_verdicts"]
         .__setitem__("verdict_p33.json", "P33-PASS")),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G0", "phase": "P36", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
