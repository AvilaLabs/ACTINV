#!/usr/bin/env python3
"""OpenMC model for the ACTINV integration example.

A 14.1 MeV point neutron source irradiates a 1 cm iron cube 10 cm away in
vacuum. The tally is exactly what `actinv import-flux openmc` accepts: a
MeshFilter (a single mesh cell fitted to the cube) plus an EnergyFilter whose
bins are the activation library's own 709-group boundaries, read straight from
the ACTINV data bundle so the two tools can never disagree on group structure.

Usage:
    python model.py --library actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz

Writes `statepoint.<N>.h5` in the current directory. Requires openmc >= 0.14
and an HDF5 cross-section library (set OPENMC_CROSS_SECTIONS, e.g. to an
ENDF/B-VIII.1 HDF5 distribution's cross_sections.xml).
"""
import argparse
import io
import zipfile

import numpy as np
import openmc


def library_group_edges(npz_path):
    """Read the activation library's energy boundaries (ascending eV)."""
    with zipfile.ZipFile(npz_path) as zf:
        return np.load(io.BytesIO(zf.read("bounds.npy")))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--library", required=True,
                    help="Path to the ACTINV neutron bundle .npz (provides the 709 group edges)")
    ap.add_argument("--particles", type=int, default=1_000_000)
    ap.add_argument("--batches", type=int, default=100)
    ap.add_argument("--tally-id", type=int, default=42)
    args = ap.parse_args()

    edges = library_group_edges(args.library)

    iron = openmc.Material(name="iron")
    iron.add_element("Fe", 1.0)
    iron.set_density("g/cm3", 7.874)

    # 1 cm Fe cube centred at (10, 0, 0); vacuum elsewhere.
    cube = openmc.model.RectangularParallelepiped(9.5, 10.5, -0.5, 0.5, -0.5, 0.5)
    world = openmc.Sphere(r=200.0, boundary_type="vacuum")
    cell_fe = openmc.Cell(fill=iron, region=-cube)
    cell_void = openmc.Cell(region=+cube & -world)
    geom = openmc.Geometry([cell_fe, cell_void])

    src = openmc.IndependentSource()
    src.space = openmc.stats.Point((0.0, 0.0, 0.0))
    src.angle = openmc.stats.Isotropic()
    src.energy = openmc.stats.Discrete([14.1e6], [1.0])
    src.particle = "neutron"

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = args.particles
    settings.batches = args.batches
    settings.source = src

    # One mesh cell fitted exactly to the cube — tracklength flux averages
    # over the iron volume, not void.
    mesh = openmc.RegularMesh()
    mesh.lower_left = (9.5, -0.5, -0.5)
    mesh.upper_right = (10.5, 0.5, 0.5)
    mesh.dimension = (1, 1, 1)

    tally = openmc.Tally(tally_id=args.tally_id, name="cube_flux")
    tally.filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter(edges)]
    tally.scores = ["flux"]
    tally.estimator = "tracklength"

    model = openmc.Model(geometry=geom, settings=settings, tallies=openmc.Tallies([tally]))
    sp = model.run()
    print(f"wrote {sp}  (tally id {args.tally_id})")


if __name__ == "__main__":
    main()
