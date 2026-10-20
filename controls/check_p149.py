#!/usr/bin/env python3
"""P149 checker: recompute every derived number in the study records from their stored per-case values.

Each study is `ok`, `fail` (with the reasons) or `missing` (no record: the study has not run or was not run, which
the verdict reports as incomplete, never as a pass). The checker imports no solver and no study driver.

Usage: check_p149.py [--results results/p149] [--out results/p149/check_p149.json] | --selftest
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTOL = 1e-9
A_KEY, F_KEY, G_KEY = "actinv_tendl2017_uW_g", "fispact_tendl2017_uW_g", "actinv_tendl2017_gas_uW_g"


def close(a, b, rtol: float = RTOL) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, int) and isinstance(b, int):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=rtol, abs_tol=1e-300)


# ---- S0 ---------------------------------------------------------------------------------------------------------

def check_s0(record: dict, fns: dict | None = None) -> list[str]:
    errors = []
    values = {**record.get("fns", {}).get("values", {}), **record.get("table3", {}).get("values", {})}
    for key, row in record["comparison"].items():
        if row["regenerated"] is None:
            errors.append(f"{key}: not regenerated")
            continue
        if not close(row["regenerated"], values.get(key)):
            errors.append(f"{key}: comparison {row['regenerated']} != stored value {values.get(key)}")
        if not close(row["difference"], row["regenerated"] - row["v1_0_0"], 1e-12):
            errors.append(f"{key}: difference {row['difference']} != regenerated - v1.0.0")
    if fns is not None:
        counts = fns_counts(fns)
        for key, value in counts.items():
            if not close(value, values.get(key)):
                errors.append(f"{key}: recomputed {value} != recorded {values.get(key)}")
    for name, run in record.get("table3", {}).get("runs", {}).items():
        if run["returncode"] != 0 or run["record_pass"] is not True:
            errors.append(f"table3 {name}: returncode {run['returncode']}, pass {run['record_pass']}")
    return errors


def fns_counts(record: dict) -> dict:
    """The manuscript's FNS counts, recomputed from the per-point pairs."""
    experiments = record["records"]
    meas = ref = neither = within20 = 0
    for e in experiments:
        pairs = e["pairs"]
        ln = [math.log(p[A_KEY] / p[F_KEY]) for p in pairs
              if p.get(A_KEY, 0) > 0 and p.get(F_KEY, 0) > 0 and p["measured_uW_g"] > 0]
        within20 += bool(ln) and max(map(abs, ln)) <= math.log(1.2)
        if all(abs(p[A_KEY] - p["measured_uW_g"]) <= max(2 * p["sigma_uW_g"], 0.1 * p["measured_uW_g"]) for p in pairs):
            meas += 1
        elif ln and max(map(abs, ln)) <= 0.1:
            ref += 1
        else:
            neither += 1
    return {"fns.disposition.agree_measurement": meas, "fns.disposition.agree_reference_only": ref,
            "fns.disposition.neither": neither, "fns.within_20pct_of_reference_every_point": within20,
            "fns.experiments": len(experiments)}


# ---- S1 ---------------------------------------------------------------------------------------------------------

def metrics(pairs: list[dict], key: str) -> dict:
    scored = [p for p in pairs if p.get(key, 0.0) > 0.0 and p["measured_uW_g"] > 0.0]
    if not scored:
        return {"points": 0}
    ln = [math.log(p[key] / p["measured_uW_g"]) for p in scored]
    return {"points": len(scored), "geometric_mean_C_over_E": math.exp(statistics.fmean(ln)),
            "maximum_abs_log_C_over_E": max(map(abs, ln))}


