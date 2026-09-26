#!/usr/bin/env python3
"""P56 G1 — mechanics of the certification surface on the fixture."""
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
OUT = ROOT / "results/g1_p56_mechanics.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p56_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)

# Feasible banded run: limit above the conservative edge.
run = p56_case.optimize(ACTINV, p56_case.optspec(fx, tmp, 1.5e-37, "feas"),
                        tmp / "out_feas")
cert = run["result"]["certification"]
check("certification block present", isinstance(cert, dict))
check("winner certified with eval_id",
      cert.get("winner", {}).get("eval_id") is not None)
check("confidence level carried from base spec",
      cert.get("confidence_level") == 0.95)
check("covariance identity carried", bool(cert.get("covariance", {})
      .get("sha256")))
check("statement names the edge rule",
      "conservative" in cert.get("statement", "").lower())
wc = cert["winner"]["constraints"]
check("winner constraint carries edge+nominal+margin",
      len(wc) == 1 and wc[0].get("edge_value") and
      wc[0].get("nominal_edge") and wc[0].get("margin_fraction") is not None)
check("banded edge above nominal on the fixture",
      wc[0]["edge_value"] > wc[0]["nominal_edge"])
check("positive margin on a feasible winner",
      wc[0]["margin_fraction"] > 0)

# Ledger carries nominal_edge iff the constraint edge is banded.
sv = [r for r in run["rows"] if r["status"] == "executed"]
check("banded constraint rows carry nominal_edge",
      all(r["constraints"]["constraint.heat_band"].get("nominal_edge")
          is not None for r in sv))
check("no overcertify when band passes too",
      cert["nominal_would_overcertify"] == 0)

# Tight run: limit between nominal (1.037e-37) and band (1.257e-37) →
# nominal-feasible, banded-infeasible on every candidate.
run2 = p56_case.optimize(ACTINV, p56_case.optspec(fx, tmp, 1.1e-37, "tight"),
                         tmp / "out_tight")
cert2 = run2["result"]["certification"]
check("no certifiable design when the band fails",
      cert2.get("winner") is None and run2["result"]["infeasible"] is True)
check("statement is honest about infeasibility",
      "no certifiable" in cert2["statement"].lower())
check("overcertify counts the nominal-only winners",
      cert2["nominal_would_overcertify"]
      == sum(1 for r in run2["rows"] if r["status"] == "executed"),
      f"n={cert2['nominal_would_overcertify']}")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
