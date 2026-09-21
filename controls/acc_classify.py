#!/usr/bin/env python3
"""Classify each FNS fail dossier: shared_library | actinv_specific | data_gap.

Reads results/acc_dossier/*.json (from acc_dossier.py) and, for each fail,
finds the dominant nuclide at the worst |C/E| point and compares ACTINV's
per-nuclide heat to FISPACT's (per-nuclide identity => library-level cause).

Output: results/acc_classify.json + a console table.
"""
import json, math, glob, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSS = os.path.join(ROOT, "results", "acc_dossier")
OUT = os.path.join(ROOT, "results", "acc_classify.json")


def classify(path):
    d = json.load(open(path))
    rec = {
        "material": d["material"],
        "experiment": d["experiment"],
        "slope": d.get("ce_slope_dlnCE_dlnt"),
        "classes": set(),
        "worst": None,
        "dominant": None,
        "fispact_ratio": None,
        "metastable_top": False,
        "missing_decay": False,
        "defect_flags": [],
        "notes": [],
    }
    # worst |log C/E| step
    steps = d.get("per_step", [])
    worst = None
    for s in steps:
        m, c = s.get("measured_uW_g"), s.get("total_heat_uW_g")
        if m and c and m > 0 and c > 0:
            e = abs(math.log(c / m))
            if worst is None or e > worst[0]:
                worst = (e, s)
    if not worst:
        rec["classes"].add("unscored")
        rec["classes"] = sorted(rec["classes"])
        return rec
    e, s = worst
    rec["worst"] = {"cooling_s": s["cooling_s"], "abs_log_ce": round(e, 4),
                    "calc": s["total_heat_uW_g"], "meas": s["measured_uW_g"]}
    if s.get("missing_decay_nuclides"):
        rec["missing_decay"] = True
        rec["classes"].add("data_gap")
        rec["notes"].append(f"missing decay data: {s['missing_decay_nuclides']}")
    top = s.get("top") or []
    if top:
        name, heat = top[0][0], top[0][1]
        rec["dominant"] = {"nuclide": name, "heat": heat,
                           "share": round(heat / s["total_heat_uW_g"], 3)}
        rec["metastable_top"] = "m" in name.lower().replace("m1", "m").rstrip("0123456789m") or name.endswith("m1") or name.endswith("m2")
        # FISPACT total-heat comparison at this cooling time
        fn = d.get("fispact_nuclides") or {}
        heats = fn.get("total_kW_kg") or []
        ts = fn.get("t_y") or []
        if heats and ts:
            ti = min(range(len(ts)), key=lambda i: abs(ts[i] - s["cooling_s"] / 3.15576e7))
            fheat = heats[ti] * 1e6  # kW/kg -> uW/g
            if fheat and fheat > 0:
                rec["fispact_ratio"] = round(s["total_heat_uW_g"] / fheat, 4)
                rec["fispact_heat"] = fheat
                # near-zero both sides: ratio is numerically meaningless
                if fheat < 0.02 * s["measured_uW_g"] and s["total_heat_uW_g"] < 0.02 * s["measured_uW_g"]:
                    rec["fispact_ratio"] = None
                    rec["notes"].append("both codes ~0 vs measurement; ratio n/a")
        # defect flags on the dominant nuclide's channels
        dn = (d.get("dominant_nuclides") or {}).get(name) or {}
        for row in dn.get("production_rows", []):
            for f in row.get("flags", []):
                rec["defect_flags"].append(f"{name}:{row.get('target','?')}MT{row.get('mt')}:{f}")
    # classification
    fr = rec["fispact_ratio"]
    if fr is not None:
        if 0.8 <= fr <= 1.25:
            rec["classes"].add("shared_library")
        else:
            rec["classes"].add("actinv_specific")
            rec["notes"].append(f"total heat differs from FISPACT by {fr}x")
    elif rec.get("fispact_heat") is not None:
        rec["classes"].add("shared_library")  # both ~0 vs measurement
    if rec["metastable_top"]:
        rec["classes"].add("isomer_dominant")
    if not rec["classes"] - {"unscored"}:
        rec["classes"].add("unclassified")
    rec["classes"] = sorted(rec["classes"])
    return rec


def main():
    recs = []
    for p in sorted(glob.glob(os.path.join(DOSS, "*.json"))):
        base = os.path.basename(p)
        if base.startswith("version_diff") or base.startswith("spec_") or base.startswith("out_"):
            continue
        try:
            r = classify(p)
            if r["worst"]:
                recs.append(r)
        except Exception as exc:
            print(f"{base}: {exc}", file=sys.stderr)
    agg = {}
    for r in recs:
        for c in r["classes"]:
            agg[c] = agg.get(c, 0) + 1
    json.dump({"aggregate": agg, "records": [
        {**r, "classes": r["classes"]} for r in recs
    ]}, open(OUT, "w"), indent=1)
    print(f"{len(recs)} dossiers classified -> {OUT}")
    print("aggregate:", agg)
    for r in recs:
        w = r["worst"]
        fr = f"{r['fispact_ratio']:.2f}" if r["fispact_ratio"] else "-"
        print(f"  {r['material']:4s} {r['experiment']:16s} CE={w['calc']/w['meas']:.3g}@{w['cooling_s']:.0f}s "
              f"top={r['dominant']['nuclide'] if r['dominant'] else '?':10s} fisp={fr} {','.join(r['classes'])}")


if __name__ == "__main__":
    main()