def s1_selection(record: dict) -> list[dict]:
    """Points the protocol names: either code beyond |ln C/E| = ln 2, or ACTINV beyond 10 % of FISPACT-II."""
    selected = []
    for e in record["records"]:
        for p in e["pairs"]:
            reasons = []
            for key in (G_KEY, F_KEY):
                if p.get(key, 0) > 0 and p["measured_uW_g"] > 0 and abs(math.log(p[key] / p["measured_uW_g"])) > math.log(2):
                    reasons.append(f"{key} beyond ln 2")
            if p.get(G_KEY, 0) > 0 and p.get(F_KEY, 0) > 0 and abs(p[G_KEY] / p[F_KEY] - 1.0) > 0.10:
                reasons.append("ACTINV/FISPACT-II beyond 10 %")
            if reasons:
                selected.append({"material": e["material"], "experiment": e["experiment"], "time_s": p["time_s"],
                                 "reasons": reasons, "actinv_leaders": p.get("actinv_leaders"),
                                 "fispact_leaders": p.get("fispact_leaders")})
    return selected


def check_s1(record: dict) -> list[str]:
    errors = []
    if record["experiment_count"] != len(record["records"]) or len(record["records"]) != 132:
        errors.append(f"{len(record['records'])} experiments, count {record['experiment_count']}; expected 132")
    for e in record["records"]:
        name = f"{e['material']}/{e['experiment']}"
        for key in (G_KEY, F_KEY):
            mine, stored = metrics(e["pairs"], key), e["metrics"][key]
            for field, value in mine.items():
                if not close(value, stored.get(field)):
                    errors.append(f"{name} {key} {field}: recomputed {value} != stored {stored.get(field)}")
        for p in e["pairs"]:
            if not p.get("actinv_leaders"):
                errors.append(f"{name} t={p['time_s']}: no ACTINV contributors")
            if F_KEY in p and not p.get("fispact_leaders"):
                errors.append(f"{name} t={p['time_s']}: no FISPACT-II contributors")
        if e["gas_ledger"] is None:
            errors.append(f"{name}: gas was not run")
    if not record.get("pass"):
        errors.append("the record's own checks did not pass")
    return errors


# ---- S2 and S3 --------------------------------------------------------------------------------------------------

