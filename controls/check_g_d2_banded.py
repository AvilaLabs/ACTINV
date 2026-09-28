#!/usr/bin/env python3
"""D2b banded-calibration gate — verifies results/d2_banded_calibration.json
(actinv-banded-calibration-1) independently: re-runs the band-record join
and re-derives every coverage aggregate from the sealed corpus; verifies
sha-256 bindings of every band record and .exp file; asserts the fitted
remainder restores holdout coverage to the ~68% target while the honest
heavy-tail deficit at z=2 stays recorded and named."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CERT = ROOT / "results/d2_banded_calibration.json"
WORK = Path(__import__("os").environ.get(
    "ACTINV_P44_WORK", Path.home() / "nuclear-data" / "p44-work"))

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


c = json.loads(CERT.read_text())
check("schema", c.get("schema") == "actinv-banded-calibration-1")
check("corpus joined", c["n_experiments"] >= 100, f"{c['n_experiments']} experiments")

# ---- sha bindings of every consumed input ----
sys.path.insert(0, str(ROOT / "controls"))
from d2_empirical_calibrate import sha256  # noqa: E402

bound = c["input_sha256"]["band_records"]
bad = [eid for eid, sha in bound.items()
       if sha256(WORK / "bands" / f"{eid.replace('/', '__')}.json") != sha]
check("band-record shas bound and verified", not bad, f"{len(bad)} mismatched")
exp_dir = Path.home() / "nuclear-data" / "conderc-fns" / "fns"
bad_exp = [eid for eid, sha in c["input_sha256"]["exp_files"].items()
           if sha256(exp_dir / eid.split("/")[0] / f"{eid.split('/')[1]}.exp") != sha]
check("exp-file shas bound and verified", not bad_exp, f"{len(bad_exp)} mismatched")

# ---- independent re-derivation of the coverage aggregates ----
from d2_banded_calibrate import load_bands, join, coverage  # noqa: E402
from d2_empirical_calibrate import partition  # noqa: E402

bands_dir = Path(c["bands_dir"])
check("bands dir is the sealed corpus", bands_dir == WORK / "bands",
      str(bands_dir))
corpus = load_bands(bands_dir)
check("independent load reproduces experiment count",
      len(corpus) == c["n_experiments"],
      f"{len(corpus)} vs {c['n_experiments']}")

fo = c["band_types"]["first_order"]
fit_pts, hold_pts = [], []
per_mat_hold = {}
for eid, e in sorted(corpus.items()):
    rows = e["legs"].get("first_order")
    if not rows:
        continue
    pts = join(e, rows)[0]
    if partition(e["material"], e["experiment"]) == "fit":
        fit_pts.extend(pts)
    else:
        hold_pts.extend(pts)
        per_mat_hold.setdefault(e["material"], []).extend(pts)

check("independent join reproduces point count",
      len(fit_pts) + len(hold_pts) == fo["n_fit_points"] + fo["n_holdout_points"],
      f"{len(fit_pts)}+{len(hold_pts)} vs {fo['n_fit_points']}+{fo['n_holdout_points']}")

# re-derive baseline + remainder holdout coverages independently
base1 = coverage(hold_pts, 0.0, 1.0)
check("independent baseline z1 matches",
      abs(base1 - fo["coverage_holdout_no_remainder"]["z1"]) < 1e-4,
      f"{base1:.4f} vs {fo['coverage_holdout_no_remainder']['z1']}")

u = fo["u_pooled_remainder"]
rem1 = coverage(hold_pts, u, 1.0)
check("independent remainder z1 matches",
      abs(rem1 - fo["coverage_holdout_pooled_remainder"]["z1"]) < 1e-4,
      f"{rem1:.4f} vs {fo['coverage_holdout_pooled_remainder']['z1']}")

# re-derive the per-material table application on holdout
u_by_mat = fo["u_by_material_fit_partition"]
hit1 = hit2 = 0
for mat, pts in per_mat_hold.items():
    um = u_by_mat.get(mat, u)
    for p in pts:
        hit1 += abs(p["ln_residual"]) <= math.sqrt(
            p["h_ln"] ** 2 + um * um + p["ln_sigma_meas"] ** 2)
        hit2 += abs(p["ln_residual"]) <= math.sqrt(
            4 * (p["h_ln"] ** 2 + um * um) + p["ln_sigma_meas"] ** 2)
n = len(hold_pts)
check("independent per-material z1 matches",
      abs(hit1 / n - fo["coverage_holdout_per_material_table"]["z1"]) < 1e-4,
      f"{hit1 / n:.4f} vs {fo['coverage_holdout_per_material_table']['z1']}")
check("independent per-material z2 matches",
      abs(hit2 / n - fo["coverage_holdout_per_material_table"]["z2"]) < 1e-4,
      f"{hit2 / n:.4f} vs {fo['coverage_holdout_per_material_table']['z2']}")

# ---- calibration claims ----
check("remainder lifts holdout z1 (baseline < remainder)",
      base1 < rem1, f"{base1:.4f} -> {rem1:.4f}")
check("holdout z1 with remainder >= 0.68", rem1 >= 0.68,
      f"{rem1:.4f}")
check("holdout z1 with per-material table >= 0.68",
      hit1 / n >= 0.68, f"{hit1 / n:.4f}")
check("remainder finite and physical", 0.0 < u < 2.0, f"u={u}")

t = c["unmodeled_table"]
check("consumable table emitted",
      t["schema"] == "actinv-unmodeled-table-1"
      and t["default"] == fo["u_pooled_remainder"]
      and len(t["per_material"]) > 30)
check("table calibrated_against names the corpus and band type",
      t["calibrated_against"]["band_type"] == "first_order")

# honest tail: the heavy-tail deficit at z=2 is recorded, not hidden —
# a Gaussian remainder cannot reach 95% on evaluation-regression residuals.
check("z2 deficit honestly recorded",
      fo["coverage_holdout_per_material_table"]["z2"] < 0.95,
      f"z2={fo['coverage_holdout_per_material_table']['z2']}")
check("tail materials named",
      bool(fo.get("uncovered_at_z2_by_material")),
      str(fo.get("uncovered_at_z2_by_material", {}))[:80])

failed = [x for x in checks if not x["pass"]]
out = {"pass": not failed, "n": len(checks), "checks": checks}
print(json.dumps(out, indent=1))
sys.exit(0 if not failed else 1)
