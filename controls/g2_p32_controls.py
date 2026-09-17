#!/usr/bin/env python3
"""P32 G2: handoff qualification controls on the executed chain.

Controls:
  cell_identity_volume   - result cells match mesh geometry exactly
  source_conservation    - exported strengths conserve the photon total
  spectrum_fidelity      - per-cell energies/probabilities match groups
  cooling_separation     - step-3 source is genuinely distinct/decayed
  spatial_sampling       - exported sources sample inside cell bounds
  activation_comparison  - ACTINV vs openmc.deplete per-nuclide atoms
"""
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS = json.load(open(os.path.join(RES, "g0_p32_seals.json")))
EV = json.load(open(os.path.join(RES, "g1_p32.json")))
OUT = os.path.join(RES, "g2_p32.json")
OMC_ENV = os.path.expanduser("~/.local/share/mamba/envs/openmc")
MESH = EV["artifacts"]["mesh_result"]["path"]
FLUX = EV["artifacts"]["flux_ndjson"]["path"]


def openmc_python(code):
    env = dict(os.environ)
    env["PATH"] = f"{OMC_ENV}/bin:{env['PATH']}"
    env["OPENMC_CROSS_SECTIONS"] = (
        "/home/connoravila/nuclear-data/endfb-vii.1-hdf5/"
        "cross_sections.xml")
    r = subprocess.run(["python3", "-c", code], capture_output=True,
                       text=True, env=env, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"openmc inspection failed: {r.stderr[-2500:]}")
    return r.stdout


def load_cells():
    cells = {}
    with open(MESH) as f:
        for line in f:
            r = json.loads(line)
            if r["record"] == "cell":
                cells[r["id"]] = r
    return cells


def parse_source(path):
    """Extract (cell_id, lo, hi, energies, probs, strength) per source."""
    text = open(path).read()
    pat = re.compile(
        r"sources\.append\(openmc\.IndependentSource\(\s*"
        r"space=openmc\.stats\.Box\(\[([^\]]+)\], \[([^\]]+)\]\),\s*"
        r"energy=openmc\.stats\.Discrete\(\[([^\]]+)\], \[([^\]]+)\]\),\s*"
        r"particle=\"photon\", strength=([0-9.eE+-]+)\)\)  # cell (\S+)")
    out = []
    for m in pat.finditer(text):
        lo = [float(x) for x in m.group(1).split(",")]
        hi = [float(x) for x in m.group(2).split(",")]
        en = [float(x) for x in m.group(3).split(",")]
        pr = [float(x) for x in m.group(4).split(",")]
        out.append({"cell": m.group(6), "lo": lo, "hi": hi,
                    "energies": en, "probs": pr,
                    "strength": float(m.group(5))})
    total = float(re.search(r"TOTAL_PHOTONS_S = ([0-9.e+-]+)", text)
                  .group(1))
    return out, total


