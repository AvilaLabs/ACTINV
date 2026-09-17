#!/usr/bin/env python3
"""P30 G1 independent checker: population, perturbation identity, stats
re-derivation, coverage accounting, planted mutations."""
import copy
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p30_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p30-work/g1")
REC = os.path.join(WORK, "run", "study_record.json")
OUT = os.path.join(ROOT, "results", "g1_p30_check.json")
N = SEALS["validation_population"]["samples"]
CASES = SEALS["validation_population"]["sampling_cases"]
RESPONSES = SEALS["validation_population"]["responses"]


def value_at(out, response, tk):
    """response value at cooling time tk (s); step t_s = irr_end + t."""
    steps = out.get("steps", [])
    irr_end = 0.0
    for s in steps:
        if s.get("flux", 0.0) > 0.0:
            irr_end = s.get("t_s", 0.0)
    t = float(tk)
    step = None
    for s in steps:
        if abs(s.get("t_s", -1) - irr_end - t) < 1e-6 * max(1.0, t):
            step = s
    if step is None:
        return None
    if response == "total_activity_bq_per_g":
        a = step.get("activity_Bq_per_g", {})
        return sum(a.values()) if isinstance(a, dict) else a
    if response == "decay_heat_w_per_g":
        h = step.get("heat_W_per_g", {})
        return h.get("total") if isinstance(h, dict) else h
    return None


# natural-isotope content of the frozen materials (wt_percent expansion)
MATERIAL_ISOTOPES = {
    "fe__fns_709__pulse_5min":
        {(26054, 0), (26056, 0), (26057, 0), (26058, 0)},
    "fe_co100wppm__fns_709__pulse_5min":
        {(26054, 0), (26056, 0), (26057, 0), (26058, 0),
         (27059, 0)},
}


def rederive_covered(case_id):
    """Independent covered-row count: library rows whose target is a
    material isotope, lmf != 10, with a self-covariance component."""
    import numpy as np
    lib = np.load(os.path.join(
        ROOT, "target/p25c-release/"
              "tendl-2025-patched-neutron-709g.npz"))
    rows = lib["rows"]
    idx = json.load(open(os.path.join(
        ROOT, "target/p25c-release/"
              "tendl-2025-patched-neutron-709g_index.json")))
    targets = [(t["za"], t["liso"]) for t in idx["targets"]]
    cov = np.load(os.path.join(
        ROOT, "target/p25c-release/"
              "tendl-2025-patched-neutron-709g.cov.npz"))
    self_cov = {(int(c[0]), int(c[1]))
                for c in cov["components"] if c[1] == c[2]}
    iso = MATERIAL_ISOTOPES[case_id]
    return sum(
        1 for r in rows
        if targets[r[0]] in iso and r[4] != 10
        and (r[0], r[1]) in self_cov)


