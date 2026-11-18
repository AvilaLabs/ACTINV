#!/usr/bin/env python3
"""Compute the numbers for the course chapter "Your first irradiation".

The chapter's bracket is examples/maintenance/bracket.json (20 kg SS316, two years of D-T
neutrons at 3e8 n/cm2/s) with a finer schedule, so the charts show the build-up and the cooling
in detail. The page lets the learner change the bracket's cobalt content over a few fixed
levels; each level is its own ACTINV run (cobalt replaces the same mass of iron), so every
number on the page is the number that run prints. The 1 m dose is not additive between runs of
pure elements (ACTINV evaluates it at the group-centroid energies of the whole material's photon
spectrum), which is why the page does not combine element runs.

Writes:
  first-irradiation/files/bracket.json        the problem the learner downloads and runs
  first-irradiation/data/bracket-data.json     what the page plots

Usage, from the repository root, with actinv-data/ present and a release actinv:

  python3 web/learn/build/make_bracket.py --actinv target/release/actinv --work target/learn-work
"""

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHAPTER = ROOT / "web/learn/first-irradiation"
DAY = 86400.0
YEAR = 365.25 * DAY

# Irradiation ends at these cumulative times; cooling steps end at these times after shutdown.
IRRADIATION_ENDS_D = [1, 7, 30, 90, 182.625, 365.25, 730.5]
COOLING_S = [
    60, 600, 3600, 3 * 3600, 6 * 3600, 12 * 3600,
    *(d * DAY for d in [1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90, 120, 180, 270]),
    *(y * YEAR for y in [1, 1.5, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100]),
]
COBALT_WT_PERCENT = [0.0, 0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3]
MASS_G = 20000.0
KEEP_SHARE = 0.01  # a nuclide is listed if it is at least this share of a total at some step


def schedule():
    steps, t = [], 0.0
    for end in IRRADIATION_ENDS_D:
        steps.append({"dt": f"{(end * DAY - t):.10g} s", "flux": 1.0})
        t = end * DAY
    prev = 0.0
    for c in COOLING_S:
        steps.append({"dt": f"{(c - prev):.10g} s", "flux": 0.0})
        prev = c
    return steps


# The neutron field: an illustrative moderated D-T field, a mix of ACTINV's generic shapes
# (`actinv new --source ...`): a 14.1 MeV line, a 1/E slowing-down spectrum and a thermal peak.
SPECTRUM_MIX = {"fusion-14mev": 0.25, "one-over-e": 0.55, "thermal": 0.20}
TOTAL_FLUX = 1e10


def spectrum(actinv, work):
    groups = None
    for shape, share in SPECTRUM_MIX.items():
        out = work / f"shape-{shape}.json"
        out.unlink(missing_ok=True)
        subprocess.run([actinv, "new", str(out), "--material", "ss316", "--source", shape, "--flux", "1"],
                       check=True, capture_output=True)
        f = json.loads(out.read_text())["spectrum"]["flux_per_group"]
        groups = [share * v for v in f] if groups is None else [g + share * v for g, v in zip(groups, f)]
    return [float(f"{TOTAL_FLUX * v:.6g}") for v in groups]


def learner_problem(actinv, work):
    p = json.loads((ROOT / "examples/maintenance/bracket.json").read_text())
    p["title"] = ("Steel bracket near a fusion machine: 20 kg SS316, two years in a moderated D-T neutron "
                  "field, then cooled to 100 years (illustrative course example)")
    p["spectrum"]["flux_per_group"] = spectrum(actinv, work)
    p["spectrum"]["total"] = TOTAL_FLUX
    p["photon"]["exclude_xrays"] = True  # the steel itself absorbs its soft X-rays
    p["schedule"] = schedule()
    resp = CHAPTER / "files/photon-response.json"
    p["photon"]["response"] = {"path": "photon-response.json",
                               "sha256": hashlib.sha256(resp.read_bytes()).hexdigest()}
    p.pop("damage", None)  # not used by the chapter; keeps the download to the standard data
    # Totals in Bq and W and the five nuclides that dominate each step, for `actinv report`.
    p["summary"] = {"nuclides": False, "elements": False, "dominant_top": 5}
    return p


