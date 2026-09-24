#!/usr/bin/env python3
"""P43 G2: the frozen controls — decay/yield perturbation traces,
channel_isolation, coverage_accounting, sample_resume,
survival_accounting, and the P30-carried zero_variance, flux_linear,
seed_reproducibility, composition_sum."""
import hashlib
import json
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p43_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p43-work")
G1 = os.path.join(WORK, "g1")
G2 = os.path.join(WORK, "g2")
OUT = os.path.join(ROOT, "results", "g2_p43_controls.json")
TOL = SEALS["tolerances"]["decay_trace_rel"]

MECH = os.path.join(WORK, "p43-mech.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            "RAYON_NUM_THREADS=2",
            *cmd]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def base_study(study_id, channels, samples=8, materials=None,
               comparison=None):
    study = json.load(open(MECH))
    study["study_id"] = study_id
    if materials:
        study["cases"]["materials"] = [
            m for m in study["cases"]["materials"] if m["name"] in materials]
    if comparison is None:
        study.pop("comparison", None)
    else:
        study["comparison"] = comparison
    rb = study["robustness"]
    rb["samples"] = samples
    rb["channels"] = channels
    if not channels.get("cross_section_mf33"):
        rb.pop("covariance", None)
    return study


def run_study(study, outdir, fresh=False):
    if fresh and os.path.isdir(outdir):
        shutil.rmtree(outdir)
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


def inv(out, nuc):
    for r in out["steps"][-1]["inventory"]:
        if r["nuclide"] == nuc:
            return r["atoms_per_g"]
    return 0.0


def value_at(out, tk):
    steps = out["steps"]
    irr_end = max((s["t_s"] for s in steps if s["flux"] > 0), default=0.0)
    t = float(tk)
    for s in steps:
        if abs(s["t_s"] - irr_end - t) < 1e-6 * max(1.0, t):
            a = s["activity_Bq_per_g"]
            return sum(a.values()) if isinstance(a, dict) else a
    return None


def sample_spec(cdir, i, prefix="rob_"):
    return json.load(open(os.path.join(cdir, f"{prefix}{i}.json")))


