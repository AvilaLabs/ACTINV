#!/usr/bin/env python3
"""D3 G1 — facility activation twin over a mesh run.

`actinv twin` reads the per-cell certified results a mesh run emitted
and evaluates declared clearance limits on the band edges: a cell
clears only when its conservative interval clears. The gate runs a
3-cell mesh with uncertainty, then checks the per-cell × time × limit
margin matrix, the facility rollup, and the binding-cell record."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g1_d3_twin.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def cli(*args, expect_err=None):
    r = subprocess.run([str(ACTINV), *args], cwd=ROOT, text=True,
                       capture_output=True, timeout=600)
    if expect_err is not None:
        assert r.returncode != 0, f"expected failure, got {r.stdout[:300]}"
        assert expect_err in r.stderr or expect_err in r.stdout, \
            f"missing '{expect_err}': {r.stderr[-400:]}"
        return None
    assert r.returncode == 0, f"{args}: {r.stderr[-800:]}"
    return r.stdout


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


tmp = Path(tempfile.mkdtemp(prefix="d3_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)

# Canonical flux: three cells on the library's exact group structure
# (bounds [1,2,3] → two groups), second cell a 10x hotter spectrum.
bounds = [1.0, 2.0, 3.0]
# cell-2 carries cell-0's exact flux — but a different material below.
spectra = [[1.0, 2.0], [10.0, 20.0], [1.0, 2.0]]
descriptor = tmp / "flux.source.json"
descriptor.write_text(json.dumps({"fixture": "d3-g1"}))
flux_path = tmp / "flux.jsonl"
header = {
    "record": "header", "schema": "actinv-flux-1",
    "source": {"format": "d3-control", "path": str(descriptor),
               "sha256": sha(descriptor)},
    "energy_boundaries_eV": bounds, "flux_units": "n cm^-2 s^-1",
    "cell_count": len(spectra),
    "geometry": {"kind": "rectilinear",
                 "dimension": [len(spectra), 1, 1],
                 "axis_boundaries_cm": [
                     [float(i) for i in range(len(spectra) + 1)],
                     [0.0, 1.0], [0.0, 1.0]]},
}
records = [header]
totals = []
for i, sp in enumerate(spectra):
    totals.append(math.fsum(sp))
    records.append({
        "record": "cell", "ordinal": i, "id": f"cell-{i}",
        "index": [i + 1, 1, 1],
        "bounds_cm": [[float(i), float(i + 1)], [0.0, 1.0], [0.0, 1.0]],
        "volume_cm3": 1.0,
        "flux_per_group": sp, "flux_total": math.fsum(sp)})
records.append({"record": "footer", "cell_count": len(spectra),
                "flux_sum_over_cells": math.fsum(totals),
                "volume_integrated_flux": math.fsum(totals)})
flux_path.write_text(
    "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))

options = json.loads(json.dumps(base["options"]))
options["outputs"] = options["outputs"] + ["photons"]
mesh = {
    "spec": "actinv-mesh-spec-1", "title": "d3 twin gate",
    "projectile": "neutron",
    "library": base["library"], "decay": base["decay"],
    "material": base["material"],
    "flux": {"path": str(flux_path), "sha256": sha(flux_path)},
    "schedule": base["schedule"], "options": options,
    "uncertainty": base["uncertainty"],
    "cell_result_fields": ["steps"],
    # Per-cell material: cell-2 solves a Mn-bearing alloy.
    "materials": {"cell-2": {"mass_g": 1.0, "basis": "atoms_per_g",
                             "composition": {"Fe56": 0.8, "Mn57": 0.2}}},
}
mp = tmp / "mesh.json"
mp.write_text(json.dumps(mesh, sort_keys=True) + "\n")
mesh_out = tmp / "mesh_out.jsonl"
cli("mesh", str(mp), str(mesh_out))
check("mesh emitted cells",
      sum(1 for l in mesh_out.read_text().splitlines()
          if '"cell"' in l) == 3)

# Materials: cell-0 and cell-2 share identical flux but differ in
# composition — the memo signature must not collapse them.
cell_recs = {}
for line in mesh_out.read_text().splitlines():
    rec = json.loads(line)
    if rec.get("record") == "cell":
        cell_recs[rec["id"]] = rec
sha_by_cell = {k: v["material_sha256"] for k, v in cell_recs.items()}
check("material sha groups cells",
      sha_by_cell["cell-0"] == sha_by_cell["cell-1"]
      and sha_by_cell["cell-0"] != sha_by_cell["cell-2"],
      f"{sha_by_cell}")
b0 = cell_recs["cell-0"]["result"]["steps"][-1]["uncertainty"]["responses"]["heat.total"]["conservative_interval"]
b2 = cell_recs["cell-2"]["result"]["steps"][-1]["uncertainty"]["responses"]["heat.total"]["conservative_interval"]
check("identical flux, different material -> different band",
      abs(b0[0] - b2[0]) > 0.0 or abs(b0[1] - b2[1]) > 0.0,
      f"{b0} vs {b2}")

# Twin: one clearance limit on heat.total. Cell ranking is cell-2
# (Mn57-seeded, ~1.5e-14) >> cell-1 (10x flux Fe) >> cell-0.
def twinspec(limit, name, times=None, assays=None,
             components=None, dose_points=None):
    doc = {"spec": "actinv-twin-1", "mesh_output": str(mesh_out),
           "limits": [{"name": "heat", "response": "heat.total",
                       "limit": limit}]}
    if times is not None:
        doc["times_s"] = times
    if assays is not None:
        doc["assays"] = assays
    if components is not None:
        doc["components"] = components
    if dose_points is not None:
        doc["dose_points"] = dose_points
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(doc))
    op = tmp / f"{name}.out.json"
    cli("twin", str(p), str(op))
    return json.loads(op.read_text())


loose = twinspec(1e-10, "loose")
check("twin schema", loose.get("schema") == "actinv-twin-1")
check("loose limit clears every cell",
      loose["facility"]["cleared"] == 3
      and loose["facility"]["restricted"] == 0)
check("per-cell entries recorded",
      len(loose["per_cell"]) == 3
      and all(len(c["entries"]) > 0 for c in loose["per_cell"]))

# Tight: below every band → all restricted; binding cell named.
tight = twinspec(1e-45, "tight")
check("tight limit restricts cells",
      tight["facility"]["restricted"] >= 1)
bind = tight["facility"]["binding_cells"]
check("binding cells recorded per (limit, time)",
      bind and all("cell" in v and "margin" in v for v in bind.values()))
hot_keys = [v["cell"] for k, v in bind.items()]
check("dominant Mn57 cell binds somewhere",
      any(c == "cell-2" for c in hot_keys),
      f"binding cells {sorted(set(hot_keys))}")

# Error paths.
bad = tmp / "bad.json"
bad.write_text(json.dumps({"spec": "actinv-twin-1",
                           "mesh_output": str(mesh_out), "limits": []}))
cli("twin", str(bad), expect_err="no clearance limits")
check("empty limits refused", True)

bad2 = tmp / "bad2.json"
bad2.write_text(json.dumps({"spec": "actinv-twin-1",
                            "mesh_output": str(mesh_out),
                            "limits": [{"name": "x",
                                        "response": "no.such",
                                        "limit": 1.0}]}))
cli("twin", str(bad2), expect_err="no certified band")
check("unfitted response refused", True)

# D5→D3: an assay on the hot cell shrinks its band below the limit and
# flips the verdict — measure the cell, clear the cell.
cells = {}
for line in mesh_out.read_text().splitlines():
    rec = json.loads(line)
    if rec.get("record") == "cell":
        st = rec["result"]["steps"][-1]
        cells[rec["id"]] = st["uncertainty"]["responses"]["heat.total"]
hot = cells["cell-1"]
cool = cells["cell-0"]
# Limit sits in the upper tail of the hot cell's band (still above the
# cool cells' edges): the hot cell is restricted on the prior band but a
# low-side assay can pull its posterior edge under the limit.
mid = cool["conservative_interval"][1] + 0.85 * (
    hot["conservative_interval"][1] - cool["conservative_interval"][1])
edge_t = json.loads(mesh_out.read_text().splitlines()[1])["result"]["steps"][-1]["t_s"]
print(f"hot band {hot['conservative_interval']} cool band {cool['conservative_interval']} limit {mid:.3e} t={edge_t}")

# Evaluate at the measured time so only the fused step decides.
pre = twinspec(mid, "pre_assay", times=[edge_t])
verdicts = {c["cell"]: c["verdict"] for c in pre["per_cell"]}
check("pre-assay: Fe-hot cell restricted, Fe-cool cleared",
      verdicts.get("cell-1") == "restricted"
      and verdicts.get("cell-0") == "cleared",
      f"{verdicts}")

# Assay inside the prior band but low enough that the posterior's upper
# edge clears the limit: posterior_hi ≈ meas·e^(m·s_post) with s_post≈2%
# → meas = limit/1.1 leaves headroom either side.
meas = mid / 1.1
assert hot["conservative_interval"][0] < meas < hot["conservative_interval"][1], \
    f"assay {meas:.3e} outside prior band {hot['conservative_interval']}"
ap = tmp / "cell1_assay.json"
ap.write_text(json.dumps({"schema": "actinv-assay-1",
                          "response": "heat.total",
                          "time_s": edge_t,
                          "value": meas,
                          "standard_uncertainty": meas * 0.02}))
post = twinspec(mid, "with_assay", times=[edge_t],
              assays=[{"cell": "cell-1", "assay": str(ap)}])
pv = {c["cell"]: c["verdict"] for c in post["per_cell"]}
check("assay flips the Fe-hot cell to cleared",
      pv.get("cell-1") == "cleared", f"{pv}")
check("Mn57 cell unaffected by an Fe assay",
      pv.get("cell-2") == "restricted", f"{pv}")
check("assimilated cells recorded",
      "cell-1" in post["facility"]["assimilated_cells"])

# The entries form is equivalent: a one-line multi-nuclide assay doc must
# fuse identically to the scalar shape.
ap_entries = tmp / "cell1_assay_entries.json"
ap_entries.write_text(json.dumps({"schema": "actinv-assay-1",
                                  "time_s": edge_t,
                                  "entries": [{"response": "heat.total",
                                               "value": meas,
                                               "standard_uncertainty":
                                                   meas * 0.02}]}))
post_e = twinspec(mid, "with_entries_assay", times=[edge_t],
                  assays=[{"cell": "cell-1", "assay": str(ap_entries)}])
pv_e = {c["cell"]: c["verdict"] for c in post_e["per_cell"]}
check("entries-form assay fuses identically",
      pv_e == pv
      and next(c for c in post_e["per_cell"] if c["cell"] == "cell-1")
          ["entries"][0]["band"][1]
      == next(c for c in post["per_cell"] if c["cell"] == "cell-1")
          ["entries"][0]["band"][1])

# D5 correlated propagation: cell-1's assay informs cell-0's band
# through a declared ln-correlation — measure one Fe component, the
# sibling's uncertainty narrows too.
prop = twinspec(mid, "with_prop", times=[edge_t],
                assays=[{"cell": "cell-1", "assay": str(ap),
                         "propagates": [{"cell": "cell-0",
                                         "rho": 0.9}]}])
c0_prior_hi = cells["cell-0"]["conservative_interval"][1]
c0_post = next(c for c in prop["per_cell"] if c["cell"] == "cell-0")
c0_post_hi = c0_post["entries"][0]["band"][1]
check("propagated assay shrinks the sibling band",
      c0_post_hi < c0_prior_hi,
      f"prior_hi={c0_prior_hi:.3e} post_hi={c0_post_hi:.3e}")
check("unpropagated cell untouched by propagation",
      next(c for c in prop["per_cell"] if c["cell"] == "cell-2")
          ["entries"][0]["band"][1]
      == cells["cell-2"]["conservative_interval"][1])

# Value-of-information: restricted cells get an assay recommendation —
# the precision a nominal-landing measurement needs to clear them.
recs = pre["facility"]["assay_recommendations"]
c1_rec = next((r for r in recs if r["cell"] == "cell-1"), None)
c2_rec = next((r for r in recs if r["cell"] == "cell-2"), None)
check("assay recommendation names the restricted cell", c1_rec is not None)
check("required precision is achievable for cell-1",
      c1_rec and 0 < c1_rec["required_measurement_rel_su"] < 1,
      f"{c1_rec}")
# cell-2's nominal sits above the limit: no nominal-landing precision
# suffices — the only path is a low-side measurement.
check("cell-2 flagged as nominal-above-limit",
      c2_rec and "required_measurement_rel_su" not in c2_rec
      and c2_rec["max_assay_value_for_clearance"] == mid,
      f"{c2_rec}")
# Achievable recommendations sort before unachievable ones.
reqs = [r.get("required_measurement_rel_su", float("inf")) for r in recs]
check("achievable recommendations sort first",
      all(not math.isinf(s) for s in reqs)
      or reqs == sorted(reqs, key=lambda s: math.isinf(s)),
      f"{reqs}")

# Component rollup: a component clears only when every member clears.
comp = twinspec(mid, "components", times=[edge_t],
                components={"fe-leg": ["cell-0"],
                            "mn-bearing": ["cell-2"],
                            "ghost": ["cell-9"]})
cv = {k: v["verdict"] for k, v in comp["components"].items()}
check("component rollup verdicts",
      cv == {"fe-leg": "cleared", "mn-bearing": "restricted",
             "ghost": "unknown"}, f"{cv}")

# Dose points: the far detector sees the hot cell's photons stronger
# than a shielded one; ordering and attenuation must be sane.
dpt = twinspec(mid, "dose", times=[edge_t],
               dose_points=[
                   {"name": "near", "position_cm": [1.0, 0.5, 0.5]},
                   {"name": "far", "position_cm": [10.0, 0.5, 0.5]},
                   {"name": "shielded", "position_cm": [1.0, 0.5, 0.5],
                    "shields": [{"mu_cm_inv": 1.0,
                                 "thickness_cm": 4.0}]}])
dp = {p["name"]: p for p in dpt["dose_points"]}
fx = {k: v["steps"][0]["photon_flux_cm2_s"] for k, v in dp.items()}
check("dose points emitted", len(dp) == 3 and all(v >= 0 for v in fx.values()),
      f"{fx}")
check("far point dimmer than near",
      fx.get("far", 1.0) < fx.get("near", 0.0), f"{fx}")
check("shielding attenuates",
      fx.get("shielded", 1.0) < fx.get("near", 0.0) * 0.5, f"{fx}")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
