#!/usr/bin/env python3
"""P74 G2 — surrogate as an optimizer evaluation tier.

When optimizer.surrogate names an artifact fitted over the same design
axes, candidates evaluate through the certified band (ŷ ± ε_cert) with
no solve at all: rows ledger `surrogate.tier = "surrogate"`, the run
pays zero full solves, and the winner certifies on surrogate-certified
edges. A surrogate that cannot certify (band crosses the limit) falls
through to the prescreen/full-solve tier and is honestly ledgered."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402
import p56_case  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_p74_surrogate_optimize.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cli(*args):
    r = subprocess.run([str(ACTINV), *args], cwd=ROOT, text=True,
                       capture_output=True, timeout=600)
    assert r.returncode == 0, f"{args}: {r.stderr[-800:]}"
    return r.stdout


tmp = Path(tempfile.mkdtemp(prefix="p74_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
bp = tmp / "base.json"
bp.write_text(json.dumps(base, sort_keys=True) + "\n")

# Fit on the optimizer's own axis — step_dt index 2 over [0.3, 0.5].
surspec = {
    "schema": "actinv-surrogate-1",
    "base_spec": str(bp),
    "axes": [{"kind": "step_dt", "step": 2, "bounds": [0.3, 0.5],
              "points": 3}],
    "responses": [{"response": "heat.total", "time_s": 0.9,
                   "edge": "conservative_upper"}],
    "holdout_points": 3,
    "seed": 74,
}
sp = tmp / "sur.json"
sp.write_text(json.dumps(surspec, sort_keys=True) + "\n")
fit_dir = tmp / "fit"
cli("surrogate", "fit", str(sp), str(fit_dir))
artifact = fit_dir / "surrogate.json"
rel_artifact = artifact.relative_to(tmp)


def optspec(limit, name):
    spec = {
        "schema": "actinv-optimize-1",
        "base_spec": str(bp),
        "design_axes": [{"kind": "step_dt", "step": 2,
                         "bounds": [0.3, 0.5]}],
        "objective": {"response": "heat.total", "time_s": 0.9,
                      "edge": "conservative_upper", "direction": "min"},
        "constraints": [{"name": f"{name}_heat", "kind": "response",
                         "response": "heat.total", "time_s": 0.9,
                         "edge": "conservative_upper", "sense": "le",
                         "limit": limit}],
        "optimizer": {"algorithm": "lhs_coordinate", "seed": 74,
                      "init_points": 3, "refine_points": 0,
                      "refine_step_fraction": 0.25,
                      "surrogate": str(rel_artifact)},
    }
    p = tmp / f"{name}.opt.json"
    p.write_text(json.dumps(spec, sort_keys=True) + "\n")
    return p


# Feasible arm — the band lies below the limit across the whole domain,
# so every candidate certifies on the surrogate tier with no solve.
pre = p56_case.optimize(
    ACTINV, optspec(4e-37, "sur_feasible"), tmp / "out_sur")
prow = [r for r in pre["rows"] if r["status"].startswith("executed")]
check("surrogate rows carry the surrogate tier",
      prow and all(
          (r.get("constraints") or {}).get("surrogate", {}).get("tier")
          == "surrogate" for r in prow),
      f"{len(prow)} executed rows")
check("surrogate status marked",
      all("surrogate" in r["status"] for r in prow))
check("artifact sha ledgered",
      all(bool((r.get("constraints") or {}).get("surrogate", {})
               .get("artifact_sha256")) for r in prow))
check("surrogate winner certifies",
      "surrogate-certified" in
      pre["result"]["certification"]["statement"],
      pre["result"]["certification"]["statement"][:120])
check("winner re-verification reproduced the surrogate eval",
      pre["result"]["winner_verification"]["bit_identical"] is True)

# Tight arm — the band crosses the limit → surrogate cannot certify →
# honest fallback detail and a full solve.
tight = p56_case.optimize(
    ACTINV, optspec(1.1e-37, "sur_tight"), tmp / "out_tight")
trows = [r for r in tight["rows"] if r["status"].startswith("executed")]
check("uncertifiable band falls through",
      trows and all(
          (r.get("constraints") or {}).get("surrogate", {}).get("tier")
          == "surrogate_fallback" for r in trows),
      f"{len(trows)} executed rows")
check("tight arm infeasible like a full search",
      tight["result"].get("infeasible") is True)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
