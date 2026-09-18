#!/usr/bin/env python3
"""P36 G2 controls: independently re-derive the flagship's decision
values, dominant nuclides, pathway attribution and uncertainty
survival from the executed artifacts."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "target", "p36-work", "w-matcmp", "out")
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p36_seals.json")))
OUT = os.path.join(RES, "g2_p36_controls.json")
T100 = "3155760000"
TS_100Y = 3218875200.0  # cumulative t_s: 2 y irradiation + 100 y cooling


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    rec = json.load(open(os.path.join(OUTDIR, "study_record.json")))
    outs = {}
    for c in rec["cases"]:
        cid = c["case_id"]
        outs[cid] = json.load(
            open(os.path.join(OUTDIR, "cases", cid, "out.json")))

    mats = ["rafm_low", "rafm_base", "rafm_high"]
    ids = {m: f"{m}__fns_709__fe_2y" for m in mats}
    per_case = {c["case_id"]: c for c in rec["cases"]}
    pt = {m: per_case[ids[m]]["per_time"][T100] for m in mats}

    # ---- control 1: case_completion
    completion = {
        "executed": all(
            c["status"] == "executed" and not c["undefined_responses"]
            for c in rec["cases"]),
        "status": rec["status"],
    }

    # ---- control 2: decision rules re-evaluated from per_time values
    def order(resp):
        v = sorted(((m, pt[m][resp]) for m in mats), key=lambda x: -x[1])
        return [m for m, _ in v]
    act_rank = order("total_activity_bq_per_g")
    heat_rank = order("decay_heat_w_per_g")
    acts = [pt[m]["total_activity_bq_per_g"] for m in mats]
    span = max(acts) / min(acts)
    decisions = {
        "act_rank_100y": {"expected": ["rafm_high", "rafm_base",
                                       "rafm_low"],
                          "observed": act_rank,
                          "pass": act_rank == ["rafm_high", "rafm_base",
                                               "rafm_low"]},
        "heat_rank_100y": {"expected": ["rafm_high", "rafm_base",
                                        "rafm_low"],
                           "observed": heat_rank,
                           "pass": heat_rank == ["rafm_high",
                                                 "rafm_base",
                                                 "rafm_low"]},
        "impurity_span_100y": {"max_ratio": span,
                               "band": [1.0, 1e6],
                               "pass": 1.0 <= span <= 1e6},
    }

    # ---- control 3: dominant nuclides at 100 y re-derived from
    # out.json activity dicts (independent of record per_time)
    dominant = {}
    for m in mats:
        st = next(s for s in outs[ids[m]]["steps"]
                  if abs(s["t_s"] - TS_100Y) < 1)
        act = st["activity_Bq_per_g"]
        tot = sum(act.values())
        top = sorted(act.items(), key=lambda x: -x[1])[:6]
        dominant[m] = {
            "total_bq_per_g": tot,
            "top": [{"nuclide": n, "bq_per_g": v,
                     "share": v / tot} for n, v in top],
        }
    # difference drivers high - low
    hi = outs[ids["rafm_high"]]["steps"]
    lo = outs[ids["rafm_low"]]["steps"]
    a_hi = next(s["activity_Bq_per_g"] for s in hi
                if abs(s["t_s"] - TS_100Y) < 1)
    a_lo = next(s["activity_Bq_per_g"] for s in lo
                if abs(s["t_s"] - TS_100Y) < 1)
    d = sorted(((n, a_hi.get(n, 0.0) - a_lo.get(n, 0.0))
                for n in set(a_hi) | set(a_lo)),
               key=lambda x: -x[1])
    diff_total = sum(a_hi.values()) - sum(a_lo.values())
    drivers = [{"nuclide": n, "delta_bq_per_g": v,
                "share_of_difference": v / diff_total}
               for n, v in d[:5] if v > 0]

    # ---- control 4: pathway attribution — closure on significant
    # nuclides (>=1e-9 of step-peak atoms) recomputed independently;
    # raw pathway_closure max over floor-level nuclides is recorded
    # as a known limitation
    pathways = {}
    for m in mats:
        o = outs[ids[m]]
        raw_closure = o["pathway_closure"]
        max_rel = 0.0
        checked = 0
        for paths, st in zip(o["pathways"], o["steps"]):
            inv = {n["nuclide"]: n["atoms_per_g"]
                   for n in st["inventory"]}
            peak = max((a for a in inv.values()), default=0.0)
            for nuc, plist in paths.items():
                a = inv.get(nuc, 0.0)
                if a > peak * 1e-9:
                    s = sum(p["atoms_per_g"] for p in plist)
                    max_rel = max(max_rel, abs(s - a) / a)
                    checked += 1
        # impurity-driver parents at 100 y
        i100 = next(i for i, s in enumerate(o["steps"])
                    if abs(s["t_s"] - TS_100Y) < 1)
        pw = o["pathways"][i100]
        parents = {}
        for nuc in ("Ni63", "Ni59", "Nb94", "Co60"):
            if nuc in pw:
                parents[nuc] = [
                    {"from": p["from"], "fraction": p["fraction"]}
                    for p in pw[nuc][:3]]
        pathways[m] = {
            "raw_pathway_closure": raw_closure,
            "significant_nuclide_closure": max_rel,
            "nuclides_checked": checked,
            "impurity_parents": parents,
            "pass": max_rel <= 1e-3,
        }

    # ---- control 5: robustness survival — sample means keep the
    # nominal rank order at 100 y
    surv = {}
    for resp in ("total_activity_bq_per_g", "decay_heat_w_per_g"):
        means = {}
        for c in rec["cases"]:
            m = c["case_id"].split("__")[0]
            stats = c["robustness"]["responses"][resp][T100]
            means[m] = stats["mean"]
        got = sorted(means, key=lambda m: -means[m])
        surv[resp] = {"sample_means": means, "rank": got,
                      "survives": got == ["rafm_high", "rafm_base",
                                          "rafm_low"]}
    survival = {
        "by_response": surv,
        "pass": all(v["survives"] for v in surv.values()),
    }

    # ---- control 6: packaging — digests of the packaged artifacts
    packaged = {
        "study_digest_matches_g0":
            sha(os.path.join(ROOT, SEALS["study"]["path"]))
            == SEALS["study"]["sha256"],
        "study_sha256": sha(os.path.join(ROOT, SEALS["study"]["path"])),
        "artifact_sha256": {
            "study_record": sha(os.path.join(OUTDIR,
                                             "study_record.json")),
            "manifest": sha(os.path.join(OUTDIR, "manifest.json")),
        },
    }

    out = {
        "gate": "G2", "phase": "P36",
        "case_completion": completion,
        "decision_rules": decisions,
        "dominant_nuclides_100y": dominant,
        "difference_drivers_100y": drivers,
        "pathway_attribution": pathways,
        "robustness_survival": survival,
        "packaging": packaged,
        "controls_pass": all([
            completion["executed"],
            all(d["pass"] for d in decisions.values()),
            all(p["pass"] for p in pathways.values()),
            survival["pass"],
            packaged["study_digest_matches_g0"],
        ]),
    }
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1)[:3000])


if __name__ == "__main__":
    main()
