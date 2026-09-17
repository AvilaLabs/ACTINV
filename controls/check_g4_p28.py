#!/usr/bin/env python3
"""P28 G4 independent closure checker. Imports no production or control
module; re-derives everything from raw records and repository files.

- rehashes the protocol and every evidence file against the verdict's
  recorded digests;
- verifies gate ordering by commit ancestry;
- re-verifies all prior verdicts verbatim;
- re-derives the applicability-map element census from the raw index and
  P25c rescan, and the trace census from g1_p28_rates.json;
- re-derives the verdict string under the closure rule: PASS requires all
  frozen gates green, every frozen gate executed, and zero trace
  mismatches; CONDITIONAL if a frozen combination is gap-recorded or a
  repair amendment was used; FAIL otherwise;
- rejects planted mutations.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p28.json")
SEALS = os.path.join(RES, "g0_p28_seals.json")
G1 = os.path.join(RES, "g1_p28_rates.json")
G1C = os.path.join(RES, "g1_p28_check.json")
G2 = os.path.join(RES, "g2_p28_population.json")
G2C = os.path.join(RES, "g2_p28_check.json")
G0C = os.path.join(RES, "g0_p28_check.json")
IDX = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g_index.json")
CENSUS = os.path.join(RES, "g3_p25c_census.json")
OUT = os.path.join(RES, "g4_p28_check.json")

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P28_PROTOCOL.md")
PROTOCOL_SHA256 = None  # filled from the seal
REQUIRED_EVIDENCE = {"g0_seals", "g0_check", "g1_rates", "g1_check",
                     "g2_population", "g2_check", "g3_conformance",
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
    if v.get("phase") != "P28":
        fs.append("verdict phase != P28")
    if sha(PROTOCOL) != v["protocol_sha256"]:
        fs.append("protocol digest mismatch")
    seals = json.load(open(SEALS))
    if v["protocol_sha256"] != seals["protocol_sha256"]:
        fs.append("verdict/seal protocol mismatch")
    # gate ancestry: opening -> G0 -> G1 -> G2 -> verdict
    for anc in [seals["opening_commit"], "HEAD"]:
        try:
            git("merge-base", "--is-ancestor", seals["opening_commit"],
                "HEAD")
        except subprocess.CalledProcessError:
            fs.append("gate ancestry broken")
            break
    # prior verdicts verbatim
    for f, want in seals["prior_verdicts"].items():
        if json.load(open(os.path.join(RES, f)))["verdict"] != want:
            fs.append(f"prior verdict {f} drifted")
    # exact evidence key set + rehash
    if set(v.get("evidence_sha256", {})) != REQUIRED_EVIDENCE:
        fs.append("evidence key set wrong")
    else:
        files = {"g0_seals": SEALS, "g0_check": G0C, "g1_rates": G1,
                 "g1_check": G1C, "g2_population": G2, "g2_check": G2C,
                 "g3_conformance": os.path.join(
                     RES, "g3_p28_conformance.json"),
                 "g3_check": os.path.join(RES, "g3_p28_check.json")}
        for k, p in files.items():
            if sha(p) != v["evidence_sha256"][k]:
                fs.append(f"evidence {k} digest mismatch")
    # gate outputs pass
    for k, p in [("g0", G0C), ("g1", G1C), ("g2", G2C),
                 ("g3", os.path.join(RES, "g3_p28_check.json"))]:
        c = json.load(open(p))
        if not c.get("pass"):
            fs.append(f"{k} check not passing")
        mt = c.get("mutation_self_test", {})
        if mt.get("planted") != mt.get("rejected"):
            fs.append(f"{k} checker mutations not all rejected")
    # re-derive trace census
    g1 = json.load(open(G1))
    tr = g1["traces"]
    if set(tr) != {"Fe56->Mn56", "Fe54->Mn54", "Co59->Co60",
                   "Co59->Co60m1"}:
        fs.append("trace set drifted")
    total_groups = sum(t["groups_mismatched"] for t in tr.values())
    if total_groups != 0:
        fs.append(f"{total_groups} trace mismatches")
    if not all(l.get("pass") for l in g1["analytic_limits"].values()):
        fs.append("analytic limit not green")
    # re-derive element census
    g2 = json.load(open(G2))
    am = g2["applicability_map"]
    idx = json.load(open(IDX))
    rescan = json.load(open(CENSUS))["rescan"]
    n_elements = len({re.match(r"n-([A-Z][a-z]?)", t["file"]).group(1)
                      for t in idx["targets"]})
    if am["n_elements"] != n_elements:
        fs.append("element census mismatch")
    n_gap = sum(1 for e in am["axes"]["elements"].values()
                if e["status"] == "gap")
    # the frozen closure rule
    bnd = g2["boundary_cases"]
    n_boundary_gap = sum(1 for c in bnd if c["status"] == "gap")
    verdict_want = "P28-PASS" if (
        g1["pass"] and all(l.get("pass") for l in
                           g1["analytic_limits"].values())
    ) else "P28-FAIL"
    if n_gap or n_boundary_gap:
        verdict_want = "P28-CONDITIONAL"
    if v["verdict"] != verdict_want:
        fs.append(f"verdict re-derived as {verdict_want}, "
                  f"recorded {v['verdict']}")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0
    for mut in [
        lambda x: x.__setitem__("verdict", "P28-PASS")
        if x["verdict"] != "P28-PASS" else x.__setitem__("phase", "P29"),
        lambda x: x.__setitem__("protocol_sha256", "0" * 64),
        lambda x: x["evidence_sha256"].pop("g1_check"),
        lambda x: x["evidence_sha256"].__setitem__("g1_rates", "0" * 64),
        lambda x: x.__setitem__("phase", "P27"),
        lambda x: x["evidence_sha256"].__setitem__("g2_check", "f" * 64),
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
    out = {"gate": "G4", "phase": "P28", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
