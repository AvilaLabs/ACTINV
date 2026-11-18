#!/usr/bin/env python3
"""Compute the numbers for the course chapter "Planning maintenance".

Two parts sat two years in chapter 1's neutron field: the bracket (with 0.1 wt% cobalt) and a 5 kg copper
cooling pipe. `actinv maintenance` plans three jobs on them after shutdown. The page lets the learner change the
value put on dose (cost per person-sievert) and the bracket's cobalt; each combination is its own plan run.

Writes:
  maintenance/files/bracket.json, pipe.json, plan.json   what the learner downloads and runs
  maintenance/data/maintenance-data.json                 what the page shows

Usage, from the repository root, with actinv-data/ present and a release actinv (under a minute):

  python3 web/learn/build/make_maintenance.py --actinv target/release/actinv --work target/learn-work/maintenance
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
CHAPTER = ROOT / "web/learn/maintenance"
PER_SV = [2e5, 1e6, 5e6, 2e7]  # cost put on one person-sievert of dose (illustrative)
PER_DAY = 2e3  # cost of one more day of outage (illustrative)
PLAN = {
    "schema": "actinv-maintenance-1",
    "dose_rates": {"runs": [{"result": "pipe-result.json", "location": "pipe"},
                            {"result": "bracket-result.json", "location": "bracket"}]},
    "access_limit_Sv_h": 3.0e-3,
    "individual_limit_Sv": 2.0e-2,
    "jobs": [
        {"name": "cut out the cooling pipe", "location": "pipe", "duration_h": 8, "crew": 2},
        {"name": "unbolt the bracket", "location": "bracket", "duration_h": 6, "crew": 3},
        {"name": "inspect the bracket's mounting", "location": "bracket", "duration_h": 2, "crew": 1},
    ],
    "waits_d": [1, 7, 14, 30, 60, 90, 120, 180, 270, 365],
    "cost": {"per_person_Sv": 1e6, "per_outage_day": PER_DAY},
}


def problems():
    b = json.loads((SRC / "bracket.json").read_text())
    b.pop("summary", None)
    bracket = copy.deepcopy(b)
    comp = dict(bracket["material"]["composition"])
    comp["Fe"] = round(comp["Fe"] - 0.1, 6)
    comp["Co"] = 0.1
    bracket["material"]["composition"] = comp
    bracket["title"] = "Steel bracket with 0.1 wt% cobalt, two years near a fusion machine (illustrative course example)"
    no_co = copy.deepcopy(b)
    no_co["title"] = "Steel bracket without cobalt, two years near a fusion machine (illustrative course example)"
    pipe = copy.deepcopy(b)
    pipe["title"] = "5 kg copper cooling pipe, two years near a fusion machine (illustrative course example)"
    pipe["material"] = {"mass_g": 5000.0, "basis": "wt_percent", "density_g_cm3": 8.96, "composition": {"Cu": 100.0}}
    return bracket, no_co, pipe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actinv", default=str(ROOT / "target/release/actinv"))
    ap.add_argument("--work", default=str(ROOT / "target/learn-work/maintenance"))
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    files = CHAPTER / "files"
    files.mkdir(parents=True, exist_ok=True)
    (CHAPTER / "data").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ACTINV_DATA_DIR": str(ROOT / "actinv-data")}
    shutil.copy(SRC / "photon-response.json", work / "photon-response.json")
    shutil.copy(SRC / "photon-response.json", files / "photon-response.json")

    bracket, no_co, pipe = problems()
    for name, doc in (("bracket.json", bracket), ("pipe.json", pipe), ("plan.json", PLAN)):
        (files / name).write_text(json.dumps(doc, indent=1) + "\n")
    (work / "bracket-no-cobalt.json").write_text(json.dumps(no_co, indent=1))
    for name in ("bracket", "pipe"):
        shutil.copy(files / f"{name}.json", work / f"{name}.json")
    stderr = {}
    for name in ("bracket", "pipe", "bracket-no-cobalt"):
        p = subprocess.run([a.actinv, "run", f"{name}.json", f"{name}-result.json"], cwd=work, env=env,
                           check=True, capture_output=True, text=True)
        stderr[name] = p.stderr.strip()

    plans = {}
    for cobalt, result in (("cobalt", "bracket-result.json"), ("no_cobalt", "bracket-no-cobalt-result.json")):
        for per_sv in PER_SV:
            plan = copy.deepcopy(PLAN)
            plan["dose_rates"]["runs"][1]["result"] = result
            plan["cost"]["per_person_Sv"] = per_sv
            name = f"plan-{cobalt}-{per_sv:g}"
            (work / f"{name}.json").write_text(json.dumps(plan, indent=1))
            p = subprocess.run([a.actinv, "maintenance", f"{name}.json", f"{name}-result.json"], cwd=work, env=env,
                               check=True, capture_output=True, text=True)
            r = json.loads((work / f"{name}-result.json").read_text())
            plans[f"{cobalt}|{per_sv:g}"] = {k: r[k] for k in ("alone", "waits", "earliest_wait_d", "best",
                                                               "interpolation_check") if k in r}
            if cobalt == "cobalt" and per_sv == 1e6:
                shutil.copy(work / f"{name}-result.json", work / "plan-result.json")
                stderr["plan"] = p.stderr.strip()

    # Dose-rate curves for the plot: 1 m dose rate after shutdown at each cooling time.
    def curve(res):
        steps = res["steps"]
        last_irr = max(i for i, s in enumerate(json.loads((work / "bracket.json").read_text())["schedule"])
                       if s["flux"] > 0)
        t0 = steps[last_irr]["t_s"]
        return [[s["t_s"] - t0, s["photon_source"]["point_source_dose"]["gamma_air_dose_Sv_h"]]
                for s in steps[last_irr + 1:]]

    curves = {n: curve(json.loads((work / f"{n}-result.json").read_text()))
              for n in ("bracket", "pipe", "bracket-no-cobalt")}
    data = {
        "about": "actinv maintenance on the course bracket and a copper pipe: plans at each cost per person-Sv, "
                 "with and without 0.1 wt% cobalt in the bracket; dose rates in Sv/h at 1 m (air kerma).",
        "actinv_version": subprocess.run([a.actinv, "--version"], capture_output=True, text=True).stdout.strip(),
        "files_sha256": {n: hashlib.sha256((files / n).read_bytes()).hexdigest()
                         for n in ("bracket.json", "pipe.json", "plan.json")},
        "plan": PLAN, "per_sv": PER_SV, "per_day": PER_DAY,
        "curves": curves, "plans": plans, "stderr": stderr,
    }
    (CHAPTER / "data/maintenance-data.json").write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    main()
