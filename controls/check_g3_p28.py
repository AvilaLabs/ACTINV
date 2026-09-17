#!/usr/bin/env python3
"""P28 G3 checker: every conformance probe's status and error text are
re-verified against the raw artifacts; the shield-passthrough ledger is
re-read from out.json. Rejects planted mutations."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g3")
EV = os.path.join(ROOT, "results", "g3_p28_conformance.json")
OUT = os.path.join(ROOT, "results", "g3_p28_check.json")

EXPECT = {
    "proton": "gap",
    "deuteron": "gap",
    "alpha": "gap",
    "structure_mismatch": "gap",
    "temperature_mismatch": "gap",
    "shield_uncovered_passthrough": "executed",
    "shield_uncovered_required": "gap",
    "shield_table_bad_hash": "gap",
}
# error text must name the refused thing (substring check)
NAMED = {
    "proton": "proton",
    "deuteron": "deuteron",
    "alpha": "alpha",
    "structure_mismatch": "xmas-172",
    "temperature_mismatch": "temperature",
    "shield_uncovered_required": "Co59",
    "shield_table_bad_hash": "SHA-256 mismatch",
}


def check(res, fs):
    by = {p["id"]: p for p in res["probes"]}
    if set(by) != set(EXPECT):
        fs.append(f"probe set drifted: {set(by) ^ set(EXPECT)}")
        return
    for p in res["probes"]:
        pid = p["id"]
        sp = os.path.join(WORK, f"{pid}.json")
        if hashlib.sha256(open(sp, "rb").read()).hexdigest() != \
                p["spec_sha256"]:
            fs.append(f"{pid}: spec digest mismatch")
        if p["status"] != EXPECT[pid]:
            fs.append(f"{pid}: status {p['status']} != {EXPECT[pid]}")
        if p["status"] == "gap":
            err = p.get("error", "")
            want = NAMED[pid]
            if want not in err:
                fs.append(f"{pid}: error does not name '{want}': {err[:80]}")
        else:
            # passthrough: ledger must name the uncovered nuclide
            led = p.get("shielding_ledger", {})
            if led.get("shielding_uncovered") != ["Co59"]:
                fs.append(f"{pid}: uncovered nuclide not ledgered")
            if led.get("require_complete") is not False:
                fs.append(f"{pid}: require_complete wrong")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0
    for mut in [
        lambda r: [p for p in r["probes"] if p["id"] == "proton"][0]
        .__setitem__("status", "executed"),
        lambda r: [p for p in r["probes"] if p["id"] == "alpha"][0]
        .__setitem__("error", "internal error"),
        lambda r: [p for p in r["probes"]
                   if p["id"] == "shield_uncovered_passthrough"][0]
        ["shielding_ledger"].__setitem__("shielding_uncovered", []),
        lambda r: [p for p in r["probes"] if p["id"] ==
                   "shield_table_bad_hash"][0]
        .__setitem__("error", "io error"),
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
    out = {"gate": "G3", "phase": "P28", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