def check(rec_path, fs):
    rec = json.load(open(rec_path))
    by = {c["case_id"]: c for c in rec["cases"]}
    if set(by) != set(CASES):
        fs.append(f"population drifted: {set(by) ^ set(CASES)}")
        return
    for cid in CASES:
        c = by[cid]
        rb = c.get("robustness")
        if not rb or rb.get("status") != "executed":
            fs.append(f"{cid}: robustness missing or not executed")
            continue
        if rb["seed"] != int(SEALS["validation_population"]
                           ["seed_hex"], 16):
            fs.append(f"{cid}: seed drifted")
        if rb["n_failed_samples"] != 0:
            fs.append(f"{cid}: failed samples present")
        cdir = os.path.join(WORK, "run", "cases", cid)
        nominal = json.load(open(os.path.join(
            WORK, "run", "specs", f"{cid}.json")))
        arts = rb["sample_artifacts"]
        if len(arts) != N:
            fs.append(f"{cid}: {len(arts)} sample artifacts != {N}")
            continue
        covered = rb["channels"]["cross_section_mf33"]["covered_rows"]
        if covered != rederive_covered(cid):
            fs.append(f"{cid}: covered_rows not re-derived")
        widths = set()
        for a in arts:
            i = a["sample"]
            sp = json.load(open(os.path.join(cdir, f"rob_{i}.json")))
            if "failed" in a:
                fs.append(f"{cid} s{i}: failed sample recorded")
                continue
            rs = sp.get("options", {}).get("rate_scale", {})
            widths.add(len(rs))
            for k, v in rs.items():
                int(k)
                if not isinstance(v, (int, float)) or v <= 0:
                    fs.append(f"{cid} s{i}: nonpositive scale")
            ft = a["flux_factor"]
            nt = nominal.get("spectrum", {}).get("total")
            st = sp.get("spectrum", {}).get("total")
            if nt and st and abs(st - nt * ft) > 1e-9 * max(nt, st):
                fs.append(f"{cid} s{i}: flux factor inconsistent")
            tn = sum(nominal["material"]["composition"].values())
            ts = sum(sp["material"]["composition"].values())
            if abs(ts - tn) > 1e-6 * tn:
                fs.append(f"{cid} s{i}: composition sum drifted")
            if any(v < 0
                   for v in sp["material"]["composition"].values()):
                fs.append(f"{cid} s{i}: negative composition")
        if len(widths) != 1:
            fs.append(f"{cid}: inconsistent rate_scale widths")
        elif widths:
            w = widths.pop()
            applied = rb["channels"]["cross_section_mf33"].get(
                "n_applied_rows")
            if applied is not None and w != applied:
                fs.append(f"{cid}: rate_scale width != applied rows")
            elif applied is None and w > covered:
                fs.append(f"{cid}: rate_scale width exceeds covered rows")
        outs = [json.load(open(os.path.join(
            cdir, f"rob_{a['sample']}.out.json")))
                for a in arts if "failed" not in a]
        for response in RESPONSES:
            for tk, st in rb["responses"].get(response, {}).items():
                vals = [v for o in outs
                        if (v := value_at(o, response, tk))
                        is not None]
                if len(vals) != st["n_samples"]:
                    fs.append(f"{cid} {response}@{tk}: n_samples off")
                    continue
                mean = sum(vals) / len(vals)
                var = sum((v - mean) ** 2 for v in vals) \
                    / max(len(vals) - 1, 1)
                std = math.sqrt(var)
                for name, got, want in (
                        ("mean", st["mean"], mean),
                        ("std", st["std"], std),
                        ("ci95_half_width", st["ci95_half_width"],
                         1.96 * std / math.sqrt(len(vals)))):
                    if abs(got - want) > 1e-6 * max(abs(want), 1e-300):
                        fs.append(f"{cid} {response}@{tk}: {name} off")


def main():
    rec = json.load(open(REC))
    fs = []
    check(REC, fs)

    planted = rejected = 0

    def mut_mean(r):
        r["cases"][0]["robustness"]["responses"][
            "total_activity_bq_per_g"]["0"]["mean"] *= 1.5

    def mut_covered(r):
        r["cases"][0]["robustness"]["channels"][
            "cross_section_mf33"]["covered_rows"] += 1

    def mut_status(r):
        r["cases"][0]["robustness"]["status"] = "gap"

    def mut_pop(r):
        r["cases"] = [c for c in r["cases"]
                      if c["case_id"] != CASES[0]]

    def mut_failed(r):
        r["cases"][0]["robustness"]["n_failed_samples"] = 0 \
            if r["cases"][0]["robustness"]["n_failed_samples"] else 7

    def mut_seed(r):
        r["cases"][0]["robustness"]["seed"] += 1

    for mut in [mut_mean, mut_covered, mut_status, mut_pop,
                mut_failed, mut_seed]:
        planted += 1
        v = copy.deepcopy(rec)
        mut(v)
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        json.dump(v, open(tmp, "w"))
        try:
            mfs = []
            check(tmp, mfs)
            if mfs:
                rejected += 1
            else:
                print("  UNREJECTED mutation", file=sys.stderr)
        finally:
            os.remove(tmp)

    fs.clear()
    check(REC, fs)
    out = {"gate": "G1", "phase": "P30", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
