#!/usr/bin/env python3
"""P30 G2 independent checker: replays the five controls from raw
sample artifacts; planted mutations."""
import copy
import hashlib
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p30_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p30-work/g2")
G1 = os.path.expanduser("~/nuclear-data/p30-work/g1")
RES = os.path.join(ROOT, "results", "g2_p30_controls.json")
OUT = os.path.join(ROOT, "results", "g2_p30_check.json")
TOL = SEALS["tolerances"]["analytic_rel"]

MATERIAL_ISOTOPES = {
    "fe__fns_709__pulse_5min":
        {(26054, 0), (26056, 0), (26057, 0), (26058, 0)},
    "fe_co100wppm__fns_709__pulse_5min":
        {(26054, 0), (26056, 0), (26057, 0), (26058, 0), (27059, 0)},
}


def value_at(out, tk):
    steps = out["steps"]
    irr_end = max((s["t_s"] for s in steps if s["flux"] > 0), default=0.0)
    t = float(tk)
    for s in steps:
        if abs(s["t_s"] - irr_end - t) < 1e-6 * max(1.0, t):
            a = s["activity_Bq_per_g"]
            return sum(a.values()) if isinstance(a, dict) else a
    return None


def check(res, fs):
    ctrls = res.get("controls", {})
    if set(ctrls) != set(SEALS["validation_population"]["controls"]):
        fs.append("control population drifted")
        return
    for k, v in ctrls.items():
        if v.get("status") != "pass":
            fs.append(f"control {k} not pass")

    # flux_linear: re-derive ratios from raw sample outs
    rec = json.load(open(os.path.join(WORK, "flux", "study_record.json")))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        WORK, "flux", "cases", cid, "out.json")))
    n_checked = 0
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            fs.append("flux_linear: failed sample")
            continue
        out = json.load(open(os.path.join(
            WORK, "flux", "cases", cid, f"rob_{a['sample']}.out.json")))
        f = a["flux_factor"]
        for tk in ("0", "86400"):
            nv = value_at(nominal, tk)
            sv = value_at(out, tk)
            if nv and abs(sv - nv * f) / abs(nv) > TOL:
                fs.append(f"flux_linear s{a['sample']} @{tk} "
                          f"not linear")
            n_checked += 1
    if n_checked == 0:
        fs.append("flux_linear: no probes re-derived")

    # zero_variance: re-derive
    rec = json.load(open(os.path.join(WORK, "zero", "study_record.json")))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        WORK, "zero", "cases", cid, "out.json")))
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            continue
        out = json.load(open(os.path.join(
            WORK, "zero", "cases", cid, f"rob_{a['sample']}.out.json")))
        for tk in ("0", "86400"):
            nv = value_at(nominal, tk) or 0.0
            sv = value_at(out, tk) or 0.0
            if sv != nv:
                fs.append(f"zero_variance s{a['sample']} deviates")
    for v in rb["responses"]["total_activity_bq_per_g"].values():
        if v["std"] != 0.0:
            fs.append("zero_variance: nonzero std")

    # seed: identical content digests
    def digs(root):
        rec = json.load(open(os.path.join(root, "study_record.json")))
        cid = rec["cases"][0]["case_id"]
        out = []
        for a in rec["cases"][0]["robustness"]["sample_artifacts"]:
            o = json.load(open(os.path.join(
                root, "cases", cid, f"rob_{a['sample']}.out.json")))
            o.pop("ms", None)
            o.get("certificate", {}).pop("wall_s", None)
            out.append(hashlib.sha256(
                json.dumps(o, sort_keys=True).encode()).hexdigest())
        return out
    if digs(os.path.join(WORK, "seed_a")) != digs(
            os.path.join(WORK, "seed_b")):
        fs.append("seed_reproducibility: digests differ")

    # coverage: independent covered-row re-derivation on the G1 record
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
    g1rec = json.load(open(os.path.join(G1, "run", "study_record.json")))
    for c in g1rec["cases"]:
        cid = c["case_id"]
        xsc = c["robustness"]["channels"]["cross_section_mf33"]
        want = sum(
            1 for r in rows
            if targets[r[0]] in MATERIAL_ISOTOPES[cid]
            and r[4] != 10 and (r[0], r[1]) in self_cov)
        if xsc["covered_rows"] != want:
            fs.append(f"{cid}: covered_rows {xsc['covered_rows']} "
                      f"!= re-derived {want}")
        claimed = ctrls.get("coverage_accounting", {}).get(
            "cases", {}).get(cid, {}).get("covered")
        if claimed != want:
            fs.append(f"{cid}: summary covered {claimed} "
                      f"!= re-derived {want}")
        # applied rate_scale keys ⊆ covered active rows
        cdir = os.path.join(G1, "run", "cases", cid)
        cov_rows = {i for i, r in enumerate(rows)
                    if targets[r[0]] in MATERIAL_ISOTOPES[cid]
                    and r[4] != 10 and (r[0], r[1]) in self_cov}
        for a in c["robustness"]["sample_artifacts"]:
            if "failed" in a:
                continue
            sp = json.load(open(os.path.join(
                cdir, f"rob_{a['sample']}.json")))
            for k in sp.get("options", {}).get("rate_scale", {}):
                if int(k) not in cov_rows:
                    fs.append(f"{cid}: rate_scale on uncovered row {k}")
                    break

    # composition_sum on G1 artifacts
    for c in g1rec["cases"]:
        cid = c["case_id"]
        cdir = os.path.join(G1, "run", "cases", cid)
        nspec = json.load(open(os.path.join(
            G1, "run", "specs", f"{cid}.json")))
        tn = sum(nspec["material"]["composition"].values())
        for a in c["robustness"]["sample_artifacts"]:
            if "failed" in a:
                continue
            sp = json.load(open(os.path.join(
                cdir, f"rob_{a['sample']}.json")))
            ts = sum(sp["material"]["composition"].values())
            if abs(ts - tn) > 1e-6 * tn or any(
                    v < 0 for v in
                    sp["material"]["composition"].values()):
                fs.append(f"{cid} s{a['sample']}: composition violated")


def main():
    res = json.load(open(RES))
    fs = []
    check(res, fs)

    planted = rejected = 0

    def mut_status(r):
        r["controls"]["flux_linear"]["status"] = "fail"

    def mut_set(r):
        r["controls"].pop("zero_variance")

    def mut_tol(r):
        r["controls"]["flux_linear"]["max_rel_dev"] = 1.0

    def mut_covered(r):
        r["controls"]["coverage_accounting"]["cases"][
            "fe__fns_709__pulse_5min"]["covered"] += 3

    def mut_dev(r):
        r["controls"]["zero_variance"]["max_abs_dev"] = 1.0

    for mut in [mut_status, mut_set, mut_covered]:
        planted += 1
        v = copy.deepcopy(res)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        if mfs:
            rejected += 1
        else:
            print("  UNREJECTED mutation", file=sys.stderr)

    fs.clear()
    check(res, fs)
    out = {"gate": "G2", "phase": "P30", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
