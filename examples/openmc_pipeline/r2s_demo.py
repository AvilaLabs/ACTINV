#!/usr/bin/env python3
"""R2S-shaped demo: ACTINV as the activation step of an OpenMC calculation.

Equivalent to openmc.deplete.R2SManager.run() for the activation + source legs:
runs the neutron model (or reuses a statepoint), solves activation on the
mesh, and emits an OpenMC decay-photon source module.

    python r2s_demo.py [--statepoint statepoint.100.h5]

Requires the same environment as model.py (openmc + OPENMC_CROSS_SECTIONS) for
the transport leg, and `actinv` + the fetched data bundle for activation.
"""
import argparse
import os
import sys

from actinv_r2s import ActinvR2S

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ.get(
    "ACTINV_LIBRARY",
    os.path.join(HERE, "..", "..", "actinv-data", "v1.1.0",
                 "activation", "tendl-2025-patched-neutron-709g.npz"))

SOURCE_RATE = 1e15      # n/s — a strong DT generator scale
TALLY_ID = 42

# 5 min beam-on, then cooling at 1 min, 5 min, 1 h, 24 h, 7 d elapsed.
TIMESTEPS = [300.0, 60.0, 240.0, 3600.0, 82800.0, 518400.0]
SOURCE_RATES = [SOURCE_RATE, 0.0, 0.0, 0.0, 0.0, 0.0]  # absolute n/s, as in R2SManager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statepoint", help="reuse an existing statepoint.N.h5")
    args = ap.parse_args()

    statepoint = args.statepoint
    if statepoint is None:
        import subprocess
        subprocess.run([sys.executable, os.path.join(HERE, "model.py"),
                        "--library", LIB, "--tally-id", str(TALLY_ID)],
                       check=True, cwd=HERE)
        statepoint = max((f for f in os.listdir(HERE)
                          if f.startswith("statepoint.") and f.endswith(".h5")),
                         key=lambda f: os.path.getmtime(os.path.join(HERE, f)))

    r2s = ActinvR2S(material={"Fe": 100.0}, mass_g=1.0,
                    actinv=os.environ.get("ACTINV", "actinv"), workdir=HERE)

    print("importing flux ...")
    summary = r2s.import_flux(statepoint, tally_id=TALLY_ID,
                              source_rate=SOURCE_RATE)
    print(f"  {summary['cells']} cell(s), {summary['groups']} groups, "
          f"total flux {summary['flux_sum_over_cells']:.3e} n/cm^2/s")

    print("solving activation ...")
    r2s.activate(TIMESTEPS, SOURCE_RATES)

    print("\n  t_elapsed [s]   heat [W/g]   activity [Bq/g]")
    for (t, h), (_, a) in zip(r2s.heat(), r2s.activity()):
        print(f"  {t:12.4g}  {h:12.4g}  {a:12.4g}")

    src = r2s.photon_source(step=2)
    print(f"\nphoton source for cooling step 2 -> {src}")
    print("import it inside an OpenMC photon run:  from photon_source import sources")


if __name__ == "__main__":
    main()
