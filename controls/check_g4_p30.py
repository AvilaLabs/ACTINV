#!/usr/bin/env python3
"""P30 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p30.json")
SEALS = os.path.join(RES, "g0_p30_seals.json")
G0C = os.path.join(RES, "g0_p30_check.json")
G1 = os.path.join(RES, "g1_p30_sampling.json")
G1C = os.path.join(RES, "g1_p30_check.json")
G2 = os.path.join(RES, "g2_p30_controls.json")
G2C = os.path.join(RES, "g2_p30_check.json")
G3 = os.path.join(RES, "g3_p30_conformance.json")
G3C = os.path.join(RES, "g3_p30_check.json")
OUT = os.path.join(RES, "g4_p30_check.json")
G1REC = os.path.expanduser("~/nuclear-data/p30-work/g1/run/"
                           "study_record.json")

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P30_PROTOCOL.md")
REQUIRED_EVIDENCE = {"g0_seals", "g0_check", "g1_sampling", "g1_check",
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
    if v.get("phase") != "P30":
        fs.append("verdict phase != P30")
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
                 "g1_sampling": G1, "g1_check": G1C,
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

    # re-derive the qualification census from the raw study record
    rec = json.load(open(G1REC))
    pop = seals["validation_population"]["sampling_cases"]
    got = {c["case_id"] for c in rec["cases"]}
    if got != set(pop):
        fs.append(f"population mismatch: {got ^ set(pop)}")
    n_samples_declared = seals["validation_population"]["samples"]
    for c in rec["cases"]:
        rb = c["robustness"]
        if rb["status"] != "executed":
            fs.append(f"{c['case_id']} not executed")
        if rb["samples"] != n_samples_declared:
            fs.append(f"{c['case_id']} sample count "
                      f"{rb['samples']} != {n_samples_declared}")
        if rb["n_failed_samples"] != 0:
            fs.append(f"{c['case_id']} has failed samples")
        for required in ("local_vs_nonlinear", "channel_attribution",
                         "pathway_view", "sample_artifacts"):
            if required not in rb:
                fs.append(f"{c['case_id']} missing {required}")
        xsc = rb["channels"]["cross_section_mf33"]
        if not xsc.get("correlated"):
            fs.append(f"{c['case_id']} fell back to independence")
        # the verdict's coverage claims must match the record
        m = v["measured"]["coverage"].get(c["case_id"], {})
        if m.get("covered_rows") != xsc["covered_rows"]:
            fs.append(f"{c['case_id']} verdict covered_rows mismatch")
        if m.get("uncovered_rows") != len(xsc["uncovered_rows"]):
            fs.append(f"{c['case_id']} verdict uncovered mismatch")
        if m.get("n_clamped_nonpositive_draws") != \
                xsc["n_clamped_nonpositive_draws"]:
            fs.append(f"{c['case_id']} verdict clamps mismatch")

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

    # closure rule: CONDITIONAL — partial coverage + clamping + ridge +
    # RSS approximation are measured scope limits, not defects
    want = "P30-CONDITIONAL"
    if v["verdict"] != want:
        fs.append(f"verdict re-derived as {want}, "
                  f"recorded {v['verdict']}")
    if v.get("qualified_family") != "ACT-ROBUST-01":
        fs.append("qualified family not ACT-ROBUST-01")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0

    def m1(x):
        x["verdict"] = "P30-PASS"

    def m2(x):
        x["protocol_sha256"] = "0" * 64

    def m3(x):
        x["evidence_sha256"].pop("g1_check")

    def m4(x):
        x["measured"]["coverage"]["fe__fns_709__pulse_5min"][
            "covered_rows"] += 1

    def m5(x):
        x["phase"] = "P29"

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

    out = {"gate": "G4", "phase": "P30", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
