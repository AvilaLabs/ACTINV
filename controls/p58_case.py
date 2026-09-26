#!/usr/bin/env python3
"""P58 shared case machinery — banded isomer-channel spec on the P58
fixture plus result extraction helpers used by every P58 gate.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def spec(fxmap: dict, isomer=True, channels=("cross_section_mf33",
                                             "decay_constants")) -> dict:
    uncertainty = {
        "covariance": {"path": str(fxmap["covariance"]),
                       "sha256": sha(fxmap["covariance"])},
        "responses": ["activity:Mn57", "activity:Mn57m1", "heat.total"],
        "channels": list(channels),
        "confidence_level": 0.95,
        "require_complete": False,
    }
    if isomer:
        uncertainty["isomer"] = {}
    return {
        "spec": "actinv-spec-1", "title": "p58 case", "projectile": "neutron",
        "library": {"path": str(fxmap["library"]),
                    "sha256": sha(fxmap["library"])},
        "decay": {"primary": str(fxmap["decay"])},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {"Fe56": 1.0}},
        "spectrum": {"structure": "custom",
                     "boundaries_eV": [1.0, 2.0, 3.0],
                     "flux_per_group": [1.0, 2.0], "total": 3.0,
                     "descending": False},
        "schedule": [{"dt": "0.7 s", "flux": 1.0},
                     {"dt": "0.2 s", "flux": 0.0},
                     {"dt": "0.4 s", "flux": 1.0},
                     {"dt": "0.6 s", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "none",
                    "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "cram_order": 16,
                    "outputs": ["inventory", "activity", "heat",
                                "ledger", "certificate", "pathways"]},
        "uncertainty": uncertainty,
    }


def run(actinv: Path, specdoc: dict, work: Path, name: str) -> dict:
    sp = work / f"{name}.spec.json"
    out = work / f"{name}.out.json"
    sp.write_text(json.dumps(specdoc, sort_keys=True) + "\n")
    r = subprocess.run([str(actinv), "run", str(sp), str(out)],
                       cwd=ROOT, text=True, capture_output=True,
                       timeout=600)
    if r.returncode:
        raise RuntimeError(f"run {name} failed: {r.stderr[-2000:]}")
    return json.loads(out.read_text())


def isomer_block(result: dict, response: str, step=-1) -> dict:
    return result["steps"][step]["uncertainty"]["responses"][response]["isomer"]


def covered_sensitivities(response: dict) -> list:
    """xs sensitivity records that entered the covariance partition, in
    emitted order — the same set `covered_parameter_positions` selects."""
    return [s for s in response["sensitivities"]
            if s["parameter"]["covariance_covered"]
            and not s["parameter"]["covariance_excluded"]]
