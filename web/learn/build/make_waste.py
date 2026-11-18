#!/usr/bin/env python3
"""Compute the numbers for the course chapter "What waste is it?".

A 50 kg support block of the course steel (SS316 with 0.1 wt% cobalt, 0.01 niobium and 0.05 nitrogen) spends
five years much closer to the plasma than chapter 1's bracket: chapter 1's spectrum shape at 5e12 n/cm2/s. It is
then classified against the U.S. 10 CFR 61.55 tables by `actinv impurities` with `waste_class` endpoints, which
derives every nuclide's half-life and alpha status from the decay data, at 1 day, 1, 10 and 100 years of cooling.
A full run of the block gives the per-nuclide shares of each table sum; the script checks that those shares add up
to the sums ACTINV reports.

Writes:
  waste/files/block.json, base.json, waste-screen.json   what the learner downloads and runs
  waste/data/waste-data.json                              what the page shows

Usage, from the repository root, with actinv-data/ present and a release actinv (about 2 minutes):

  python3 web/learn/build/make_waste.py --actinv target/release/actinv --work target/learn-work/waste-ch
"""

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "web/learn/first-irradiation/files"
CHAPTER = ROOT / "web/learn/waste"
RULES = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
DAY = 86400.0
YEAR = 365.25 * DAY
FLUX = 5e12
YEARS = 5
COOLING = [("1 day", DAY), ("1 year", YEAR), ("10 years", 10 * YEAR), ("100 years", 100 * YEAR)]
MASS_G = 50000.0
DENSITY = 7.95
IMPURITIES = {
    "Nb": {"nominal": 0.01, "min": 0.0, "max": 0.05},
    "N": {"nominal": 0.05, "min": 0.02, "max": 0.1},
    "Co": {"nominal": 0.1, "min": 0.02, "max": 0.2},
}
# Part 61 rows that apply to an activated metal and that this steel makes (table, rule pack id, nuclide).
ROWS = [(1, "C-14_activated_metal", "C14"), (1, "Ni-59_activated_metal", "Ni59"), (1, "Nb-94_activated_metal", "Nb94"),
        (1, "Tc-99", "Tc99"), (1, "I-129", "I129"),
        (2, "Ni-63_activated_metal", "Ni63"), (2, "Co-60", "Co60"), (2, "H-3", "H3")]
LN2 = 0.6931471805599453


