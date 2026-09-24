#!/usr/bin/env python3
"""P49 G4 — verdict producer. Collects the gate records into
results/verdict_p49.json after the independent checker passes.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p49_artifacts as p49a  # noqa: E402

OUT = ROOT / "results/verdict_p49.json"


def main() -> int:
    gates = {}
    for g, name in [("g1", "results/p49_g1.json"),
                    ("g2", "results/p49_g2.json"),
                    ("g3", "results/p49_campaign_report.json")]:
        p = ROOT / name
        if not p.exists():
            print(json.dumps({"verdict": "P49-FAIL",
                              "reason": f"missing {name}"}))
            return 1
        gates[g] = json.loads(p.read_text())
    if not all(g.get("pass") for g in gates.values()):
        print(json.dumps({"verdict": "P49-FAIL",
                          "reason": "a gate failed",
                          "gates": {k: v.get("pass") for k, v in gates.items()}}))
        return 1

    check = ROOT / "results/check_g4_p49.json"
    if not check.exists() or not json.loads(check.read_text()).get("pass"):
        print(json.dumps({"verdict": "P49-FAIL",
                          "reason": "independent checker did not pass"}))
        return 1

    campaign = gates["g3"]
    verdict = {
        "schema": "actinv-verdict-1",
        "phase": "P49",
        "verdict": "P49-CONDITIONAL",
        "closed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gates": {k: {"pass": v["pass"]} for k, v in gates.items()},
        "checker": "results/check_g4_p49.json",
        "campaign": {
            "n_evals": campaign["n_evals"],
            "wall_s": campaign["wall_s"],
            "n_feasible": campaign["n_feasible"],
            "n_infeasible": campaign["n_infeasible"],
            "best_feasible": campaign["best_feasible"],
            "winner_bit_identical": campaign["winner_verification"].get(
                "bit_identical"),
        },
        "conditions": [
            "Derivative-free seeded bounded search (LHS fill + coordinate "
            "refinement); no global-optimality claim is made.",
            "Chance constraints are expressed on the spec's declared "
            "confidence-level band edges (normal/conservative); they are not "
            "posterior probabilities over model error.",
            "Batch campaign only: per-candidate band propagation cost rules "
            "out interactive latency until the persistent-worker item ships.",
            "Winner re-verification is a same-process re-solve (identical "
            "code path), not an independent implementation.",
        ],
    }
    OUT.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": verdict["verdict"],
                      "n_evals": campaign["n_evals"],
                      "wall_s": campaign["wall_s"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
