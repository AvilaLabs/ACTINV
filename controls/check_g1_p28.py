#!/usr/bin/env python3
"""P28 G1 checker: re-derive trace metrics from raw out.json artifacts and
the npz bytes; verify analytic-limit arithmetic; reject planted mutations."""
import copy
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g1")
EV = os.path.join(ROOT, "results", "g1_p28_rates.json")
OUT = os.path.join(ROOT, "results", "g1_p28_check.json")
NPZ = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.npz")
IDX = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g_index.json")

NP, PHI, T = 1e20, 1e10, 1e-4


def check(res, fs):
    z = np.load(NPZ)
    idx = json.load(open(IDX))
    rows, sig = z["rows"], z["sig"]
    targets = idx["targets"]
    tidx = {t["za"]: i for i, t in enumerate(targets)}
    pza = {"Fe56": 26056, "Fe54": 26054, "Co59": 27059}

    # 1. every trace's artifact values must equal the npz row bytes
    for name, tr in res["traces"].items():
        parent, daughter = name.split("->")
        cand = [i for i in range(len(rows))
                if rows[i, 0] == tidx[pza[parent]]
                and rows[i, 1] == tr["mt"] and rows[i, 2] == tr["zap"]
                and rows[i, 3] == tr["lfs"]]
        if cand != [tr["row_index"]]:
            fs.append(f"{name}: row_index does not resolve")
            continue
        col = (lambda g: g) if tr["column_order"] == "identity" \
            else (lambda g: 708 - g)
        mism = 0
        for gg in tr["per_group"]:
            a = sig[tr["row_index"], col(gg["g"])]
            if "artifact" in gg and abs(gg["artifact"] - a) > 0:
                fs.append(f"{name} g{gg['g']}: artifact value != npz")
            if "recovered" in gg:
                # re-derive recovered from the raw out.json
                op = os.path.join(WORK, "outs",
                                  f"{parent}-g{gg['g']:03d}.out.json")
                inv = {n["nuclide"]: n["atoms_per_g"]
                       for n in json.load(open(op))["steps"][0]
                       ["inventory"]}
                want = inv.get(daughter, 0.0) / (NP * PHI * T) * 1e24
                if abs(gg["recovered"] - want) > 0:
                    fs.append(f"{name} g{gg['g']}: recovered not "
                              f"re-derivable from out.json")
            if gg["status"] == "mismatch":
                mism += 1
        if mism != tr["groups_mismatched"]:
            fs.append(f"{name}: mismatched count inconsistent")
        if tr["pass"] != (mism == 0):
            fs.append(f"{name}: pass flag inconsistent")
        if mism:
            fs.append(f"{name}: {mism} group(s) exceed tolerance")

    # 2. analytic limits re-checked arithmetically
    lim = res["analytic_limits"]
    if not lim["zero_flux"]["pass"]:
        fs.append("zero_flux limit failed")
    pd = lim["pure_decay"]
    lam = math.log(2) / pd["halflife_s"]
    if abs(pd["expected_ratio"] - math.exp(-lam * pd["hold_s"])) > 0:
        fs.append("pure_decay expected_ratio not re-derivable")
    if abs(pd["measured_ratio"] - pd["n1"] / pd["n0"]) > 0:
        fs.append("pure_decay measured_ratio not re-derivable")
    if not pd["pass"]:
        fs.append("pure_decay limit failed")
    if not lim["production_loss_closure"]["pass"]:
        fs.append("production/loss closure failed")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0
    for mut in [
        lambda r: r["traces"]["Fe56->Mn56"]
        .__setitem__("column_order", "identity"),
        lambda r: r["traces"]["Fe56->Mn56"]["per_group"][60]
        .__setitem__("recovered", 9.99),
        lambda r: r["analytic_limits"]["pure_decay"]
        .__setitem__("measured_ratio", 1.0),
        lambda r: r["analytic_limits"]["zero_flux"]
        .__setitem__("pass", False),
        lambda r: r["traces"]["Co59->Co60"].__setitem__("pass", False),
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
    out = {"gate": "G1", "phase": "P28", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
