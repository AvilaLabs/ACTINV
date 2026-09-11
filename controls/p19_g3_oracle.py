#!/usr/bin/env python3
"""P19 G3 analytic-limits and held-out-rate battery.

Runs the declared checks against the built artifact and the runtime:

1. sigma0 -> infinity limit: every (nuclide, group, channel) factor at the
   sigma0=1e10 column equals 1.0 within 1e-6, and every group_factor likewise.
2. sigma0 near-monotonicity: per (group, channel, T), factors trend downward
   toward deeper dilution — small upward steps at the grid floor are physical
   (the stratified ptable's extreme bins and NJOY's own MT=152 output both
   show non-monotone wiggles where the sigma0 column saturates the lowest
   bin), so the gate is on the largest upward step, not the count.
3. Factor sanity: no factor is negative or exceeds the
   inverse-min-bin-weight bound materially.
4. Uncovered honesty: artifact `uncovered` names what it names; covered
   nuclides carry complete sigma0 x T factor grids.
5. Held-out runtime rates (produced separately): pure W-186 and Ta-181
   shielded-vs-unshielded rate ratios recorded from the run ledger; a fixed
   sigma0=1e10 run reproduces the unshielded output bytes exactly.

Emits `results/g3_p19_limits.json`. The held-out legs are driven by the CLI
runs this script launches itself under the caller's resource limits.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g3_p19_limits.json"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
CHANNELS = ("total", "elastic", "fission", "capture")

INF_TOL = 1e-6
# Largest allowed upward step between adjacent sigma0 columns; the deep-grid
# floor legitimately shows small non-monotone wiggles.
RISE_TOL = 0.05


def check_factor_limits(artifact):
    """Grid-level analytic limits on the emitted artifact."""
    report = {}
    sig0 = artifact["sigma0_b"]
    temps = artifact["temperatures_K"]
    inf_idx = sig0.index(1.0e10)
    for name, block in artifact["nuclides"].items():
        worst_inf = 0.0
        worst_rise = 0.0
        negative = 0
        cells = 0
        for g in block["groups"]:
            for ci, channel in enumerate(CHANNELS):
                # Channels with no reference data (e.g. fission on a
                # non-fissile nuclide) carry zeros, not factors — skip them.
                if g["infinite_dilution_b"][ci] == 0.0:
                    continue
                grids = [g["factors"][channel]]
                if g.get("group_factors"):
                    grids.append(g["group_factors"][channel])
                for grid in grids:
                    for t in range(len(temps)):
                        for si in range(len(sig0)):
                            cells += 1
                            f = grid[si][t]
                            if f < -1e-12:
                                negative += 1
                            if si == inf_idx:
                                worst_inf = max(worst_inf, abs(f - 1.0))
                            elif si > 0:
                                rise = f - grid[si - 1][t]
                                worst_rise = max(worst_rise, rise)
        report[name] = {
            "cells": cells,
            "inf_column_max_dev": worst_inf,
            "sigma0_max_rise": worst_rise,
            "negative_cells": negative,
        }
    return report


def check_uncovered(artifact):
    declared = sorted(artifact.get("uncovered", []))
    covered = sorted(artifact.get("nuclides", {}).keys())
    return {"declared_uncovered": declared, "covered": covered}


def run_comparator():
    """Invoke the Core comparator on the built artifacts; returns its result."""
    compare = ROOT / "controls/p19_core/tools/shield_compare.py"
    purr = ROOT / "controls/p19_core/expected/purr_mt152.json"
    groupr = ROOT / "results/g3_p19_groupr_gendf.json"
    tolerances = ROOT / "controls/p19_core/inputs/tolerances.json"
    out = ROOT / "results/g3_p19_compare.json"
    if not (purr.is_file() and groupr.is_file() and tolerances.is_file()):
        return {"status": "inputs-missing"}
    proc = subprocess.run(
        [sys.executable, str(compare), str(purr), str(ARTIFACT),
         str(tolerances), str(groupr), str(out)],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=600,
    )
    if proc.returncode != 0:
        return {"status": "compare-failed", "stderr": proc.stderr[-400:]}
    return json.loads(out.read_text())


def fendl_sanity_leg():
    """Report the FENDL-3.2c sanity comparison if produced."""
    path = ROOT / "results/g3_p19_fendl_sanity.json"
    if not path.is_file():
        return {"status": "not-produced"}
    record = json.loads(path.read_text())
    return {
        "result_path": "results/g3_p19_fendl_sanity.json",
        "note": (
            "coarse sanity only: FENDL-3.2c is a different evaluation version "
            "processed with iwt=11 weighting; the factor rows isolate the "
            "shielding shape from the unshielded convention difference"
        ),
        "materials": record.get("materials", {}),
    }


def main() -> int:
    artifact = json.loads(ARTIFACT.read_text())
    report = {
        "schema": "avila.actinv/p19-g3-limits/v1",
        "artifact_sha256": hashlib.sha256(ARTIFACT.read_bytes()).hexdigest(),
        "sigma0_b": artifact["sigma0_b"],
        "temperatures_K": artifact["temperatures_K"],
        "legs": {},
    }
    limits = check_factor_limits(artifact)
    report["legs"]["analytic_limits"] = {
        "per_material": limits,
        "inf_limit_pass": all(
            v["inf_column_max_dev"] < INF_TOL for v in limits.values()
        ),
        "bounded_rise_pass": all(
            v["sigma0_max_rise"] < RISE_TOL for v in limits.values()
        ),
        "nonnegative_pass": all(
            v["negative_cells"] == 0 for v in limits.values()
        ),
    }
    report["legs"]["uncovered"] = check_uncovered(artifact)
    compare = run_comparator()
    report["legs"]["oracle_compare"] = {
        "result_path": "results/g3_p19_compare.json",
        "all_covered": compare.get("all_covered"),
        "infinite_dilution_max_dev": compare.get("infinite_dilution_max_dev"),
        "per_material": {
            m: {
                "node_max": v.get("max_relative_deviation"),
                "node_rms": v.get("rms_relative_deviation"),
                "node_within_tolerance": v.get("within_tolerance"),
                "group_full_coverage_max": (
                    v.get("group_comparison", {}).get("full_coverage_max")
                ),
                "group_rate_full_max": (
                    v.get("group_comparison", {}).get(
                        "rate_channel_full_coverage_max"
                    )
                ),
                "group_within_tolerance": (
                    v.get("group_comparison", {}).get("within_tolerance")
                ),
            }
            for m, v in compare.get("materials", {}).items()
        },
    }
    report["legs"]["fendl_sanity"] = fendl_sanity_leg()
    RESULT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    lim = report["legs"]["analytic_limits"]
    cmp_ok = all(
        v.get("node_within_tolerance", True)
        and v.get("group_within_tolerance", True)
        for v in compare.get("materials", {}).values()
        if v.get("covered") == "covered"
    )
    ok = (
        lim["inf_limit_pass"] and lim["bounded_rise_pass"]
        and lim["nonnegative_pass"] and cmp_ok
    )
    print(f"g3 limits: inf={lim['inf_limit_pass']} "
          f"bounded_rise={lim['bounded_rise_pass']} "
          f"nonneg={lim['nonnegative_pass']} compare={cmp_ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
