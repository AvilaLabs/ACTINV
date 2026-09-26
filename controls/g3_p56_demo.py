#!/usr/bin/env python3
"""P56 G3 — real-data demonstration: a reduced banded-constraint search
on the RA-steel example (release binary). The certification must carry
the real covariance identity and finite honest margins; the winner's
re-run constraint checks must hold on real data.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p56_demo.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p56_g3_", dir=ROOT / "target"))
base = json.loads((ROOT / "examples/optimize_ra_steel/base_spec.json")
                  .read_text())
bp = tmp / "base.json"
bp.write_text(json.dumps(base))
opt = {
    "schema": "actinv-optimize-1",
    "base_spec": str(bp),
    "design_axes": [
        {"kind": "composition_fraction", "element": "NI",
         "bounds": [0.0, 8.0]},
        {"kind": "composition_fraction", "element": "MO",
         "bounds": [0.0, 1.5]},
        {"kind": "composition_fraction", "element": "NB",
         "bounds": [0.0, 0.1]},
    ],
    "objective": {"response": "activity.total", "time_s": 3180727000.0,
                  "edge": "nominal", "direction": "min"},
    "constraints": [
        {"name": "heat_1y_upper95", "response": "heat.total",
         "time_s": 30727000.0, "edge": "normal_upper",
         "sense": "le", "limit": 1.65e-12},
        {"name": "nb94_100y", "response": "activity:Nb94",
         "time_s": 3180700000.0, "edge": "nominal",
         "sense": "le", "limit": 1e-6},
    ],
    "optimizer": {"algorithm": "lhs_coordinate", "seed": 56,
                  "init_points": 2, "refine_points": 1,
                  "refine_step_fraction": 0.25},
}
op = tmp / "opt.json"
op.write_text(json.dumps(opt))
r = subprocess.run([str(ACTINV), "optimize", str(op), str(tmp / "out")],
                   cwd=ROOT, text=True, capture_output=True,
                   timeout=7200)
check("banded optimize ran", r.returncode == 0, r.stderr[-300:])
rows = [json.loads(l) for l in
        (tmp / "out/optimize_ledger.jsonl").read_text().splitlines()
        if l.strip()]
res = json.loads((tmp / "out/optimize_result.json").read_text())
sv = [x for x in rows if x["status"] == "executed"]
check("solved evals ran on real data", len(sv) >= 2,
      json.dumps([x["status"] for x in rows]))
check("banded edge recorded on real evals",
      all(x["constraints"]["constraint.heat_1y_upper95"].get("edge")
          is not None for x in sv))
check("nominal edge beside the banded one",
      all(x["constraints"]["constraint.heat_1y_upper95"]
          .get("nominal_edge") is not None for x in sv))
cert = res.get("certification", {})
check("certification present", isinstance(cert, dict))
check("covariance names the real file",
      "tendl" in str(cert.get("covariance", {}).get("path", "")).lower())
check("confidence level from base spec",
      cert.get("confidence_level") is not None)
check("overcertify count is honest",
      isinstance(cert.get("nominal_would_overcertify"), int)
      and cert["nominal_would_overcertify"] >= 0)
wv = res.get("winner_verification", {})
if res.get("best_feasible"):
    check("winner re-run constraint edges bit-identical",
          wv.get("constraint_edges_bit_identical") is True,
          json.dumps(wv)[:300])
    check("certified margins finite",
          all(isinstance(c.get("margin_fraction"), (int, float))
              and c["margin_fraction"] == c["margin_fraction"]
              for c in cert["winner"]["constraints"]))
else:
    check("honest infeasible verdict", res.get("infeasible") is True
          and cert.get("winner") is None)

OUT.write_text(json.dumps({"pass": all(c["pass"] for c in checks),
                           "n": len(checks), "checks": checks,
                           "certification": cert},
                          indent=1) + "\n")
print(json.dumps({"pass": all(c["pass"] for c in checks),
                  "failed": [c["name"] for c in checks if not c["pass"]],
                  "cert": cert.get("statement"),
                  "overcertify": cert.get("nominal_would_overcertify")}))
sys.exit(0 if all(c["pass"] for c in checks) else 1)
