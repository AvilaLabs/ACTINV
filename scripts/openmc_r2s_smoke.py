"""End-to-end smoke run of ``actinv.openmc.ActinvR2SManager`` against the installed OpenMC.

An iron sphere under a 14.1 MeV point source, irradiated for an hour and cooled for an hour, run
cell-based and mesh-based through ``R2SManager.run`` (whatever steps the installed OpenMC's
``run`` calls), then reloaded with ``load_results``. Checks that each run finishes, that the
photon flux in the sphere after cooling is positive and finite, and that the reload gives the same
tally. Not a physics check: a few thousand particles, no reference answer.

Needs OpenMC with ``OPENMC_CROSS_SECTIONS`` (neutron and photon data for iron) and the ACTINV data
bundle (``actinv data fetch``; ``ACTINV_DATA_DIR`` if it is not ``./actinv-data``).

    python scripts/openmc_r2s_smoke.py [WORKDIR]
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import openmc

from actinv.openmc import ActinvR2SManager

RADIUS_CM = 5.0
TIMESTEPS = [3600.0, 3600.0]
SOURCE_RATES = [1.0e12, 0.0]
COOLED = 2  # index into the times [0, 1 h, 2 h]


def build_model():
    iron = openmc.Material(name="iron")
    iron.add_element("Fe", 1.0)
    iron.set_density("g/cm3", 7.874)
    inner = openmc.Sphere(r=RADIUS_CM)
    outer = openmc.Sphere(r=4.0 * RADIUS_CM, boundary_type="vacuum")
    sphere = openmc.Cell(fill=iron, region=-inner)
    sphere.volume = 4.0 / 3.0 * math.pi * RADIUS_CM**3
    void = openmc.Cell(region=+inner & -outer)
    model = openmc.Model()
    model.geometry = openmc.Geometry([sphere, void])
    model.materials = openmc.Materials([iron])
    model.settings.run_mode = "fixed source"
    model.settings.particles = 2000
    model.settings.batches = 3
    model.settings.source = openmc.IndependentSource(
        space=openmc.stats.Point(), energy=openmc.stats.Discrete([14.1e6], [1.0]))
    photons = openmc.Tally(name="photon flux in the sphere")
    photons.filters = [openmc.CellFilter([sphere]), openmc.ParticleFilter(["photon"])]
    photons.scores = ["flux"]
    model.tallies = [photons]
    return model, sphere


def photon_flux(r2s):
    tally = r2s.results["photon_tallies"][COOLED][0]
    return float(np.sum(tally.mean))


def run_case(name, workdir, domains_of):
    model, sphere = build_model()
    out = workdir / name
    kwargs = {}
    if name == "cell-based":
        kwargs["bounding_boxes"] = {sphere.id: sphere.bounding_box}
    r2s = ActinvR2SManager(model, domains_of(sphere))
    r2s.run(TIMESTEPS, SOURCE_RATES, photon_time_indices=[COOLED], output_dir=out, **kwargs)
    flux = photon_flux(r2s)
    if not (math.isfinite(flux) and flux > 0.0):
        raise SystemExit(f"{name}: photon flux after cooling is {flux}, expected positive and finite")
    model2, sphere2 = build_model()
    reloaded = ActinvR2SManager(model2, domains_of(sphere2))
    reloaded.load_results(out)
    again = photon_flux(reloaded)
    if again != flux:
        raise SystemExit(f"{name}: load_results gives photon flux {again}, the run gave {flux}")
    print(f"{name}: photon flux after cooling {flux:.6e} (reload matches)")


def mesh_over(sphere):
    mesh = openmc.RegularMesh()
    mesh.lower_left = (-RADIUS_CM,) * 3
    mesh.upper_right = (RADIUS_CM,) * 3
    mesh.dimension = (2, 2, 2)
    return mesh


def main(argv):
    print(f"openmc {openmc.__version__}")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(argv[1]) if len(argv) > 1 else Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        run_case("cell-based", workdir, lambda sphere: [sphere])
        run_case("mesh-based", workdir, mesh_over)
    print("smoke: PASS")


if __name__ == "__main__":
    main(sys.argv)
