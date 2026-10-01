"""ACTINV as the activation engine of OpenMC's rigorous two-step (R2S) workflow.

``ActinvR2SManager`` is a drop-in subclass of ``openmc.deplete.R2SManager`` (OpenMC >= 0.15.3).
OpenMC keeps neutron transport (step 1) and photon transport (step 3); ACTINV replaces the
activation solve (step 2) and supplies every region's decay-photon source::

    import openmc
    from actinv_openmc_r2s import ActinvR2SManager

    r2s = ActinvR2SManager(model, mesh, library="actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz",
                           decay_primary="actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat",
                           decay_fallback="actinv-data/v1.1.0/decay/jeff-3-3_decay.dat")
    r2s.run(timesteps=[3.15e7, 1e6], source_rates=[1e17, 0.0], photon_time_indices=[2],
            micro_kwargs={"chain_file": "chain.xml"})

How it fits OpenMC's own code path:

- Step 1 is OpenMC's, except that the flux tally uses the ACTINV library's exact group boundaries,
  so the spectrum reaches ACTINV without rebinning.
- Step 2 writes one ACTINV flux record per activation region (a mesh element × material
  combination, or a cell), in the same order OpenMC enumerates its activation materials, and
  solves them all with a single ``actinv mesh`` call: the library is loaded once and each region
  gets its own material and absolute mass.
- Step 3 is OpenMC's own code, unmodified. It asks ``results[t].get_material(id)
  .get_decay_photon_energy()`` for each region, and a small results shim answers from ACTINV's
  24-group decay-photon source (photons/s at the group centroids). Cell-based and mesh-based
  R2S both work because OpenMC's step 3 handles both.

Result index 0 is the pre-irradiation state (no source), as in OpenMC; index i is after
schedule step i. OpenMC's ``get_microxs_and_flux`` still needs a depletion chain in step 1 (it
also tallies reaction rates ACTINV does not use): pass ``micro_kwargs={"chain_file": ...}`` or
set ``openmc.config['chain_file']``.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import openmc
import openmc.deplete
from openmc.deplete.r2s import get_activation_materials

_UNITS_S = {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0, "d": 86400.0,
            "a": 365.25 * 86400.0}
_OPENMC_NUCLIDE = re.compile(r"^([A-Z][a-z]?)(\d+)(?:_m(\d+))?$")


def library_bounds(library: str | os.PathLike) -> np.ndarray:
    """Ascending group boundaries (eV) stored in an ACTINV activation library (.npz)."""
    with zipfile.ZipFile(library) as z:
        return np.load(io.BytesIO(z.read("bounds.npy")))


def material_to_actinv(material: "openmc.Material", volume_cm3: float) -> dict:
    """OpenMC material -> ACTINV material with explicit nuclides (atoms per gram) and absolute mass."""
    density = material.get_mass_density()
    if not density > 0.0:
        raise ValueError(f"material {material.id} has no positive mass density")
    composition = {}
    for nuclide, atoms_b_cm in material.get_nuclide_atom_densities().items():
        m = _OPENMC_NUCLIDE.match(nuclide)
        if m is None:
            raise ValueError(f"cannot map OpenMC nuclide name {nuclide!r} to ACTINV")
        sym, a, iso = m.groups()
        key = f"{sym}{a}" + (f"m{iso}" if iso else "")
        composition[key] = composition.get(key, 0.0) + atoms_b_cm * 1.0e24 / density
    return {"mass_g": volume_cm3 * density, "basis": "atoms_per_g", "composition": composition}


class _PhotonMaterial:
    """What OpenMC's step 3 needs from an activated material: its decay-photon energy distribution."""

    def __init__(self, groups):
        self._groups = groups  # [(centroid_eV, photons_s)] or None

    def get_decay_photon_energy(self, *args, **kwargs):
        if not self._groups:
            return None
        energies, strengths = zip(*self._groups)
        return openmc.stats.Discrete(list(energies), list(strengths))


class _Step:
    def __init__(self, by_material):
        self._by_material = by_material

    def get_material(self, mat_id: str):
        return _PhotonMaterial(self._by_material.get(str(mat_id)))


class _Results(list):
    """Stand-in for openmc.deplete.Results: index 0 = before irradiation, i = after step i."""


def first_tally_nuclide(materials):
    """First nuclide found in any of ``materials``, in order.

    Used to pick step 1's throwaway tally nuclide (see ``ActinvR2SManager.step1_neutron_transport``).
    Materials that raise on ``get_nuclide_atom_densities()`` (e.g. an unfilled/void material) are
    skipped rather than propagating. Raises ``ValueError`` with an adapter-specific message,
    rather than letting a bare ``next(iter(...))`` raise ``StopIteration``, if none of the given
    materials has any nuclide at all.
    """
    for mat in materials:
        try:
            nuclides = mat.get_nuclide_atom_densities()
        except Exception:
            continue
        for name in nuclides:
            return name
    raise ValueError(
        "step1_neutron_transport: no material in the model has any nuclide to tally for the "
        "slimmed step-1 flux tally; pass micro_kwargs={'nuclides': [...], 'reactions': [...]} "
        "explicitly"
    )


