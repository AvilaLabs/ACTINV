#!/usr/bin/env python3
"""P56 G2 — recompute every certified margin and the overcertify count
from raw ledger fields; compare bit-exactly."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p56_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p56_exactness.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p56_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
run = p56_case.optimize(ACTINV, p56_case.optspec(fx, tmp, 1.5e-37, "feas"),
                        tmp / "out")
cert = run["result"]["certification"]

# Recompute margins from emitted edge/limit/sense.
for c in cert["winner"]["constraints"]:
    e, lim = c["edge_value"], c["limit"]
    expect = (lim - e) / abs(lim) if c["sense"] == "le" else (e - lim) / abs(lim)
    check(f"margin_fraction exact for {c['name']}",
          c["margin_fraction"] == expect,
          f"{c['margin_fraction']!r} vs {expect!r}")

# Recompute overcertify from raw rows.
opt_c = run["result"]["constraints"]
n = 0
for r in run["rows"]:
    if r["status"] != "executed":
        continue
    nom_ok, banded_fail, has_all = True, False, True
    for i, c in enumerate(opt_c):
        v = r["violations"][i]
        if c["kind"] == "axis":
            if v is not None and v > 0:
                nom_ok = False
            continue
        if c["edge"] == "nominal":
            if v is None or v > 0:
                nom_ok = False
            continue
        ent = r["constraints"].get(f"constraint.{c['name']}")
        if not isinstance(ent, dict) or "nominal_violation" not in ent:
            has_all = False
            break
        if not ent["nominal_violation"] <= 0:
            nom_ok = False
        if v is not None and v > 0:
            banded_fail = True
    if has_all and nom_ok and banded_fail:
        n += 1
check("nominal_would_overcertify recomputed exactly",
      cert["nominal_would_overcertify"] == n,
      f"{cert['nominal_would_overcertify']} vs {n}")

# Recompute ledger violations from emitted edges.
for r in run["rows"]:
    if r["status"] != "executed":
        continue
    for i, c in enumerate(opt_c):
        if c["kind"] != "response":
            continue
        ent = r["constraints"][f"constraint.{c['name']}"]
        e = ent["edge"]
        expect = ((e - c["limit"]) if c["sense"] == "le"
                  else (c["limit"] - e)) / abs(c["limit"])
        check(f"eval {r['eval_id']} violation exact for {c['name']}",
              ent["violation"] == expect,
              f"{ent['violation']!r} vs {expect!r}")
        if "nominal_edge" in ent:
            nexpect = ((ent["nominal_edge"] - c["limit"])
                       if c["sense"] == "le"
                       else (c["limit"] - ent["nominal_edge"])) / abs(c["limit"])
            check(f"eval {r['eval_id']} nominal_violation exact",
                  ent["nominal_violation"] == nexpect)

# Winner verification cross-check.
wv = run["result"]["winner_verification"]
check("re-run constraint edges bit-identical",
      wv.get("constraint_edges_bit_identical") is True
      and all(e["bit_identical"] for e in wv["constraint_edges"]))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
