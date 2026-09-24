#!/usr/bin/env python3
"""P48 G3 — measured latency on the flagship workload. Reads the
per-point wall times recorded by the sealed G1 smoke run (release
binary, real isolated workers) and issues the latency claim with
hardware and workload named. Emits results/g3_p48_latency.json.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/g3_p48_latency.json"
WORK = Path.home() / "nuclear-data/p48-work/g1"


def main() -> int:
    g1 = json.loads((ROOT / "results/g1_p48_identity.json").read_text())
    report = json.loads(
        (WORK / "out/sweep-smoke.json").read_text())
    lat = report.get("per_point_latency_ms") or []
    if not lat:
        OUT.write_text(json.dumps(
            {"pass": False, "error": "no latency samples"}))
        return 1
    result = {
        "schema": "actinv-p48-g3-1",
        "pass": True,
        "claim": {
            "workload": "examples/fns_fe_5min.json — FNS Fe 5-minute "
                        "irradiation, tendl-2025-patched 709g",
            "axis": "flux_normalization × [0.5, 1.0, 2.0]",
            "measured": {
                "per_point_ms": lat,
                "median_ms": statistics.median(lat),
                "min_ms": min(lat),
                "max_ms": max(lat),
                "n": len(lat),
            },
            "basis": "each point is one full isolated worker solve "
                     "(process spawn + library load + CRAM solve + "
                     "JSON emission) — the identical qualified path; "
                     "no amortization across points is claimed",
            "binary": g1["binary"],
            "hardware": g1["hardware"],
            "cgroup": g1["cgroup"],
        },
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": True,
                      "median_ms": result["claim"]["measured"]["median_ms"],
                      "n": len(lat)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
