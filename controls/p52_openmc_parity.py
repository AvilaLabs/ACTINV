#!/usr/bin/env python3
"""P52 OpenMC parity arm — builds the one-cell deplete job and invokes the
sealed P45 driver (controls/p45_openmc_driver.py, sha pinned by
results/g0_p45_seals.json) under the openmc016 env python.

The driver already implements the qualified recipe:
MicroXS.from_multigroup_flux(energies, flux_norm) -> IndependentOperator ->
PredictorIntegrator, normalization_mode="source-rate" with
source_rates=[total_flux, 0, ...].
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENMC_PY = Path("/home/connoravila/micromamba/envs/openmc016/bin/python")
DRIVER = ROOT / "controls" / "p45_openmc_driver.py"
DRIVER_SEALED_SHA = ("4d7d751a4ac3d93f881b64ee9cadc11d37ef92ade865b3a0079d19"
                     "6d04e7cc9e")
CHAIN_XML = Path.home() / "nuclear-data/p32-work/chain/depletion/chain.xml"
XS_XML = Path.home() / "nuclear-data/endfb-viii.1-hdf5/cross_sections.xml"
BOUNDS = ROOT / "crates/actinv-data/data/fispact_709_groups.json"

IRR_S = 300.0
COOL_S = [86400.0, 2592000.0, 31536000.0]


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fns_flux_descending() -> list[float]:
    spec = json.loads((ROOT / "examples/fns_fe_5min.json").read_text())
    return spec["spectrum"]["flux_per_group"]


def run_openmc_parity(work: Path) -> dict:
    """Run the one-cell Fe56/FNS deplete arm; return the driver's record."""
    work.mkdir(parents=True, exist_ok=True)
    if sha256_file(DRIVER) != DRIVER_SEALED_SHA:
        raise SystemExit(
            "p45_openmc_driver.py drifted from its P45 seal — refusing to "
            "inherit its parity recipe")
    flux = fns_flux_descending()
    total = sum(flux)
    job = {
        "work_dir": str(work / "openmc"),
        "chain_file": str(CHAIN_XML),
        "bounds_file": str(BOUNDS),
        "reach_depth": 3,
        "groups": [{"name": "fns", "flux_descending": flux,
                    "total_flux": total}],
        "cases": [{
            "case": "fe56_parity", "group": "fns",
            "composition_isotope_fraction": {"Fe56": 1.0},
            "timesteps_s": [IRR_S] + COOL_S,
            "source_rates": [total] + [0.0] * len(COOL_S)}],
    }
    job_path = work / "p52_parity_job.json"
    job_path.write_text(json.dumps(job, indent=1))
    env = dict(__import__("os").environ)
    env["OPENMC_CROSS_SECTIONS"] = str(XS_XML)
    r = subprocess.run(
        [str(OPENMC_PY), str(DRIVER), str(job_path)],
        capture_output=True, text=True, timeout=3600, env=env)
    (work / "p52_parity_driver.stdout").write_text(r.stdout)
    (work / "p52_parity_driver.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise SystemExit(f"openmc parity driver failed: {r.stderr[-2000:]}")
    return json.loads((work / "openmc" / "openmc_results.json").read_text())


if __name__ == "__main__":
    out = run_openmc_parity(Path(sys.argv[1] if len(sys.argv) > 1
                                 else ROOT / "target/p52-parity"))
    print(json.dumps({"cases": list(out["cases"].keys()),
                      "reachable": out["reachable_nuclides"]}))
