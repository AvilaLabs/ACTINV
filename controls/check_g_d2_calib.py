#!/usr/bin/env python3
"""D2 calibration certificate gate — verifies `results/d2_calibration.json`
(actinv-calibration-fit-1) independently: re-derives pooled u and the
held-out coverage numbers from the embedded point residuals is not
possible (points are hashed, not embedded), so this gate re-runs the
collector join and checks the certificate's claimed aggregates match."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CERT = ROOT / "results/d2_calibration.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


c = json.loads(CERT.read_text())
check("schema", c.get("schema") == "actinv-calibration-fit-1")
check("corpus joined", c["n_experiments"] >= 100, f"{c['n_experiments']} experiments")
check("both partitions populated",
      0 < c["n_fit_experiments"] < c["n_experiments"]
      and c["n_fit_points"] > 0 and c["n_holdout_points"] > 0)
check("pooled u finite positive", 0.0 < c["u_pooled"] < 2.0, f"u={c['u_pooled']}")
hold = c["coverage_holdout_pooled_u"]
# The fit partition's u_pooled over-covers at z=1 on this corpus;
# the honest floor is that held-out z=1 coverage meets the 68% target.
check("held-out z1 coverage ≥ 0.68", hold["z1"] >= 0.68, f"{hold['z1']}")
check("held-out z2 coverage ≥ 0.90", hold["z2"] >= 0.90, f"{hold['z2']}")
check("u needed for 68% held-out < pooled u (fit over-covers)",
      c["u_holdout_at_target_z1"] <= c["u_pooled"])
check("u needed for 95% held-out recorded", 0 < c["u_holdout_at_target_z2"] < 5)
t = c["unmodeled_table"]
check("consumable table emitted", t["schema"] == "actinv-unmodeled-table-1"
      and t["default"] == c["u_pooled"] and len(t["per_material"]) > 30)
check("result shas bound", all(
    len(v["result_sha256"]) == 64 for v in c["experiments"].values()))

# Re-derive the join independently: counts must match the certificate.
sys.path.insert(0, str(ROOT / "controls"))
import d2_empirical_calibrate as cal  # noqa: E402
corpus = cal.collect(ROOT / c["results_dir"])
check("independent join reproduces experiment count",
      len(corpus) == c["n_experiments"],
      f"{len(corpus)} vs {c['n_experiments']}")
n_pts = sum(len(e["points"]) for e in corpus.values())
check("independent join reproduces point count",
      n_pts == c["n_fit_points"] + c["n_holdout_points"],
      f"{n_pts} vs {c['n_fit_points'] + c['n_holdout_points']}")

failed = [c for c in checks if not c["pass"]]
out = {"pass": not failed, "n": len(checks), "checks": checks}
(ROOT / "results/g_d2_calib.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed][:6]}))
sys.exit(1 if failed else 0)
