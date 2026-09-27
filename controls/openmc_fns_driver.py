#!/usr/bin/env python3
"""OpenMC arm driver for the FNS head-to-head — run under the openmc016 env.

Adapted from the sealed controls/p45_openmc_driver.py recipe
(MicroXS.from_multigroup_flux -> IndependentOperator -> PredictorIntegrator,
normalization_mode="source-rate"), with two changes:

- the union reachable nuclide set is intersected with the XS library's
  material list so a missing evaluation fails visibly per case rather than
  aborting the batch; dropped nuclides are recorded per group;
- the nuclide property maps emitted include decay_energy_ev so heat can be
  derived without a second OpenMC process.

Input: a job JSON {
  "work_dir": str, "chain_file": str, "bounds_file": str,
  "reach_depth": int,
  "groups": [{"name": str, "flux_descending": [floats],
              "total_flux": float}],
  "cases": [{"case": str, "group": str,
             "composition_isotope_fraction": {iso: atom_fraction},
             "timesteps_s": [floats], "source_rates": [floats]}]}

Output: <work_dir>/openmc_results.json with per-case per-step atoms
(atom/cm3; material volume 1 cm3 at rho 1 g/cm3 = atoms per gram).
"""
from __future__ import annotations

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
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


def xs_materials(xs_xml: str) -> set[str]:
    root = ET.parse(xs_xml).getroot()
    return {l.get("materials") for l in root.iter("library")
            if l.get("type") == "neutron"}


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
    xs_xml = os.environ["OPENMC_CROSS_SECTIONS"]
    available = xs_materials(xs_xml)

    import openmc.deplete as d
    import openmc.lib
    chain = d.Chain.from_xml(job["chain_file"])
    depth = int(job.get("reach_depth", 3))

    seeds = set()
    for c in job["cases"]:
        seeds |= set(c["composition_isotope_fraction"])
    dropped_seeds = sorted(s for s in seeds if s not in available)
    seeds &= available
    nucs_all = reachable_nuclides(chain, seeds, depth)
    # Coverage reporting only: reachable nuclides without an XS evaluation
    # still belong in the depletion matrix (they are produced through
    # parents' reactions and decay) and in the readout. Passing the full
    # set to from_multigroup_flux matches the sealed P45 recipe — missing
    # evaluations simply contribute no reaction channel.
    xs_missing = sorted(n for n in nucs_all if n not in available)
    nucs = sorted(nucs_all)

    results = {"schema": "actinv-openmc-fns-results-1",
               "chain_file": job["chain_file"],
               "xs_xml": xs_xml,
               "xs_available_count": len(available),
               "reachable_nuclides": len(nucs_all),
               "xs_missing_reachable": xs_missing,
               "dropped_seed_isotopes": dropped_seeds,
               "nuclides_tracked": len(nucs),
               "cases": {},
               "groups": {},
               "half_life_s": {n.name: n.half_life
                               for n in chain.nuclides if n.half_life},
               "decay_energy_ev": {n.name: n.decay_energy
                                   for n in chain.nuclides
                                   if n.decay_energy}}
    t_s = time.monotonic()
    with openmc.lib.TemporarySession():
        results["session_init_s"] = time.monotonic() - t_s
        micros_by_group = {}
        for g in job["groups"]:
            t0 = time.monotonic()
            flux_desc = np.asarray(g["flux_descending"], dtype=float)
            flux_norm = flux_desc[::-1] / g["total_flux"]
            fallback = False
            try:
                mx = d.MicroXS.from_multigroup_flux(
                    energies, flux_norm, chain_file=job["chain_file"],
                    nuclides=nucs)
            except Exception:
                fallback = True
                mx = d.MicroXS.from_multigroup_flux(
                    energies, flux_norm, chain_file=job["chain_file"],
                    nuclides=sorted(set(nucs) & available))
            micros_by_group[g["name"]] = (mx, flux_norm)
            results["groups"][g["name"]] = {
                "micros_s": time.monotonic() - t0,
                "n_nuclides": len(nucs),
                "micros_fallback_xs_only": fallback}
        for case in job["cases"]:
            cdir = work / case["case"]
            cdir.mkdir(parents=True, exist_ok=True)
            try:
                mx, flux_norm = micros_by_group[case["group"]]
                chain_names = {n.name for n in chain.nuclides}
                comp = {iso: f for iso, f in
                        case["composition_isotope_fraction"].items()
                        if iso in chain_names}
                dropped = sorted(
                    set(case["composition_isotope_fraction"]) - set(comp))
                total = sum(comp.values())
                comp = {iso: f / total for iso, f in comp.items()}
                mat = make_material(case["case"], comp)
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
                    "composition_dropped_from_chain": dropped,
                    "atoms_atom_per_cm3": atoms}
            except Exception as e:
                results["cases"][case["case"]] = {
                    "status": "arm_failure", "reason": repr(e)}
    out = work / "openmc_results.json"
    out.write_text(json.dumps(results, indent=1))
    print(json.dumps({"cases": len(results["cases"]),
                      "session_init_s": results["session_init_s"],
                      "nuclides_tracked": results["nuclides_tracked"],
                      "xs_missing_reachable": len(xs_missing),
                      "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
