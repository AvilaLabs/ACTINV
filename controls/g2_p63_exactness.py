#!/usr/bin/env python3
"""P63 G2 — independent exactness: rebuild the fold from raw emitted
fields and rebuild the calibration curve from raw coverage points."""
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
import p63_calibrate  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p63_exactness.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p63_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)
sp = json.loads(json.dumps(base))
sp["uncertainty"]["unmodeled_relative"] = 0.41
res = p60_case.run(ACTINV, sp, tmp, "wide")

# Fold rebuilt independently from mf33 σ + emitted components.
for step in res["steps"]:
    for name, r in step["uncertainty"]["responses"].items():
        modeled = r["modeled_standard_uncertainty"]
        mv, uv = r["modeled_variance"], r["unmodeled_variance"]
        z = r["normal_multiplier"]
        n = r["nominal"]
        hw = z * math.sqrt(mv + uv)
        check(f"{name}@{step['t_s']}: w==z*sqrt(mv+uv)",
              r["normal_interval"][0] == n - hw
              and r["normal_interval"][1] == n + hw)
        check(f"{name}@{step['t_s']}: components re-derive",
              uv == (r["unmodeled_relative"] * r["nominal"]) * (r["unmodeled_relative"] * r["nominal"])
              and modeled == math.sqrt(mv))
        # rebuild modeled variance from raw channel σ's + mf33 σ
        extra = 0.0
        for ch in r["channels"]:
            if (ch["channel"] != "cross_section_mf33"
                    and ch.get("status") == "propagated"
                    and ch.get("standard_uncertainty") is not None):
                extra += ch["standard_uncertainty"] ** 2
        # ulp-level agreement: emitted σ values are sqrt-rounded, so the
        # variance rebuild is compared at a tiny relative tolerance
        check(f"{name}@{step['t_s']}: modeled rebuild",
              math.isclose(mv, r["mf33_standard_uncertainty"] ** 2 + extra,
                           rel_tol=1e-12),
              f"{mv!r} vs {r['mf33_standard_uncertainty'] ** 2 + extra!r}")

# Calibration curve rebuilt from raw sealed points.
doc = json.loads((ROOT / "results/p44_sealed_coverage.json").read_bytes())
finite, unc = p63_calibrate.extract(doc)
check("extract splits all points",
      len(finite) + len(unc) == doc["n_points"])
cert_path = tmp / "cert.json"
subprocess.run([sys.executable, str(ROOT / "controls/p63_calibrate.py"),
                str(ROOT / "results/p44_sealed_coverage.json"),
                "--out", str(cert_path)],
               check=True, capture_output=True)
cert = json.loads(cert_path.read_text())
# Recompute two grid rows independently.
for u in (0.0, 0.5, 1.0):
    cov = sum(1 for p in finite
              if abs(p["measured_W_g"]
                     - (p["band"]["hi"] + p["band"]["lo"]) / 2)
              <= math.sqrt(((p["band"]["hi"] - p["band"]["lo"]) / 2) ** 2
                           + (p63_calibrate.Z_95 * u
                              * p["band"]["nominal"]) ** 2
                           + p["sigma_W_g"] ** 2)) / len(finite)
    check(f"coverage(u={u}) exact",
          cert["coverage_curve"][f"{u:.4g}"]["coverage_finite"] == cov)
check("certificate names sealed doc sha",
      cert["input"]["sha256"] == p60_case.sha(
          ROOT / "results/p44_sealed_coverage.json"))
check("uncoverable count matches", cert["n_uncoverable_by_relative_term"]
      == len(unc))

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:10]}))
sys.exit(1 if failed else 0)
