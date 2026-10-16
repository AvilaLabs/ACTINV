#!/usr/bin/env python3
"""P145 C2: writes the stainless-steel-like activated-metal case (problem and uncertainty spec).

The spectrum, library, decay files, options and the twelve cooling steps are copied from
examples/optimize_ra_steel/base_spec.json. One departure, chosen by hand estimate before any run: that example
irradiates for 300 s at a total flux of 1.1e10 n/cm2/s, so every Table 1 and Table 2 sum of a ppm-level steel is
orders of magnitude below its limit and no impurity level can reach a class boundary. The irradiation step's flux
multiplier IRRADIATION_MULTIPLIER scales that pulse (fluence 3.3e12 n/cm2 times the multiplier; the regime stays linear:
burn-up is below 1e-3). With 1e9, hand estimates from the library's one-group cross sections give at the last cooling
steps a Table 1 sum near 0.1 (Ni-59 about 0.09, Nb-94 about 0.02 for 100 ppm Nb, so Class B against Class C) and a
Table 2 Column 2 sum near 1 for Ni-63 between steps 12 and 13.

The coordinator tunes COMPOSITION_WT (the Co, Nb and Ni levels) and, if needed, IRRADIATION_MULTIPLIER with NOMINAL
waste runs so that one cooling step in TARGETS sits near a class boundary, commits the result, and changes nothing
afterwards.

    python3 controls/p145_c2_make.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "controls/p145_inputs"
BASE = ROOT / "examples/optimize_ra_steel/base_spec.json"

# --- parameters (weight percent; Fe closes the balance) ---------------------------------------------------------
COMPOSITION_WT = {"Cr": 18.0, "Ni": 8.0, "Mn": 1.5, "Co": 0.05, "Nb": 0.005}
IRRADIATION_MULTIPLIER = 1.0e4  # 1.1e14 n/cm2/s, an in-vessel flux level
IRRADIATION_DT = "31557600 s"  # one year
TARGETS = [10, 11, 12, 13]  # one-based steps: about 1, 4, 32 and 100 y after shutdown
MASS_G = 1000.0
DENSITY_G_CM3 = 7.9
SAMPLES = 100
SEED = 145002
FLUX_REL_STD = 0.05
COVARIANCE_PATH = "../../actinv-data/v1.1.0/uncertainty/tendl-2025-neutron-709g.cov.npz"
COVARIANCE_SHA256 = "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde"
OUTPUTS = ["inventory", "activity", "heat", "ledger", "certificate"]

NOTE = ("P145 C2. Co, Nb and Ni levels and the one-year irradiation were set with 20-sample development runs before "
        "the candidate commit, so that the 32-year Table 2 B/C sum is near 1 and the 100-year Table 1 sum straddles "
        "its Class A limit; nothing is tuned afterwards. Keys starting with an underscore are removed by "
        "controls/check_p145.py when it stages the case.")


def problem_doc() -> dict:
    base = json.loads(BASE.read_text())
    composition = dict(COMPOSITION_WT)
    composition["Fe"] = round(100.0 - sum(COMPOSITION_WT.values()), 6)
    schedule = [dict(step) for step in base["schedule"]]
    schedule[0]["flux"] = IRRADIATION_MULTIPLIER
    schedule[0]["dt"] = IRRADIATION_DT
    options = dict(base["options"])
    options["outputs"] = OUTPUTS
    return {
        "_note": NOTE,
        "spec": "actinv-spec-1",
        "title": "P145 C2: stainless-steel-like activated metal, Fe-Cr-Ni with Mn and Co/Nb impurities",
        "projectile": "neutron",
        "library": base["library"],
        "decay": base["decay"],
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": composition},
        "spectrum": base["spectrum"],
        "schedule": schedule,
        "options": options,
    }


def uncertainty_doc() -> dict:
    return {
        "_note": NOTE,
        "schema": "actinv-waste-uncertainty-spec-1",
        "problem": "c2_steel_problem.json",
        "rules": "us-nrc-10cfr61.55-v1",
        "targets": TARGETS,
        "nuclide_properties": "from_decay_data",
        "components": [{"id": "steel-block", "mass_g": MASS_G, "density_g_cm3": DENSITY_G_CM3,
                        "waste_type": "activated_metal", "external_tritium": {"status": "not_applicable"}}],
        "sampling": {"channels": {"cross_section_mf33": True, "flux_rel_std": FLUX_REL_STD,
                                  "decay_constants": True},
                     "covariance": {"path": COVARIANCE_PATH, "sha256": COVARIANCE_SHA256},
                     "samples": SAMPLES, "seed": SEED},
        "confidence_level": 0.95,
    }


if __name__ == "__main__":
    INPUTS.mkdir(parents=True, exist_ok=True)
    for name, doc in (("c2_steel_problem.json", problem_doc()), ("c2_steel.json", uncertainty_doc())):
        (INPUTS / name).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print("wrote", INPUTS / name)
