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
spectra = [[1.0, 2.0], [10.0, 20.0], [0.5, 1.0]]
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

mesh = {
    "spec": "actinv-mesh-spec-1", "title": "d3 twin gate",
    "projectile": "neutron",
    "library": base["library"], "decay": base["decay"],
    "material": base["material"],
    "flux": {"path": str(flux_path), "sha256": sha(flux_path)},
    "schedule": base["schedule"], "options": base["options"],
    "uncertainty": base["uncertainty"],
    "cell_result_fields": ["steps"],
}
mp = tmp / "mesh.json"
mp.write_text(json.dumps(mesh, sort_keys=True) + "\n")
mesh_out = tmp / "mesh_out.jsonl"
cli("mesh", str(mp), str(mesh_out))
check("mesh emitted cells",
      sum(1 for l in mesh_out.read_text().splitlines()
          if '"cell"' in l) == 3)

# Twin: one clearance limit on heat.total. Loose limit clears all
# cells; a tight one restricts the hot cell (and possibly others).
def twinspec(limit, name):
    p = tmp / f"{name}.json"
    p.write_text(json.dumps({
        "spec": "actinv-twin-1", "mesh_output": str(mesh_out),
        "limits": [{"name": "heat", "response": "heat.total",
                    "limit": limit}]}))
    op = tmp / f"{name}.out.json"
    cli("twin", str(p), str(op))
    return json.loads(op.read_text())


loose = twinspec(1e-30, "loose")
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
check("hot cell binds somewhere",
      any(c == "cell-1" for c in hot_keys),
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

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps({"pass": not failed, "n": len(checks),
                           "checks": checks}, indent=1) + "\n")
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks)-len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
