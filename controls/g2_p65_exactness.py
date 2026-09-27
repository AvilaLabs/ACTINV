#!/usr/bin/env python3
"""P65 G2 — rebuild the dropped-state bounds from the rate_pruning
ledger + fixture decay half-lives and verify every certified interval's
arithmetic exactly."""
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
OUT = ROOT / "results/g2_p65_exactness.json"

LAMBDA = {"Mn56": math.log(2.0) / 2.0, "Mn57": math.log(2.0) / 3.0,
          "Mn57m1": math.log(2.0) / 1.5}
# fixture decay energies (e_light, e_em, e_heavy) per nuclide
ENERGY = {"Mn56": (1.0e6, 2.0e6, 0.5e6), "Mn57": (0.7e6, 1.1e6, 0.2e6),
          "Mn57m1": (0.4e6, 0.6e6, 0.1e6)}
EV = 1.602176634e-19  # J/eV

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p65_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
base["options"]["prune"] = "rate"
base["options"]["screen"] = {"bmin_atoms_per_g": 1e-3}
p = tmp / "s.json"
p.write_text(json.dumps(base) + "\n")
out = tmp / "o.json"
r = subprocess.run([str(ACTINV), "run", str(p), str(out)],
                   cwd=ROOT, text=True, capture_output=True, timeout=600)
assert r.returncode == 0, r.stderr[-400:]
res = json.loads(out.read_text())
sc = res["screen"]

# Rebuild the bounds from the emitted dropped ledger.
dropped = res["ledger"]["rate_pruning"]["dropped"]
act_by_nuc, act_total, heat_total = {}, 0.0, 0.0
for d in dropped:
    nuc, B = d["nuclide"], d["atoms_per_g_bound"]
    lam = LAMBDA.get(nuc, 0.0)
    a = lam * B
    act_by_nuc[nuc] = act_by_nuc.get(nuc, 0.0) + a
    act_total += a
    e = ENERGY.get(nuc)
    if e:
        heat_total += lam * B * sum(e) * EV
def bound_for(resp):
    if resp.startswith("heat."):
        return sc["removed_heat_W_per_g_bound"]
    if resp == "activity.total":
        return act_total
    if resp.startswith("activity:"):
        return act_by_nuc.get(resp.split(":", 1)[1], 0.0)
    raise AssertionError(resp)


def nominal_of(step, resp):
    if resp.startswith("heat."):
        return step["heat_W_per_g"][resp.split(".", 1)[1]]
    if resp == "activity.total":
        return sum(step["activity_Bq_per_g"].values())
    if resp.startswith("activity:"):
        return step["activity_Bq_per_g"].get(resp.split(":", 1)[1], 0.0)
    raise AssertionError(resp)


check("activity total bound re-derived",
      math.isclose(sc["removed_activity_Bq_per_g_bound"], act_total,
                   rel_tol=1e-12),
      f"{sc['removed_activity_Bq_per_g_bound']!r} vs {act_total!r}")
check("heat bound re-derived",
      math.isclose(sc["removed_heat_W_per_g_bound"], heat_total,
                   rel_tol=1e-12),
      f"{sc['removed_heat_W_per_g_bound']!r} vs {heat_total!r}")
check("kept == pruned_states", sc["kept_states"] == res["pruned_states"])
check("dropped bounded by unkept",
      sc["dropped_states"] <= res["total_states"] - sc["kept_states"])

n_steps = len(res["steps"])
for si, step in enumerate(res["steps"]):
    block = sc["certified"][str(si)]
    floor_b = step["heat_bound_from_below_floor_W_per_g"]
    for resp, entry in block.items():
        unc = step.get("uncertainty") or {}
        if resp in unc.get("responses", {}):
            iv = unc["responses"][resp]["conservative_interval"]
            check(f"s{si} {resp}: edge conservative", entry["edge"]
                  == "conservative")
            lo, hi = iv
        else:
            check(f"s{si} {resp}: edge nominal", entry["edge"] == "nominal")
            lo = hi = nominal_of(step, resp)
        b = bound_for(resp)
        exp_lo = lo - b
        exp_hi = hi + b + (floor_b if resp.startswith("heat.") else 0.0)
        check(f"s{si} {resp}: lower exact", entry["lower"] == exp_lo,
              f"{entry['lower']!r} vs {exp_lo!r}")
        check(f"s{si} {resp}: upper exact", entry["upper"] == exp_hi)


failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:10]}))
sys.exit(1 if failed else 0)
