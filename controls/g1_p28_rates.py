#!/usr/bin/env python3
"""P28 G1: source-to-rate traces + analytic limits.

Rate trace: for each frozen (parent, channel) run 709 single-group
unit-flux probes through `actinv run`. A single-group spectrum forces
sigma_eff = sigma_g; the daughter inventory after a 1e-4 s pulse gives
sigma_g = N_d / (N_parent * phi_g * t) with decay bias < 6e-8 (shortest-
lived daughter Mn-56). Recovered sigma_g is compared to the artifact
`sig` row; the npz column order is resolved empirically.

Analytic limits: zero-flux (production exactly zero), pure-decay tail
(Bateman decay of Mn-56 over a 1 d zero-flux step), and the recorded
pathway-closure / production-loss accounting on an executed case.
"""
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
NPZ = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.npz")
IDX = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g_index.json")
DECAY = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                     "endf-b-viii-0_decay.dat")
DECAY_FB = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                        "jeff-3-3_decay.dat")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p28_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g1")
OUT = os.path.join(ROOT, "results", "g1_p28_rates.json")
NP = 1e20          # parent atoms_per_g
PHI = 1e10         # probe flux n/cm2/s
T = 1e-4           # pulse duration s
TOL = SEALS["tolerances"]["rate_trace_relative"]

PARENTS = {
    "Fe56": {"material": "Fe56",
             "channels": [("Mn56", 103, 25056, 0)]},
    "Fe54": {"material": "Fe54",
             "channels": [("Mn54", 103, 25054, 0)]},
    "Co59": {"material": "Co59",
             "channels": [("Co60", 102, 27060, 0),
                          ("Co60m1", 102, 27060, 1)]},
}
PARENT_ZA = {"Fe56": 26056, "Fe54": 26054, "Co59": 27059}


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env", f"TMPDIR={WORK}", *cmd]


def spec_for(material_key, flux_desc, name, dt=None):
    return {
        "spec": "actinv-spec-1",
        "title": name,
        "projectile": "neutron",
        "library": {"path": NPZ,
                    "sha256": SEALS["identities"]["activation_library"]
                    ["sha256"]},
        "decay": {"primary": DECAY, "fallback": DECAY_FB},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {material_key: NP}},
        "spectrum": {"structure": "fispact-709",
                     "flux_per_group": flux_desc,
                     "total": PHI, "descending": True},
        "schedule": [{"dt": f"{dt or T} s", "flux": 1.0}],
        "options": {"mode": "trace"},
        "self_shielding": None, "radiological": None, "damage": None,
        "fission_yields": {"energy": "spectrum_average", "files": [],
                           "fixed_energy_eV": None},
        "uncertainty": None,
    }