def run(actinv, spec, work, name):
    sp, out = work / f"{name}.json", work / f"{name}-result.json"
    text = json.dumps(spec, indent=1)
    if not out.exists() or not sp.exists() or sp.read_text() != text:
        sp.write_text(text)
        subprocess.run([actinv, "run", sp.name, out.name], check=True, cwd=work,
                       env={**os.environ, "ACTINV_DATA_DIR": str(ROOT / "actinv-data")})
    return json.loads(out.read_text())


def g(x):
    return float(f"{x:.5g}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actinv", default=str(ROOT / "target/release/actinv"))
    ap.add_argument("--work", default=str(ROOT / "target/learn-work"))
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "photon-response.json").write_bytes((CHAPTER / "files/photon-response.json").read_bytes())

    problem = learner_problem(a.actinv, work)
    (CHAPTER / "files/bracket.json").write_text(json.dumps(problem, indent=1) + "\n")
    base = problem["material"]["composition"]

    runs = []
    for co in COBALT_WT_PERCENT:
        spec = copy.deepcopy(problem)
        comp = dict(base)
        if co:
            comp["Fe"] = round(comp["Fe"] - co, 6)
            comp["Co"] = co
        spec["material"]["composition"] = comp
        res = run(a.actinv, spec, work, "bracket" if not co else f"bracket-co{co}")
        steps = []
        for s in res["steps"]:
            ps = s["photon_source"]
            steps.append({
                "activity": {k: v * MASS_G for k, v in s["activity_Bq_per_g"].items() if v > 0},
                "heat_W": s["heat_W_per_g"]["total"] * MASS_G,
                "dose_Gy_h": ps["point_source_dose"]["gamma_air_dose_Sv_h"],
                "dose_by": {r["nuclide"]: r["point_source_gamma_air_dose_Sv_h"]
                            for r in ps["by_nuclide"] if r.get("point_source_gamma_air_dose_Sv_h")},
            })
        runs.append((co, res, steps))
        print(f"Co {co} wt%: done", file=sys.stderr)

    def keep(field):
        names = set()
        for _, _, steps in runs:
            for s in steps:
                tot = sum(s[field].values())
                names |= {k for k, v in s[field].items() if tot and v >= KEEP_SHARE * tot}
        return sorted(names)

    act_names, dose_names = keep("activity"), keep("dose_by")
    data = {
        "about": "ACTINV results for the course bracket at each cobalt level; activity in Bq, heat in W "
                 "and the air kerma rate 1 m from the bracket as a point source in Gy/h (about Sv/h for "
                 "these photons); by-nuclide doses use each nuclide's own photon groups.",
        "actinv_version": subprocess.run([a.actinv, "--version"], capture_output=True,
                                         text=True).stdout.strip(),
        "problem_sha256": hashlib.sha256((CHAPTER / "files/bracket.json").read_bytes()).hexdigest(),
        "mass_g": MASS_G,
        "composition_wt_percent": base,
        "irradiation_steps": len(IRRADIATION_ENDS_D),
        "shutdown_s": IRRADIATION_ENDS_D[-1] * DAY,
        "t_s": [s["t_s"] for s in runs[0][1]["steps"]],
        "runs": [],
    }
    for co, res, steps in runs:
        data["runs"].append({
            "cobalt_wt_percent": co,
            "activity_Bq": [g(sum(s["activity"].values())) for s in steps],
            "heat_W": [g(s["heat_W"]) for s in steps],
            "dose_Gy_h": [g(s["dose_Gy_h"]) for s in steps],
            "activity_by": {n: [g(s["activity"].get(n, 0.0)) for s in steps] for n in act_names},
            "dose_by": {n: [g(s["dose_by"].get(n, 0.0)) for s in steps] for n in dose_names},
        })
    out = CHAPTER / "data/bracket-data.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print("wrote", out.relative_to(ROOT), out.stat().st_size, "bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
