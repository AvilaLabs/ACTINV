#!/usr/bin/env python3
"""Compute the numbers for the course chapter "The impurities that matter".

`actinv impurities` screens the bracket steel of chapter 1 (SS316, iron the balance) for seven trace elements
that real steel carries, in chapter 1's neutron field and irradiation, at 30 days, 1, 10 and 100 years of cooling.
A second screen puts an illustrative limit on the contact dose rate at 10 years and asks how much of each
impurity it allows.

Writes:
  impurities/files/base.json, screen.json, limit.json   what the learner downloads and runs
  impurities/data/impurities-data.json                   what the page shows

Usage, from the repository root, with actinv-data/ present and a release actinv (about 4 minutes):

  python3 web/learn/build/make_impurities.py --actinv target/release/actinv --work target/learn-work/impurities
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "web/learn/first-irradiation/files"
CHAPTER = ROOT / "web/learn/impurities"
DAY = 86400.0
YEAR = 365.25 * DAY
COOLING = [("30 days", 30 * DAY), ("1 year", YEAR), ("10 years", 10 * YEAR), ("100 years", 100 * YEAR)]
# Typical trace amounts in a commercial austenitic steel, wt%: nominal and the range a purchase might allow.
IMPURITIES = {
    "Co": {"nominal": 0.1, "min": 0.02, "max": 0.2},
    "Cu": {"nominal": 0.1, "min": 0.0, "max": 0.3},
    "N": {"nominal": 0.05, "min": 0.02, "max": 0.1},
    "Nb": {"nominal": 0.005, "min": 0.0, "max": 0.05},
    "Ta": {"nominal": 0.005, "min": 0.0, "max": 0.02},
    "B": {"nominal": 0.001, "min": 0.0, "max": 0.003},
    "Ag": {"nominal": 0.0005, "min": 0.0, "max": 0.002},
}
DOSE_LIMIT_GY_H = 0.02  # illustrative: contact dose rate at 10 years for hands-on recycling


def base_problem():
    b = json.loads((SRC / "bracket.json").read_text())
    b["title"] = "Steel bracket near a fusion machine, for the impurity screen (illustrative course example)"
    steps, prev = list(b["schedule"][:7]), 0.0
    for _, c in COOLING:
        steps.append({"dt": f"{c - prev:.10g} s", "flux": 0.0})
        prev = c
    b["schedule"] = steps
    b.pop("summary", None)
    # The screen sums the grouped photon source, so no estimated spectra.
    b["photon"]["missing_spectra"] = "report"
    return b


def screen(base, targets, endpoints):
    comp = base["material"]["composition"]
    return {
        "schema": "actinv-impurities-1",
        "base_spec": "base.json",
        "balance": "Fe",
        "matrix": {k: v for k, v in comp.items() if k != "Fe"},
        "impurities": IMPURITIES,
        "targets": targets,
        "endpoints": endpoints,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actinv", default=str(ROOT / "target/release/actinv"))
    ap.add_argument("--work", default=str(ROOT / "target/learn-work/impurities"))
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    files = CHAPTER / "files"
    files.mkdir(parents=True, exist_ok=True)
    (CHAPTER / "data").mkdir(parents=True, exist_ok=True)

    base = base_problem()
    first = len(base["schedule"]) - len(COOLING) + 1
    targets = list(range(first, first + len(COOLING)))
    docs = {
        "base.json": base,
        "screen.json": screen(base, targets, ["activity.total", "dose.contact_proxy", "clearance_index"]),
        "limit.json": screen(base, [targets[2]], [{"endpoint": "dose.contact_proxy", "limit": DOSE_LIMIT_GY_H}]),
    }
    for name, doc in docs.items():
        (files / name).write_text(json.dumps(doc, indent=1) + "\n")
        shutil.copy(files / name, work / name)
    shutil.copy(SRC / "photon-response.json", files / "photon-response.json")
    shutil.copy(SRC / "photon-response.json", work / "photon-response.json")

    env = {**os.environ, "ACTINV_DATA_DIR": str(ROOT / "actinv-data")}
    lines = {}
    for name in ("screen", "limit"):
        p = subprocess.run([a.actinv, "impurities", f"{name}.json", f"{name}-result.json"], cwd=work, env=env,
                           check=True, capture_output=True, text=True)
        lines[name] = p.stderr.strip()
    res = json.loads((work / "screen-result.json").read_text())
    lim = json.loads((work / "limit-result.json").read_text())

    def endpoint(ep):
        return {
            "unit": ep["unit"], "matrix_only": ep["matrix_only"], "nominal": ep["nominal"],
            "worst": ep["worst_case"]["value"],
            "impurities": {k: {"nominal_wt": IMPURITIES[k]["nominal"], "gradient": v["gradient_per_wt_pct"],
                               "contribution": v["contribution_nominal"], "at_max": v["contribution_at_max"],
                               "top": [n for n, _ in (v.get("top_nuclides") or [])][:2]}
                           for k, v in ep["impurities"].items()},
        }

    data = {
        "about": "actinv impurities on the course bracket steel: per cooling time and endpoint, the matrix-only and "
                 "nominal values, each impurity's contribution and gradient per wt%, and the limits at 10 years.",
        "actinv_version": subprocess.run([a.actinv, "--version"], capture_output=True, text=True).stdout.strip(),
        "files_sha256": {n: hashlib.sha256((files / n).read_bytes()).hexdigest() for n in docs},
        "impurities": IMPURITIES,
        "times": [label for label, _ in COOLING],
        "screen": [{e["response"]: endpoint(e) for e in t["endpoints"]} for t in res["targets"]],
        "limit_Gy_h": DOSE_LIMIT_GY_H,
        "limits": {k: {kk: v["limits"][kk] for kk in ("alone", "others_at_nominal", "others_at_worst")}
                   for k, v in lim["targets"][0]["endpoints"][0]["impurities"].items()},
        "joint_k": lim["targets"][0]["endpoints"][0].get("joint_k"),
        "stderr": lines,
    }
    (CHAPTER / "data/impurities-data.json").write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    main()
