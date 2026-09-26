#!/usr/bin/env python3
"""P56 shared machinery — banded-constraint optimize specs over the P58
fixture, sized so nominal and banded feasibility disagree on command.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import p60_case

sha = p60_case.sha


def optspec(fx: dict, work: Path, banded_limit: float,
            name: str = "p56") -> Path:
    """`heat_band` limit sits between the nominal (~1.04e-37) and
    conservative_upper (~1.26e-37) fixture edges when banded_limit ≈
    1.1e-37 — nominal-feasible but banded-infeasible."""
    base = p60_case.spec(fx)
    bp = work / f"{name}_base.json"
    bp.write_text(json.dumps(base, sort_keys=True) + "\n")
    opt = {
        "schema": "actinv-optimize-1",
        "base_spec": str(bp),
        "design_axes": [
            {"kind": "step_dt", "step": 2, "bounds": [0.3, 0.5]},
        ],
        "objective": {"response": "heat.total", "time_s": 0.9,
                      "edge": "normal_upper", "direction": "min"},
        "constraints": [
            {"name": "heat_band", "response": "heat.total", "time_s": 0.9,
             "edge": "conservative_upper", "sense": "le",
             "limit": banded_limit},
        ],
        "optimizer": {"algorithm": "lhs_coordinate", "seed": 56,
                      "init_points": 2, "refine_points": 1,
                      "refine_step_fraction": 0.25},
    }
    op = work / f"{name}.opt.json"
    op.write_text(json.dumps(opt, sort_keys=True) + "\n")
    return op


def optimize(actinv: Path, opt_path: Path, out_dir: Path) -> dict:
    r = subprocess.run(
        [str(actinv), "optimize", str(opt_path), str(out_dir)],
        cwd=Path(__file__).resolve().parents[1],
        text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, f"optimize failed: {r.stderr[-800:]}"
    rows = [json.loads(l) for l in
            (out_dir / "optimize_ledger.jsonl").read_text().splitlines()
            if l.strip()]
    result = json.loads((out_dir / "optimize_result.json").read_text())
    return {"rows": rows, "result": result}
