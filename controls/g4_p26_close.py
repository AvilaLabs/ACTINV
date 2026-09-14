#!/usr/bin/env python3
"""P26 G4: assemble the feasibility record and emit the verdict.

The verdict rule is mechanical over the recorded feasibility inputs:

- every draft target is judged feasible | infeasible | undetermined, each with
  the measurement or absence-of-evidence that produced the judgment;
- per the frozen protocol, an undetermined target is a failure mode of the
  phase, not of the target;
- Amendment 1 (one repair round) was used, so an otherwise-passing close would
  be CONDITIONAL — but undetermined/infeasible targets mean the ambition is
  not established, so the verdict is P26-FAIL and the roadmap's draft section
  must carry a dated replan before any extension phase opens.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "verdict_p26.json"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    headroom = json.loads((ROOT / "results" / "g3_p26_headroom.json").read_text())
    coverage = json.loads((ROOT / "results" / "g3_p26_coverage.json").read_text())
    g1 = json.loads((ROOT / "results" / "g1_p26_workloads.json").read_text())

    ledger = [json.loads(l) for l in
              (ROOT / "results" / "g3_p26_campaign_ledger.jsonl").read_text().splitlines()
              if l.strip() and json.loads(l).get("case") != "__batch_driver__"]
    by_mode: dict[str, list[float]] = {}
    for r in ledger:
        if r.get("ok", r.get("returncode") == 0):
            by_mode.setdefault(r["mode"], []).append(r["wall_s"])
    import statistics
    med = {m: statistics.median(v) for m, v in by_mode.items()}
    ratio = med["per_invocation"] / med["batched_inprocess"]

    feasibility = {
        "useful_predictive_results": {
            "status": "feasible",
            "evidence": "P24-CONDITIONAL demonstrates independent controls and explicit "
                        "coverage accounting on a qualified domain; P25-FAIL bounds the "
                        "breadth. Predictive superiority remains unmeasured by design "
                        "(no predeclared measurement comparison was run).",
        },
        "investigations_previously_too_expensive": {
            "status": "undetermined",
            "evidence": (f"Measured in-process amortization on the 1000-case W-CAMPAIGN "
                         f"grid is {ratio:.3f}x (median {med['batched_inprocess']:.3f}s vs "
                         f"{med['per_invocation']:.3f}s per case), far short of the drafted "
                         "10x. The comparator leg is unmeasurable on this workstation: "
                         "ALARA's only library covers zero contract elements "
                         "(contract_gap on all 1016 cases); FISPACT-II, SCALE/ORIGEN and "
                         "OpenMC are unavailable. A deeper amortization route (shared "
                         "prepared-network load) was outside P26's prototype budget."),
            "measured": {"amortization_ratio_median": ratio,
                          "per_invocation_median_s": med["per_invocation"],
                          "batched_median_s": med["batched_inprocess"],
                          "campaign_cases": len(ledger)},
        },
        "faster_scientific_understanding": {
            "status": "undetermined",
            "evidence": "No practitioner study is recorded; the task decomposition was "
                        "published as diagnostic-only. The 50% hands-on target has no "
                        "baseline and cannot be judged in this phase.",
        },
        "reliable_assisted_work": {
            "status": "undetermined",
            "evidence": "Depends on P27 contract families that do not exist yet; no "
                        "evidence either way was produced.",
        },
        "reproduction_and_interoperability": {
            "status": "undetermined",
            "evidence": "Hash-pinned provenance and the certificate/ledger path are "
                        "demonstrated (feasible component); the spatial handoff leg has "
                        "no executable comparator and no transport validation was run.",
        },
    }

    verdict = "P26-FAIL"
    record = {
        "schema": "actinv-p26-verdict-1",
        "phase": "P26",
        "verdict": verdict,
        "protocol_sha256": PROTOCOL_SHA256,
        "opening_commit": OPENING_COMMIT,
        "amendments_used": ["protocols/ACTINV-P26_AMENDMENT_1.md"],
        "flagship": g1["flagship"],
        "selected": g1["selected"],
        "feasibility": feasibility,
        "evidence_sha256": {
            "g1_workloads": sha256_file(ROOT / "results" / "g1_p26_workloads.json"),
            "g1_check": sha256_file(ROOT / "results" / "g1_p26_check.json"),
            "g2_contract": sha256_file(ROOT / "results" / "g2_p26_contract.json"),
            "g2_check": sha256_file(ROOT / "results" / "g2_p26_check.json"),
            "g3_headroom": sha256_file(ROOT / "results" / "g3_p26_headroom.json"),
            "g3_coverage": sha256_file(ROOT / "results" / "g3_p26_coverage.json"),
            "g3_ledger": sha256_file(ROOT / "results" / "g3_p26_campaign_ledger.jsonl"),
            "g3_tasks": sha256_file(ROOT / "results" / "g3_p26_task_decomposition.json"),
            "g3_check": sha256_file(ROOT / "results" / "g3_p26_check.json"),
        },
        "verdict_rule": ("P26-FAIL: three draft targets undetermined and the headroom ambition "
                          "not supported by measured evidence (1.33x amortization; comparator "
                          "leg unmeasurable). Amendment 1 used the single repair round."),
        "release_hold": "unchanged — the 1.1.0 decision remains the maintainer's",
    }
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"verdict": verdict, "amortization_ratio": ratio}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
