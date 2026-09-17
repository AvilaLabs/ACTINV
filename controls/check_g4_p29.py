#!/usr/bin/env python3
"""P29 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files.

- rehashes the protocol and every evidence file against the verdict's
  recorded digests;
- verifies gate ordering by commit ancestry;
- re-verifies all prior verdicts verbatim;
- re-derives the criteria census and conformance outcomes from the raw
  evidence;
- re-derives the verdict under the closure rule: PASS only if every
  frozen control holds and no unresolved component passed unchecked;
  CONDITIONAL if an amendment was used or a frozen control is
  unmet/unestablished; FAIL otherwise;
- rejects planted mutations.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p29.json")
SEALS = os.path.join(RES, "g0_p29_seals.json")
G0C = os.path.join(RES, "g0_p29_check.json")
G1 = os.path.join(RES, "g1_p29_refinement.json")
G1C = os.path.join(RES, "g1_p29_check.json")
G2 = os.path.join(RES, "g2_p29_controls.json")
G2C = os.path.join(RES, "g2_p29_check.json")
G3 = os.path.join(RES, "g3_p29_conformance.json")
G3C = os.path.join(RES, "g3_p29_check.json")
OUT = os.path.join(RES, "g4_p29_check.json")

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P29_PROTOCOL.md")
REQUIRED_EVIDENCE = {"g0_seals", "g0_check", "g1_refinement", "g1_check",
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
    if v.get("phase") != "P29":
        fs.append("verdict phase != P29")
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
                 "g1_refinement": G1, "g1_check": G1C,
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

    # re-derive the criteria census from the raw evidence
    g1 = json.load(open(G1))
    pop = set(seals["validation_population"]["criteria_cases"])
    got = {c["case_id"] for c in g1["cases"]}
    if got != pop:
        fs.append("criteria population mismatch")
    n_satisfied = n_unmet = n_unest = 0
    for c in g1["cases"]:
        rf = c.get("refinement")
        if not rf:
            fs.append(f"{c['case_id']}: no refinement record")
            continue
        for cr in rf["criteria"]:
            if cr["verdict"] == "satisfied":
                n_satisfied += 1
            elif cr["verdict"] == "unmet":
                n_unmet += 1
            else:
                n_unest += 1
    if v["measured"]["criteria_population"]["satisfied"] != n_satisfied:
        fs.append("satisfied census mismatch")
    if v["measured"]["criteria_population"]["unmet"] != n_unmet:
        fs.append("unmet census mismatch")
    if v["measured"]["criteria_population"]["unestablished"] != n_unest:
        fs.append("unestablished census mismatch")

    # analytic controls must have executed
    g2 = json.load(open(G2))
    if not all(c["status"] == "executed" for c in g2["controls"]):
        fs.append("a frozen control did not execute")
    g3 = json.load(open(G3))
    probes = {p["id"]: p for p in g3["probes"]}
    if probes["unproducible_response"]["criteria_verdicts"] != \
            ["unestablished"]:
        fs.append("unproducible response did not record unestablished")
    if probes["resource_limit_exhaustion"]["criteria_verdicts"] != \
            ["unmet"]:
        fs.append("exhaustion probe did not record unmet")
    if probes["envelope_violating_criterion"]["status"] != "gap":
        fs.append("envelope probe was not refused")

    # closure rule: PASS requires all controls green AND zero amendments
    # AND no unmet/unestablished; CONDITIONAL on amendment or a gap that
    # leaves downstream scope revised; FAIL otherwise.
    n_amend = len(seals.get("amendments", []))
    controls_ok = all(c["status"] == "executed" for c in g2["controls"]) \
        and not json.load(open(G2C))["failures"]
    if n_unmet + n_unest == 0 and controls_ok:
        want = "P29-CONDITIONAL" if n_amend else "P29-PASS"
    else:
        want = "P29-FAIL"
    if v["verdict"] != want:
        fs.append(f"verdict re-derived as {want}, "
                  f"recorded {v['verdict']}")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0
    for mut in [
        lambda x: x.__setitem__("verdict", "P29-PASS")
        if x["verdict"] != "P29-PASS" else x.__setitem__("phase", "P28"),
        lambda x: x.__setitem__("protocol_sha256", "0" * 64),
        lambda x: x["evidence_sha256"].pop("g1_check"),
        lambda x: x["evidence_sha256"]
        .__setitem__("g1_refinement", "0" * 64),
        lambda x: x.__setitem__("phase", "P28"),
        lambda x: x["measured"]["criteria_population"]
        .__setitem__("satisfied", 999),
    ]:
        planted += 1
        w = copy.deepcopy(v)
        mut(w)
        fs.clear()
        check(w, fs)
        if fs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(v, fs)
    out = {"gate": "G4", "phase": "P29", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