def run_one(spec, tag):
    sp = os.path.join(WORK, "specs", f"{tag}.json")
    op = os.path.join(WORK, "outs", f"{tag}.out.json")
    if os.path.exists(op):
        prior = json.dumps(json.load(open(sp)), sort_keys=True) \
            if os.path.exists(sp) else ""
        if prior == json.dumps(spec, sort_keys=True):
            return json.load(open(op)), None
    json.dump(spec, open(sp, "w"))
    r = subprocess.run(cgroup([ACTINV, "run", sp, op]),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr[-300:] or r.stdout[-300:]
    return json.load(open(op)), None


def main():
    os.makedirs(os.path.join(WORK, "specs"), exist_ok=True)
    os.makedirs(os.path.join(WORK, "outs"), exist_ok=True)
    z = np.load(NPZ)
    idx = json.load(open(IDX))
    rows, sig = z["rows"], z["sig"]
    targets = idx["targets"]
    tidx = {t["za"]: i for i, t in enumerate(targets)}

    traces = {}
    n_runs = 0
    for parent, pdef in PARENTS.items():
        rec_by_g = {}
        for g in range(709):
            f = [0.0] * 709
            f[g] = PHI
            out, err = run_one(spec_for(pdef["material"], f,
                                        f"{parent}-g{g:03d}"),
                               f"{parent}-g{g:03d}")
            n_runs += 1
            if out is None:
                print(f"{parent} g{g}: run failed: {err}")
                sys.exit(1)
            inv = {n["nuclide"]: n["atoms_per_g"]
                   for n in out["steps"][0]["inventory"]}
            rec_by_g[g] = inv
        for (daughter, mt, zap, lfs) in pdef["channels"]:
            ti = tidx[PARENT_ZA[parent]]
            cand = [i for i in range(len(rows))
                    if rows[i, 0] == ti and rows[i, 1] == mt
                    and rows[i, 2] == zap and rows[i, 3] == lfs]
            assert len(cand) == 1, (parent, daughter, cand)
            ri = cand[0]
            art = sig[ri]
            rec = np.array([rec_by_g[g].get(daughter, 0.0)
                            / (NP * PHI * T) * 1e24  # cm^2 -> barns
                            for g in range(709)])
            # choose the column order by fewest detectable mismatches
            # (a below-floor artifact sigma legitimately recovers as zero)
            best = None
            for order_name, col in [("identity", lambda g: g),
                                    ("reversed", lambda g: 708 - g)]:
                score = 0
                mx = 0.0
                for g in range(709):
                    a, r_ = art[col(g)], rec[g]
                    det = NP * PHI * T * a * 1e-24 > 1e-8 or r_ > 0
                    if not det:
                        continue
                    d = abs(a - r_) / max(a, r_)
                    mx = max(mx, d)
                    if d > TOL:
                        score += 1
                if best is None or (score, mx) < (best[0], best[1]):
                    best = (score, mx, order_name, col)
            _score, mx, order_name, col = best
            groups = []
            below = mism = 0
            for g in range(709):
                a, r_ = art[col(g)], rec[g]
                det = NP * PHI * T * a * 1e-24 > 1e-8  # above bmin floor
                if a == 0 and r_ == 0:
                    groups.append({"g": g, "status": "zero"})
                elif not det and r_ == 0.0:
                    below += 1
                    groups.append({"g": g, "artifact": a,
                                   "status": "below_detection"})
                else:
                    d = abs(a - r_) / max(a, r_)
                    st = "match" if d <= TOL else "mismatch"
                    if st == "mismatch":
                        mism += 1
                    groups.append({"g": g, "artifact": a, "recovered": r_,
                                   "rel": d, "status": st})
            ok = mism == 0
            traces[f"{parent}->{daughter}"] = {
                "row_index": ri, "mt": mt, "zap": zap, "lfs": lfs,
                "column_order": order_name,
                "groups_mismatched": mism,
                "groups_below_detection": below,
                "max_rel": mx, "pass": ok,
                "per_group": groups}
            print(f"{parent}->{daughter}: order={order_name} "
                  f"max_rel={mx:.2e} mismatched={mism} pass={ok}",
                  flush=True)

    limits = {}
    # zero flux -> zero production
    out, err = run_one(spec_for("Fe56", [0.0] * 709, "zero_flux"),
                       "zero_flux")
    if out is None:
        limits["zero_flux"] = {"pass": False, "error": err}
    else:
        produced = {n["nuclide"]: n["atoms_per_g"]
                    for n in out["steps"][0]["inventory"]
                    if n["nuclide"] != "Fe56"}
        limits["zero_flux"] = {
            "produced_states": len(produced),
            "max_produced_atoms_per_g": max(produced.values(), default=0.0),
            "pass": not produced}
    n_runs += 1

    # pure decay: 10 s irradiation on a high-sigma Fe56(n,p) group then a
    # 1 d zero-flux hold; Mn-56 ratio vs e^-lam t
    fd = [0.0] * 709
    fd[100] = PHI
    s = spec_for("Fe56", fd, "decay_tail", dt="10")
    s["schedule"].append({"dt": "86400 s", "flux": 0.0})
    out, err = run_one(s, "decay_tail")
    if out is None:
        limits["pure_decay"] = {"pass": False, "error": err}
    else:
        i0 = {n["nuclide"]: n["atoms_per_g"]
              for n in out["steps"][0]["inventory"]}
        i1 = {n["nuclide"]: n["atoms_per_g"]
              for n in out["steps"][-1]["inventory"]}
        n0, n1 = i0.get("Mn56"), i1.get("Mn56", 0.0)
        lam = math.log(2) / 9284.04
        exp_ratio = math.exp(-lam * 86400.0)
        if n0 and n1 > 0:
            rel = abs(n1 / n0 - exp_ratio) / exp_ratio
            limits["pure_decay"] = {
                "nuclide": "Mn56", "halflife_s": 9284.04, "hold_s": 86400.0,
                "n0": n0, "n1": n1, "expected_ratio": exp_ratio,
                "measured_ratio": n1 / n0, "rel_err": rel,
                "pass": rel < 1e-6}
        else:
            limits["pure_decay"] = {"pass": False, "error": "Mn56 absent",
                                    "n0": n0, "n1": n1}
    n_runs += 1

    # production/loss closure on an executed single-group case
    f = [0.0] * 709
    f[354] = PHI
    s = spec_for("Fe56", f, "closure", dt="300")
    out, err = run_one(s, "closure")
    if out is None:
        limits["production_loss_closure"] = {"pass": False, "error": err}
    else:
        pc = out.get("pathway_closure")
        limits["production_loss_closure"] = {
            "pathway_closure": pc,
            "pass": pc is not None and abs(pc) < 1e-2,
            "note": "recorded production/loss accounting residual"}
    n_runs += 1

    result = {
        "schema": "actinv-p28-g1-1", "gate": "G1", "phase": "P28",
        "probe": {"parent_atoms_per_g": NP, "flux": PHI, "pulse_s": T,
                  "tolerance": TOL, "actinv_runs": n_runs},
        "traces": traces,
        "analytic_limits": limits,
        "pass": all(t["pass"] for t in traces.values())
        and all(l.get("pass") for l in limits.values()),
    }
    json.dump(result, open(OUT, "w"), indent=2, sort_keys=True)
    print("trace pass:", {k: v["pass"] for k, v in traces.items()})
    print("limits pass:", {k: v.get("pass") for k, v in limits.items()})
    print("overall:", result["pass"])


if __name__ == "__main__":
    main()