def relative(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def check_s2(record: dict) -> list[str]:
    errors = []
    for variant in record["variants"]:
        for name, row in variant["nuclides"].items():
            if not close(row["actinv_alara_relative"], relative(row["actinv"], row["alara"])):
                errors.append(f"{variant['variant']} {name}: stored relative difference does not match its values")
        if variant["alara_run"]["run_returncode"] != 0 or variant["alara_run"]["conversion_returncode"] != 0:
            errors.append(f"{variant['variant']}: ALARA did not run")
        worst = max((r for r, _n in variant["worst_alara"]), default=None)
        if not close(worst, variant["maximum_actinv_alara_relative_reportable"]):
            errors.append(f"{variant['variant']}: maximum {variant['maximum_actinv_alara_relative_reportable']}"
                          f" != largest listed {worst}")
    if "full_precision_decay" not in record.get("not_run", {}):
        errors.append("the not-run variant is not stated")
    return errors


def check_s3(record: dict) -> list[str]:
    errors = []
    threshold_fraction = record["reportable_fraction_of_initial"]
    for name, case in record["cases"].items():
        initial = 2.5e19 * len(case["initial"])
        maxima = {"actinv_dense": None, "openmc_dense": None, "actinv_alara": None}
        if len(case["boundaries"]) != len(case["schedule"]):
            errors.append(f"{name}: {len(case['boundaries'])} boundaries for {len(case['schedule'])} segments")
        for boundary in case["boundaries"]:
            for label, row in boundary["rows"].items():
                values = [row[k] for k in ("actinv", "dense", "openmc", "alara") if k in row]
                if max(values) <= threshold_fraction * initial:
                    errors.append(f"{name} step {boundary['step']} {label}: below the threshold but compared")
                for key, (a, b) in {"actinv_dense": ("actinv", "dense"), "openmc_dense": ("openmc", "dense"),
                                    "actinv_alara": ("actinv", "alara")}.items():
                    if key in row:
                        if not close(row[key], relative(row[a], row[b])):
                            errors.append(f"{name} step {boundary['step']} {label} {key}: inconsistent")
                        maxima[key] = row[key] if maxima[key] is None else max(maxima[key], row[key])
        for key, value in maxima.items():
            stored = case["maximum_relative"][key]
            if not close(value, None if stored is None else stored[0]):
                errors.append(f"{name} {key}: maximum {stored} != recomputed {value}")
        if case["alara_run"].get("run_returncode") not in (0, None) and not case["extended_network"]:
            errors.append(f"{name}: ALARA failed on the base network")
    return errors


# ---- S4 ---------------------------------------------------------------------------------------------------------

def check_s4(record: dict) -> list[str]:
    errors = []
    seen = {(c["target"], c["temperature_K"]) for c in record["cases"]}
    wanted = {(t, T) for t in ("Fe56", "Ag107") for T in (293.6, 600.0, 1200.0)}
    if seen != wanted:
        errors.append(f"cases {sorted(seen)} != {sorted(wanted)}")
    for c in record["cases"]:
        for mt, s in c["pointwise"].items():
            if not s["median_relative"] <= s["p99_relative"] <= s["maximum_relative"] or s["points"] <= 0:
                errors.append(f"{c['target']} {c['temperature_K']} {mt}: statistics out of order or empty")
            low, high = c["resolved_range_ev"]
            if not low <= s["energy_at_maximum_ev"] <= high:
                errors.append(f"{c['target']} {c['temperature_K']} {mt}: maximum outside the resolved range")
        g = c["groups_MT102"]
        # S4 states differences relative to NJOY's value.
        if not close(g["maximum_relative"], abs(g["actinv_b"] - g["groupr_b"]) / abs(g["groupr_b"])):
            errors.append(f"{c['target']} {c['temperature_K']}: group maximum does not match its values")
        if not g["worst_group_low_ev"] < g["worst_group_high_ev"]:
            errors.append(f"{c['target']} {c['temperature_K']}: worst group bounds out of order")
        regions = g.get("by_region", {})
        high = c["resolved_range_ev"][1]
        if (sum(r["groups"] for r in regions.values()) != g["groups_compared"]
                or not close(max((r["maximum_relative"] for r in regions.values()), default=-1.0),
                             g["maximum_relative"])):
            errors.append(f"{c['target']} {c['temperature_K']}: group regions do not partition the groups compared")
        for name, r in regions.items():
            lo, hi = r["worst_group_low_ev"], r["worst_group_high_ev"]
            where = "inside_resolved" if hi <= high else "above_resolved" if lo >= high else "straddling_top"
            if where != name or not r["median_relative"] <= r["maximum_relative"]:
                errors.append(f"{c['target']} {c['temperature_K']}: group region {name} inconsistent")
    return errors


# ---- S5 ---------------------------------------------------------------------------------------------------------

def check_s5(record: dict) -> list[str]:
    errors = []
    for name, case in record["cases"].items():
        first = case["first_order"]
        if first["probe"]["collapsed_cross_section_max_relative_difference"] > 1e-9:
            errors.append(f"{name}: probe collapse differs from the run's")
        for point in first["points"]:
            for selector, d in point["responses"].items():
                where = f"{name} t={point['cooling_s']} {selector}"
                if d["variance_relative_difference"] is not None and d["variance_relative_difference"] > 1e-6:
                    errors.append(f"{where}: s^T C s differs from the run's variance by "
                                  f"{d['variance_relative_difference']}")
                split = d["variance_split"]
                if not close(sum(split.values()), d["variance_recomputed"], 1e-9):
                    errors.append(f"{where}: the variance split does not sum to the variance")
                coverage = d["coverage"]["response_weighted"]
                if coverage is not None and not 0.0 <= coverage <= 1.0 + 1e-12:
                    errors.append(f"{where}: response-weighted coverage {coverage} outside [0, 1]")
                shares = [row["share"] for row in d["top_rows"] if row["share"] is not None]
                if shares != sorted(shares, key=abs, reverse=True):
                    errors.append(f"{where}: top rows are not ordered by |share|")
        sampling = case.get("sampling")
        if sampling is None:
            errors.append(f"{name}: no sampling run")
            continue
        if sampling["samples"] != sampling["samples_declared"] or sampling["truncated_by_resource_limit"]:
            errors.append(f"{name}: {sampling['samples']} of {sampling['samples_declared']} samples")
        for response, times in sampling["per_time"].items():
            for time, row in times.items():
                if not row["recomputed_matches_tool"]:
                    errors.append(f"{name} {response} t={time}: recomputed moments differ from the tool's")
                for key, interval in row["bootstrap_95"].items():
                    if interval is not None and not interval[0] <= interval[1]:
                        errors.append(f"{name} {response} t={time} {key}: bootstrap interval reversed")
    return errors


# ---- S6 ---------------------------------------------------------------------------------------------------------

def check_s6(record: dict) -> list[str]:
    errors = []
    for name, case in record["cases"].items():
        kept = {}
        for row in case["rows"]:
            where = f"{name} {row['prune']} {row['cram_order']}"
            if not row["pattern_matches_kept"] and row["prune"] != "none":
                errors.append(f"{where}: capture keeps {row['pattern']['kept_states']}, result {row['kept_states']}")
            if row["pattern"]["nonzeros"] != row["pattern"]["off_diagonal"] + row["pattern"]["kept_states"]:
                errors.append(f"{where}: non-zeros != off-diagonal + diagonal")
            for key in ("core_ms", "wall_s"):
                s = row[key]
                if not s["min"] <= s["median"] <= s["max"]:
                    errors.append(f"{where}: {key} median outside its range")
            kept.setdefault(row["prune"], set()).add(row["kept_states"])
        if any(len(v) != 1 for v in kept.values()):
            errors.append(f"{name}: kept states depend on the CRAM order")
        elif {"rate", "reach", "none"} <= kept.keys():
            r, c, n = (next(iter(kept[k])) for k in ("rate", "reach", "none"))
            if not r <= c <= n:
                errors.append(f"{name}: kept states rate {r}, reach {c}, none {n} not increasing")
    if not record.get("host", {}).get("cgroup_limits", {}).get("cpu.max"):
        errors.append("the cgroup CPU quota is not recorded")
    return errors


STUDIES = {
    "s0": ("s0/s0.json", check_s0),
    "s1": ("s1_fns_tendl2017_gas.json", check_s1),
    "s2": ("s2_alara_nickel.json", check_s2),
    "s3": ("s3_histories.json", check_s3),
    "s4": ("s4_doppler.json", check_s4),
    "s5": ("s5_covariance.json", check_s5),
    "s6": ("s6_scaling.json", check_s6),
}


def check_all(results: Path) -> dict:
    report = {}
    for study, (relative_path, check) in STUDIES.items():
        path = results / relative_path
        if not path.is_file():
            report[study] = {"status": "missing", "record": relative_path}
            continue
        record = json.loads(path.read_text())
        extra = {}
        if study == "s0" and (results / "s0/fns_eaf2010.json").is_file():
            errors = check_s0(record, json.loads((results / "s0/fns_eaf2010.json").read_text()))
        else:
            errors = check(record)
        if study == "s1":
            extra["selected_points"] = s1_selection(record)
        report[study] = {"status": "fail" if errors else "ok", "record": relative_path, "errors": errors[:200],
                         "error_count": len(errors)} | extra
    statuses = {r["status"] for r in report.values()}
    verdict = "fail" if "fail" in statuses else "incomplete" if "missing" in statuses else "P149-COMPLETE"
    return {"schema": "actinv-p149-check-1", "studies": report, "verdict": verdict}


# ---- selftest ---------------------------------------------------------------------------------------------------

def fixtures() -> dict:
    pairs = [{"time_s": 60.0, "measured_uW_g": 10.0, "sigma_uW_g": 1.0, A_KEY: 10.5, F_KEY: 10.2, G_KEY: 10.5,
              "actinv_leaders": [{"nuclide": "Mn56", "percent": 90.0}],
              "fispact_leaders": [{"nuclide": "Mn56", "percent": 91.0}]},
             {"time_s": 600.0, "measured_uW_g": 5.0, "sigma_uW_g": 0.5, A_KEY: 12.0, F_KEY: 5.1, G_KEY: 12.0,
              "actinv_leaders": [{"nuclide": "Mn56", "percent": 80.0}],
              "fispact_leaders": [{"nuclide": "Mn56", "percent": 81.0}]}]
    experiments = [{"material": "Fe", "experiment": f"x{i}", "pairs": copy.deepcopy(pairs), "gas_ledger": {},
                    "metrics": {G_KEY: metrics(pairs, G_KEY), F_KEY: metrics(pairs, F_KEY)}} for i in range(132)]
    s1 = {"experiment_count": 132, "records": experiments, "pass": True}
    fns = {"records": experiments}
    counts = fns_counts(fns)
    s0 = {"fns": {"values": counts}, "table3": {"values": {"table3.dense_relative": 1e-15},
                                                 "runs": {"g4_p9_pulses_openmc": {"returncode": 0, "record_pass": True}}},
          "comparison": {"fns.disposition.neither": {"v1_0_0": 59, "regenerated": counts["fns.disposition.neither"],
                                                     "difference": counts["fns.disposition.neither"] - 59},
                         "table3.dense_relative": {"v1_0_0": 1.33e-15, "regenerated": 1e-15,
                                                   "difference": 1e-15 - 1.33e-15}}}
    row = {"actinv": 1.0, "alara": 1.0004, "dense": 1.0, "openmc": 1.0, "actinv_alara_relative": relative(1.0, 1.0004)}
    s2 = {"not_run": {"full_precision_decay": "x"},
          "variants": [{"variant": "baseline", "nuclides": {"Ni61": row}, "worst_alara": [[relative(1.0, 1.0004), "Ni61"]],
                        "maximum_actinv_alara_relative_reportable": relative(1.0, 1.0004),
                        "alara_run": {"run_returncode": 0, "conversion_returncode": 0}}]}
    r3 = {"actinv": 3e10, "dense": 3e10 * (1 + 1e-14), "openmc": 3e10, "alara": 3.0001e10}
    r3 |= {"actinv_dense": relative(r3["actinv"], r3["dense"]), "openmc_dense": relative(r3["openmc"], r3["dense"]),
           "actinv_alara": relative(r3["actinv"], r3["alara"])}
    s3 = {"reportable_fraction_of_initial": 1e-12, "cases": {"H1": {
        "initial": ["Na23", "Fe56", "Co59", "Ni58"], "schedule": [[1.0, 1.0]], "extended_network": False,
        "alara_run": {"run_returncode": 0}, "boundaries": [{"step": 0, "rows": {"Co60": r3}}],
        "maximum_relative": {"actinv_dense": [r3["actinv_dense"], "Co60"], "openmc_dense": [r3["openmc_dense"], "Co60"],
                             "actinv_alara": [r3["actinv_alara"], "Co60"]}}}}
    s4 = {"cases": [{"target": t, "temperature_K": T, "resolved_range_ev": [1e-5, 1e5],
                     "pointwise": {"MT102": {"points": 10, "median_relative": 1e-5, "p99_relative": 1e-4,
                                             "maximum_relative": 2e-4, "energy_at_maximum_ev": 10.0}},
                     "groups_MT102": {"maximum_relative": 0.001 / 1.001, "actinv_b": 1.0, "groupr_b": 1.001,
                                      "worst_group_low_ev": 1.0, "worst_group_high_ev": 2.0, "groups_compared": 3,
                                      "by_region": {
                                          "inside_resolved": {"groups": 2, "median_relative": 1e-5,
                                                              "maximum_relative": 0.001 / 1.001,
                                                              "worst_group_low_ev": 1.0, "worst_group_high_ev": 2.0},
                                          "straddling_top": {"groups": 1, "median_relative": 1e-4,
                                                             "maximum_relative": 1e-4, "worst_group_low_ev": 9e4,
                                                             "worst_group_high_ev": 2e5}}}}
                    for t in ("Fe56", "Ag107") for T in (293.6, 600.0, 1200.0)]}
    d = {"variance_relative_difference": 1e-12, "variance_recomputed": 4.0,
         "variance_split": {"diagonal": 3.0, "cross_within_target": 1.5, "cross_between_targets": -0.5},
         "coverage": {"response_weighted": 0.9}, "top_rows": [{"share": 0.6}, {"share": -0.3}, {"share": 0.1}]}
    s5 = {"cases": {"fe": {"first_order": {"probe": {"collapsed_cross_section_max_relative_difference": 0.0},
                                           "points": [{"cooling_s": 66.0, "responses": {"heat.total": d}}]},
                           "sampling": {"samples": 1000, "samples_declared": 1000, "truncated_by_resource_limit": False,
                                        "per_time": {"decay_heat_w_per_g": {"66.0": {
                                            "recomputed_matches_tool": True,
                                            "bootstrap_95": {"mean": [1.0, 2.0], "std": [0.1, 0.2]}}}}}}}}
    s6_rows = []
    for prune, kept in (("rate", 40), ("reach", 60), ("none", 900)):
        for order in (16, 48):
            s6_rows.append({"prune": prune, "cram_order": order, "kept_states": kept, "pattern_matches_kept": True,
                            "pattern": {"kept_states": kept, "off_diagonal": 2 * kept, "nonzeros": 3 * kept},
                            "core_ms": {"min": 1.0, "median": 2.0, "max": 3.0},
                            "wall_s": {"min": 1.0, "median": 1.5, "max": 2.0}})
    s6 = {"host": {"cgroup_limits": {"cpu.max": "200000 100000"}}, "cases": {"fe": {"rows": s6_rows}}}
    return {"s0": (s0, fns), "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6}


def run_check(study: str, value) -> list[str]:
    if study == "s0":
        return check_s0(*value)
    return STUDIES[study][1](value)


def plant(study: str, value):
    v = copy.deepcopy(value)
    if study == "s0":
        v[0]["comparison"]["table3.dense_relative"]["difference"] = 0.0
    elif study == "s1":
        v["records"][3]["metrics"][G_KEY]["maximum_abs_log_C_over_E"] *= 1.01
    elif study == "s2":
        v["variants"][0]["nuclides"]["Ni61"]["alara"] = 1.0
    elif study == "s3":
        v["cases"]["H1"]["maximum_relative"]["actinv_alara"][0] = 0.0
    elif study == "s4":
        v["cases"][2]["pointwise"]["MT102"]["p99_relative"] = 1.0
    elif study == "s5":
        v["cases"]["fe"]["first_order"]["points"][0]["responses"]["heat.total"]["variance_split"]["diagonal"] = 3.5
    elif study == "s6":
        v["cases"]["fe"]["rows"][0]["kept_states"] = 70
        v["cases"]["fe"]["rows"][1]["kept_states"] = 70
        for row in v["cases"]["fe"]["rows"][:2]:
            row["pattern"] = {"kept_states": 70, "off_diagonal": 140, "nonzeros": 210}
    return v


def selftest() -> int:
    failures = []
    for study, value in fixtures().items():
        clean = run_check(study, value)
        if clean:
            failures.append(f"{study}: clean fixture rejected: {clean}")
        planted = run_check(study, plant(study, value))
        if not planted:
            failures.append(f"{study}: planted error not caught")
    region = copy.deepcopy(fixtures()["s4"])
    region["cases"][0]["groups_MT102"]["by_region"]["straddling_top"]["worst_group_high_ev"] = 5.0e4
    if not run_check("s4", region):
        failures.append("s4: mislabelled group region not caught")
    selection = s1_selection(fixtures()["s1"])
    if len(selection) != 132 or selection[0]["time_s"] != 600.0:
        failures.append(f"s1 selection: {len(selection)} points")
    print(json.dumps({"selftest": "pass" if not failures else "fail", "failures": failures}, indent=1))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=ROOT / "results/p149")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    report = check_all(args.results)
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    print(json.dumps({k: v["status"] for k, v in report["studies"].items()} | {"verdict": report["verdict"]}, indent=1))
    return 0 if report["verdict"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
