#!/usr/bin/env python3
"""P65 G5 — independent reparse of a screened result; planted mutations
in the certificate must be caught."""
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
OUT = ROOT / "results/g5_p65_checker.json"

LAMBDA = {"Mn56": math.log(2.0) / 2.0, "Mn57": math.log(2.0) / 3.0,
          "Mn57m1": math.log(2.0) / 1.5}
ENERGY = {"Mn56": (1.0e6, 2.0e6, 0.5e6), "Mn57": (0.7e6, 1.1e6, 0.2e6),
          "Mn57m1": (0.4e6, 0.6e6, 0.1e6)}
EV = 1.602176634e-19

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def audit(res: dict) -> list:
    found = []
    sc = res.get("screen")
    if not isinstance(sc, dict):
        return ["no screen block"]
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
    if not math.isclose(sc["removed_activity_Bq_per_g_bound"], act_total,
                        rel_tol=1e-12):
        found.append("activity bound mismatch")
    if not math.isclose(sc["removed_heat_W_per_g_bound"], heat_total,
                        rel_tol=1e-12):
        found.append("heat bound mismatch")
    if sc["kept_states"] != res["pruned_states"]:
        found.append("kept_states mismatch")
    if sc["dropped_states"] > res["total_states"] - sc["kept_states"]:
        found.append("dropped exceeds unkept")
    for si, step in enumerate(res["steps"]):
        block = sc["certified"].get(str(si), {})
        floor_b = step["heat_bound_from_below_floor_W_per_g"]
        for resp, entry in block.items():
            unc = (step.get("uncertainty") or {}).get("responses", {})
            if resp in unc:
                lo, hi = unc[resp]["conservative_interval"]
            else:
                lo = hi = nominal_of(step, resp)
            b = (heat_total if resp.startswith("heat.")
                 else act_total if resp == "activity.total"
                 else act_by_nuc.get(resp.split(":", 1)[1], 0.0))
            exp_lo = lo - b
            exp_hi = hi + b + (floor_b if resp.startswith("heat.") else 0.0)
            if entry["lower"] != exp_lo or entry["upper"] != exp_hi:
                found.append(f"s{si} {resp}: certified edge mismatch")
            if entry["bound_upper"] != exp_hi - hi or entry["bound_lower"] != b:
                found.append(f"s{si} {resp}: bound field mismatch")
    return found


def nominal_of(step, resp):
    if resp.startswith("heat."):
        return step["heat_W_per_g"][resp.split(".", 1)[1]]
    if resp == "activity.total":
        return sum(step["activity_Bq_per_g"].values())
    return step["activity_Bq_per_g"].get(resp.split(":", 1)[1], 0.0)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p65_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    base = p60_case.spec(fx)
    base["options"]["prune"] = "rate"
    base["options"]["screen"] = {"bmin_atoms_per_g": 1e-4}
    p = tmp / "s.json"
    p.write_text(json.dumps(base) + "\n")
    o = tmp / "o.json"
    r = subprocess.run([str(ACTINV), "run", str(p), str(o)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    assert r.returncode == 0, r.stderr[-300:]
    doc = json.loads(o.read_text())

    ok("reference document clean", not audit(doc),
       json.dumps(audit(doc)[:3]))

    mut = json.loads(json.dumps(doc))
    mut["screen"]["removed_heat_W_per_g_bound"] *= 2.0
    ok("bound mutation caught", audit(mut))

    mut = json.loads(json.dumps(doc))
    c = mut["screen"]["certified"]["0"]["heat.total"]
    c["upper"] *= 1.5
    ok("certified edge mutation caught", audit(mut))

    mut = json.loads(json.dumps(doc))
    mut["screen"]["certified"]["0"]["activity:Mn57"]["bound_upper"] = 0.0
    ok("per-nuclide bound zeroing caught", audit(mut))

    mut = json.loads(json.dumps(doc))
    mut["screen"]["kept_states"] += 1
    ok("kept_states mutation caught", audit(mut))

    out = {"pass": not problems, "checks": len(checked),
           "problems": problems}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": not problems, "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
