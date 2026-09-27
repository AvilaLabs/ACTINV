#!/usr/bin/env python3
"""P64 G2 — reparse the emitted decision document: recompute every
margin from the raw run result and re-derive the verdict counts."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p64_exactness.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def edge_value(step, response, edge):
    if edge == "nominal":
        if response == "heat.total":
            return step["heat_W_per_g"]["total"]
        if response.startswith("heat."):
            return step["heat_W_per_g"][response.split(".", 1)[1]]
        if response == "activity.total":
            return sum(step["activity_Bq_per_g"].values())
        return step["activity_Bq_per_g"].get(
            response.split(":", 1)[1], 0.0)
    u = step["uncertainty"]["responses"][response]
    iv = (u["normal_interval"] if edge.startswith("normal")
          else u["conservative_interval"])
    return iv[0] if edge.endswith("lower") else iv[1]


tmp = Path(tempfile.mkdtemp(prefix="p64_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
spec_path = tmp / "run.json"
spec_path.write_text(json.dumps(base, sort_keys=True) + "\n")

probe = p60_case.run(ACTINV, base, tmp, "probe")
hi = probe["steps"][1]["uncertainty"]["responses"]["heat.total"]["normal_interval"][1]
nom = probe["steps"][1]["uncertainty"]["responses"]["heat.total"]["nominal"]

dspec = {
    "schema": "actinv-decide-1",
    "run_spec": str(spec_path),
    "decision": {"measurement_top": 4, "constraints": [
        {"name": "a", "response": "heat.total", "time_s": 0.9,
         "edge": "normal_upper", "sense": "le", "limit": hi * 1.05},
        {"name": "b", "response": "heat.total", "time_s": 1.9,
         "edge": "conservative_upper", "sense": "le", "limit": hi * 1.05},
        {"name": "c", "response": "activity:Mn57", "time_s": 0.9,
         "edge": "nominal", "sense": "le", "limit": 0.0},
    ]},
}
dp = tmp / "decide.json"
dp.write_text(json.dumps(dspec) + "\n")
out = tmp / "decision.json"
r = subprocess.run([str(ACTINV), "decide", str(dp), str(out)],
                   cwd=ROOT, text=True, capture_output=True, timeout=600)
assert r.returncode == 0, r.stderr[-500:]
doc = json.loads(out.read_text())
run = p60_case.run(ACTINV, json.loads(json.dumps({**base, "options": {
    **base["options"], "outputs": base["options"]["outputs"] + ["audit"]}})),
    tmp, "rerun")

n_over = 0
for c, cc in zip(doc["constraints"], dspec["decision"]["constraints"]):
    step = min(run["steps"], key=lambda s: abs(s["t_s"] - cc["time_s"]))
    ev = edge_value(step, cc["response"], cc["edge"])
    viol = ((ev - cc["limit"]) if cc["sense"] == "le"
            else (cc["limit"] - ev)) / max(abs(cc["limit"]), 1e-30)
    check(f"{c['name']}: value exact", c["value"] == ev,
          f"{c['value']!r} vs {ev!r}")
    check(f"{c['name']}: violation exact",
          math.isclose(c["violation"], viol, rel_tol=1e-12))
    check(f"{c['name']}: satisfied re-derived",
          c["satisfied"] == (viol <= 0.0))
    if cc["edge"] != "nominal":
        nv = edge_value(step, cc["response"], "nominal")
        nviol = ((nv - cc["limit"]) if cc["sense"] == "le"
                 else (cc["limit"] - nv)) / max(abs(cc["limit"]), 1e-30)
        check(f"{c['name']}: nominal re-derived", c["nominal"] == nv)
        if not c["satisfied"] and nviol <= 0.0:
            n_over += 1
check("overcertify count re-derived",
      doc["verdict"]["nominal_would_overcertify"] == n_over)
check("certified re-derived",
      doc["verdict"]["certified"]
      == all(c["satisfied"] for c in doc["constraints"]))
check("sha binds run result",
      len(doc["run"]["result_sha256"]) == 64)
check("measurements truncated to top",
      all(len(m["top_parameters"]) <= 4
          for m in doc["measurements"].values()))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:10]}))
sys.exit(1 if failed else 0)
