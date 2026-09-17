#!/usr/bin/env python3
"""P28 G2 checker: re-form response metrics from raw out.json artifacts,
verify the applicability map reconciles to the regime axes, and reject
planted mutations."""
import copy
import hashlib
import json
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g2")
EV = os.path.join(ROOT, "results", "g2_p28_population.json")
OUT = os.path.join(ROOT, "results", "g2_p28_check.json")
IDX = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g_index.json")
CENSUS = os.path.join(ROOT, "results", "g3_p25c_census.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p28_seals.json")))
SMOKE_REC = os.path.join(WORK, "smoke", "run", "study_record.json")


def check(res, fs):
    seals_ids = {c["id"] for c in
                 SEALS["validation_population"]["boundary"]["cases"]}
    got_ids = {c["id"] for c in res["boundary_cases"]}
    if got_ids != seals_ids:
        fs.append(f"boundary population drifted: {got_ids ^ seals_ids}")
        return

    for c in res["boundary_cases"]:
        sp = os.path.join(WORK, "cases", f"{c['id']}.json")
        if hashlib.sha256(open(sp, "rb").read()).hexdigest() != \
                c["spec_sha256"]:
            fs.append(f"{c['id']}: spec digest mismatch")
        if c["status"] == "executed":
            op = os.path.join(WORK, "cases", f"{c['id']}.out.json")
            if not os.path.isfile(op) or "out_sha256" not in c:
                fs.append(f"{c['id']}: missing out.json/digest")
                continue
            if hashlib.sha256(open(op, "rb").read()).hexdigest() != \
                    c["out_sha256"]:
                fs.append(f"{c['id']}: out digest mismatch")
            o = json.load(open(op))
            st = o["steps"][-1]
            act = sum(v for v in st["activity_Bq_per_g"].values())
            if act != c["responses"]["total_activity_bq_per_g"]:
                fs.append(f"{c['id']}: activity not re-formed")
        elif c["status"] != "gap":
            fs.append(f"{c['id']}: unexpected status {c['status']}")

    # boundary expectations
    by_id = {c["id"]: c for c in res["boundary_cases"]}
    if by_id["fe__fns_709__pulse_5min__proton"]["status"] != "gap":
        fs.append("proton boundary did not fail closed")
    zf = by_id["fe__fns_709__zero_flux"]
    if zf["status"] == "executed" and \
            zf["responses"]["total_activity_bq_per_g"] != 0.0:
        fs.append("zero-flux schedule produced activity")
    di = by_id["fe__fns_709__pulse_5min__dilute_1e10b"]
    base_act = None
    # infinite-dilution must reproduce unshielded within 1e-9
    smoke_rec = json.load(open(SMOKE_REC))
    fe_fns = [c for c in smoke_rec["cases"]
              if c["case_id"] == "fe__fns_709__pulse_5min"]
    if fe_fns:
        base_act = fe_fns[0]["per_time"]["0"]["total_activity_bq_per_g"]
        if di["status"] == "executed" and base_act:
            rel = abs(di["responses"]["total_activity_bq_per_g"]
                      - base_act) / base_act
            if rel > SEALS["tolerances"][
                    "dilution_infinite_limit_relative"]:
                fs.append(f"infinite-dilution limit rel err {rel}")
    ni = by_id["ni__fns_709__pulse_5min"]
    if ni["status"] == "executed" and \
            not ni.get("ledger_flags"):
        fs.append("Ni case executed without a coverage ledger entry")
    # the missing Ni-58 must be recorded as a silent-coverage fact by the
    # producer, and the raw run ledger must NOT be credited with naming it
    if ni["status"] == "executed":
        if ni.get("silent_coverage_absence") != ["Ni-58"]:
            fs.append("silent-coverage record missing Ni-58")
        lim = json.dumps(ni.get("ledger_flags", {}))
        if "Ni58" in lim or "28058" in lim:
            fs.append("Ni-58 absence was in fact ledgered; the "
                      "silent-coverage record is stale")

    # smoke population accounting
    if res["smoke_study"].get("verdict") not in ("complete", "partial"):
        fs.append("smoke study verdict missing")
    pop = res["smoke_study"].get("population", {})
    if pop.get("executed", 0) != 8:
        fs.append(f"smoke executed != 8: {pop}")

    # applicability map completeness
    am = res["applicability_map"]
    idx = json.load(open(IDX))
    rescan = json.load(open(CENSUS))["rescan"]
    n_elements = len({re.match(r"n-([A-Z][a-z]?)", t["file"]).group(1)
                      for t in idx["targets"]})
    if am["n_elements"] != n_elements:
        fs.append("element map size != artifact coverage")
    # temperature axis must reflect the measured boundary
    tb = am["axes"]["temperature_K"]
    if tb.get("qualified") != [293.6] or not tb.get("measured_boundary"):
        fs.append("temperature axis does not record the measured boundary")
    bc = {c["id"]: c["status"] for c in res["boundary_cases"]}
    for cid in ("fe__fns_709__pulse_5min__T0K",
                "fe__fns_709__pulse_5min__T1200K"):
        if bc.get(cid) != "gap":
            fs.append(f"{cid}: expected fail-closed gap")
    cc = am["element_counts"]
    if sum(cc.values()) != am["n_elements"]:
        fs.append("element counts do not reconcile")
    # every element's defect list must match the P25c rescan verbatim
    for el, e in am["axes"]["elements"].items():
        for d in e["defect_ledger_files"]:
            r = rescan.get(d["file"])
            if not r or r.get("pre_class") != d["pre_class"]:
                fs.append(f"{el}/{d['file']}: defect record mismatch")
    # no element may be 'qualified' while carrying a ledger file
    for el, e in am["axes"]["elements"].items():
        want = ("gap" if e["dosimetry_missing_za"] else
                "qualified_with_ledger" if e["defect_ledger_files"]
                else "qualified")
        if e["status"] != want:
            fs.append(f"{el}: status {e['status']} != derived {want}")

    # excluded axes must stay excluded
    for proj in ("proton", "deuteron", "alpha"):
        if am["axes"]["projectile"][proj]["status"] != "excluded":
            fs.append(f"projectile {proj} not excluded")
    if am["axes"]["group_structure"]["other"]["status"] != "excluded":
        fs.append("non-709 group structures not excluded")

    # independent reference integrity
    for cid, ref in res["independent_reference"]["cases"].items():
        if "alara" not in ref["arms"] or "actinv" not in ref["arms"]:
            fs.append(f"{cid}: reference arm missing")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0
    for mut in [
        lambda r: r["boundary_cases"][0].__setitem__("status", "executed"),
        lambda r: r["applicability_map"]["axes"]["elements"]["Ni"]
        .__setitem__("status", "qualified"),
        lambda r: r["applicability_map"]["axes"]["projectile"]["proton"]
        .__setitem__("status", "qualified"),
        lambda r: r["smoke_study"]["population"].__setitem__("executed", 7),
        lambda r: r["boundary_cases"].pop(),
    ]:
        planted += 1
        v = copy.deepcopy(res)
        mut(v)
        fs.clear()
        check(v, fs)
        if fs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(res, fs)
    out = {"gate": "G2", "phase": "P28", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
