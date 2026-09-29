#!/usr/bin/env python3
"""End-to-end check of ActinvR2SManager against OpenMC's own R2SManager on a small model.

A 20 cm cube of SS316-type steel with a 14.1 MeV point source at its centre, a 2x2x2 activation
mesh, one year of irradiation at 1e17 n/s, then 1e6 s of cooling. The shutdown photon dose rate
is tallied in a thin air shell at 30 cm. Both managers share step 1 (neutron transport) and the
photon transport settings; only the activation engine differs.

    OPENMC_CROSS_SECTIONS=... python demo_steel_cube.py --chain CHAIN.xml --actinv-bin ACTINV \
        --library LIB.npz --decay-primary D.dat --decay-fallback F.dat --out DIR [--particles N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import openmc
import openmc.deplete

sys.path.insert(0, str(Path(__file__).resolve().parent))
from actinv_openmc_r2s import ActinvR2SManager  # noqa: E402


DET_VOLUME_CM3 = 4.0 / 3.0 * np.pi * (31.0 ** 3 - 30.0 ** 3)


def build_model(particles: int) -> tuple[openmc.Model, openmc.RegularMesh]:
    steel = openmc.Material(name="SS316")
    steel.set_density("g/cm3", 7.93)
    for el, w in {"Fe": 65.06, "Cr": 17.5, "Ni": 12.25, "Mo": 2.5, "Mn": 1.8, "Si": 0.5, "Co": 0.05,
                  "Nb": 0.01, "Ta": 0.01, "Cu": 0.3, "C": 0.03}.items():
        steel.add_element(el, w, "wo")
    air = openmc.Material(name="air")
    air.set_density("g/cm3", 0.0012)
    air.add_element("N", 0.755, "wo")
    air.add_element("O", 0.245, "wo")
    box = openmc.model.RectangularParallelepiped(-10, 10, -10, 10, -10, 10)
    inner = openmc.Sphere(r=30.0)
    outer = openmc.Sphere(r=31.0)
    world = openmc.Sphere(r=40.0, boundary_type="vacuum")
    c_steel = openmc.Cell(fill=steel, region=-box)
    c_gap = openmc.Cell(region=+box & -inner)
    c_det = openmc.Cell(fill=air, region=+inner & -outer)
    c_out = openmc.Cell(region=+outer & -world)
    geom = openmc.Geometry([c_steel, c_gap, c_det, c_out])
    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = particles
    settings.batches = 5
    settings.source = openmc.IndependentSource(space=openmc.stats.Point(), energy=openmc.stats.Discrete([14.1e6], [1.0]))
    model = openmc.Model(geom, openmc.Materials([steel, air]), settings)
    mesh = openmc.RegularMesh()
    mesh.lower_left, mesh.upper_right, mesh.dimension = (-10, -10, -10), (10, 10, 10), (2, 2, 2)
    return model, mesh, c_det


def photon_model(model: openmc.Model, det: openmc.Cell, particles: int) -> openmc.Model:
    pm = model.clone() if hasattr(model, "clone") else openmc.Model(model.geometry, model.materials, openmc.Settings())
    pm.settings = openmc.Settings()
    pm.settings.run_mode = "fixed source"
    pm.settings.particles = particles
    pm.settings.batches = 5
    pm.settings.photon_transport = True
    energies, coeffs = openmc.data.dose_coefficients("photon", "AP")
    tally = openmc.Tally(name="dose")
    tally.filters = [openmc.CellFilter([det]), openmc.ParticleFilter(["photon"]),
                     openmc.EnergyFunctionFilter(energies, coeffs)]
    tally.scores = ["flux"]
    pm.tallies = openmc.Tallies([tally])
    return pm


def dose_pSv_s(mgr, index) -> tuple[float, float]:
    """Detector tally per source photon: dose coefficient (pSv·cm²) × track length (cm); divide by volume."""
    tally = mgr.results["photon_tallies"][index][0]
    mean, sd = float(tally.mean.sum()), float(np.sqrt((tally.std_dev ** 2).sum()))
    return mean, sd


def main():
    ap = argparse.ArgumentParser()
    for k in ("--chain", "--actinv-bin", "--library", "--decay-primary", "--decay-fallback", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--particles", type=int, default=20000)
    ap.add_argument("--arms", default="openmc,actinv")
    a = ap.parse_args()
    openmc.config["chain_file"] = a.chain
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    timesteps, rates = [365.25 * 86400.0, 1.0e6], [1.0e17, 0.0]
    report = {}
    for label in a.arms.split(","):
        model, mesh, det = build_model(a.particles)
        pm = photon_model(model, det, a.particles)
        if label == "actinv":
            mgr = ActinvR2SManager(model, mesh, pm, library=a.library, decay_primary=a.decay_primary,
                                   decay_fallback=a.decay_fallback, actinv_bin=a.actinv_bin)
        else:
            mgr = openmc.deplete.R2SManager(model, mesh, pm)
        t0 = time.monotonic()
        mgr.step1_neutron_transport(output_dir=out / label / "neutron")
        t1 = time.monotonic()
        mgr.step2_activation(timesteps, rates, output_dir=out / label / "activation")
        t2 = time.monotonic()
        mgr.step3_photon_transport(time_indices=[2], output_dir=out / label / "photon")
        t3 = time.monotonic()
        mean, sd = dose_pSv_s(mgr, 2)
        # dose tally is per source photon; OpenMC normalises the source mixture to its total strength
        strength = sum(src.strength for ms in mgr.get_decay_photon_source_mesh(2) for src in ms.sources)
        report[label] = {"step1_s": t1 - t0, "step2_s": t2 - t1, "step3_s": t3 - t2,
                         "photons_per_s": strength, "dose_per_photon_pSv_cm2": mean, "dose_sd": sd,
                         "dose_rate_uSv_h": mean / DET_VOLUME_CM3 * 3600 * 1e-6}  # OpenMC scales tallies by total source strength
        print(label, json.dumps(report[label], indent=1), flush=True)
    (out / f"report_{a.arms.replace(',', '_')}.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
