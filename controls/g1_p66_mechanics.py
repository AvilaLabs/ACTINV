#!/usr/bin/env python3
"""P66 G1 — Python SDK coverage: decide on all three arms, optimize
returns the certified result doc, solve carries the screen block.
Requires the extension built at python/target/debug/libactinv.so."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))

LIB = ROOT / "python/target/debug/libactinv.so"
check_import = shutil.which("maturin") is not None or LIB.exists()

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


if not LIB.exists():
    subprocess.run(
        ["cargo", "build"], cwd=ROOT / "python", check=True,
        env={"CARGO_BUILD_JOBS": "1", "TMPDIR": str(ROOT / "target/preflight-tmp"),
             "PATH": str(Path.home() / ".cargo/bin") + ":"
             + __import__("os").environ["PATH"]},
    )
tmp0 = Path(tempfile.mkdtemp(prefix="p66_", dir=ROOT / "target"))
(tmp0 / "actinv.so").write_bytes(LIB.read_bytes())
sys.path.insert(0, str(tmp0))
import actinv  # noqa: E402

import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="p66_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
s = p60_case.spec(fx)
(tmp / "s.json").write_text(json.dumps(s))
con = [{"name": "h", "response": "heat.total", "time_s": 1.9,
        "edge": "normal_upper", "sense": "le", "limit": 1e-6}]
dec = {"schema": "actinv-decide-1", "run_spec": "s.json",
       "decision": {"constraints": con}}

d1 = actinv.decide(dec, base_dir=tmp)
check("decide dict arm", d1["schema"] == "actinv-decision-1"
      and d1["verdict"]["certified"] is True)
d2 = actinv.decide({**dec, "run_spec": s})
check("decide embedded-run_spec arm", d2["verdict"]["certified"] is True
      and d2["run"]["run_spec"] == "<embedded>")
(tmp / "d.json").write_text(json.dumps(dec))
d3 = actinv.decide(tmp / "d.json")
check("decide path arm", d3["verdict"]["certified"] is True)
check("all arms agree on the edge value",
      d1["constraints"][0]["value"] == d2["constraints"][0]["value"]
      == d3["constraints"][0]["value"])

# refusal parity with the CLI
bad = json.loads(json.dumps(dec))
bad_run = json.loads(json.dumps(s))
bad_run.pop("uncertainty")
bad["run_spec"] = bad_run
try:
    actinv.decide(bad)
    check("unbanded decide refused", False)
except RuntimeError as e:
    check("unbanded decide refused", "no 'uncertainty' block" in str(e))

# optimize
op = tmp / "opt.json"
op.write_text(json.dumps({
    "schema": "actinv-optimize-1", "base_spec": "s.json",
    "optimizer": {"algorithm": "lhs_coordinate", "seed": 7,
                  "init_points": 2, "refine_points": 0},
    "objective": {"direction": "min", "response": "heat.total",
                  "time_s": 1.9, "edge": "nominal"},
    "design_axes": [{"kind": "composition_fraction", "element": "MN",
                     "bounds": [0.0, 1.0]}]}))
o = actinv.optimize(op, outdir=tmp / "o")
check("optimize returns result doc", o["n_evals"] == 2
      and "certification" in o and "ranked" in o)

# screen through solve
s2 = json.loads(json.dumps(s))
s2["options"]["prune"] = "rate"
s2["options"]["screen"] = {"bmin_atoms_per_g": 1e-4}
r = actinv.solve(s2)
check("solve emits the screen certificate",
      isinstance(r.get("screen"), dict) and "certified" in r["screen"])

failed = [c for c in checks if not c["pass"]]
out = ROOT / "results/g1_p66_mechanics.json"
out.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:8]}))
sys.exit(1 if failed else 0)
