#!/usr/bin/env python3
"""Derive the waste checkpoint status from retained gate and CI evidence."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / "results" / name).read_text())


def main(write):
    g0 = read("g0_p105_successor.json")
    g1 = read("g1_p105_classes.json")
    g2 = read("g2_p105_budget.json")
    quality = read("g3_p105_quality.json")
    local_pass = (
        g0["sealed"] is True and g1["pass"] is True and g2["pass"] is True
        and len(g1["vectors"]) == 126 and all(v["pass"] for v in g1["vectors"].values())
        and g2["verified_point_count"] == 4 and g2["verified_target_count"] == 8
        and g2["usable_interval_count"] == 2 and g2["analytic_nb94"]["passed"] is True
        and g2["above_class_c_perturbation"]["checked"] is True
        and all(g2["mutation_controls"].values()) and all(g2["refusal_controls"].values())
        and g2["g3_waste_identity"]["checked"] is True
        and g2["g3_waste_identity"]["repeat_byte_identical"] is True
        and quality["pass"] is True and all(g["exit_code"] == 0 for g in quality["gates"].values())
        and set(quality["passed_interval_regressions"]) == set(g2["required_rust_regressions"])
    )
    ci_path = ROOT / "results/p105_ci_runs.json"
    ci_pass = False
    if ci_path.is_file():
        runs = json.loads(ci_path.read_text())
        required = {"controls", "desktop builds", "Build browser workbench", "Build handbook"}
        ci_pass = (
            isinstance(runs, list) and bool(runs)
            and required.issubset({r["workflowName"] for r in runs})
            and len({r["headSha"] for r in runs}) == 1
            and all(r["status"] == "completed" and r["conclusion"] == "success" for r in runs)
        )
    verdict = {
        "schema": "actinv-p105-verdict-1", "phase": "P105", "repair_rounds": 1,
        "verdict": "P105-PASS" if local_pass and ci_pass else "P105-LOCAL-PASS" if local_pass else "P105-FAIL",
        "gates": {"G0": "PASS" if g0["sealed"] else "FAIL",
                  "G1": "PASS" if g1["pass"] else "FAIL",
                  "G2": "PASS" if g2["pass"] else "FAIL",
                  "G3_local": "PASS" if quality["pass"] else "FAIL",
                  "G3_CI": "PASS" if ci_pass else "PENDING"},
        "evidence_sha256": {name: hashlib.sha256((ROOT / "results" / name).read_bytes()).hexdigest()
                            for name in ("g0_p105_successor.json", "g1_p105_classes.json",
                                         "g2_p105_budget.json", "g3_p105_quality.json")},
        "qualification": "Nominal single-component U.S. Part 61 arithmetic and fixed-rate composition budgets; artificial activation control, no disposal acceptance or predictive library qualification.",
        "predecessors": {"P103": "FAIL", "P104": "FAIL"},
    }
    if ci_path.is_file():
        verdict["ci_evidence_sha256"] = hashlib.sha256(ci_path.read_bytes()).hexdigest()
        verdict["implementation_commit"] = runs[0]["headSha"]
    path = ROOT / "results/p105_verdict.json"
    same = True
    if write:
        path.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    else:
        same = json.loads(path.read_text()) == verdict
    print(verdict["verdict"], "evidence matches" if same else "evidence differs")
    return 0 if local_pass and same and (not ci_path.is_file() or ci_pass) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    raise SystemExit(main(parser.parse_args().write))
