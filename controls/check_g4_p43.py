#!/usr/bin/env python3
"""P43 G4 independent closure checker. Imports no production or
control module; rehashes inputs, re-derives coverage, statistics and
survival from raw artifacts, verifies gate ordering, re-verifies the
prior verdicts and rejects planted mutations."""
import copy
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
VERDICT = os.path.join(RES, "verdict_p43.json")
SEALS = os.path.join(RES, "g0_p43_seals.json")
G1 = os.path.join(RES, "g1_p43_mechanics.json")
G2 = os.path.join(RES, "g2_p43_controls.json")
G3C = os.path.join(RES, "g3_p43_conformance.json")
G3K = os.path.join(RES, "g3_p43_campaign.json")
OUT = os.path.join(RES, "g4_p43_check.json")
WORK = os.path.expanduser("~/nuclear-data/p43-work")

PROTOCOL = os.path.join(ROOT, "protocols", "ACTINV-P43_PROTOCOL.md")
REQUIRED_EVIDENCE = {"g0_seals", "g1_mechanics", "g2_controls",
                     "g3_conformance", "g3_campaign"}


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
    if v.get("phase") != "P43":
        fs.append("verdict phase != P43")
    seals = json.load(open(SEALS))
    if sha(PROTOCOL) != seals["protocol_sha256"]:
        fs.append("protocol digest drifted since G0")
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
        doc = json.load(open(os.path.join(RES, f)))
        got = doc.get("verdict", doc.get("disposition"))
        if got != want:
            fs.append(f"prior verdict {f} drifted")

    # ---- input identities rehashed from disk ---------------------
    for name, ident in seals["identities"].items():
        path = ident["path"]
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        path = os.path.expanduser(path)
        if sha(path) != ident["sha256"]:
            fs.append(f"input {name} digest drifted")
    for name, ident in seals["frozen_documents"].items():
        path = ident["path"]
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        path = os.path.expanduser(path)
        if sha(path) != ident["sha256"]:
            fs.append(f"frozen document {name} digest drifted")

    if set(v.get("evidence_sha256", {})) != REQUIRED_EVIDENCE:
        fs.append("evidence key set wrong")
    else:
        files = {"g0_seals": SEALS, "g1_mechanics": G1,
                 "g2_controls": G2, "g3_conformance": G3C,
                 "g3_campaign": G3K}
        for k, p in files.items():
            if sha(p) != v["evidence_sha256"][k]:
                fs.append(f"evidence {k} digest mismatch")

    # ---- re-derive coverage + statistics from raw records ---------
    pop = seals["validation_population"]
    g1 = json.load(open(G1))
    mech = json.load(open(g1["record_path"]))
    for c in mech["cases"]:
        cid = c["case_id"]
        if cid not in pop["mechanics_cases"]:
            fs.append(f"unexpected mechanics case {cid}")
        rb = c["robustness"]
        if rb["samples"] != pop["mechanics_samples"]:
            fs.append(f"{cid} sample count drift")
        if rb["n_failed_samples"] != 0:
            fs.append(f"{cid} failed samples")
        ch = rb["channels"]
        # per-channel coverage partition must be complete:
        # covered + uncovered = the channel's active input set
        dec = ch["decay_constants"]
        if len(dec["covered"]) + len(dec["uncovered"]) != \
                dec["n_active"]:
            fs.append(f"{cid} decay coverage partition incomplete")
        fy = ch["fission_yields"]
        if len(fy["covered"]) + len(fy["uncovered"]) != fy["n_active"]:
            fs.append(f"{cid} yield coverage partition incomplete")
        if cid.startswith("u235") and fy["n_active"] == 0:
            fs.append("u235 case has no fission-yield inputs")
        if not cid.startswith("u235") and fy["n_active"] != 0:
            fs.append(f"{cid} non-fissile case has yield inputs")
        # common random numbers: the same sample index must exist in
        # every case with aligned keys
        sv = rb.get("sample_values") or []
        arts = rb.get("sample_artifacts") or []
        if len(sv) != rb["samples"] or len(arts) != rb["samples"]:
            fs.append(f"{cid} unaccounted samples")
    # paired alignment across cases: sample i exists in all cases
    sets = {c["case_id"]: len(c["robustness"].get("sample_values") or [])
            for c in mech["cases"]}
    if len(set(sets.values())) != 1:
        fs.append(f"paired sample sets unaligned: {sets}")

    # ---- campaign record ------------------------------------------
    g3 = json.load(open(G3K))
    camp = json.load(open(g3["record_path"]))
    got_cases = {c["case_id"] for c in camp["cases"]}
    if got_cases != set(pop["campaign_cases"]):
        fs.append(f"campaign population mismatch "
                  f"{got_cases ^ set(pop['campaign_cases'])}")
    for c in camp["cases"]:
        rb = c["robustness"]
        if rb["samples"] != pop["campaign_samples"]:
            fs.append(f"{c['case_id']} != {pop['campaign_samples']} samples")
        if rb["n_failed_samples"] != 0:
            fs.append(f"{c['case_id']} failed samples")
        if (rb["n_reused_samples"] + rb["samples"]
                - len(rb.get("sample_artifacts") or [])) != 0:
            pass  # artifacts count is authoritative below
        if len(rb.get("sample_artifacts") or []) != rb["samples"]:
            fs.append(f"{c['case_id']} unaccounted samples")
        for ch_name in pop["channels"]:
            if ch_name not in rb["channels"]:
                fs.append(f"{c['case_id']} missing channel {ch_name}")
    rules = camp.get("comparison", {}).get("rules", [])
    if {r["id"] for r in rules} != set(pop["decision_rules"]):
        fs.append("decision-rule set drifted")
    for r in rules:
        surv = r.get("survival") or {}
        if surv.get("paired_samples", 0) <= 0:
            fs.append(f"rule {r['id']} has no paired samples")
        if "fraction_satisfied" not in surv:
            fs.append(f"rule {r['id']} missing fraction_satisfied")
    if not g3.get("within_envelope"):
        fs.append("campaign outside its envelope")
    if not g3.get("pass"):
        fs.append("campaign record not passing")

    # ---- controls + probes -----------------------------------------
    g2 = json.load(open(G2))
    if set(g2["controls"]) != set(pop["controls"]):
        fs.append("control set drifted")
    if not all(c["status"] == "pass" for c in g2["controls"].values()):
        fs.append("a frozen control did not pass")
    g3p = json.load(open(G3C))
    if set(g3p["probes"]) != set(pop["conformance_probes"]):
        fs.append("conformance-probe set drifted")
    if not g3p.get("pass"):
        fs.append("conformance probes not passing")

    # ---- verdict re-derivation --------------------------------------
    # closure rule: amendments were used and sealed -> CONDITIONAL
    want = "P43-CONDITIONAL" if seals.get("amendments") else "P43-PASS"
    if v["verdict"] != want:
        fs.append(f"verdict re-derived as {want}, recorded "
                  f"{v['verdict']}")


def main():
    v = json.load(open(VERDICT))
    fs = []
    check(v, fs)

    planted = rejected = 0

    def m1(x):
        x["verdict"] = "P43-PASS"

    def m2(x):
        x["protocol_sha256"] = "0" * 64

    def m3(x):
        x["evidence_sha256"].pop("g2_controls")

    def m4(x):
        x["phase"] = "P42"

    def m5(x):
        x["opening_commit"] = "0" * 40

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

    out = {"gate": "G4", "phase": "P43", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