def problems():
    b = json.loads((SRC / "bracket.json").read_text())
    b["title"] = ("50 kg steel support block, five years close to the plasma of a fusion machine "
                  "(illustrative course example)")
    f, tot = b["spectrum"]["flux_per_group"], b["spectrum"]["total"]
    b["spectrum"]["flux_per_group"] = [float(f"{v * FLUX / tot:.6g}") for v in f]
    b["spectrum"]["total"] = FLUX
    b["material"]["mass_g"] = MASS_G
    steps, prev = [{"dt": f"{YEAR:.10g} s", "flux": 1.0} for _ in range(YEARS)], 0.0
    for _, c in COOLING:
        steps.append({"dt": f"{c - prev:.10g} s", "flux": 0.0})
        prev = c
    b["schedule"] = steps
    b.pop("summary", None)
    b.pop("photon", None)
    base = copy.deepcopy(b)  # the matrix without the three impurities; the screen adds them
    block = copy.deepcopy(b)
    comp = dict(b["material"]["composition"])
    comp["Fe"] = round(comp["Fe"] - sum(v["nominal"] for v in IMPURITIES.values()), 6)
    comp.update({k: v["nominal"] for k, v in IMPURITIES.items()})
    block["material"]["composition"] = comp
    return base, block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actinv", default=str(ROOT / "target/release/actinv"))
    ap.add_argument("--work", default=str(ROOT / "target/learn-work/waste-ch"))
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    files = CHAPTER / "files"
    files.mkdir(parents=True, exist_ok=True)
    (CHAPTER / "data").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ACTINV_DATA_DIR": str(ROOT / "actinv-data")}

    base, block = problems()
    first = YEARS + 1
    targets = list(range(first, first + len(COOLING)))
    comp = base["material"]["composition"]
    screen = {
        "schema": "actinv-impurities-1", "base_spec": "base.json", "balance": "Fe",
        "matrix": {k: v for k, v in comp.items() if k != "Fe"}, "impurities": IMPURITIES, "targets": targets,
        "endpoints": [{"endpoint": "waste_class", "class": "A"}, {"endpoint": "waste_class", "class": "C"}],
        "waste": {"rules": "us-nrc-10cfr61.55-v1", "mass_g": MASS_G, "density_g_cm3": DENSITY,
                  "waste_type": "activated_metal", "external_tritium": {"status": "not_applicable"}},
    }
    for name, doc in (("block.json", block), ("base.json", base), ("waste-screen.json", screen)):
        (files / name).write_text(json.dumps(doc, indent=1) + "\n")
        shutil.copy(files / name, work / name)
    stderr = {}
    p = subprocess.run([a.actinv, "run", "block.json", "block-result.json"], cwd=work, env=env, check=True,
                       capture_output=True, text=True)
    stderr["run"] = p.stderr.strip()
    p = subprocess.run([a.actinv, "impurities", "waste-screen.json", "waste-screen-result.json"], cwd=work, env=env,
                       check=True, capture_output=True, text=True)
    stderr["screen"] = p.stderr.strip()
    run = json.loads((work / "block-result.json").read_text())
    res = json.loads((work / "waste-screen-result.json").read_text())

    rules = json.loads(RULES.read_text())
    limits = {r["id"]: r["limits"] for r in rules["rows"]}
    ci = rules["ci_bq"]
    volume_m3 = MASS_G / DENSITY / 1e6
    times = []
    for (label, _), step, tgt in zip(COOLING, targets, res["targets"]):
        st = run["steps"][step - 1]
        act = st["activity_Bq_per_g"]
        ci_m3 = {n: act.get(n, 0.0) * MASS_G / ci / volume_m3 for _, _, n in ROWS}
        # Every other nuclide with a half-life under 5 years (from its atoms and activity) is one Table 2 row.
        atoms = {r["nuclide"]: r["atoms_per_g"] for r in st["inventory"]}
        listed = {n for _, _, n in ROWS}
        short = sum(a for n, a in act.items() if a > 0 and n not in listed and atoms.get(n)
                    and LN2 * atoms[n] / a < 5 * YEAR)
        ci_m3["short_lived"] = short * MASS_G / ci / volume_m3
        shares = {"table1_C": {}, "table2_A": {"short_lived": ci_m3["short_lived"] / limits["half_life_lt5y"][0]},
                  "table2_C": {}}
        for table, rid, n in ROWS:
            lim = limits[rid]
            if table == 1:
                shares["table1_C"][n] = ci_m3[n] / lim[0]
            else:
                if lim[0]:
                    shares["table2_A"][n] = ci_m3[n] / lim[0]
                if len(lim) > 2 and lim[2]:
                    shares["table2_C"][n] = ci_m3[n] / lim[2]
        eps = {e["response"]: e for e in tgt["endpoints"]}
        reported = {}
        for resp, key in (("waste_class:C", "C"), ("waste_class:A", "A")):
            for c in eps[resp]["nominal"]["target_class_constraints"]:
                reported[f"{key}|{c['table']}|{c['column']}"] = c["normalized_sum"]
        # Table 1 for class A is a tenth of the class C limits; the shares above are against class C.
        for key, mine in (("C|1|None", sum(shares["table1_C"].values())), ("C|2|3", sum(shares["table2_C"].values())),
                          ("A|2|1", sum(shares["table2_A"].values()))):
            assert abs(mine - reported[key]) <= 0.02 * reported[key], (label, key, mine, reported)
        times.append({
            "label": label,
            "ci_per_m3": ci_m3,
            "shares": shares,
            "reported_sums": reported,
            "class": {resp.split(":")[1]: {"matrix_only": eps[resp]["matrix_only"]["class"],
                                           "nominal": eps[resp]["nominal"]["class"],
                                           "worst": eps[resp]["worst_case"]["class"],
                                           "binding": eps[resp].get("binding_constraint_at_nominal")}
                      for resp in eps},
            "impurities": {k: {"contribution": v["contribution_nominal"], "at_max": v["contribution_at_max"],
                               "gradient": v["gradient_normalized_sum_per_wt_pct"]}
                           for k, v in eps["waste_class:C"]["impurities"].items()},
            "matrix_sum_C_table1": next(c["normalized_sum"] for c in
                                        eps["waste_class:C"]["matrix_only"]["target_class_constraints"]
                                        if c["table"] == 1),
        })
    data = {
        "about": "The course support block classified against 10 CFR 61.55 (activated metal) by actinv impurities; "
                 "shares are each nuclide's concentration over its table limit, from a full run of the block.",
        "actinv_version": subprocess.run([a.actinv, "--version"], capture_output=True, text=True).stdout.strip(),
        "files_sha256": {n: hashlib.sha256((files / n).read_bytes()).hexdigest()
                         for n in ("block.json", "base.json", "waste-screen.json")},
        "flux": FLUX, "years": YEARS, "mass_g": MASS_G, "density_g_cm3": DENSITY, "impurities": IMPURITIES,
        "limits_ci_m3": {n: limits[rid] for _, rid, n in ROWS},
        "times": times, "stderr": stderr,
    }
    (CHAPTER / "data/waste-data.json").write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    main()
