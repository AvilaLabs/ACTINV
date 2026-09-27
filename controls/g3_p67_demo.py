#!/usr/bin/env python3
"""P67 G3 — real-data certified Pareto front: RA-steel two-objective run
(banded heat at 1y vs banded Nb94 activity at ~100y) on the release
binary; front re-derived from the ledger bit-exactly."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/release/actinv"
OUT = ROOT / "results/g3_p67_demo.json"
RESULT_DOC = ROOT / "results/p67_pareto_ra.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p67_g3_", dir=ROOT / "target"))
base = json.loads((ROOT / "examples/optimize_ra_steel/base_spec.json")
                  .read_text())
bp = tmp / "base.json"
bp.write_text(json.dumps(base))
objs = [
    {"response": "heat.total", "time_s": 30727000.0,
     "edge": "normal_upper", "direction": "min"},
    {"response": "activity:Nb94", "time_s": 3180700000.0,
     "edge": "normal_upper", "direction": "min"},
]
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
    "objectives": objs,
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
                   cwd=ROOT, text=True, capture_output=True, timeout=7200)
check("pareto'd optimize ran", r.returncode == 0, r.stderr[-300:])
if r.returncode == 0:
    res = json.loads((tmp / "out/optimize_result.json").read_text())
    RESULT_DOC.write_text(json.dumps(res) + "\n")
    rows = [json.loads(l) for l in
            (tmp / "out/optimize_ledger.jsonl").read_text().splitlines()
            if l.strip()]
    pz = res["pareto"]
    check("pareto emitted over the evals",
          pz["n_evals"] == res["n_evals"])
    check("banded objective edges carried on ledger rows",
          all(all(v is not None for v in x["objectives"].values())
              for x in rows if x["status"] == "executed"),
          json.dumps([x.get("objectives") for x in rows])[:400])
    check("front bounded by the certified count",
          0 <= pz["n_front"] <= pz["n_certified"] <= pz["n_evals"])
    check("front members certified + feasible",
          all(f["certified"] for f in pz["front"])
          and all(f["violation_sum"] <= 0 for f in pz["front"]))

    # re-derive the front bit-exactly from the ledger
    names = [o["name"] for o in pz["objectives"]]
    dirs = [o["direction"] for o in pz["objectives"]]
    feas = [x for x in rows
            if sum(max(v or 0.0, 0.0) for v in x["violations"]) <= 0.0
            and all(v is not None for v in x["objectives"].values())]

    def dominates(a, b):
        strict = False
        for n, d in zip(names, dirs):
            av, bv = a["objectives"][n], b["objectives"][n]
            if d == "min":
                if av > bv:
                    return False
                strict |= av < bv
            else:
                if av < bv:
                    return False
                strict |= av > bv
        return strict

    expected = {x["eval_id"] for x in feas
                if not any(y["eval_id"] != x["eval_id"]
                           and dominates(y, x) for y in feas)}
    got = {f["eval_id"] for f in pz["front"]}
    check("front = nondominated certified set (ledger re-derivation)",
          got == expected, f"{sorted(got)} vs {sorted(expected)}")
    by_id = {x["eval_id"]: x["objectives"] for x in rows}
    check("front objectives ledgered identically",
          all(f["objectives"] == by_id[f["eval_id"]] for f in pz["front"]))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:6]}))
sys.exit(1 if failed else 0)
