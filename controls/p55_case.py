#!/usr/bin/env python3
"""P55 shared case machinery — the synthetic banded run spec, truth
synthesis, and the reverse-qualified driver used by every P55 gate.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_spec(fxmap: dict, multipliers=(1.0, 1.0), sha=None) -> dict:
    """Two-segment irradiation problem on the P53 fixture data."""
    return {
        "spec": "actinv-spec-1",
        "title": "p55 case",
        "projectile": "neutron",
        "library": {"path": str(fxmap["library"]),
                    "sha256": sha(fxmap["library"])},
        "decay": {"primary": str(fxmap["decay"])},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": {"Fe56": 1.0}},
        "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0],
                     "flux_per_group": [1.0, 2.0], "total": 3.0,
                     "descending": False},
        "schedule": [{"dt": "0.7 s", "flux": multipliers[0]},
                     {"dt": "0.2 s", "flux": 0.0},
                     {"dt": "0.4 s", "flux": multipliers[1]},
                     {"dt": "0.6 s", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "none",
                    "bmin_atoms_per_g": 0.0, "temperature_K": 293.6,
                    "cram_order": 16,
                    "outputs": ["inventory", "activity", "heat",
                                "ledger", "certificate"]},
        "uncertainty": {
            "covariance": {"path": str(fxmap["covariance"]),
                           "sha256": sha(fxmap["covariance"])},
            "responses": ["activity:Mn56", "activity:Mn57"],
            "confidence_level": 0.95,
            "require_complete": True},
    }


def forward(actinv: Path, spec: dict, work: Path, name: str) -> dict:
    sp = work / f"{name}.json"
    out = work / f"{name}.out.json"
    sp.write_text(json.dumps(spec, sort_keys=True) + "\n")
    r = subprocess.run([str(actinv), "run", str(sp), str(out)],
                       cwd=ROOT, text=True, capture_output=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"forward {name} failed: {r.stderr[-2000:]}")
    return json.loads(out.read_text())


def qualified(actinv: Path, problem: Path, meas: dict, work: Path,
              name: str):
    mp = work / f"{name}.meas.json"
    mp.write_text(json.dumps(meas, sort_keys=True) + "\n")
    out = work / f"{name}.ndjson"
    r = subprocess.run(
        [str(actinv), "reverse-qualified", str(problem), str(mp), str(out)],
        cwd=ROOT, text=True, capture_output=True, timeout=3600)
    return r.returncode, r.stderr + r.stdout, out


def measurements_from(fwd: dict, rel_sigma=0.01,
                      wobble=(1.02, 0.98, 1.0)) -> dict:
    """Three planted measurements: both nuclides at the last step plus
    Mn56 at the mid cooling step (the second is causal-zero for segment 2)."""
    by_step = {s["step"]: s for s in fwd["steps"]}
    last = max(by_step)
    vals = [
        by_step[last]["activity_Bq_per_g"]["Mn56"],
        by_step[last]["activity_Bq_per_g"]["Mn57"],
        by_step[2]["activity_Bq_per_g"]["Mn56"],
    ]
    return {"format": "actinv-reverse-input-1", "measurements": [
        {"step": s, "nuclide": n, "activity_Bq_per_g": v * w,
         "sigma_Bq_per_g": v * rel_sigma}
        for (s, n, v, w) in zip(
            [last, last, 2], ["Mn56", "Mn57", "Mn56"], vals, wobble)]}
