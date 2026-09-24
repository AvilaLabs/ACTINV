#!/usr/bin/env python3
"""P45 OpenMC arm driver — run under the openmc016 env python.

Input: a job JSON {
  "work_dir": str, "chain_file": str, "bounds_file": str,
  "reach_depth": int,
  "groups": [          # one entry per distinct flux
    {"name": str, "flux_descending": [floats], "total_flux": float}],
  "cases": [
    {"case": str, "group": str,
     "composition_isotope_fraction": {iso: frac},
     "timesteps_s": [floats], "source_rates": [floats]}]}

Runs inside ONE openmc.lib.TemporarySession (documented amortized
pattern). Micros are computed once per distinct flux for the union
reachable nuclide set (reaction targets to `reach_depth` plus all decay
targets), then every case's IndependentOperator selects its slice —
per-case cost is the deplete only. Timing decomposition: session_init_s,
micros per group (micros_s), deplete per case (deplete_s).

Output: <work_dir>/openmc_results.json with per-case per-step
atoms (atom/cm3; material volume is 1 cm3 at rho 1 g/cm3 so this is
atoms per gram) for every nuclide the operator tracks.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def reachable_nuclides(chain, seeds: set[str], depth: int) -> set[str]:
    """Reaction targets to `depth` reaction steps; decay targets always."""
    by_name = {n.name: n for n in chain.nuclides}
    seen: set[str] = set()

    def decay_targets(name: str):
        stack = [name]
        while stack:
            cur = stack.pop()
            for dm in by_name[cur].decay_modes:
                t = dm.target
                if t and t in by_name and t not in seen:
                    seen.add(t)
                    stack.append(t)

    frontier = set(seeds)
    seen |= set(seeds)
    for _ in range(depth):
        nxt = set()
        for name in frontier:
            n = by_name.get(name)
            if n is None:
                continue
            for r in n.reactions:
                if r.target and r.target in by_name \
                        and r.target not in seen:
                    seen.add(r.target)
                    nxt.add(r.target)
        for name in list(seen):
            decay_targets(name)
        frontier = nxt
    return seen


def make_material(name: str, composition: dict):
    import openmc
    mat = openmc.Material(name=name)
    for iso, frac in composition.items():
        mat.add_nuclide(iso, float(frac))
    mat.set_density("g/cm3", 1.0)
    mat.depletable = True
    mat.volume = 1.0
    return mat


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text())
    work = Path(job["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    bounds = json.loads(Path(job["bounds_file"]).read_text())[
        "boundaries_eV"]
    energies = list(reversed(bounds))          # ascending eV

    import openmc.deplete as d
    import openmc.lib
    chain = d.Chain.from_xml(job["chain_file"])
    depth = int(job.get("reach_depth", 3))

    # union reachable set over all cases
    seeds = set()
    for c in job["cases"]:
        seeds |= set(c["composition_isotope_fraction"])
    nucs = sorted(reachable_nuclides(chain, seeds, depth))

    results = {"schema": "actinv-p45-openmc-results-1",
               "reachable_nuclides": len(nucs), "cases": {},
               "groups": {},
               "half_life_s": {n.name: n.half_life
                               for n in chain.nuclides
                               if n.half_life}}
    t_s = time.monotonic()
    with openmc.lib.TemporarySession():
        results["session_init_s"] = time.monotonic() - t_s
        micros_by_group = {}
        for g in job["groups"]:
            t0 = time.monotonic()
            flux_desc = np.asarray(g["flux_descending"], dtype=float)
            flux_norm = flux_desc[::-1] / g["total_flux"]
            mx = d.MicroXS.from_multigroup_flux(
                energies, flux_norm, chain_file=job["chain_file"],
                nuclides=nucs)
            micros_by_group[g["name"]] = (mx, flux_norm)
            results["groups"][g["name"]] = {
                "micros_s": time.monotonic() - t0,
                "n_nuclides": len(nucs)}
        for case in job["cases"]:
            cdir = work / case["case"]
            cdir.mkdir(parents=True, exist_ok=True)
            try:
                mx, flux_norm = micros_by_group[case["group"]]
                mat = make_material(
                    case["case"], case["composition_isotope_fraction"])
                import openmc
                openmc.Materials([mat]).export_to_xml(
                    str(cdir / "materials.xml"))
                os.chdir(cdir)
                t0 = time.monotonic()
                op = d.IndependentOperator(
                    [mat], [flux_norm], [mx],
                    chain_file=job["chain_file"],
                    normalization_mode="source-rate")
                op.output_dir = str(cdir)
                integ = d.PredictorIntegrator(
                    op, list(case["timesteps_s"]),
                    source_rates=list(case["source_rates"]))
                integ.integrate(output=False)
                dep_s = time.monotonic() - t0
                res = d.Results(cdir / "depletion_results.h5")
                mat_id = str(mat.id)
                atoms: dict[str, list] = {}
                for nuc in nucs:
                    try:
                        _, series = res.get_atoms(mat_id, nuc)
                        atoms[nuc] = [float(v) for v in series]
                    except (KeyError, ValueError):
                        pass
                results["cases"][case["case"]] = {
                    "status": "executed", "deplete_s": dep_s,
                    "atoms_atom_per_cm3": atoms}
            except Exception as e:
                results["cases"][case["case"]] = {
                    "status": "arm_failure", "reason": repr(e)}
    out = work / "openmc_results.json"
    out.write_text(json.dumps(results, indent=1))
    print(json.dumps({"cases": len(results["cases"]),
                      "session_init_s": results["session_init_s"],
                      "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
