#!/usr/bin/env python3
"""P54 fixture helpers — a `run`-spec builder on the P53 synthetic fixture
(2-group library, photon-bearing decay file, banded Mn56/Mn57 responses).
Mn56 is in the IAEA table (10 Bq/g); Mn57 is absent → exercises the
unregulated ledger.
"""
from __future__ import annotations

from pathlib import Path

import p53_fixture as p53fx


def run_spec(fxmap: dict, uncertainty: bool = True) -> dict:
    """A single-cell actinv run spec over the same synthetic library."""
    spec = {
        "spec": "actinv-spec-1",
        "title": "P54 synthetic banded run",
        "projectile": "neutron",
        "library": {"path": str(fxmap["library"]),
                    "sha256": p53fx.fx.sha256(fxmap["library"])},
        "decay": {"primary": str(fxmap["decay"])},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {"Fe56": 1.0}},
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": p53fx.BOUNDS,
            "flux_per_group": [1.0, 2.0],
            "total": 3.0e24,
            "descending": False,
        },
        "schedule": [{"dt": "0.7 s", "flux": 1.0},
                     {"dt": "0.2 s", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "none",
                    "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "cram_order": 16,
                    "outputs": ["inventory", "activity", "heat",
                                "photons", "ledger", "certificate"]},
    }
    if uncertainty:
        spec["uncertainty"] = {
            "covariance": {"path": str(fxmap["covariance"]),
                           "sha256": p53fx.fx.sha256(fxmap["covariance"])},
            "responses": ["activity:Mn56", "activity:Mn57"],
            "confidence_level": 0.95,
            "require_complete": True}
    return spec
