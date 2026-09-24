#!/usr/bin/env python3
"""P48 G4 — verdict. Aggregates gate records. CONDITIONAL because the
panel is verified through the shipped binary's headless smoke mode (the
identical sweep machinery) rather than an on-screen click-through —
named verbatim, not hidden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/verdict_p48.json"


def load(name):
    return json.loads((ROOT / "results" / name).read_text())


def main() -> int:
    seal = load("g0_p48_seals.json")
    g1 = load("g1_p48_identity.json")
    g2 = load("g2_p48_controls.json")
    g3 = load("g3_p48_latency.json")

    gates_ok = g1["pass"] and g2["pass"] and g3["pass"]
    conditions = [
        "verification path: headless — the desktop binary's opt-in "
        "sweep smoke mode exercises the identical sweep machinery "
        "(sweep_specs -> worker::spawn per point); no on-screen "
        "interactive session was recorded, so the claim is qualified "
        "to the machinery, not the rendered panel",
        "library/evaluation-selection axis declared in the roadmap is "
        "not shipped — named as unimplemented, not hidden",
        "per-interaction bound: MAX_SWEEP_POINTS=32; a single sweep at "
        "a time; supersession cancels the running sweep",
        "latency basis: per-point time includes process spawn and "
        "library load per point — no amortization claimed; debug "
        "builds are slower and were not used for the claim",
    ]
    verdict = {
        "schema": "actinv-p48-verdict-1",
        "verdict": "P48-CONDITIONAL" if gates_ok else "P48-FAILED",
        "gates": {"g0": True, "g1": g1["pass"],
                  "g2": g2["pass"], "g3": g3["pass"]},
        "opening_commit": seal["opening_commit"],
        "protocol_sha256": seal["protocol_sha256"],
        "latency_claim_ms": g3["claim"]["measured"],
        "conditions": conditions,
    }
    OUT.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": verdict["verdict"],
                      "gates": verdict["gates"]}))
    return 0 if gates_ok else 1


if __name__ == "__main__":
    sys.exit(main())
