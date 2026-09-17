#!/usr/bin/env python3
"""P29 G2 checker: independently re-derives the analytic expectations
from the tabulated half-lives and rejects planted mutations."""
import copy
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p29-work/g2")
EV = os.path.join(ROOT, "results", "g2_p29_controls.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p29_seals.json")))
OUT = os.path.join(ROOT, "results", "g2_p29_check.json")
REL = SEALS["tolerances"]["analytic_chain_rel"]
# independent half-lives (ENDF/B-VIII.0 tabulated), not read from the
# producer's record so a planted drift is caught.
T12 = {"Mn56": 9284.04, "Mo99": 237513.6, "Tc99m": 21624.12}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def inv_at(out, nuclide):
    for n in out["steps"][-1].get("inventory", []):
        if n["nuclide"] == nuclide:
            return n["atoms_per_g"]
    return 0.0


def check(res, fs):
    by = {c["id"]: c for c in res["controls"]}
    want = {"impulse_decay_mn56", "chain_mo99_tc99m", "stiff_1y_declared",
            "stiff_1y_reference",
            "fe__fns_709__pulse_5min__near_zero_activity"}
    if set(by) != want:
        fs.append(f"control set drifted: {set(by) ^ want}")
        return
    for c in res["controls"]:
        if c["id"] == "chain_mo99_tc99m":
            continue  # per-hold digests verified below
        sp = os.path.join(WORK, c["id"].replace(
            "fe__fns_709__pulse_5min__near_zero_activity",
            "near_zero_activity") + ".json")
        if sha(sp) != c["spec_sha256"]:
            fs.append(f"{c['id']}: spec digest mismatch")

    # analytic re-derivation against the raw outputs
    imp = by["impulse_decay_mn56"]
    if imp["status"] != "executed":
        fs.append("impulse decay not executed")
    else:
        out = json.load(open(os.path.join(
            WORK, "impulse_decay_mn56.json.out.json")))
        got = inv_at(out, "Mn56")
        exp = 1e15 * math.exp(-math.log(2) / T12["Mn56"] * 86400.0)
        if abs(got - exp) / exp > REL:
            fs.append(f"impulse decay rel err {abs(got-exp)/exp:.3g}")
        if abs(imp["observed"] - got) / exp > REL:
            fs.append("impulse recorded value not re-derivable")
    ch = by["chain_mo99_tc99m"]
    if ch["status"] != "executed":
        fs.append("Mo99 chain not executed")
    else:
        l1 = math.log(2) / T12["Mo99"]
        l2 = math.log(2) / T12["Tc99m"]
        n0 = 1e15

        def bateman_d(b, t):
            return n0 * b * l1 * (math.exp(-l1 * t)
                                  - math.exp(-l2 * t)) / (l2 - l1)

        obs = {}
        for tag, t in (("short", 3600.0), ("long", 86400.0)):
            out = json.load(open(os.path.join(
                WORK, f"chain_mo99_tc99m_{tag}.json.out.json")))
            got_p = inv_at(out, "Mo99")
            exp_p = n0 * math.exp(-l1 * t)
            if abs(got_p - exp_p) / exp_p > REL:
                fs.append(f"chain {tag} parent rel err "
                          f"{abs(got_p-exp_p)/exp_p:.3g}")
            got_d = inv_at(out, "Tc99m") + inv_at(out, "Tc99m1")
            obs[tag] = got_d
            # recorded holds must match the raw artifact
            h = ch["holds"][tag]
            if abs(h.get("Mo99", -1) - got_p) / max(exp_p, 1e-300) > REL:
                fs.append(f"chain {tag} recorded parent not re-derivable")
            if abs(h.get("Tc99m", -1) - got_d) / max(got_d, 1e-300) > 1e-9:
                fs.append(f"chain {tag} recorded daughter not "
                          "re-derivable")
            if sha(os.path.join(WORK,
                                f"chain_mo99_tc99m_{tag}.json")) != \
                    h.get("spec_sha256"):
                fs.append(f"chain {tag} spec digest mismatch")
        # infer the effective Mo99->Tc99m branch at the short hold,
        # then predict the long-hold daughter independently
        denom_s = n0 * l1 * (math.exp(-l1 * 3600.0)
                             - math.exp(-l2 * 3600.0)) / (l2 - l1)
        b_hat = obs["short"] / denom_s
        if not (0.0 < b_hat <= 1.0 + 1e-9):
            fs.append(f"inferred branch {b_hat} outside (0,1]")
        pred_long = bateman_d(b_hat, 86400.0)
        if pred_long > 0:
            rel = abs(obs["long"] - pred_long) / pred_long
            if rel > REL:
                fs.append(f"chain long-hold prediction rel err "
                          f"{rel:.3g}")

    sd = by["stiff_1y_declared"]
    sr = by["stiff_1y_reference"]
    if sd["status"] != "executed" or sr["status"] != "executed":
        fs.append("stiff case did not execute on both settings")
    elif sr["total_activity"] > 0:
        r = abs(sd["total_activity"] - sr["total_activity"]) \
            / sr["total_activity"]
        if r > SEALS["tolerances"]["reference_rel"]:
            fs.append(f"stiff declared-vs-reference rel {r:.3g}")
        res["_stiff_rel_diff"] = r

    nz = by["fe__fns_709__pulse_5min__near_zero_activity"]
    if nz["status"] != "executed":
        fs.append("near-zero case not executed")
    elif nz["total_activity"] != 0.0:
        fs.append("near-zero activity is not exactly zero")


def main():
    res = json.load(open(EV))
    fs = []
    check(res, fs)

    planted = rejected = 0
    for mut in [
        lambda r: [c for c in r["controls"]
                   if c["id"] == "impulse_decay_mn56"][0]
        .__setitem__("observed", 1.0),
        lambda r: [c for c in r["controls"]
                   if c["id"] == "chain_mo99_tc99m"][0]
        ["holds"]["long"].__setitem__("Tc99m", 1.0),
        lambda r: [c for c in r["controls"]
                   if c["id"] == "stiff_1y_declared"][0]
        .__setitem__("spec_sha256", "0" * 64),
        lambda r: [c for c in r["controls"] if c["id"] ==
                   "fe__fns_709__pulse_5min__near_zero_activity"][0]
        .__setitem__("total_activity", 1e3),
        lambda r: r["controls"].pop(),
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
    out = {"gate": "G2", "phase": "P29", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
