#!/usr/bin/env python3
"""P62 shared case machinery — a small `actinv-optimize-1` search over the
P58 synthetic fixture, plus ledger extraction helpers.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import p60_case

sha = p60_case.sha


def optspec(fx: dict, work: Path, seed: int = 61,
            init_points: int = 3, refine_points: int = 1) -> Path:
    """3–4 evals on the fixture: composition axis on FE plus one axis
    constraint that infeasible-flags the bottom corner without a solve."""
    base = p60_case.spec(fx)
    base.pop("uncertainty", None)
    base_path = work / "p62_base.json"
    base_path.write_text(json.dumps(base, sort_keys=True) + "\n")
    opt = {
        "schema": "actinv-optimize-1",
        "base_spec": str(base_path),
        # Axis on step index 2 (the second on-step, base dt 0.4 s): the
        # objective time t=0.9 s (end of step 1) is never perturbed, so
        # every candidate resolves the same objective step.
        "design_axes": [
            {"kind": "step_dt", "step": 2, "bounds": [0.3, 0.5]},
        ],
        "objective": {"response": "heat.total", "time_s": 0.9,
                      "edge": "nominal", "direction": "min"},
        "constraints": [
            {"name": "dt_upper", "kind": "axis", "axis": 0,
             "sense": "le", "limit": 0.45},
        ],
        "optimizer": {"algorithm": "lhs_coordinate", "seed": seed,
                      "init_points": init_points,
                      "refine_points": refine_points,
                      "refine_step_fraction": 0.25},
    }
    op = work / f"p62_seed{seed}.opt.json"
    op.write_text(json.dumps(opt, sort_keys=True) + "\n")
    return op


def optimize(actinv: Path, opt_path: Path, out_dir: Path) -> dict:
    r = subprocess.run(
        [str(actinv), "optimize", str(opt_path), str(out_dir)],
        cwd=Path(__file__).resolve().parents[1],
        text=True, capture_output=True, timeout=300)
    assert r.returncode == 0, f"optimize failed: {r.stderr[-800:]}"
    ledger = out_dir / "optimize_ledger.jsonl"
    rows = [json.loads(l) for l in ledger.read_text().splitlines()
            if l.strip()]
    result = json.loads((out_dir / "optimize_result.json").read_text())
    return {"rows": rows, "result": result, "stdout": r.stdout}


def solved(rows: list) -> list:
    """Ledger rows that actually consumed a solver run."""
    skip = ("infeasible_by_axis", "axis_apply_error", "catalog_resolve_error",
            "spec_error")
    return [r for r in rows if not any(s in r["status"] for s in skip)]
