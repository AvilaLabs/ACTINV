#!/usr/bin/env python3
"""P149 S0: regenerate every number the v1.0.0 manuscript reports, on the revision release.

Parts (each can be run alone with --only):
  fns       the 132 FNS experiments with EAF-2010 through `cb3_fns.py` (spec path, paper settings), scored with the
            manuscript's rules: median experiment geometric-mean C/E, median maximum |ln C/E|, experiments within a
            factor 1.3 at every point, ACTINV within 20 % of the FISPACT-II reference at every point, experiment
            geometric mean within 10 % of the reference, the run_fns.py dispositions, kept states and solve time;
  table3    the Table 3 controls: g4_p9_pulses_openmc, g5_p9_alara, g3_p11_sensitivity, g4_p11_propagation and
            g4_p12_fng, each rerun; its record is copied to OUT/<name>.json and the committed record restored.
Every value is reported next to the v1.0.0 manuscript value, with the difference.

Usage: p149_s0_regenerate.py [--out results/p149/s0] [--only fns|table3 ...]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / "nuclear-data"
EAF = DATA / "eaf-2010/actinv_eaf2010_709g.npz"
FNG_ARCHIVE = Path(os.environ.get("ACTINV_P12_FNG_ARCHIVE",
                                  Path.home() / ".cache/avila-night/zenodo-10660030/research_data.tar.xz"))

# The manuscript's values (v1.0.0), as printed.
PAPER = {
    "fns.median_geomean_CE.actinv": 1.024,
    "fns.median_geomean_CE.fispact": 1.009,
    "fns.median_max_abs_lnCE.actinv": 0.284,
    "fns.median_max_abs_lnCE.fispact": 0.223,
    "fns.within_1p3_every_point.actinv": 62,
    "fns.within_1p3_every_point_fraction.fispact": 0.52,
    "fns.within_20pct_of_reference_every_point": 103,
    "fns.geomean_within_10pct_of_reference": 88,
    "fns.disposition.agree_measurement": 41,
    "fns.disposition.agree_reference_only": 32,
    "fns.disposition.neither": 59,
    "table3.dense_relative": 1.33e-15,
    "table3.openmc_cram48_relative": 3.91e-15,
    "table3.alara_fe56_mn56_relative": 4.12e-8,
    "table3.sensitivity_maximum_relative": 3.013e-11,
    "table3.sensitivity_maximum_absolute": 7.124e-13,
    "table3.scst_relative": 2.055e-16,
    "table3.sampling_relative": 1.943e-4,
    "table3.fng_history_relative": 2.88e-14,
    "table3.fng_endpoints": 170,
    "table3.fng_rates_relative": 3.24e-16,
    "table3.fng_rate_comparisons": 120,
}

TABLE3 = [
    ("g4_p9_pulses_openmc", "results/g4_p9_pulses_openmc.json", {}),
    ("g5_p9_alara", "results/g5_p9_alara.json", {}),
    ("g3_p11_sensitivity", "results/g3_p11_sensitivity.json", {}),
    ("g4_p11_propagation", "results/g4_p11_propagation.json", {}),
    ("g4_p12_fng", "results/g4_p12_fng.json", {"ACTINV_P12_FNG_ARCHIVE": str(FNG_ARCHIVE)}),
]


def table3_values(records: dict) -> dict:
    pulses, alara = records["g4_p9_pulses_openmc"], records["g5_p9_alara"]
    sens, prop, fng = records["g3_p11_sensitivity"], records["g4_p11_propagation"], records["g4_p12_fng"]
    nuclides = fng["history_comparison"]["nuclides"]
    return {
        "table3.dense_relative": pulses["worst_actinv_vs_dense_resolvable_relative"],
        "table3.openmc_cram48_relative": pulses["worst_actinv_vs_openmc_cram48_resolvable_relative"],
        "table3.alara_fe56_mn56_relative": alara["maximum_inventory_relative_above_1e-10_initial"],
        "table3.sensitivity_maximum_relative": sens["sensitivities"]["maximum_relative"],
        "table3.sensitivity_maximum_absolute": sens["sensitivities"]["maximum_absolute"],
        "table3.scst_relative": max(prop["rust_relative"], prop["reported_relative"]),
        "table3.sampling_relative": prop["sample_relative"],
        "table3.fng_history_relative": max(n["maximum_relative_error_at_or_above_1e6_atoms"] for n in nuclides.values()),
        "table3.fng_endpoints": min(n["endpoints"] for n in nuclides.values()),
        "table3.fng_rates_relative": fng["independent_reaction_rates"]["maximum_relative_error"],
        "table3.fng_rate_comparisons": fng["independent_reaction_rates"]["comparisons"],
    }


def run_table3(out: Path) -> dict:
    records, runs = {}, {}
    for name, result, env in TABLE3:
        path = ROOT / result
        original = path.read_bytes() if path.exists() else None
        try:
            completed = subprocess.run([sys.executable, f"controls/{name}.py"], cwd=ROOT,
                                       env={**os.environ, **env}, capture_output=True, text=True)
            fresh = path.read_bytes()
        finally:
            if original is not None:
                path.write_bytes(original)
        (out / f"{name}.json").write_bytes(fresh)
        records[name] = json.loads(fresh)
        runs[name] = {"returncode": completed.returncode, "record_pass": records[name].get("pass"),
                      "stderr_tail": completed.stderr[-400:]}
    return {"runs": runs, "values": table3_values(records)}


def fns_values(record: dict) -> dict:
    a_key, f_key = "actinv_tendl2017_uW_g", "fispact_tendl2017_uW_g"
    experiments = record["records"]
    within = math.log(1.3)
    counts = {"within_20": 0, "geomean_10": 0, "meas": 0, "ref_only": 0, "neither": 0, "a13": 0, "f13": 0}
    for e in experiments:
        pairs = [p for p in e["pairs"] if p.get(a_key, 0) > 0 and p.get(f_key, 0) > 0 and p["measured_uW_g"] > 0]
        ln_ratio = [math.log(p[a_key] / p[f_key]) for p in pairs]
        counts["within_20"] += bool(ln_ratio) and max(map(abs, ln_ratio)) <= math.log(1.2)
        ma, mf = e["metrics"][a_key], e["metrics"][f_key]
        counts["geomean_10"] += abs(math.log(ma["geometric_mean_C_over_E"] / mf["geometric_mean_C_over_E"])) <= math.log(1.1)
        counts["a13"] += ma["maximum_abs_log_C_over_E"] <= within
        counts["f13"] += mf["maximum_abs_log_C_over_E"] <= within
        # run_fns.py dispositions: measurement agreement at every point, else reference agreement at every point.
        meas = all(abs(p[a_key] - p["measured_uW_g"]) <= max(2 * p["sigma_uW_g"], 0.1 * p["measured_uW_g"])
                   for p in e["pairs"])
        ref = bool(ln_ratio) and max(map(abs, ln_ratio)) <= 0.1
        counts["meas" if meas else "ref_only" if ref else "neither"] += 1
    n = len(experiments)
    return {
        "fns.median_geomean_CE.actinv": statistics.median(e["metrics"][a_key]["geometric_mean_C_over_E"]
                                                          for e in experiments),
        "fns.median_geomean_CE.fispact": statistics.median(e["metrics"][f_key]["geometric_mean_C_over_E"]
                                                           for e in experiments),
        "fns.median_max_abs_lnCE.actinv": statistics.median(e["metrics"][a_key]["maximum_abs_log_C_over_E"]
                                                            for e in experiments),
        "fns.median_max_abs_lnCE.fispact": statistics.median(e["metrics"][f_key]["maximum_abs_log_C_over_E"]
                                                             for e in experiments),
        "fns.within_1p3_every_point.actinv": counts["a13"],
        "fns.within_1p3_every_point_fraction.fispact": counts["f13"] / n,
        "fns.within_20pct_of_reference_every_point": counts["within_20"],
        "fns.geomean_within_10pct_of_reference": counts["geomean_10"],
        "fns.disposition.agree_measurement": counts["meas"],
        "fns.disposition.agree_reference_only": counts["ref_only"],
        "fns.disposition.neither": counts["neither"],
        "fns.experiments": n,
        "fns.points": sum(len(e["pairs"]) for e in experiments),
        "fns.median_kept_states": statistics.median(e["states"]["pruned"] for e in experiments),
        "fns.median_core_ms": statistics.median(e["core_ms"] for e in experiments),
        "fns.sum_core_ms": sum(e["core_ms"] for e in experiments),
    }


def run_fns(out: Path) -> dict:
    result = out / "fns_eaf2010.json"
    env = {**os.environ, "ACTINV_LIBRARY": str(EAF), "ACTINV_CB3_FNS_RESULT": str(result),
           "ACTINV_CB3_FNS_WORK": str(ROOT / "target/p149-s0-fns")}
    completed = subprocess.run([sys.executable, "controls/cb3_fns.py"], cwd=ROOT, env=env, capture_output=True,
                               text=True)
    record = json.loads(result.read_text())
    return {"run": {"returncode": completed.returncode, "record_pass": record.get("pass"),
                    "stderr_tail": completed.stderr[-400:]}, "values": fns_values(record)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s0")
    ap.add_argument("--only", nargs="*", choices=["fns", "table3"], default=["fns", "table3"])
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    record_path = args.out / "s0.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {"schema": "actinv-p149-s0-1"}
    if "table3" in args.only:
        record["table3"] = run_table3(args.out)
    if "fns" in args.only:
        record["fns"] = run_fns(args.out)
    values = {**record.get("fns", {}).get("values", {}), **record.get("table3", {}).get("values", {})}
    record["comparison"] = {key: {"v1_0_0": paper, "regenerated": values.get(key),
                                  "difference": None if values.get(key) is None else values[key] - paper}
                            for key, paper in PAPER.items()}
    record["paper_values_source"] = "ACTINV v1.0.0 manuscript as submitted to J Radioanal Nucl Chem (Tables 2-3, §4)"
    record_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record["comparison"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
