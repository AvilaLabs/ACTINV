#!/usr/bin/env python3
"""Minimal R2S bridge: ACTINV as the activation step of an OpenMC workflow.

Mirrors the shape of openmc.deplete.R2SManager.run(): per-region groupwise
fluxes come from an OpenMC statepoint (MeshFilter + EnergyFilter tally),
the irradiation/decay timeline is (timesteps, source_rates), and the decay
photon source comes back as an OpenMC source module for the photon leg.

    r2s = ActinvR2S(material={"Fe": 100.0}, mass_g=1.0)
    r2s.import_flux("statepoint.100.h5", tally_id=42, source_rate=1e15)
    r2s.activate(timesteps=[300, 60, 300, 3600],
                 source_rates=[1.0, 0.0, 0.0, 0.0])
    r2s.photon_source(step=2)            # -> photon_source.py
    print(r2s.heat())                  # [(t_s, W/g), ...]

The flux file is imported once at a reference source rate; per-step rates map
to ACTINV schedule multipliers, so beam-on/off and ramp schedules work without
re-importing.

Requires `actinv` on PATH (or pass actinv=...), the data bundle fetched
(`actinv data fetch`), and either a catalog: library reference or ACTINV_DATA_DIR.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


class ActinvR2S:
    def __init__(self, *, material, mass_g=1.0, basis="wt_percent",
                 library="catalog:tendl-2025-neutron-709g",
                 decay_primary="catalog:endfb-viii-0-decay",
                 decay_fallback="catalog:jeff-3-3-decay",
                 actinv="actinv", workdir=".", temperature_K=293.6):
        self.material = dict(material)
        self.mass_g = mass_g
        self.basis = basis
        self.library = library
        self.decay = {"primary": decay_primary}
        if decay_fallback:
            self.decay["fallback"] = decay_fallback
        self.actinv = actinv
        self.workdir = Path(workdir)
        self.temperature_K = temperature_K
        self.flux_path = None
        self.result_path = None

    def _run(self, *args):
        cmd = [self.actinv, *args]
        out = subprocess.run(cmd, cwd=self.workdir, capture_output=True,
                             text=True, timeout=3600)
        if out.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed:\n{out.stderr or out.stdout}")
        return out.stdout

    def import_flux(self, statepoint, *, tally_id, source_rate,
                    out="flux.ndjson", energy_floor_eV=None):
        """Convert an OpenMC statepoint (MeshFilter+EnergyFilter flux tally)."""
        args = ["import-flux", "openmc", str(statepoint), out,
                "--tally", str(tally_id), "--source-rate", str(source_rate)]
        if energy_floor_eV is not None:
            args += ["--energy-floor-eV", str(energy_floor_eV)]
        summary = json.loads(self._run(*args))
        self.flux_path = self.workdir / out
        self._reference_rate = float(source_rate)
        return summary

    def _spec(self, timesteps, source_rates):
        sha = hashlib.sha256(self.flux_path.read_bytes()).hexdigest()
        ref = self._reference_rate
        schedule = [{"dt": f"{dt} s", "flux": rate / ref}
                    for dt, rate in zip(timesteps, source_rates)]
        return {
            "spec": "actinv-mesh-spec-1",
            "title": "ACTINV R2S activation",
            "projectile": "neutron",
            "library": {"path": self.library},
            "decay": self.decay,
            "material": {"mass_g": self.mass_g, "basis": self.basis,
                         "composition": self.material},
            "flux": {"path": self.flux_path.name, "sha256": sha},
            "schedule": schedule,
            "options": {"mode": "auto", "prune": "rate",
                        "bmin_atoms_per_g": 1e-8,
                        "temperature_K": self.temperature_K},
        }

    def activate(self, timesteps, source_rates, *, spec_out="mesh.json",
                 result_out="result.ndjson"):
        """Solve activation over the schedule. Rates are normalized to the
        source_rate used at import; 0.0 marks a decay step."""
        if self.flux_path is None:
            raise RuntimeError("import_flux first")
        if len(timesteps) != len(source_rates):
            raise ValueError("timesteps and source_rates must be the same length")
        spec_path = self.workdir / spec_out
        spec_path.write_text(json.dumps(self._spec(timesteps, source_rates),
                                        indent=2) + "\n")
        self._run("mesh", spec_out, result_out)
        self.result_path = self.workdir / result_out
        return self.result_path

    def photon_source(self, step, *, out="photon_source.py"):
        """Export the decay-photon source at a cooling step as an OpenMC module
        defining `sources` (list of openmc.IndependentSource)."""
        if self.result_path is None:
            raise RuntimeError("activate first")
        self._run("export-openmc-mesh", self.result_path.name, str(step), out)
        return self.workdir / out

    def _cells(self):
        if self.result_path is None:
            raise RuntimeError("activate first")
        for line in self.result_path.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("record") == "cell":
                yield rec

    def heat(self, cell=0):
        """[(t_s, total decay heat W/g)] for one cell."""
        for rec in self._cells():
            if rec["ordinal"] == cell:
                return [(s["t_s"], s["heat_W_per_g"]["total"])
                        for s in rec["result"]["steps"]]
        raise IndexError(f"no cell {cell}")

    def activity(self, cell=0, nuclide=None):
        """[(t_s, Bq/g)] for one cell, all nuclides or one."""
        for rec in self._cells():
            if rec["ordinal"] == cell:
                return [(s["t_s"], s["activity_Bq_per_g"].get(nuclide, 0.0)
                         if nuclide else sum(s["activity_Bq_per_g"].values()))
                        for s in rec["result"]["steps"]]
        raise IndexError(f"no cell {cell}")
