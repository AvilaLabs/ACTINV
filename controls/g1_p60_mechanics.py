#!/usr/bin/env python3
"""P60 G1 — mechanics: design block present on every banded response,
per-entry posterior + reduction = total, reduction ranking order, the
target-grouped reaction block carries both correlated covered rows,
diagonal-channel entries carry the half-uncertainty figure, parse
rejections fire, and output is byte-identical when uncertainty.design is
absent.
"""
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
OUT = ROOT / "results/g1_p60_mechanics.json"


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p60_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

# --- banded design run -------------------------------------------------
res = p60_case.run(ACTINV, p60_case.spec(fx), tmp, "design")
u = res["steps"][-1]["uncertainty"]["responses"]

for response in ("activity:Mn57", "activity:Mn57m1", "heat.total"):
    resp = u[response]
    des = resp.get("design")
    check(f"design present: {response}", des is not None)
    if des is None:
        continue
    total = des["total_propagated_variance"]
    propagated = sum(ch["standard_uncertainty"] ** 2
                     for ch in resp["channels"]
                     if ch.get("status") == "propagated")
    check(f"total variance matches: {response}",
          propagated > 0.0 and abs(total - propagated) <= 1e-12 * propagated,
          f"design={total!r} sum={propagated!r}")
    params = des["top_parameters"]
    check(f"params sorted by reduction desc: {response}",
          all(abs(params[i]["variance_reduction"])
              >= abs(params[i + 1]["variance_reduction"])
              for i in range(len(params) - 1)))
    check(f"reductions nonnegative: {response}",
          all(e["variance_reduction"] >= -1e-25 for e in params))
    check(f"posterior + reduction = total: {response}",
          all(e["posterior_variance"] is None
              or abs(e["posterior_variance"] + e["variance_reduction"]
                     - total) <= 1e-15 * max(1.0, abs(total))
              for e in params))
    check(f"share_of_total consistent: {response}",
          all(e["share_of_total"] is None
              or abs(e["share_of_total"] * total
                     - e["variance_reduction"]) <= 1e-15 * max(1.0, abs(total))
              for e in params))
    check(f"reaction block present: {response}",
          len(des["top_reactions"]) >= 1)
    block = next((r for r in des["top_reactions"]
                  if r["target_za"] == 26056), None)
    n_covered_target = sum(1 for c in p60_case.covered_sensitivities(resp)
                           if c["parameter"]["target_ZA"] == 26056)
    check(f"Fe56 block holds all its covered rows: {response}",
          block is not None and block["covered_parameters"] >= 2
          and block["covered_parameters"] == n_covered_target
          and block["mts"] == [102, 103] and block["status"] == "emitted",
          json.dumps(des["top_reactions"]))
    if block and block["variance_reduction"] is not None:
        check(f"block reduction <= total: {response}",
              block["variance_reduction"] <= total * (1.0 + 1e-9))
        check(f"block posterior + reduction = total: {response}",
              abs(block["posterior_variance"] + block["variance_reduction"]
                  - total) <= 1e-15 * max(1.0, abs(total)))
        check(f"block reduction >= each member's param reduction: {response}",
              all(block["variance_reduction"] + 1e-12 * max(1.0, abs(total))
                  >= e["variance_reduction"]
                  for e in params
                  if e["channel"] == "cross_section_mf33"
                  and e["parameter"]["target_ZA"] == 26056))
    # xs entries expose the marginal share alongside the reduction
    xs = [e for e in params if e["channel"] == "cross_section_mf33"]
    check(f"xs entries carry variance_share: {response}",
          all(e["variance_share"] is not None
              and "standard_uncertainty" not in e
              and "reduction_at_half_uncertainty" not in e
              for e in xs))
    # diagonal-channel entries carry the half-uncertainty figure
    dec = [e for e in params if e["channel"] == "decay_constants"]
    check(f"decay entries carry half-uncertainty reduction: {response}",
          all(e["reduction_at_half_uncertainty"] is not None
              and abs(e["reduction_at_half_uncertainty"]
                      - 0.75 * e["variance_reduction"])
              <= 1e-15 * max(1e-30, abs(e["variance_reduction"]))
              and e["variance_share"] == e["variance_reduction"]
              for e in dec),
          json.dumps(dec[:1]))

heat = u["heat.total"]["design"]
check("heat design ranks an xs parameter",
      any(e["channel"] == "cross_section_mf33"
          for e in heat["top_parameters"]))
check("unranked named when present",
      "unranked" in heat)

# --- absent design option -> byte-identical record ----------------------
res_off = p60_case.run(ACTINV, p60_case.spec(fx, design=False), tmp, "off")
res_on = p60_case.run(ACTINV, p60_case.spec(fx, design=False), tmp, "off2")
check("no design key on responses when absent",
      all("design" not in r for st in res_off["steps"]
          for r in st["uncertainty"]["responses"].values()))
res_off.pop("ms", None)
res_on.pop("ms", None)
check("repeat run byte-comparable (ms excluded)",
      json.dumps(res_off, sort_keys=True) == json.dumps(res_on, sort_keys=True))

# --- parse rejections ----------------------------------------------------
bad = p60_case.spec(fx)
bad["uncertainty"]["design"] = 5
sp = tmp / "bad.spec.json"; sp.write_text(json.dumps(bad))
r = subprocess.run([str(ACTINV), "run", str(sp), str(tmp / "bad.out.json")],
                   cwd=ROOT, text=True, capture_output=True, timeout=60)
check("non-object design rejected", r.returncode != 0)

bad2 = p60_case.spec(fx)
bad2["uncertainty"]["design"] = {"top": 0}
sp2 = tmp / "bad2.spec.json"; sp2.write_text(json.dumps(bad2))
r2 = subprocess.run([str(ACTINV), "run", str(sp2), str(tmp / "bad2.out.json")],
                    cwd=ROOT, text=True, capture_output=True, timeout=60)
check("design.top=0 rejected", r2.returncode != 0
      and "design.top" in r2.stderr, r2.stderr[-200:])

bad3 = p60_case.spec(fx)
bad3["uncertainty"]["design"] = {"bogus": 1}
sp3 = tmp / "bad3.spec.json"; sp3.write_text(json.dumps(bad3))
r3 = subprocess.run([str(ACTINV), "run", str(sp3), str(tmp / "bad3.out.json")],
                    cwd=ROOT, text=True, capture_output=True, timeout=60)
check("unknown design field rejected", r3.returncode != 0)

bad4 = p60_case.spec(fx)
bad4["uncertainty"]["design"] = {"top": 300}
sp4 = tmp / "bad4.spec.json"; sp4.write_text(json.dumps(bad4))
r4 = subprocess.run([str(ACTINV), "run", str(sp4), str(tmp / "bad4.out.json")],
                    cwd=ROOT, text=True, capture_output=True, timeout=60)
check("design.top=300 rejected", r4.returncode != 0)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