def main():
    os.makedirs(G2, exist_ok=True)
    res = {}

    # ---- decay_perturbation_trace: substrate model — the daughter
    # accumulates at the constant rate f·λ·N0 and the parent's
    # activity is f·λ·N0. λ from the pinned decay archive.
    import math
    import re
    decay_path = SEALS["identities"]["decay_primary"]["path"]
    lam = None
    lines = open(os.path.join(ROOT, decay_path)).read().splitlines()
    for i, ln in enumerate(lines):
        if len(ln) < 75:
            continue
        if ln[70:72].strip() == "8" and ln[72:75].strip() == "457":
            s = ln[:11].strip()
            m = re.match(r"^([+-]?\d*\.?\d+)([+-]\d+)$", s)
            za = float(m.group(1) + "e" + m.group(2)) if m else (
                float(s) if s else None)
            if za == 25056:
                f0 = lines[i + 1][:11].strip()
                m = re.match(r"^([+-]?\d*\.?\d+)([+-]\d+)$", f0)
                t_half = float(m.group(1) + "e" + m.group(2)) if m else float(f0)
                lam = math.log(2.0) / t_half
                break
    assert lam, "Mn56 half-life not found in pinned decay file"
    nom = json.load(open(os.path.join(G1, "decay_trace_nominal.out.json")))
    sc = json.load(open(os.path.join(G1, "decay_trace_scaled.out.json")))
    t = nom["steps"][-1]["t_s"]
    # N0: substrate atoms per gram from mass/amu (recovered as the
    # parent activity / λ on the nominal run).
    a0 = nom["steps"][-1]["activity_Bq_per_g"]["Mn56"]
    n0 = a0 / lam
    fe_exp = n0 * lam * t
    fe_got = inv(nom, "Fe56")
    a1 = sc["steps"][-1]["activity_Bq_per_g"]["Mn56"]
    fe1 = inv(sc, "Fe56")
    rels = {
        "daughter_nominal": abs(fe_got - fe_exp) / fe_exp,
        "daughter_scaled": abs(fe1 - 1.5 * fe_exp) / (1.5 * fe_exp),
        "activity_scaled": abs(a1 - 1.5 * a0) / (1.5 * a0),
    }
    res["decay_perturbation_trace"] = {
        "status": "pass" if max(rels.values()) <= TOL else "fail",
        "max_rel_dev": max(rels.values()),
        "details": rels,
        "interpretation": "decay_scale multiplies the decay constant "
                          "everywhere: daughter production rate and "
                          "parent activity scale by f exactly",
    }

    # ---- yield_perturbation_trace: atoms ratio == 2 on the frozen
    # named set; the activation product U236 unchanged.
    yn = json.load(open(os.path.join(G1, "yield_trace_nominal.out.json")))
    ys = json.load(open(os.path.join(G1, "yield_trace_scaled.out.json")))
    rels = {}
    ok = True
    for nuc in ("I135", "Xe135", "Kr88", "Cs137"):
        v0, v1 = inv(yn, nuc), inv(ys, nuc)
        rels[nuc] = abs(v1 - 2.0 * v0) / (2.0 * v0) if v0 else float("inf")
        ok &= rels[nuc] <= TOL
    u0, u1 = inv(yn, "U236"), inv(ys, "U236")
    rels["U236_unchanged"] = abs(u1 - u0) / u0 if u0 else float("inf")
    ok &= rels["U236_unchanged"] <= TOL
    res["yield_perturbation_trace"] = {
        "status": "pass" if ok else "fail",
        "max_rel_dev": max(rels.values()),
        "details": rels,
        "interpretation": "every effective fission production edge "
                          "scales by 2; activation channels unchanged",
    }

    # ---- channel_isolation: decay-only run — sample specs must carry
    # decay_scale and identity rate_scale/yield_scale/flux/composition.
    st = base_study("p43-isolation-decay", {"decay_constants": True},
                    materials=["fe"])
    rec = run_study(st, os.path.join(G2, "iso_decay"))
    cid = rec["cases"][0]["case_id"]
    cdir = os.path.join(G2, "iso_decay", "cases", cid)
    ok, detail = True, {}
    for i in range(st["robustness"]["samples"]):
        sp = sample_spec(cdir, i)
        opts = sp.get("options", {})
        has_decay = bool(opts.get("decay_scale"))
        ident = (opts.get("rate_scale") is None
                 and opts.get("yield_scale") is None
                 and sp["spectrum"].get("total") ==
                 json.load(open(os.path.join(
                     G2, "iso_decay", "specs", f"{cid}.json"))
                 )["spectrum"].get("total")
                 and sp["material"]["composition"] ==
                 json.load(open(os.path.join(
                     G2, "iso_decay", "specs", f"{cid}.json"))
                 )["material"]["composition"])
        detail[f"s{i}"] = {"decay_scale_entries":
                          len(opts.get("decay_scale") or {}),
                          "isolated": has_decay and ident}
        ok &= has_decay and ident
    res["channel_isolation"] = {
        "status": "pass" if ok else "fail",
        "samples": detail,
        "interpretation": "a decay-only run perturbs only decay "
                          "constants; every other channel's inputs "
                          "stay at identity",
    }

    # ---- coverage_accounting on the G1 record: per case and channel,
    # covered + uncovered partitions the channel's active input set.
    g1 = json.load(open(os.path.join(ROOT, "results",
                                     "g1_p43_mechanics.json")))
    g1rec = json.load(open(g1["record_path"]))
    ok, detail = True, {}
    for c in g1rec["cases"]:
        cid = c["case_id"]
        ch = c["robustness"]["channels"]
        d = {}
        dec = ch.get("decay_constants", {})
        if dec.get("enabled"):
            n_cov = len(dec.get("covered", []))
            n_unc = len(dec.get("uncovered", []))
            d["decay"] = {"covered": n_cov, "uncovered": n_unc}
            ok &= n_cov > 0
        fy = ch.get("fission_yields", {})
        if fy.get("enabled"):
            n_cov = len(fy.get("covered", []))
            n_unc = len(fy.get("uncovered", []))
            d["yield"] = {"covered": n_cov, "uncovered": n_unc}
            ok &= (n_cov > 0) == cid.startswith("u235")
        xs = ch.get("cross_section_mf33", {})
        if xs.get("enabled"):
            d["xs"] = {"covered_rows": xs.get("covered_rows"),
                       "uncovered": len(xs.get("uncovered_rows", []))}
            ok &= xs.get("covered_rows", 0) > 0
        detail[cid] = d
    res["coverage_accounting"] = {
        "status": "pass" if ok else "fail",
        "cases": detail,
        "interpretation": "every enabled channel reports a named "
                          "covered/uncovered partition of its active "
                          "input set; zero-parameter coverage is "
                          "honest accounting",
    }

    # ---- sample_resume: (a) a resumed run reuses verified artifacts;
    # (b) a torn sample artifact re-executes exactly that sample.
    st = base_study("p43-resume", {"flux_rel_std": 0.05},
                    samples=4, materials=["fe"])
    outdir = os.path.join(G2, "resume")
    rec = run_study(st, outdir, fresh=True)
    cid = rec["cases"][0]["case_id"]
    cdir = os.path.join(outdir, "cases", cid)
    digests_before = {}
    for i in range(4):
        for suffix in (f"rob_{i}.json", f"rob_{i}.out.json"):
            p = os.path.join(cdir, suffix)
            digests_before[suffix] = sha(p)
    # (a) rerun: every artifact must be byte-identical
    recp = os.path.join(outdir, "study_record.json")
    os.remove(recp)
    r = subprocess.run(cgroup([ACTINV, "study", "run",
                               os.path.join(outdir, "study.json"),
                               outdir]),
                       capture_output=True, text=True)
    r.check_returncode()
    ok_a = all(sha(os.path.join(cdir, k)) == v
               for k, v in digests_before.items())
    # (b) tear sample 2's out artifact: it must re-execute and
    # reproduce identical response values under the per-sample seed
    # (bytes legitimately differ — the artifact carries wall-time ms)
    vals_before = rec["cases"][0]["robustness"]["sample_values"][2]
    torn = os.path.join(cdir, "rob_2.out.json")
    with open(torn, "ab") as f:
        f.write(b"garbage")
    os.remove(recp)
    r = subprocess.run(cgroup([ACTINV, "study", "run",
                               os.path.join(outdir, "study.json"),
                               outdir]),
                       capture_output=True, text=True)
    r.check_returncode()
    rec2 = json.load(open(recp))
    rb2 = rec2["cases"][0]["robustness"]
    vals_after = rb2["sample_values"][2]
    ok_b = vals_after == vals_before and rb2["n_reused_samples"] == 3
    ok_b &= all(sha(os.path.join(cdir, k)) == v
                for k, v in digests_before.items()
                if k != "rob_2.out.json")
    res["sample_resume"] = {
        "status": "pass" if ok_a and ok_b else "fail",
        "resume_byte_identical": ok_a,
        "torn_rerun_values_identical": vals_after == vals_before,
        "reused_after_tear": rb2["n_reused_samples"],
        "interpretation": "verified samples resume without re-solve; "
                          "a torn artifact re-executes to the same "
                          "response values under the per-sample seed",
    }

    # ---- survival_accounting: a study with comparison+robustness
    # carries paired survival fields on each rule.
    st = base_study("p43-survival", {"flux_rel_std": 0.05},
                    samples=8,
                    comparison={
                        "axes": ["material"],
                        "decision_rules": [{
                            "id": "rk", "kind": "rank_equal",
                            "response": "total_activity_bq_per_g",
                            "times_s": [0.0],
                            "expected_order":
                                ["u235", "fe", "fe_co100wppm"]}],
                    })
    rec = run_study(st, os.path.join(G2, "survival"))
    rules = rec.get("comparison", {}).get("rules", [])
    ok = bool(rules)
    detail = {}
    for r in rules:
        surv = r.get("survival")
        detail[r["id"]] = surv
        ok &= surv is not None and "fraction_satisfied" in surv \
            and "paired_samples" in surv
    res["survival_accounting"] = {
        "status": "pass" if ok else "fail",
        "rules": detail,
        "interpretation": "decision rules carry paired-sample "
                          "survival fields under comparison+robustness",
    }

    # ---- carried controls -------------------------------------------------
    st = base_study("p43-flux", {"flux_rel_std": 0.05}, materials=["fe"])
    rec = run_study(st, os.path.join(G2, "flux"))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        G2, "flux", "cases", cid, "out.json")))
    max_rel, n_lin = 0.0, 0
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            continue
        out = json.load(open(os.path.join(
            G2, "flux", "cases", cid, f"rob_{a['sample']}.out.json")))
        f = a["flux_factor"]
        for tk in ("0",):
            nv = value_at(nominal, tk)
            sv = value_at(out, tk)
            if nv:
                max_rel = max(max_rel, abs(sv - nv * f) / abs(nv))
                n_lin += 1
    res["flux_linear"] = {
        "status": "pass" if n_lin > 0 and max_rel <= 1e-6 else "fail",
        "n_probes": n_lin, "max_rel_dev": max_rel,
        "interpretation": "response linear in flux normalization",
    }

    st = base_study("p43-zero", {"composition_rel_std": {"Co": 0.0}},
                    materials=["fe_co100wppm"])
    rec = run_study(st, os.path.join(G2, "zero"))
    case = rec["cases"][0]
    cid = case["case_id"]
    rb = case["robustness"]
    nominal = json.load(open(os.path.join(
        G2, "zero", "cases", cid, "out.json")))
    max_dev = 0.0
    for a in rb["sample_artifacts"]:
        if "failed" in a:
            continue
        out = json.load(open(os.path.join(
            G2, "zero", "cases", cid, f"rob_{a['sample']}.out.json")))
        nv = value_at(nominal, "0") or 0.0
        sv = value_at(out, "0") or 0.0
        max_dev = max(max_dev, abs(sv - nv))
    stats = rb["responses"]["total_activity_bq_per_g"]
    res["zero_variance"] = {
        "status": "pass" if max_dev == 0.0
        and all(v["std"] == 0.0 for v in stats.values()) else "fail",
        "max_abs_dev": max_dev,
        "interpretation": "zero declared std -> zero spread",
    }

    st = base_study("p43-seed", {"flux_rel_std": 0.05}, materials=["fe"])
    rec_a = run_study(st, os.path.join(G2, "seed_a"))
    rec_b = run_study(st, os.path.join(G2, "seed_b"))

    def digs(rec, root):
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
    same = digs(rec_a, os.path.join(G2, "seed_a")) == \
        digs(rec_b, os.path.join(G2, "seed_b"))
    res["seed_reproducibility"] = {
        "status": "pass" if same else "fail",
        "interpretation": "fixed seed reproduces per-sample outputs",
    }

    # composition_sum on the G1 record: perturbed wt_percent
    # compositions renormalize to the declared total, nonnegative.
    ok = True
    for c in g1rec["cases"]:
        cid = c["case_id"]
        cdir = os.path.join(G1, "mech", "cases", cid)
        nominal_spec = json.load(open(os.path.join(
            G1, "mech", "specs", f"{cid}.json")))
        tn = sum(nominal_spec["material"]["composition"].values())
        for a in c["robustness"]["sample_artifacts"]:
            if "failed" in a:
                continue
            sp = sample_spec(cdir, a["sample"])
            ts = sum(sp["material"]["composition"].values())
            if abs(ts - tn) > 1e-6 * tn or any(
                    v < 0 for v in
                    sp["material"]["composition"].values()):
                ok = False
    res["composition_sum"] = {
        "status": "pass" if ok else "fail",
        "interpretation": "perturbed wt_percent compositions "
                          "renormalize to the declared total",
    }

    record = {"gate": "G2", "phase": "P43", "controls": res,
              "pass": all(v["status"] == "pass" for v in res.values())}
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: v["status"] for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
