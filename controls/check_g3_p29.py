#!/usr/bin/env python3
"""P29 G3 checker: verifies the three frozen conformance probes
exercised the required surfaces and records the observed outcomes."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p29-work/g3")
EV = os.path.join(ROOT, "results", "g3_p29_conformance.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
OUT = os.path.join(ROOT, "results", "g3_p29_check.json")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(res, fs):
    by = {p["id"]: p for p in res["probes"]}
    want = set(SEALS["validation_population"]["negative_controls"])
    if set(by) != want:
        fs.append(f"probe set drifted: {set(by) ^ want}")
        return
    for p in res["probes"]:
        sp = os.path.join(WORK, p["id"] + ".json")
        if sha(sp) != p["spec_sha256"]:
            fs.append(f"{p['id']}: spec digest mismatch")

    up = by["unproducible_response"]
    if up["status"] != "executed":
        fs.append("unproducible_response did not execute")
    elif up["criteria_verdicts"] != ["unestablished"]:
        fs.append(f"unproducible_response verdicts "
                  f"{up['criteria_verdicts']}")
    # verify against the raw record
    rr = os.path.join(WORK, "unprod_run", "study_record.json")
    if os.path.isfile(rr):
        crit = json.load(open(rr))["cases"][0].get(
            "refinement", {}).get("criteria", [])
        if not crit or crit[0]["verdict"] != "unestablished":
            fs.append("raw record does not show unestablished")
        if not crit or crit[0]["time_s"] != 3600.0:
            fs.append("raw record criterion time drifted")

    ex = by["resource_limit_exhaustion"]
    if ex["status"] != "executed":
        fs.append("resource_limit_exhaustion did not execute")
    else:
        if ex["criteria_verdicts"] != ["unmet"]:
            fs.append(f"exhaustion verdicts {ex['criteria_verdicts']}")
        if ex.get("escalation_runs") != [0]:
            fs.append("exhaustion probe ran escalations")
    rr = os.path.join(WORK, "exhaust_run", "study_record.json")
    if os.path.isfile(rr):
        crit = json.load(open(rr))["cases"][0].get(
            "refinement", {}).get("criteria", [])
        if not crit or crit[0]["verdict"] != "unmet":
            fs.append("raw record does not show unmet")
        elif not crit[0].get("resource_limit_reached"):
            fs.append("raw record missing resource_limit_reached")

    ev = by["envelope_violating_criterion"]
    if ev["status"] != "gap":
        fs.append("envelope-violating criterion was not refused")
    elif "not qualified" not in (ev.get("error") or ""):
        fs.append("refusal did not name the unqualified response")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0
    for mut in [
        lambda r: [p for p in r["probes"]
                   if p["id"] == "unproducible_response"][0]
        .__setitem__("criteria_verdicts", ["satisfied"]),
        lambda r: [p for p in r["probes"]
                   if p["id"] == "resource_limit_exhaustion"][0]
        .__setitem__("criteria_verdicts", ["satisfied"]),
        lambda r: [p for p in r["probes"]
                   if p["id"] == "envelope_violating_criterion"][0]
        .__setitem__("status", "executed"),
        lambda r: [p for p in r["probes"]][0]
        .__setitem__("spec_sha256", "0" * 64),
        lambda r: r["probes"].pop(),
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
    out = {"gate": "G3", "phase": "P29", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
