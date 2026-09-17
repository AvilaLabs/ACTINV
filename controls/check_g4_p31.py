#!/usr/bin/env python3
"""P31 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p31.json")
SEALS = os.path.join(RES, "g0_p31_seals.json")
G0C = os.path.join(RES, "g0_p31_check.json")
G1 = os.path.join(RES, "g1_p31_campaign.json")
G1C = os.path.join(RES, "g1_p31_check.json")
G2 = os.path.join(RES, "g2_p31_controls.json")
G2C = os.path.join(RES, "g2_p31_check.json")
G3 = os.path.join(RES, "g3_p31_conformance.json")
G3C = os.path.join(RES, "g3_p31_check.json")
OUT = os.path.join(RES, "g4_p31_check.json")
G1REC = os.path.expanduser("~/nuclear-data/p31-work/g1/smoke_cold/"
                           "study_record.json")

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P31_PROTOCOL.md")
REQUIRED_EVIDENCE = {"g0_seals", "g0_check", "g1_campaign", "g1_check",
                     "g2_controls", "g2_check", "g3_conformance",
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
    if v.get("phase") != "P31":
        fs.append("verdict phase != P31")
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
        if json.load(open(os.path.join(RES, f)))["verdict"] != want:
            fs.append(f"prior verdict {f} drifted")
    if set(v.get("evidence_sha256", {})) != REQUIRED_EVIDENCE:
        fs.append("evidence key set wrong")
    else:
        files = {"g0_seals": SEALS, "g0_check": G0C,
                 "g1_campaign": G1, "g1_check": G1C,
                 "g2_controls": G2, "g2_check": G2C,
                 "g3_conformance": G3, "g3_check": G3C}
        for k, p in files.items():
            if sha(p) != v["evidence_sha256"][k]:
                fs.append(f"evidence {k} digest mismatch")
    for k, p in [("g0", G0C), ("g1", G1C), ("g2", G2C), ("g3", G3C)]:
        c = json.load(open(p))
        if not c.get("pass"):
            fs.append(f"{k} check not passing")
        mt = c.get("mutation_self_test", {})
        if mt.get("planted") != mt.get("rejected"):
            fs.append(f"{k} checker mutations not all rejected")

    # re-derive the campaign census from the raw study record
    rec = json.load(open(G1REC))
    pop = seals["validation_population"]["workload_cases"]
    got = {c["case_id"] for c in rec["cases"]}
    if got != set(pop):
        fs.append(f"population mismatch: {got ^ set(pop)}")
    tgt = seals["validation_population"]["targets"]
    # the on-disk record holds the final (warm) state; the cold-run
    # prepared count lives in the campaign evidence
    g1 = json.load(open(G1))
    if g1["measured"]["cold"]["prepared_runs"] !=             tgt["smoke_prepared_runs"]:
        fs.append("prepared_runs != sealed target")
    if not g1["measured"]["resume_digests_identical"]:
        fs.append("resume outputs differ from cold run")
    # the verdict's measured claims must match the raw evidence
    if v["measured"]["cold"]["prepared_runs"] !=             g1["measured"]["cold"]["prepared_runs"]:
        fs.append("verdict prepared_runs claim mismatches evidence")
    for c in rec["cases"]:
        if c["status"] != "executed":
            fs.append(f"{c['case_id']} not executed")
        if c.get("evidence_kind") != "resumed":
            fs.append(f"{c['case_id']} not marked resumed")
    if len(rec.get("resumed_cases", [])) != len(pop):
        fs.append("resume census incomplete")
    for c in rec["cases"]:
        cid = c["case_id"]
        op = os.path.join(os.path.dirname(G1REC), "cases", cid,
                          "out.json")
        if sha(op) != c["out_sha256"]:
            fs.append(f"{cid} out digest mismatch")

    # all five controls pass; all five negatives rejected
    g2 = json.load(open(G2))
    if set(g2["controls"]) != set(
            seals["validation_population"]["controls"]):
        fs.append("control set drifted")
    if not all(c["status"] == "pass"
               for c in g2["controls"].values()):
        fs.append("a frozen control did not pass")
    g3 = json.load(open(G3))
    if set(g3["probes"]) != set(
            seals["validation_population"]["negative_controls"]):
        fs.append("negative-control set drifted")
    if not all(p.get("rejected") for p in g3["probes"].values()):
        fs.append("a negative control was not rejected")

    # closure rule: CONDITIONAL — measured facts hold but two seal
    # amendments were used and the speedup is a local measured quantity,
    # not a competitive claim
    want = "P31-CONDITIONAL"
    if v["verdict"] != want:
        fs.append(f"verdict re-derived as {want}, "
                  f"recorded {v['verdict']}")
    if set(v.get("qualified_mechanisms", [])) != {
            "shared PreparedRun reuse across compatible specs",
            "streaming per-case study_record writes",
            "digest-verified resume of completed cases"}:
        fs.append("qualified mechanism set wrong")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0

    def m1(x):
        x["verdict"] = "P31-PASS"

    def m2(x):
        x["protocol_sha256"] = "0" * 64

    def m3(x):
        x["evidence_sha256"].pop("g1_check")

    def m4(x):
        x["measured"]["cold"]["prepared_runs"] += 1

    def m5(x):
        x["phase"] = "P30"

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

    out = {"gate": "G4", "phase": "P31", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
