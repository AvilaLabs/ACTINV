#!/usr/bin/env python3
"""P63 G3 — the standing certificate: run the calibrator on the sealed
P44 corpus and assert the honest-state fields are present and the
baseline matches the sealed measured number."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p60_case  # noqa: E402

OUT = ROOT / "results/g3_p63_demo.json"
CERT = ROOT / "results/p63_calibration.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


subprocess.run([sys.executable, str(ROOT / "controls/p63_calibrate.py"),
                str(ROOT / "results/p44_sealed_coverage.json"),
                "--out", str(CERT)],
               check=True, capture_output=True)
cert = json.loads(CERT.read_text())
sealed = json.loads((ROOT / "results/p44_sealed_coverage.json").read_bytes())

check("schema", cert["schema"] == "actinv-calibration-1")
check("input sha binds the sealed doc",
      cert["input"]["sha256"] == p60_case.sha(
          ROOT / "results/p44_sealed_coverage.json"))
check("point bookkeeping conserves",
      cert["n_finite_bandable"] + cert["n_uncoverable_by_relative_term"]
      == sealed["n_points"])
# The sealed combined_sigma pooled coverage over all points must match
# our u=0 all-points number (same point set, same rule).
pooled = sealed["aggregates"]["first_order.combined_sigma"]["pooled"]["all"]
check("u=0 all-point coverage equals the sealed measured number",
      abs(cert["coverage_curve"]["0"]["coverage_all_points"]
          - pooled["coverage"]) < 1e-12,
      f"{cert['coverage_curve']['0']['coverage_all_points']} "
      f"vs {pooled['coverage']}")
check("u_at_target reported (fitted)",
      cert["u_at_target_finite"] is None
      or cert["u_at_target_finite"] > 0.0)
check("curve reaches target only if u_at_target exists",
      (cert["u_at_target_finite"] is None)
      == (cert["coverage_curve"]["4"]["coverage_finite"]
          < cert["target_coverage"]))
check("statement is honest about fitting",
      "fitted" in cert["statement"].lower())
check("uncoverable class enumerated",
      cert["n_uncoverable_by_relative_term"] > 0
      and cert["uncoverable_reasons"])
check("per-material split present",
      len(cert["per_material"]) >= 60)

OUT.write_text(json.dumps({"pass": all(c["pass"] for c in checks),
                           "n": len(checks), "checks": checks},
                          indent=1) + "\n")
print(json.dumps({"pass": all(c["pass"] for c in checks),
                  "failed": [c["name"] for c in checks if not c["pass"]],
                  "u_at_target": cert["u_at_target_finite"],
                  "coverage_u0_all": cert["coverage_curve"]["0"]
                  ["coverage_all_points"]}))
sys.exit(0 if all(c["pass"] for c in checks) else 1)