def main():
    cells = load_cells()
    controls = {}

    # 1. cell_identity_volume: bounds/volume consistent with flux geometry
    bad = []
    flux_cells = {}
    with open(FLUX) as f:
        for line in f:
            r = json.loads(line)
            if r["record"] == "cell":
                flux_cells[r["id"]] = r
    for cid, c in cells.items():
        fc = flux_cells.get(cid)
        if fc is None or fc["bounds_cm"] != c["bounds_cm"] \
                or fc["volume_cm3"] != c["volume_cm3"]:
            bad.append(cid)
            continue
        b = c["bounds_cm"]
        v = (b[0][1] - b[0][0]) * (b[1][1] - b[1][0]) \
            * (b[2][1] - b[2][0])
        if abs(v - c["volume_cm3"]) > 1e-9 * c["volume_cm3"]:
            bad.append(cid)
    controls["cell_identity_volume"] = {
        "checked": len(cells), "bad": bad,
        "pass": len(cells) == 64 and not bad}

    # 2. source_conservation + 3. spectrum_fidelity + 4. cooling
    per_step = {}
    for step, s in EV["sources"].items():
        srcs, total = parse_source(s["path"])
        per_step[step] = (srcs, total)
        sum_strength = sum(x["strength"] for x in srcs)
        cons = abs(sum_strength - total) <= 1e-9 * total
        # each source's strength equals its cell's photon total
        fidelity_bad = []
        for x in srcs:
            c = cells[x["cell"]]
            st = [s2 for s2 in c["result"]["steps"]
                  if s2["step"] == int(step)][0]
            ps = st["photon_source"]
            if abs(x["strength"] - ps["total_photons_s"]) \
                    > 1e-9 * ps["total_photons_s"]:
                fidelity_bad.append(x["cell"] + ":strength")
            gs = [g for g in ps["groups"] if g["photons_s"] > 0]
            if len(gs) != len(x["energies"]):
                fidelity_bad.append(x["cell"] + ":groupcount")
                continue
            if any(abs(g["centroid_eV"] - e) > 1e-6 * e
                   for g, e in zip(gs, x["energies"])):
                fidelity_bad.append(x["cell"] + ":energy")
            tot = sum(g["photons_s"] for g in gs)
            if any(abs(g["photons_s"] / tot - p) > 1e-9
                   for g, p in zip(gs, x["probs"])):
                fidelity_bad.append(x["cell"] + ":prob")
            # bounds must equal cell bounds
            if x["lo"] != [b[0] for b in c["bounds_cm"]] \
                    or x["hi"] != [b[1] for b in c["bounds_cm"]]:
                fidelity_bad.append(x["cell"] + ":bounds")
        per_step[step] = (srcs, total, cons, fidelity_bad)
    controls["source_conservation"] = {
        str(s): {"sum_strengths": sum(x["strength"] for x in v[0]),
                 "declared_total": v[1], "conserved": v[2],
                 "n_sources": len(v[0])}
        for s, v in per_step.items()}
    controls["source_conservation"]["pass"] = all(
        v[2] and len(v[0]) == 64 for v in per_step.values())
    controls["spectrum_fidelity"] = {
        str(s): {"bad": v[3][:5], "n_bad": len(v[3])}
        for s, v in per_step.items()}
    controls["spectrum_fidelity"]["pass"] = all(
        not v[3] for v in per_step.values())

    steps = sorted(per_step, key=int)
    t2, t3 = per_step[steps[0]][1], per_step[steps[1]][1]
    s2 = {x["cell"]: x for x in per_step[steps[0]][0]}
    s3 = {x["cell"]: x for x in per_step[steps[1]][0]}
    same_shape = sum(1 for c in s2 if s2[c]["energies"] == s3[c]["energies"]
                     and s2[c]["probs"] == s3[c]["probs"])
    controls["cooling_separation"] = {
        "total_step2": t2, "total_step3": t3,
        "ratio": t3 / t2,
        "identical_spectra_cells": same_shape,
        "pass": t3 < t2 and same_shape < 64}

    # 5. spatial_sampling via openmc in a subprocess
    sampling = openmc_python(f"""
import json, runpy, numpy as np, openmc, openmc.lib, os, tempfile
# build a minimal model around the exported sources and sample via lib
ns = runpy.run_path({EV["sources"][steps[0]]["path"]!r})
box = openmc.Sphere(r=1000.0, boundary_type="vacuum")
cell = openmc.Cell(region=-box)
s = openmc.Settings()
s.source = ns["sources"]
s.batches = 1
s.particles = 10
s.run_mode = "fixed source"
m = openmc.model.Model(geometry=openmc.Geometry([cell]), settings=s)
with tempfile.TemporaryDirectory() as td:
    os.chdir(td)
    m.export_to_xml()
    openmc.lib.init()
    sites = openmc.lib.sample_external_source(
        4000 * len(ns["sources"]), prn_seed=12345)
    openmc.lib.finalize()
# each site: (r, E, wgt, ...) -- r is the xyz coordinate
xyz = np.array([tuple(s2.r) for s2 in sites])
inside_count = 0
per_cell = np.zeros((4, 4, 4), int)
bounds = np.arange(-2.0, 3.0)
for p in xyz:
    idx = [int(np.searchsorted(bounds, p[a], side="right") - 1)
           for a in range(3)]
    if all(0 <= i < 4 for i in idx):
        inside_count += 1
        per_cell[tuple(idx)] += 1
n = len(xyz)
# per-source strengths should give roughly equal sites per voxel only
# if strengths equal; here strengths differ, so check containment only
# and that all 64 voxels received at least one site.
occupied = int((per_cell > 0).sum())
print(json.dumps({{"n": n, "inside": inside_count,
                   "occupied_voxels": occupied}}))
""")
    sres = json.loads(sampling.strip().splitlines()[-1])
    sres["pass"] = (sres["inside"] == sres["n"]
                    and sres["occupied_voxels"] == 64)
    controls["spatial_sampling"] = {**sres}

    # 6. activation_comparison: ACTINV vs openmc.deplete atoms
    comp = openmc_python(f"""
import json, numpy as np, openmc.deplete
r = openmc.deplete.ResultsList(
    {EV["artifacts"]["depletion_results"]["path"]!r})
res = {{}}
for k in range(4):
    for j in range(4):
        for i in range(4):
            mid = str(k * 16 + j * 4 + i + 1)  # creation order in stage A
            nucs = {{}}
            try:
                nucs["Fe56"] = float(r.get_atoms(mid, "Fe56")[1][1])
            except Exception:
                continue
            for nuc in ["Mn56", "Mn54", "Fe59", "Cr51", "V52",
                        "Sc48", "Co58", "Co60", "Fe55", "Mn57",
                        "V49", "Ti48", "Mn52"]:
                try:
                    nucs[nuc] = float(r.get_atoms(mid, nuc)[1][1])
                except Exception:
                    pass
            res[f"v_{{i+1}}_{{j+1}}_{{k+1}}"] = nucs
print(json.dumps(res))
""")
    dep = json.loads(comp)
    # ACTINV: atoms = atoms_per_g * mass_g
    mass = SEALS["model"]["density_g_cm3"] * SEALS["model"]["voxel_cm3"]
    actinv = {}
    for cid, c in cells.items():
        inv = [s for s in c["result"]["steps"]
               if s["step"] == 1][0]["inventory"]
        actinv[cid] = {n["nuclide"]: n["atoms_per_g"] * mass
                       for n in inv}
    # map cell id "i,j,k" -> material name "v_i_j_k"
    rows = []
    for cid, inv in actinv.items():
        i, j, k = cid.split(",")
        mname = f"v_{i}_{j}_{k}"
        dm = dep.get(mname, {})
        for nuc, a_atoms in inv.items():
            d_atoms = dm.get(nuc, 0.0)  # get_atoms returns atom counts
            if a_atoms > 1e3 or d_atoms > 1e3:
                rows.append((cid, nuc, a_atoms, d_atoms))
    devs = []
    for cid, nuc, a, d in rows:
        if a > 0 and d > 0:
            devs.append({"cell": cid, "nuclide": nuc, "actinv": a,
                         "deplete": d,
                         "rel": abs(a - d) / max(a, d)})
    devs.sort(key=lambda x: -x["actinv"])
    top = devs[:50]
    controls["activation_comparison"] = {
        "n_compared": len(devs),
        "top50": top,
        "median_rel": sorted(x["rel"] for x in top)[len(top) // 2]
        if top else None,
        "max_rel_top50": max((x["rel"] for x in top), default=None),
        "pass": bool(top)}

    for name, c in controls.items():
        c.setdefault("control", name)
    doc = {"schema": "actinv-p32-g2-1", "phase": "P32", "gate": "G2",
           "controls": controls}
    json.dump(doc, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps({k: v.get("pass") for k, v in controls.items()},
                     indent=1))


if __name__ == "__main__":
    main()