def build_schedule(timesteps, source_rates, timestep_units="s"):
    """ACTINV schedule entries (durations in seconds, flux relative to a reference source rate).

    ``source_rates`` may be a single value (applied to every timestep, matching OpenMC's own
    convention) or one value per timestep. The reference rate is the first positive rate in the
    sequence; every schedule entry's "flux" multiplier is its own rate divided by that reference,
    so this assumes a single, constant neutron spectral shape that only scales up and down with
    source strength (irradiation steps share one reference rate; cooling steps use 0.0). Returns
    ``(schedule, reference)``. Raises ``ValueError`` if no timestep has a positive source rate,
    since there would then be no irradiation to activate anything (a pure-decay-only schedule is
    not a useful R2S run and silently defaulting the reference to 1.0 would hide that).
    """
    rates = [float(r) for r in (source_rates if np.iterable(source_rates) else [source_rates] * len(timesteps))]
    durations = []
    for step in timesteps:
        value, unit = step if isinstance(step, tuple) else (step, timestep_units)
        if unit not in _UNITS_S:
            raise ValueError(f"unsupported timestep unit {unit!r}")
        durations.append(float(value) * _UNITS_S[unit])
    if not any(r > 0.0 for r in rates):
        raise ValueError("build_schedule: at least one timestep must have a positive source rate "
                         "(every entry in source_rates was zero or negative)")
    reference = next(r for r in rates if r > 0.0)
    schedule = [{"dt": f"{d!r} s", "flux": r / reference} for d, r in zip(durations, rates)]
    return schedule, reference


def flux_record_lines(bounds, region_names, region_volumes, fluxes, reference):
    """``actinv-flux-1`` NDJSON lines (header, one cell record per region, footer).

    ``fluxes[i]`` is OpenMC's per-group track length per source particle for region ``i``
    (n·cm / source particle); dividing by the region volume and scaling by ``reference`` (see
    ``build_schedule``) converts it to a physical flux in n cm^-2 s^-1.
    """
    bounds = [float(b) for b in bounds]
    lines = [json.dumps({"record": "header", "schema": "actinv-flux-1",
                         "source": {"format": "openmc.deplete.get_microxs_and_flux", "path": "memory",
                                    "sha256": "0" * 64},
                         "energy_boundaries_eV": bounds, "flux_units": "n cm^-2 s^-1",
                         "cell_count": len(region_volumes)})]
    total = 0.0
    for i, (vol, flux) in enumerate(zip(region_volumes, fluxes)):
        if not vol or vol <= 0.0:
            raise ValueError(f"activation region {i} ({region_names[i]}) has no volume")
        phi = np.asarray(flux, dtype=float).ravel() / vol * reference
        if phi.size != len(bounds) - 1:
            raise RuntimeError(f"region {i}: {phi.size} flux groups, library has {len(bounds) - 1}")
        lines.append(json.dumps({"record": "cell", "ordinal": i, "id": str(i),
                                 "flux_per_group": [float(x) for x in phi], "flux_total": float(phi.sum())}))
        total += float(phi.sum())
    lines.append(json.dumps({"record": "footer", "cell_count": len(region_volumes), "flux_sum_over_cells": total}))
    return lines


