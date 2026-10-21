#!/usr/bin/env python3
"""FNS decay-heat error metrics for the JRNC revision (reviewer comments 1, 2 and 8).

Reads the P149 records on the revision release (Amendment A8):
  results/p149/s0/fns_eaf2010.json         ACTINV/EAF-2010 (S0, the manuscript's settings)
  results/p149/s1_fns_tendl2017_gas.json   ACTINV/TENDL-2017 with gas production, with the FISPACT-II reference (S1)

Writes paper/jrnc-revision/analysis/out/fns_error_metrics.json and a Markdown summary.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"
LN13 = math.log(1.3)

Z = {
    "F": 9, "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15, "S": 16, "Cl": 17, "K": 19, "Ca": 20,
    "Sc": 21, "Ti": 22, "V": 23, "Cr": 24, "Mn": 25, "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30,
    "Ga": 31, "Ge": 32, "As": 33, "Se": 34, "Br": 35, "Rb": 37, "Sr": 38, "Y": 39, "Zr": 40, "Nb": 41,
    "Mo": 42, "Ru": 44, "Rh": 45, "Pd": 46, "Ag": 47, "Cd": 48, "In": 49, "Sn": 50, "Sb": 51, "Te": 52,
    "I": 53, "Cs": 55, "Ba": 56, "La": 57, "Ce": 58, "Pr": 59, "Nd": 60, "Sm": 62, "Eu": 63, "Gd": 64,
    "Tb": 65, "Dy": 66, "Ho": 67, "Er": 68, "Tm": 69, "Yb": 70, "Lu": 71, "Hf": 72, "Ta": 73, "W": 74,
    "Re": 75, "Os": 76, "Ir": 77, "Pt": 78, "Au": 79, "Hg": 80, "Tl": 81, "Pb": 82, "Bi": 83,
}
ALLOYS = {"Inc600", "NiCr", "SS304", "SS316"}

GROUPS = [
    ("Alloys", lambda m: m in ALLOYS),
    ("Z 9-20", lambda m: m not in ALLOYS and Z[m] <= 20),
    ("Z 21-30", lambda m: m not in ALLOYS and 21 <= Z[m] <= 30),
    ("Z 31-56", lambda m: m not in ALLOYS and 31 <= Z[m] <= 56),
    ("Z 57-71", lambda m: m not in ALLOYS and 57 <= Z[m] <= 71),
    ("Z 72-83", lambda m: m not in ALLOYS and Z[m] >= 72),
]
BANDS = [
    ("< 10 min", 0.0, 600.0),
    ("10 min - 1 h", 600.0, 3600.0),
    ("1 h - 1 d", 3600.0, 86400.0),
    (">= 1 d", 86400.0, math.inf),
]
SERIES = ("actinv_eaf2010", "actinv_tendl2017", "fispact_tendl2017")


def group_of(material: str) -> str:
    for name, test in GROUPS:
        if test(material):
            return name
    raise KeyError(material)


def band_of(t_s: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= t_s < hi:
            return name
    raise ValueError(t_s)


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_points() -> list[dict]:
    """One row per (experiment, measured time), from the P149 records on the revision release.

    S0 (`results/p149/s0/fns_eaf2010.json`) is the ACTINV/EAF-2010 run; it is written by the cb3 harness, whose
    series key keeps the name `actinv_tendl2017_uW_g`. S1 (`results/p149/s1_fns_tendl2017_gas.json`) is the
    ACTINV/TENDL-2017 run with gas production and carries the published FISPACT-II/TENDL-2017 reference.
    """
    s0 = json.loads((ROOT / "results/p149/s0/fns_eaf2010.json").read_text())
    s1 = json.loads((ROOT / "results/p149/s1_fns_tendl2017_gas.json").read_text())
    eaf = {(r["material"], r["experiment"]): r["pairs"] for r in s0["records"]}
    points = []
    unmatched = 0
    for rec in s1["records"]:
        key = (rec["material"], rec["experiment"])
        rows = eaf[key]
        if len(rows) != len(rec["pairs"]):
            raise SystemExit(f"row count differs for {key}: {len(rows)} vs {len(rec['pairs'])}")
        t_irr = 300.0 if key[1].endswith("5min") else 25200.0
        for e_row, p in zip(rows, rec["pairs"]):
            if e_row["measured_uW_g"] != p["measured_uW_g"] or e_row["time_s"] != p["time_s"]:
                unmatched += 1
                continue
            c_eaf = e_row["actinv_tendl2017_uW_g"]
            if not (c_eaf and c_eaf > 0 and math.isfinite(c_eaf)):
                unmatched += 1
                continue
            points.append({
                "material": key[0],
                "experiment": key[1],
                "t_irr_s": t_irr,
                "t_s": p["time_s"],
                "E": p["measured_uW_g"],
                "sigma": p["sigma_uW_g"],
                "ln_ce": {
                    "actinv_eaf2010": math.log(c_eaf / p["measured_uW_g"]),
                    "actinv_tendl2017": math.log(p["actinv_tendl2017_gas_uW_g"] / p["measured_uW_g"]),
                    "fispact_tendl2017": math.log(p["fispact_tendl2017_uW_g"] / p["measured_uW_g"]),
                },
                "C": {
                    "actinv_tendl2017": p["actinv_tendl2017_gas_uW_g"],
                    "fispact_tendl2017": p["fispact_tendl2017_uW_g"],
                },
                "leaders": {"actinv": p.get("actinv_leaders"), "fispact": p.get("fispact_leaders")},
            })
    return points, unmatched


def pooled(points: list[dict], series: str) -> dict:
    a = [abs(p["ln_ce"][series]) for p in points]
    s = [p["ln_ce"][series] for p in points]
    z = [abs(math.exp(p["ln_ce"][series]) * p["E"] - p["E"]) / p["sigma"] for p in points if p["sigma"] > 0]
    return {
        "points": len(a),
        "mean_signed_ln_ce": statistics.fmean(s),
        "median_abs_ln_ce": statistics.median(a),
        "p75_abs_ln_ce": quantile(a, 0.75),
        "p90_abs_ln_ce": quantile(a, 0.90),
        "rms_ln_ce": math.sqrt(statistics.fmean(x * x for x in s)),
        "within_10pct": sum(x <= math.log(1.1) for x in a) / len(a),
        "within_factor_1p3": sum(x <= LN13 for x in a) / len(a),
        "within_factor_2": sum(x <= math.log(2.0) for x in a) / len(a),
        "within_2_sigma": sum(x <= 2.0 for x in z) / len(z) if z else None,
    }


def by_experiment(points: list[dict]) -> dict:
    exps: dict[tuple, list[dict]] = {}
    for p in points:
        exps.setdefault((p["material"], p["experiment"]), []).append(p)
    return exps


def experiment_metrics(exps: dict, series: str) -> dict:
    g = [math.exp(statistics.fmean(p["ln_ce"][series] for p in ps)) for ps in exps.values()]
    d = [max(abs(p["ln_ce"][series]) for p in ps) for ps in exps.values()]
    return {
        "experiments": len(g),
        "median_geometric_mean_ce": statistics.median(g),
        "median_max_abs_ln_ce": statistics.median(d),
        "all_points_within_factor_1p3": sum(x <= LN13 for x in d),
    }


def code_to_code(exps: dict) -> dict:
    """ACTINV/TENDL-2017 against FISPACT-II/TENDL-2017: same evaluation, different processing and solver."""
    r = [abs(math.log(p["C"]["actinv_tendl2017"] / p["C"]["fispact_tendl2017"])) for ps in exps.values() for p in ps]
    per_exp_max = {k: max(abs(math.log(p["C"]["actinv_tendl2017"] / p["C"]["fispact_tendl2017"])) for p in ps)
                   for k, ps in exps.items()}
    worst = sorted(per_exp_max.items(), key=lambda kv: -kv[1])[:10]
    return {
        "points": len(r),
        "median_abs_ln_ratio": statistics.median(r),
        "p90_abs_ln_ratio": quantile(r, 0.90),
        "max_abs_ln_ratio": max(r),
        "experiments_all_points_within_5pct": sum(v <= math.log(1.05) for v in per_exp_max.values()),
        "experiments_all_points_within_10pct": sum(v <= math.log(1.10) for v in per_exp_max.values()),
        "experiments_all_points_within_20pct": sum(v <= math.log(1.20) for v in per_exp_max.values()),
        "worst_experiments": [{"material": k[0], "experiment": k[1], "max_abs_ln_ratio": v} for k, v in worst],
    }


def outliers(exps: dict, series: str, n: int = 12) -> list[dict]:
    rows = []
    for (m, e), ps in exps.items():
        worst = max(ps, key=lambda p: abs(p["ln_ce"][series]))
        rows.append({
            "material": m, "experiment": e, "group": group_of(m),
            "max_abs_ln_ce": abs(worst["ln_ce"][series]),
            "ce_at_worst": math.exp(worst["ln_ce"][series]),
            "t_at_worst_s": worst["t_s"],
            "E_at_worst_uW_g": worst["E"],
        })
    return sorted(rows, key=lambda r: -r["max_abs_ln_ce"])[:n]


def main() -> int:
    points, unmatched = load_points()
    exps = by_experiment(points)
    result = {
        "schema": "jrnc-revision-fns-error-metrics-1",
        "inputs": ["results/p149/s0/fns_eaf2010.json", "results/p149/s1_fns_tendl2017_gas.json"],
        "matched_points": len(points),
        "unmatched_cb3_points": unmatched,
        "experiments": len(exps),
        "experiment_level": {s: experiment_metrics(exps, s) for s in SERIES},
        "pooled": {s: pooled(points, s) for s in SERIES},
        "by_group": {g: {s: pooled([p for p in points if group_of(p["material"]) == g], s) for s in SERIES}
                     for g, _ in GROUPS},
        "by_cooling_band": {b: {s: pooled([p for p in points if band_of(p["t_s"]) == b], s) for s in SERIES}
                            for b, _, _ in BANDS},
        "by_irradiation": {
            label: {s: pooled([p for p in points if (p["t_irr_s"] < 3600) == short], s) for s in SERIES}
            for label, short in (("5 min", True), ("7 h", False))
        },
        "code_to_code_tendl2017": code_to_code(exps),
        "outliers": {s: outliers(exps, s) for s in SERIES},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fns_error_metrics.json").write_text(json.dumps(result, indent=1) + "\n")
    (OUT / "fns_error_metrics.md").write_text(render(result))
    print(render(result))
    return 0


def render(r: dict) -> str:
    lab = {"actinv_eaf2010": "ACTINV/EAF-2010", "actinv_tendl2017": "ACTINV/TENDL-2017",
           "fispact_tendl2017": "FISPACT-II/TENDL-2017"}
    out = [f"# FNS error metrics\n\nMatched points: {r['matched_points']} "
           f"(unmatched cb3 points: {r['unmatched_cb3_points']}), experiments: {r['experiments']}\n"]
    out.append("## Experiment level\n\n| Series | median G | median max abs ln | all points in 1.3 |\n|---|---|---|---|")
    for s, m in r["experiment_level"].items():
        out.append(f"| {lab[s]} | {m['median_geometric_mean_ce']:.3f} | {m['median_max_abs_ln_ce']:.3f} | "
                   f"{m['all_points_within_factor_1p3']}/{m['experiments']} |")

    def table(title: str, block: dict) -> None:
        out.append(f"\n## {title}\n\n| Subset | n | Series | mean ln C/E | median abs | p90 abs | RMS ln | in 10% | in 1.3x | in 2x | in 2 sigma |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for k, per in block.items():
            for s, m in per.items():
                out.append(f"| {k} | {m['points']} | {lab[s]} | {m['mean_signed_ln_ce']:+.3f} | {m['median_abs_ln_ce']:.3f} | "
                           f"{m['p90_abs_ln_ce']:.3f} | {m['rms_ln_ce']:.3f} | {m['within_10pct']:.0%} | "
                           f"{m['within_factor_1p3']:.0%} | {m['within_factor_2']:.0%} | {m['within_2_sigma']:.0%} |")

    table("Pooled", {"all": r["pooled"]})
    table("By material group", r["by_group"])
    table("By cooling time", r["by_cooling_band"])
    table("By irradiation length", r["by_irradiation"])
    c = r["code_to_code_tendl2017"]
    out.append(f"\n## ACTINV vs FISPACT-II, both TENDL-2017\n\npoints {c['points']}; median abs ln ratio "
               f"{c['median_abs_ln_ratio']:.4f}; p90 {c['p90_abs_ln_ratio']:.4f}; max {c['max_abs_ln_ratio']:.3f}\n"
               f"experiments with every point within 5% / 10% / 20%: {c['experiments_all_points_within_5pct']} / "
               f"{c['experiments_all_points_within_10pct']} / {c['experiments_all_points_within_20pct']} of {r['experiments']}\n")
    out.append("| material | experiment | max abs ln(ACTINV/FISPACT) |\n|---|---|---|")
    for w in c["worst_experiments"]:
        out.append(f"| {w['material']} | {w['experiment']} | {w['max_abs_ln_ratio']:.3f} |")
    for s, rows in r["outliers"].items():
        out.append(f"\n## Largest deviations: {lab[s]}\n\n| material | experiment | group | max abs ln | C/E | t (s) | E (uW/g) |\n|---|---|---|---|---|---|---|")
        for o in rows:
            out.append(f"| {o['material']} | {o['experiment']} | {o['group']} | {o['max_abs_ln_ce']:.3f} | "
                       f"{o['ce_at_worst']:.3g} | {o['t_at_worst_s']:.4g} | {o['E_at_worst_uW_g']:.3g} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
