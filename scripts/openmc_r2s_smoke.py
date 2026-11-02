"""End-to-end smoke run of ``actinv.openmc.ActinvR2SManager`` against the installed OpenMC.

An iron sphere under a 14.1 MeV point source, irradiated for an hour and cooled for an hour, run
cell-based and mesh-based through ``R2SManager.run`` (whatever steps the installed OpenMC's
``run`` calls), then reloaded with ``load_results``. Checks that each run finishes, that the
photon flux in the sphere after cooling is positive and finite, and that the reload gives the same
tally. Not a physics check: a few thousand particles, no reference answer.

With ``--uncertainty`` each case also asks for the dose band (``uncertainty={}``: MF=33 cross
sections, decay constants and flux-tally noise) and checks that the band's dose is the photon flux
times the cooled photon source ACTINV reported, and that every standard deviation is finite, the
total no smaller than its parts. Needs the covariance sidecar installed beside the library.

Needs OpenMC with ``OPENMC_CROSS_SECTIONS`` (neutron and photon data for iron) and the ACTINV data
bundle (``actinv data fetch``; ``ACTINV_DATA_DIR`` if it is not ``./actinv-data``).

    python scripts/openmc_r2s_smoke.py [--uncertainty] [WORKDIR]
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


def check_band(name, r2s, flux):
    """The band's dose against the flux and ACTINV's cooled source; its deviations sane."""
    (band,) = r2s.results["dose_uncertainty"][COOLED]
    source = sum(s for groups in r2s.results["depletion_results"][COOLED]._by_material.values()
                 if groups for _, s in groups)
    dose = float(np.sum(band["dose"]))
    if not math.isclose(dose, flux * source, rel_tol=1.0e-9):
        raise SystemExit(f"{name}: band dose {dose} is not photon flux x source {flux * source}")
    parts = {**{k: float(np.sum(v)) for k, v in band["std_dev"].items()},
             "statistical": float(np.sum(band["statistical_std_dev"]))}
    total = float(np.sum(band["total_std_dev"]))
    if not all(math.isfinite(v) and v >= 0.0 for v in [*parts.values(), total]) \
            or total < max(parts.values()) or set(band["std_dev"]) != {"cross_section_mf33",
                                                                    "decay_constants", "flux"}:
        raise SystemExit(f"{name}: band deviations {parts}, total {total}")
    shown = ", ".join(f"{k} {v / dose:.2%}" for k, v in parts.items())
    print(f"{name}: band {total / dose:.2%} of the dose ({shown}; {len(band['bands'])} nuclides banded, "
          f"{band['unbanded_fraction']:.1e} of the source unbanded)")


def run_case(name, workdir, domains_of, uncertainty=False):
    model, sphere = build_model()
    out = workdir / name
    kwargs = {}
    if name == "cell-based":
        kwargs["bounding_boxes"] = {sphere.id: sphere.bounding_box}
    r2s = ActinvR2SManager(model, domains_of(sphere), uncertainty={} if uncertainty else None)
    r2s.run(TIMESTEPS, SOURCE_RATES, photon_time_indices=[COOLED], output_dir=out, **kwargs)
    flux = photon_flux(r2s)
    if not (math.isfinite(flux) and flux > 0.0):
        raise SystemExit(f"{name}: photon flux after cooling is {flux}, expected positive and finite")
    if uncertainty:
        check_band(name, r2s, flux)
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
    args = argv[1:]
    uncertainty = "--uncertainty" in args
    args = [a for a in args if a != "--uncertainty"]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(args[0]) if args else Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        run_case("cell-based", workdir, lambda sphere: [sphere], uncertainty)
        run_case("mesh-based", workdir, mesh_over, uncertainty)
    print("smoke: PASS")


if __name__ == "__main__":
    main(sys.argv)
