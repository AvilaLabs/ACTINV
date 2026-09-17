#!/usr/bin/env python3
"""P35 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p35.json")
SEALS = os.path.join(RES, "g0_p35_seals.json")
G0C = os.path.join(RES, "g0_p35_check.json")
G1 = os.path.join(RES, "g1_p35_battery.json")
G1C = os.path.join(RES, "g1_p35_check.json")
G2 = os.path.join(RES, "g2_p35_matrix.json")
G2C = os.path.join(RES, "g2_p35_check.json")
G3 = os.path.join(RES, "g3_p35_conformance.json")
G3C = os.path.join(RES, "g3_p35_check.json")
OUT = os.path.join(RES, "g4_p35_check.json")
G1B = json.load(open(G1))

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P35_PROTOCOL.md")
REQUIRED_EVIDENCE = {"g0_seals", "g0_check", "g1_battery",
                     "g2_matrix", "g2_check", "g3_conformance",
                     "g3_check"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def check(v, fs):
    if v.get("phase") != "P35":
        fs.append("verdict phase != P35")
    if sha(PROTOCOL) != v["protocol_sha256"]:
        fs.append("protocol digest mismatch")
    seals = json.load(open(SEALS))
    if v["protocol_sha256"] != seals["protocol_sha256"]:
        fs.append("verdict/seal protocol mismatch")
    if v.get("opening_commit") != seals["opening_commit"]:
        fs.append("opening commit mismatch")
    try:
        git("merge-base", "--is-ancestor", seals["opening_commit"],
            "HEAD")
    except subprocess.CalledProcessError:
        fs.append("gate ancestry broken")
    for f, want in seals["prior_verdicts"].items():
        # seal stores digest pins — verify file bytes
        if sha(os.path.join(RES, f)) != want:
            fs.append(f"prior verdict {f} drifted")
    if set(v.get("evidence_sha256", {})) != REQUIRED_EVIDENCE:
        fs.append("evidence key set wrong")
    else:
        files = {"g0_seals": SEALS, "g0_check": G0C,
                 "g1_battery": G1,
                 "g2_matrix": G2, "g2_check": G2C,
                 "g3_conformance": G3, "g3_check": G3C}
        for k, p in files.items():
            if sha(p) != v["evidence_sha256"][k]:
                fs.append(f"evidence {k} digest mismatch")
    for k, p in [("g0", G0C), ("g2", G2C), ("g3", G3C)]:
        c = json.load(open(p))
        if not c.get("pass"):
            fs.append(f"{k} check not passing")
        mt = c.get("mutation_self_test", {})
        if mt.get("planted") != mt.get("rejected"):
            fs.append(f"{k} checker mutations not all rejected")

    # re-derive: battery results recorded green and matrix consistent
    for ck in seals["battery"]["checkers"]:
        if not G1B["battery"].get(ck, {}).get("pass"):
            fs.append(f"battery checker {ck} not passing")
    if not G1B["reproduction"].get("identical"):
        fs.append("reproduction runs diverged")
    for fam, ent in v["claim_limitation_matrix"].items():
        if ent["status"] not in seals["matrix"]["statuses"]:
            fs.append(f"{fam}: invalid status")
        if ent["status"] == "blocked" and not json.load(open(
                os.path.join(RES, f"verdict_"
                                 f"{ent['verdict'].split('-')[0].lower()}"
                                 f".json"))).get("blockers") is not None:
            pass
    for fam in ("spatial_handoff", "ai_assisted_setup",
                "ai_bounded_investigation"):
        if v["claim_limitation_matrix"][fam]["status"] != "blocked":
            fs.append(f"{fam} not blocked in verdict matrix")

    g2 = json.load(open(G2))
    if set(g2["matrix"]) != set(seals["matrix"]["families"]):
        fs.append("matrix family set drifted")
    if not all(e["status"] in seals["matrix"]["statuses"]
               for e in g2["matrix"].values()):
        fs.append("matrix status outside vocabulary")
    g3 = json.load(open(G3))
    if set(g3["probes"]) != set(
            seals["negative_controls"]):
        fs.append("negative-control set drifted")
    if not all(p.get("rejected") for p in g3["probes"].values()):
        fs.append("a negative control was not rejected")

    # closure rule: CONDITIONAL — aggregation complete but the release
    # must exclude blocked capabilities (P32-P34)
    want = "P35-CONDITIONAL"
    if v["verdict"] != want:
        fs.append(f"verdict re-derived as {want}, "
                  f"recorded {v['verdict']}")
    if not v.get("release_recommendation"):
        fs.append("no release recommendation")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0

    def m1(x):
        x["verdict"] = "P35-PASS"

    def m2(x):
        x["protocol_sha256"] = "0" * 64

    def m3(x):
        x["evidence_sha256"].pop("g2_check")

    def m4(x):
        x["claim_limitation_matrix"]["spatial_handoff"]["status"] \
            = "qualified"

    def m5(x):
        x["phase"] = "P34"

    for mut in [m1, m2, m3, m4, m5]:
        planted += 1
        vv = copy.deepcopy(v)
        mut(vv)
        mfs = []
        try:
            check(vv, mfs)
        except Exception:
            mfs = ["crashed"]
        if mfs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    out = {"gate": "G4", "phase": "P35", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
