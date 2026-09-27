#!/usr/bin/env python3
"""P63 G1 — unmodeled_relative fold mechanics on the fixture."""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p63_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p63_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)

plain = p60_case.run(ACTINV, base, tmp, "plain")
sp = json.loads(json.dumps(base))
sp["uncertainty"]["unmodeled_relative"] = 0.3
wide = p60_case.run(ACTINV, sp, tmp, "wide")

# Absent ⇒ byte-identical emit (only ms/wall fields may differ).
def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if k != "ms"}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


sp2 = json.loads(json.dumps(base))  # no option again
plain2 = p60_case.run(ACTINV, sp2, tmp, "plain2")
check("absent-option runs are byte-identical modulo ms",
      scrub(plain) == scrub(plain2))
check("absent emit has no unmodeled fields",
      all("unmodeled" not in k and "modeled_" not in k
          for resp in plain["steps"][0]["uncertainty"]["responses"].values()
          for k in resp))

for step in wide["steps"]:
    for name, resp in step["uncertainty"]["responses"].items():
        z = resp["normal_multiplier"]
        n = resp["nominal"]
        # locate the matching plain response
        p_resp = [s["uncertainty"]["responses"][name]
                  for s in plain["steps"] if s["t_s"] == step["t_s"]][0]
        # interval bounds are n ± z·sqrt(mv+uv) — mirror the emit's exact
        # arithmetic rather than reconstructing the half-width
        mv, uv = resp["modeled_variance"], resp["unmodeled_variance"]
        hw = z * math.sqrt(mv + uv)
        check(f"{name}@{step['t_s']}: fold exact",
              resp["normal_interval"][0] == n - hw
              and resp["normal_interval"][1] == n + hw
              and uv == (0.3 * n) * (0.3 * n))
        check(f"{name}@{step['t_s']}: emitted fields",
              resp["unmodeled_relative"] == 0.3
              and resp["unmodeled_standard_uncertainty"] == 0.3 * abs(n)
              and resp["modeled_standard_uncertainty"] == math.sqrt(mv))
        check(f"{name}@{step['t_s']}: band widened vs plain",
              resp["normal_interval"][1] - resp["normal_interval"][0]
              > p_resp["normal_interval"][1] - p_resp["normal_interval"][0]
              or n == 0.0)

# Validation rejects bad u.
bad = json.loads(json.dumps(base))
bad["uncertainty"]["unmodeled_relative"] = -0.1
import subprocess
bp = tmp / "bad.json"
bp.write_text(json.dumps(bad))
r = subprocess.run([str(ACTINV), "run", str(bp), str(tmp / "bad.out")],
                   text=True, capture_output=True, cwd=ROOT)
check("negative u rejected", r.returncode != 0
      and "unmodeled_relative" in r.stderr)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:10]}))
sys.exit(1 if failed else 0)
