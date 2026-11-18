#!/usr/bin/env python3
"""Compute the numbers for the course chapter "Where does it come from?" (pathways).

The chapter traces the bracket of chapter 1, with 0.1 wt% cobalt, one day after shutdown: its schedule is
chapter 1's, cut at 1 day of cooling, and it asks for ACTINV's `pathways` route analysis for eight nuclides.
Three runs: the chapter 1 field with 0.1% cobalt, the same without cobalt, and 0.1% cobalt in a field of
14 MeV neutrons only (the same total flux), to show which routes need slow neutrons.

Writes:
  where-from/files/routes.json         the problem the learner downloads (0.1% cobalt, chapter 1 field)
  where-from/data/routes-data.json     what the page shows

Usage, from the repository root, with actinv-data/ present and a release actinv:

  python3 web/learn/build/make_routes.py --actinv target/release/actinv --work target/learn-work/routes
"""

import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "web/learn/first-irradiation/files"
CHAPTER = ROOT / "web/learn/where-from"
TARGETS = ["Mn56", "Cr51", "Fe55", "Co58", "Mn54", "Co60", "Fe59", "Co57"]
ONE_DAY_STEPS = 14  # chapter 1's schedule: 7 irradiation steps, then cooling to 1 day at step 14
MASS_G = 20000.0


def with_cobalt(problem, co):
    p = copy.deepcopy(problem)
    comp = dict(p["material"]["composition"])
    if co:
        comp["Fe"] = round(comp["Fe"] - co, 6)
        comp["Co"] = co
    p["material"]["composition"] = comp
    return p


def run(actinv, spec, work, name):
    sp, out = work / f"{name}.json", work / f"{name}-result.json"
    sp.write_text(json.dumps(spec, indent=1))
    subprocess.run([actinv, "run", sp.name, out.name], check=True, cwd=work,
                   env={**os.environ, "ACTINV_DATA_DIR": str(ROOT / "actinv-data")})
    return json.loads(out.read_text())


def fast_only(actinv, work, total):
    out = work / "shape-fusion-14mev.json"
    out.unlink(missing_ok=True)
    subprocess.run([actinv, "new", str(out), "--material", "ss316", "--source", "fusion-14mev", "--flux", "1"],
                   check=True, capture_output=True)
    f = json.loads(out.read_text())["spectrum"]["flux_per_group"]
    return [float(f"{total * v:.6g}") for v in f]


def summarise(res):
    step = res["steps"][-1]
    ps = step["photon_source"]
    dose = {r["nuclide"]: r["point_source_gamma_air_dose_Sv_h"] for r in ps["by_nuclide"]
            if r.get("point_source_gamma_air_dose_Sv_h")}
    out = {}
    for t in res["pathway_routes"]["targets"]:
        out[t["target"]] = {
            "activity_Bq": step["activity_Bq_per_g"].get(t["target"], 0.0) * MASS_G,
            "dose_Gy_h": dose.get(t["target"], 0.0),
            "explained": t["explained_fraction"],
            "routes": [{"nuclides": r["nuclides"], "edges": r["edges"], "fraction": round(r["fraction"], 5)}
                       for r in t["paths"] if r["fraction"] >= 0.005 and not r["is_loop"]],
        }
    return {"total_dose_Gy_h": ps["point_source_dose"]["gamma_air_dose_Sv_h"],
            "total_activity_Bq": sum(step["activity_Bq_per_g"].values()) * MASS_G, "targets": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actinv", default=str(ROOT / "target/release/actinv"))
    ap.add_argument("--work", default=str(ROOT / "target/learn-work/routes"))
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "photon-response.json").write_bytes((SRC / "photon-response.json").read_bytes())

    base = json.loads((SRC / "bracket.json").read_text())
    base["title"] = ("Steel bracket with 0.1 wt% cobalt, one day after two years near a fusion machine: "
                     "where its radioactive nuclides come from (illustrative course example)")
    base["schedule"] = base["schedule"][:ONE_DAY_STEPS]
    base["pathways"] = {"targets": TARGETS, "path_floor": 0.005, "max_paths": 20}
    problem = with_cobalt(base, 0.1)
    (CHAPTER / "files").mkdir(parents=True, exist_ok=True)
    (CHAPTER / "data").mkdir(parents=True, exist_ok=True)
    (CHAPTER / "files/routes.json").write_text(json.dumps(problem, indent=1) + "\n")
    (CHAPTER / "files/photon-response.json").write_bytes((SRC / "photon-response.json").read_bytes())

    fast = copy.deepcopy(problem)
    fast["spectrum"]["flux_per_group"] = fast_only(a.actinv, work, problem["spectrum"]["total"])
    cases = {
        "cobalt": run(a.actinv, problem, work, "routes"),
        "no_cobalt": run(a.actinv, with_cobalt(base, 0.0), work, "routes-no-cobalt"),
        "fast_only": run(a.actinv, fast, work, "routes-fast-only"),
    }
    data = {
        "about": "ACTINV pathway routes for the course bracket one day after shutdown: each route's share of its "
                 "nuclide's atoms; activity in Bq and the 1 m point-source air kerma rate in Gy/h (about Sv/h).",
        "actinv_version": subprocess.run([a.actinv, "--version"], capture_output=True, text=True).stdout.strip(),
        "problem_sha256": hashlib.sha256((CHAPTER / "files/routes.json").read_bytes()).hexdigest(),
        "composition_wt_percent": problem["material"]["composition"],
        "cases": {k: summarise(v) for k, v in cases.items()},
    }
    (CHAPTER / "data/routes-data.json").write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    main()
