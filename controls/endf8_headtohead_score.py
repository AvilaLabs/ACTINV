#!/usr/bin/env python3
"""Score the ACTINV/ENDF-B-VIII.1 FNS arm (results/fns_endfb8) onto the
sealed head-to-head record (results/openmc_fns_headtohead.json): adds
actinv_endfb8_uW_g per measurement pair and recomputes the summary.
Writes results/endf8_fns_headtohead.json."""
import json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
H2H = RES / "openmc_fns_headtohead.json"
ARMDIR = RES / "fns_endfb8"
OUT = RES / "endf8_fns_headtohead.json"
LN2 = math.log(2.0); WITHIN_LOG = math.log(1.3)


def product_metrics(pairs, key):
    scored = [r for r in pairs
              if r.get(key, 0.0) > 0.0 and r["measured_uW_g"] > 0.0]
    if not scored:
        return {"points": 0,
                "unscored_nonpositive_calculation": len(pairs)}
    logs = [math.log(r[key] / r["measured_uW_g"]) for r in scored]
    abs_log = [abs(v) for v in logs]
    sig = [(r[key] - r["measured_uW_g"]) / r["sigma_uW_g"]
           for r in scored if r["sigma_uW_g"] > 0.0]
    return {
        "points": len(scored),
        "unscored_nonpositive_calculation": len(pairs) - len(scored),
        "geometric_mean_C_over_E": math.exp(sum(logs) / len(logs)),
        "maximum_abs_log_C_over_E": max(abs_log),
        "all_points_within_30_percent": all(v <= WITHIN_LOG
                                            for v in abs_log),
        "positive_sigma_points": len(sig),
        "rms_measurement_sigma": math.sqrt(
            sum(v * v for v in sig) / len(sig)) if sig else None}


def aggregate(records, key):
    scored_pairs = [r for rec in records for r in rec["pairs"]
                    if r.get(key, 0.0) > 0.0 and r["measured_uW_g"] > 0.0]
    if not scored_pairs:
        return {"experiments_scored": 0}
    logs = [math.log(r[key] / r["measured_uW_g"]) for r in scored_pairs]
    abs_log = sorted(abs(v) for v in logs)
    exps = [rec["metrics"][key] for rec in records
            if rec["metrics"].get(key, {}).get("points", 0) > 0]
    return {
        "experiments_scored": len(exps),
        "points_scored": len(scored_pairs),
        "pooled_geometric_mean_C_over_E": math.exp(
            sum(logs) / len(logs)),
        "median_pooled_abs_log_C_over_E": abs_log[len(abs_log) // 2],
        "p90_pooled_abs_log_C_over_E":
            abs_log[min(len(abs_log) - 1, int(0.9 * len(abs_log)))],
        "experiments_all_points_within_30_percent": sum(
            m["all_points_within_30_percent"] for m in exps)}


def main():
    h2h = json.loads(H2H.read_text())
    missing = []
    for rec in h2h["records"]:
        if not rec.get("pairs"):
            continue
        f = ARMDIR / f"{rec['material']}_{rec['experiment']}.json"
        if not f.is_file():
            missing.append(f"{rec['material']}_{rec['experiment']}")
            continue
        r = json.loads(f.read_text())
        steps = r["steps"]
        heat = r["heat_uW_g"]            # heat[k-1] == steps[k]
        t_irr = steps[0]["t_s"]
        ts = [s["t_s"] for s in steps]
        for pair in rec["pairs"]:
            target = t_irr + pair["time_s"]
            k = min(range(1, len(ts)), key=lambda i: abs(ts[i] - target))
            # only accept an exact cooling-boundary match
            if abs(ts[k] - target) <= max(0.02 * target, 1.0):
                pair["actinv_endfb8_uW_g"] = heat[k - 1]
        rec["metrics"]["actinv_endfb8_uW_g"] = product_metrics(
            rec["pairs"], "actinv_endfb8_uW_g")
    arms = ["openmc_endfb8_uW_g", "actinv_endfb8_uW_g",
            "actinv_tendl2017_uW_g", "actinv_tendl2025_uW_g",
            "fispact_tendl2017_uW_g"]
    h2h["summary"] = {a: aggregate(h2h["records"], a) for a in arms}
    h2h["note"] += "; actinv_endfb8 arm added by endf8_headtohead_score"
    h2h["actinv_endfb8_missing_experiments"] = missing
    OUT.write_text(json.dumps(h2h, indent=1) + "\n")
    print(json.dumps({"missing": missing,
                      "summary": h2h["summary"]}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
