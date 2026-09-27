#!/usr/bin/env python3
"""P67 G1 — Pareto mechanics: objectives ledgered, front nondominated,
certified=feasible, invalid configs rejected, byte-identical absence."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p67_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p67_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
s = p60_case.spec(fx)
(tmp / "s.json").write_text(json.dumps(s))


def opt(objectives, **extra):
    o = {"schema": "actinv-optimize-1", "base_spec": "s.json",
         "optimizer": {"algorithm": "lhs_coordinate", "seed": 3,
                       "init_points": 6, "refine_points": 0},
         "objective": {"direction": "min", "response": "heat.total",
                       "time_s": 1.9, "edge": "nominal"},
         "design_axes": [{"kind": "composition_fraction", "element": "MN",
                          "bounds": [0.0, 1.0]}],
         "constraints": [],
         "objectives": objectives}
    o.update(extra)
    p = tmp / f"opt_{len(list(tmp.glob('opt_*')))}.json"
    p.write_text(json.dumps(o) + "\n")
    return p


def run(p, outdir):
    r = subprocess.run([str(ACTINV), "optimize", str(p), str(outdir)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    return r


objs = [{"response": "heat.total", "time_s": 1.9, "edge": "nominal", "direction": "min"},
        {"response": "activity:Mn57", "time_s": 1.9, "edge": "nominal", "direction": "max"}]
p = opt(objs)
outdir = tmp / "run"
r = run(p, outdir)
assert r.returncode == 0, r.stderr[-400:]
res = json.loads((outdir / "optimize_result.json").read_text())

check("pareto block emitted", "pareto" in res)
pz = res["pareto"]
check("objectives metadata", len(pz["objectives"]) == 2
      and pz["objectives"][0]["name"] == "heat.total@1.9")
check("front nonempty", pz["n_front"] >= 2)

# nondominance re-derived over feasible rows
ledger = [json.loads(l) for l in
          (outdir / "optimize_ledger.jsonl").read_text().splitlines()]
feas = [r for r in ledger
        if sum(max(v or 0.0, 0.0) for v in r["violations"]) <= 0.0
        and all(v is not None for v in r["objectives"].values())]


def dominates(a, b):
    strict = False
    for o in objs:
        av, bv = a["objectives"][o["response"] + "@1.9"], \
            b["objectives"][o["response"] + "@1.9"]
        if o["direction"] == "min":
            if av > bv:
                return False
            strict |= av < bv
        else:
            if av < bv:
                return False
            strict |= av > bv
    return strict


front_ids = {f["eval_id"] for f in pz["front"]}
expected = {r["eval_id"] for r in feas
            if not any(o2["eval_id"] != r["eval_id"] and dominates(o2, r)
                       for o2 in feas)}
check("front is exactly the nondominated feasible set",
      front_ids == expected,
      f"{sorted(front_ids)} vs {sorted(expected)}")
check("front certified flag", all(f["certified"] for f in pz["front"]))
check("n_certified = feasible count", pz["n_certified"] == len(feas))
check("ledger rows carry objectives",
      all(isinstance(r.get("objectives"), dict) for r in ledger))

# rejections
bad = opt([objs[0]])
r = subprocess.run([str(ACTINV), "optimize", str(bad), str(tmp / "b1")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("len<2 objectives rejected", r.returncode != 0 and ">=2" in r.stderr)
bad = opt(objs[:1] + [objs[1] | {"direction": "sideways"}])
r = subprocess.run([str(ACTINV), "optimize", str(bad), str(tmp / "b2")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("bad direction rejected", r.returncode != 0 and "min" in r.stderr)
bad = opt([objs[0], objs[0]])
r = subprocess.run([str(ACTINV), "optimize", str(bad), str(tmp / "b3")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("duplicate objective name rejected", r.returncode != 0
      and "duplicates" in r.stderr)

# absent objectives = no pareto key (byte-identical surface)
p0 = tmp / "opt_noobj.json"
p0.write_text(json.dumps({k: v for k, v in json.loads(p.read_text()).items()
                          if k != "objectives"}) + "\n")
r = subprocess.run([str(ACTINV), "optimize", str(p0), str(tmp / "noobj")],
                   cwd=ROOT, text=True, capture_output=True, timeout=600)
assert r.returncode == 0
res0 = json.loads((tmp / "noobj/optimize_result.json").read_text())
check("absent objectives = no pareto key", "pareto" not in res0)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:8]}))
sys.exit(1 if failed else 0)
