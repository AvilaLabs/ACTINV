#!/usr/bin/env python3
"""P36 G2 checker: the controls record is internally consistent and
its headline values are re-derived from the artifacts once more."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "target", "p36-work", "w-matcmp", "out")
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p36_seals.json")))
CTR = os.path.join(RES, "g2_p36_controls.json")
OUT = os.path.join(RES, "g2_p36_check.json")
T100 = "3155760000"
TS_100Y = 3218875200.0


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(ctr, fs):
    if ctr.get("gate") != "G2" or ctr.get("phase") != "P36":
        fs.append("gate/phase wrong")
        return
    if not ctr.get("controls_pass"):
        fs.append("controls record itself reports failure")
    rec = json.load(open(os.path.join(OUTDIR, "study_record.json")))
    per = {c["case_id"]: c for c in rec["cases"]}
    mats = ["rafm_low", "rafm_base", "rafm_high"]
    ids = {m: f"{m}__fns_709__fe_2y" for m in mats}
    # re-derive the rank independently
    acts = {m: per[ids[m]]["per_time"][T100]["total_activity_bq_per_g"]
            for m in mats}
    got = sorted(acts, key=lambda m: -acts[m])
    if got != ctr["decision_rules"]["act_rank_100y"]["observed"]:
        fs.append("recorded act rank != re-derived rank")
    if got != ["rafm_high", "rafm_base", "rafm_low"]:
        fs.append("re-derived act rank wrong")
    span = max(acts.values()) / min(acts.values())
    if abs(span - ctr["decision_rules"]["impurity_span_100y"]
           ["max_ratio"]) > 1e-9 * span:
        fs.append("recorded span != re-derived span")
    # dominant driver must be an impurity daughter
    drv = ctr.get("difference_drivers_100y", [])
    if not drv or drv[0]["nuclide"] not in (
            "Ni63", "Ni59", "Nb94", "Mo93", "Co60", "Ag108m", "Tc99"):
        fs.append("no impurity-daughter difference driver")
    if not drv or drv[0]["share_of_difference"] < 0.5:
        fs.append("dominant driver share <50% — conclusion weak")
    # pathway closures
    for m, p in ctr.get("pathway_attribution", {}).items():
        if p.get("significant_nuclide_closure", 1) > 1e-3:
            fs.append(f"{m} significant closure too large")
        if not p.get("impurity_parents"):
            fs.append(f"{m} no impurity pathway parents")
        ni = p.get("impurity_parents", {}).get("Ni63", [])
        if not ni or ni[0]["from"] != "Ni64":
            fs.append(f"{m} Ni63 dominant parent != Ni64")
    # survival flags
    if not ctr.get("robustness_survival", {}).get("pass"):
        fs.append("rank does not survive sampling")
    for resp, v in ctr["robustness_survival"]["by_response"].items():
        means = v["sample_means"]
        if not (means["rafm_high"] > means["rafm_base"]
                > means["rafm_low"]):
            fs.append(f"{resp} means misordered")
    # packaging
    if not ctr.get("packaging", {}).get("study_digest_matches_g0"):
        fs.append("study digest does not match G0")
    if sha(os.path.join(ROOT, SEALS["study"]["path"])) \
            != SEALS["study"]["sha256"]:
        fs.append("live study digest drifted from G0 seal")


def main():
    ctr = json.load(open(CTR))
    fs = []
    check(ctr, fs)
    planted = rejected = 0
    muts = [
        ("rank", lambda v: v["decision_rules"]["act_rank_100y"]
         .__setitem__("observed", ["rafm_low", "rafm_base",
                                   "rafm_high"])),
        ("span", lambda v: v["decision_rules"]["impurity_span_100y"]
         .__setitem__("max_ratio", 999.0)),
        ("driver", lambda v: v["difference_drivers_100y"]
         .insert(0, {"nuclide": "Fe55", "delta_bq_per_g": 1.0,
                     "share_of_difference": 0.9})),
        ("closure", lambda v: v["pathway_attribution"]["rafm_base"]
         .__setitem__("significant_nuclide_closure", 0.9)),
        ("survival", lambda v: v["robustness_survival"]
         ["by_response"]["total_activity_bq_per_g"]["sample_means"]
         .__setitem__("rafm_low", 1e9)),
        ("pass", lambda v: v.__setitem__("controls_pass", False)),
    ]
    for label, mut in muts:
        planted += 1
        v = copy.deepcopy(ctr)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G2", "phase": "P36", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
