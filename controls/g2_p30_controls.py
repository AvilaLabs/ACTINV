#!/usr/bin/env python3
"""P30 G2: the five frozen controls — zero_variance, flux_linear,
seed_reproducibility, coverage_accounting, composition_sum."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p30_seals.json")))
P27 = os.path.expanduser("~/nuclear-data/p27-work/study/smoke_study.json")
WORK = os.path.expanduser("~/nuclear-data/p30-work/g2")
OUT = os.path.join(ROOT, "results", "g2_p30_controls.json")
TOL = SEALS["tolerances"]["analytic_rel"]

COV = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.cov.npz")
COV_SHA = SEALS["identities"]["covariance_sidecar"]["sha256"]


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            *cmd]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def base_study(study_id, channels, samples=8, materials=None):
    study = json.load(open(P27))
    study["study_id"] = study_id
    study["cases"]["spectra"] = [s for s in study["cases"]["spectra"]
                                 if s["name"] == "fns_709"]
    study["cases"]["schedules"] = [s for s in study["cases"]["schedules"]
                                  if s["name"] == "pulse_5min"]
    if materials:
        study["cases"]["materials"] = [
            m for m in study["cases"]["materials"] if m["name"] in materials]
    study.pop("comparison", None)
    rb = {"samples": samples, "seed": int(SEALS["validation_population"]
                                          ["seed_hex"], 16),
          "channels": channels,
          "responses": ["total_activity_bq_per_g"]}
    if channels.get("cross_section_mf33"):
        rb["covariance"] = {"path": COV, "sha256": COV_SHA}
    study["robustness"] = rb
    return study


def run_study(study, outdir):
    os.makedirs(outdir, exist_ok=True)
    sp = os.path.join(outdir, "study.json")
    json.dump(study, open(sp, "w"), indent=1)
    recp = os.path.join(outdir, "study_record.json")
    if not os.path.isfile(recp):
        r = subprocess.run(cgroup([ACTINV, "study", "run", sp, outdir]),
                           capture_output=True, text=True)
        print(r.stdout[-300:], r.stderr[-300:])
        r.check_returncode()
    return json.load(open(recp))


def value_at(out, tk):
    steps = out["steps"]
    irr_end = max((s["t_s"] for s in steps if s["flux"] > 0), default=0.0)
    t = float(tk)
    for s in steps:
        if abs(s["t_s"] - irr_end - t) < 1e-6 * max(1.0, t):
            a = s["activity_Bq_per_g"]
            return sum(a.values()) if isinstance(a, dict) else a
    return None


def main():
    os.makedirs(WORK, exist_ok=True)
    res = {}

    # ---- flux_linear: flux-only channel, response must scale by factor
    st = base_study("p30-flux", {"flux_rel_std": 0.05},
                    materials=["fe"])
    rec = run_study(st, os.path.join(WORK, "flux"))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        WORK, "flux", "cases", cid, "out.json")))
    max_rel = 0.0
    n_lin = 0
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            continue
        out = json.load(open(os.path.join(
            WORK, "flux", "cases", cid, f"rob_{a['sample']}.out.json")))
        f = a["flux_factor"]
        for tk in ("0", "86400"):
            nv = value_at(nominal, tk)
            sv = value_at(out, tk)
            if nv:
                max_rel = max(max_rel, abs(sv - nv * f) / abs(nv))
                n_lin += 1
    res["flux_linear"] = {
        "status": "pass" if n_lin > 0 and max_rel <= TOL else "fail",
        "n_probes": n_lin, "max_rel_dev": max_rel,
        "interpretation": "response linear in flux normalization: "
                          "sample = nominal x factor within 1e-6",
    }

    # ---- zero_variance: zero-std channel -> samples identical to nominal
    st = base_study("p30-zero", {"composition_rel_std": {"Co": 0.0}},
                    materials=["fe_co100wppm"])
    rec = run_study(st, os.path.join(WORK, "zero"))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        WORK, "zero", "cases", cid, "out.json")))
    max_dev = 0.0
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            continue
        out = json.load(open(os.path.join(
            WORK, "zero", "cases", cid, f"rob_{a['sample']}.out.json")))
        for tk in ("0", "86400"):
            nv = value_at(nominal, tk) or 0.0
            sv = value_at(out, tk) or 0.0
            d = abs(sv - nv)
            if d > max_dev:
                max_dev = d
    stats = rb["responses"]["total_activity_bq_per_g"]
    res["zero_variance"] = {
        "status": "pass" if max_dev == 0.0
        and all(v["std"] == 0.0 for v in stats.values()) else "fail",
        "max_abs_dev": max_dev,
        "interpretation": "zero declared std -> zero spread, samples "
                          "identical to nominal",
    }

    # ---- seed_reproducibility: same study twice -> identical artifacts
    st = base_study("p30-seed", {"flux_rel_std": 0.05},
                    materials=["fe"])
    rec_a = run_study(st, os.path.join(WORK, "seed_a"))
    rec_b = run_study(st, os.path.join(WORK, "seed_b"))
    ca, cb = rec_a["cases"][0], rec_b["cases"][0]

    def content_digests(rec, root):
        digs = []
        cid = rec["cases"][0]["case_id"]
        for a in rec["cases"][0]["robustness"]["sample_artifacts"]:
            if "failed" in a:
                digs.append(None)
                continue
            o = json.load(open(os.path.join(
                root, "cases", cid, f"rob_{a['sample']}.out.json")))
            for volatile in ("ms",):
                o.pop(volatile, None)
            for k in ("wall_s",):
                o.get("certificate", {}).pop(k, None)
            digs.append(hashlib.sha256(
                json.dumps(o, sort_keys=True).encode()).hexdigest())
        return digs

    digs_a = content_digests(rec_a, os.path.join(WORK, "seed_a"))
    digs_b = content_digests(rec_b, os.path.join(WORK, "seed_b"))
    same = digs_a == digs_b and all(d for d in digs_a)
    res["seed_reproducibility"] = {
        "status": "pass" if same else "fail",
        "interpretation": "fixed seed reproduces per-sample outputs; "
                          "not convergence evidence",
    }

    # ---- coverage_accounting on the G1 record
    g1 = json.load(open(os.path.join(ROOT, "results",
                                     "g1_p30_sampling.json")))
    ok = True
    detail = {}
    g1rec = json.load(open(g1["record_path"]))
    for c in g1rec["cases"]:
        xsc = c["robustness"]["channels"]["cross_section_mf33"]
        covered = xsc["covered_rows"]
        applied = xsc["n_applied_rows"]
        un = len(xsc["uncovered_rows"])
        ok &= applied <= covered
        detail[c["case_id"]] = {
            "covered": covered, "applied": applied,
            "uncovered": un, "correlated": xsc["correlated"],
        }
    res["coverage_accounting"] = {
        "status": "pass" if ok else "fail",
        "cases": detail,
        "interpretation": "applied factors <= covered rows; uncovered "
                          "rows named by index (zero-uncertainty rows "
                          "are not claimed covered)",
    }

    # ---- composition_sum on the G1 record
    ok = True
    for c in g1rec["cases"]:
        cid = c["case_id"]
        cdir = os.path.join(ROOT, "..", "..", "nuclear-data",
                            "p30-work", "g1", "run", "cases", cid)
        cdir = os.path.normpath(cdir)
        nominal_spec = json.load(open(os.path.join(
            os.path.dirname(cdir), "..", "specs", f"{cid}.json")))
        tn = sum(nominal_spec["material"]["composition"].values())
        for a in c["robustness"]["sample_artifacts"]:
            if "failed" in a:
                continue
            sp = json.load(open(os.path.join(
                cdir, f"rob_{a['sample']}.json")))
            ts = sum(sp["material"]["composition"].values())
            if abs(ts - tn) > 1e-6 * tn or any(
                    v < 0 for v in
                    sp["material"]["composition"].values()):
                ok = False
    res["composition_sum"] = {
        "status": "pass" if ok else "fail",
        "interpretation": "perturbed wt_percent compositions renormalize "
                          "to the declared total with nonnegative weights",
    }

    record = {"gate": "G2", "phase": "P30", "controls": res,
              "pass": all(v["status"] == "pass" for v in res.values())}
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: v["status"] for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