def parse_photon_groups(result_path, mats, n_steps):
    """Per-schedule-step photon groups keyed by activation-material id, from an ``actinv mesh``
    result NDJSON. Each ``actinv mesh`` cell record's ``id`` field round-trips the region index
    (as a string) written into the flux NDJSON by ``flux_record_lines``, so ``mats[int(id)]``
    recovers the OpenMC activation material for that region.
    """
    per_step = [dict() for _ in range(n_steps)]
    with open(result_path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") != "cell":
                continue
            mat_id = str(mats[int(rec["id"])].id)
            for k, step in enumerate(rec["result"]["steps"]):
                groups = [(g["centroid_eV"], g["photons_s"])
                          for g in (step.get("photon_source") or {}).get("groups", []) if g["photons_s"] > 0.0]
                per_step[k][mat_id] = groups or None
    return per_step


def build_results_shim(per_step):
    """``_Results`` with index 0 = pre-irradiation (no source), index i = after schedule step i."""
    return _Results([_Step({})] + [_Step(m) for m in per_step])


def require_activation_regions(mats):
    """Guard against a zero-region schedule (see ``ActinvR2SManager.step2_activation``).

    A zero-length ``mats`` means the mesh/cell domain produced no activation region at all
    (e.g. an all-void domain), in which case there is nothing to solve; raises ``ValueError``
    rather than letting the ACTINV mesh spec silently substitute a placeholder material.
    """
    if len(mats) == 0:
        raise ValueError("step2_activation: no activation regions found (the mesh/cell domain "
                         "has no material to activate); nothing to solve")


class ActinvR2SManager(openmc.deplete.R2SManager):
    def __init__(self, neutron_model, domains, photon_model=None, *, library, decay_primary,
                 decay_fallback=None, actinv_bin="actinv", options=None, photon=None, threads=1):
        super().__init__(neutron_model, domains, photon_model)
        self.library = str(Path(library).resolve())
        self.decay_primary = str(Path(decay_primary).resolve())
        self.decay_fallback = str(Path(decay_fallback).resolve()) if decay_fallback else None
        self.actinv_bin = actinv_bin
        self.options = {"mode": "auto", "prune": "rate", **(options or {})}
        self.options["outputs"] = sorted(set(self.options.get("outputs", [])) | {"photons", "ledger"})
        self.photon = photon or {}
        self.threads = threads

    # -- step 1: OpenMC's, with the tally on ACTINV's exact group boundaries --------------------
    def step1_neutron_transport(self, output_dir="neutron_transport", mat_vol_kwargs=None, micro_kwargs=None):
        micro_kwargs = dict(micro_kwargs or {})
        micro_kwargs.setdefault("energies", list(library_bounds(self.library)))
        # OpenMC also tallies per-nuclide reaction rates here for its own depletion solver; ACTINV
        # uses only the flux. With 709 groups the default (every chain nuclide x every reaction)
        # runs to gigabytes, so tally one throwaway rate unless the caller asks otherwise. Prefer a
        # nuclide from the actual activation-region materials (the cell domain, for cell-based R2S);
        # for mesh-based R2S those aren't known until material_volumes runs inside step 1, so fall
        # back to the first material anywhere in the model that has a nuclide.
        if "nuclides" not in micro_kwargs:
            domain_mats = [cell.fill for cell in self.domains if cell.fill is not None] \
                if self.method == "cell-based" else []
            micro_kwargs["nuclides"] = [first_tally_nuclide(domain_mats + list(self.neutron_model.materials))]
            micro_kwargs.setdefault("reactions", ["(n,gamma)"])
        return super().step1_neutron_transport(output_dir, mat_vol_kwargs, micro_kwargs)

    # -- step 2: ACTINV --------------------------------------------------------------------------
    def step2_activation(self, timesteps, source_rates, timestep_units="s", output_dir="activation",
                         operator_kwargs=None):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.method == "mesh-based":
            mats = get_activation_materials(self.neutron_model, self.results["mesh_material_volumes"])
        else:
            mats = openmc.Materials()
            for cell in self.domains:
                mat = cell.fill.clone()
                mat.name = f"Cell {cell.id}"
                mat.volume = cell.volume
                mats.append(mat)
        require_activation_regions(mats)
        self.results["activation_materials"] = mats
        fluxes = self.results["fluxes"]
        if len(fluxes) != len(mats):
            raise RuntimeError(f"{len(fluxes)} flux vectors for {len(mats)} activation regions")

        # schedule: durations in seconds; ACTINV multipliers relative to a reference source rate
        schedule, reference = build_schedule(timesteps, source_rates, timestep_units)

        # flux records: OpenMC reports n-cm per source particle for each region
        bounds = library_bounds(self.library)
        lines = flux_record_lines(bounds, [mat.name for mat in mats], [mat.volume for mat in mats],
                                  fluxes, reference)
        materials = {str(i): material_to_actinv(mat, mat.volume) for i, mat in enumerate(mats)}
        flux_path = output_dir / "flux.ndjson"
        flux_path.write_text("\n".join(lines) + "\n")

        spec = {
            "spec": "actinv-mesh-spec-1", "title": "ACTINV activation for OpenMC R2S", "projectile": "neutron",
            "library": {"path": self.library},
            "decay": {"primary": self.decay_primary, **({"fallback": self.decay_fallback} if self.decay_fallback else {})},
            "material": materials["0"],
            "materials": materials,
            "flux": {"path": str(flux_path.resolve()),
                     "sha256": hashlib.sha256(flux_path.read_bytes()).hexdigest()},
            "schedule": schedule, "options": self.options, "photon": self.photon,
            "threads": self.threads,
            # P90: step 3 only reads photon_source.groups (parse_photon_groups below), so the
            # mesh output no longer carries the full inventory/activity/heat per step.
            "cell_result_fields": ["mode", "ledger", "steps.photon_source.groups"],
        }
        spec_path = output_dir / "mesh.json"
        spec_path.write_text(json.dumps(spec, indent=1))
        result_path = output_dir / "mesh_result.ndjson"
        proc = subprocess.run([self.actinv_bin, "mesh", str(spec_path), str(result_path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"actinv mesh failed:\n{proc.stderr[-4000:]}")

        # photon groups per region and step -> results shim keyed by activation-material id
        per_step = parse_photon_groups(result_path, mats, len(schedule))
        self.results["depletion_results"] = build_results_shim(per_step)
        self.results["actinv_mesh_result"] = result_path
