#!/usr/bin/env python3
"""P45 frozen population: variant campaign cases + mesh spectra.

Deterministic builder for the two frozen workloads of
protocols/ACTINV-P45_PROTOCOL.md. Emits a population document consumed
by the sealed arms/harness; every byte here is sealed at G0.

- Campaign: 28-case stratified subset of the P26b W-CAMPAIGN
  product_plus_data-executable population.
- Mesh: 16 cells, pure Fe, spectra = fns_709 * (1 + 0.5*sin(2*pi*i*g/709
  + i*0.7)) rescaled to the fns_709 total.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
from g3_p26_headroom import fns_spectrum  # noqa: E402
from g2_p26b_contract import sha256_array, sha256_file  # noqa: E402

CONTRACT_PATH = ROOT / "results" / "g2_p26b_leg_contract.json"
OUT_PATH = ROOT / "results" / "p45_population.json"

COOLING_TIMES_S = [0.0, 86400.0, 2592000.0, 31536000.0, 315360000.0]
DOPANTS = ["co", "cr", "mn", "v"]
LEVELS_WPPM = [10, 1000, 100000]
IRR_S = [60.0, 86400.0]
EXTRA_CASES = [
    "fe__fns_709__pulse_5min",
    "fe__fns_709__cont_2y",
    "fe_co100wppm__fns_709__pulse_5min",
    "fe_co100wppm__fns_709__cont_2y",
]
N_MESH_CELLS = 16
MESH_IRR_S = 300.0
MESH_MATERIAL = "fe__fns_709__pulse_5min"  # composition source case


def campaign_cases(contract: dict) -> list[dict]:
    by_name = {c["case"]: c for c in contract["cases"]}
    names = []
    for d in DOPANTS:
        for lv in LEVELS_WPPM:
            for irr in IRR_S:
                names.append(f"fe_{d}{lv}wppm__fns_709__{int(irr)}s")
    names += EXTRA_CASES
    out = []
    for n in names:
        c = by_name.get(n)
        if c is None:
            raise SystemExit(f"campaign case {n} absent from contract")
        if c["legs"]["product_plus_data"]["status"] != "executable":
            raise SystemExit(f"campaign case {n} not executable: "
                             f"{c['legs']['product_plus_data']}")
        out.append(c)
    return out


def mesh_cells(contract: dict) -> list[dict]:
    base = {c["case"]: c for c in contract["cases"]}[MESH_MATERIAL]
    fns = fns_spectrum()
    total = sum(fns)
    cells = []
    for i in range(N_MESH_CELLS):
        phi = [fns[g] * (1.0 + 0.5 * math.sin(2.0 * math.pi * i * g / 709.0
                                              + i * 0.7))
               for g in range(709)]
        scale = total / sum(phi)
        phi = [v * scale for v in phi]
        cells.append({
            "cell": i,
            "case": f"cell_{i:02d}",
            "composition_wt_fraction": dict(
                base["composition_wt_fraction"]),
            "required_isotopes": base["required_isotopes"],
            "irradiation_s": MESH_IRR_S,
            "spectrum": "mesh_cell",
            "flux_descending": phi,
            "flux_sha256": sha256_array(phi),
        })
    return cells


def main() -> int:
    contract = json.loads(CONTRACT_PATH.read_text())
    cases = campaign_cases(contract)
    cells = mesh_cells(contract)
    pop = {
        "schema": "actinv-p45-population-1",
        "protocol": "protocols/ACTINV-P45_PROTOCOL.md",
        "contract": {"file": "results/g2_p26b_leg_contract.json",
                     "sha256": sha256_file(CONTRACT_PATH)},
        "cooling_times_s": COOLING_TIMES_S,
        "campaign": {
            "n_cases": len(cases),
            "case_names": [c["case"] for c in cases],
            "cases": cases,
        },
        "mesh": {
            "n_cells": len(cells),
            "irradiation_s": MESH_IRR_S,
            "schedule_flux": [1.0],
            "cells": cells,
        },
    }
    OUT_PATH.write_text(json.dumps(pop, indent=1))
    print(json.dumps({
        "campaign_cases": len(cases), "mesh_cells": len(cells),
        "population_sha256": sha256_file(OUT_PATH),
        "out": str(OUT_PATH)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
