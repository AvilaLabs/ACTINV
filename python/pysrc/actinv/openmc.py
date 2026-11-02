"""ACTINV as the activation engine of OpenMC's rigorous two-step (R2S) workflow.

``ActinvR2SManager`` is a drop-in subclass of ``openmc.deplete.R2SManager`` (OpenMC >= 0.15.3).
OpenMC keeps neutron transport (step 1) and photon transport (step 3); ACTINV replaces the
activation solve (step 2) and supplies every region's decay-photon source::

    import openmc
    from actinv.openmc import ActinvR2SManager

    r2s = ActinvR2SManager(model, mesh)   # data and binary come from the installed actinv
    r2s.run(timesteps=[3.15e7, 1e6], source_rates=[1e17, 0.0], photon_time_indices=[2])

With no arguments beyond the model and domains, the library and decay data are the default
bundle of the installed data catalog (``actinv data fetch``; ``$ACTINV_DATA_DIR`` or
``./actinv-data``) and the solver is the ``actinv`` CLI that ships with this package. Pass
``library``, ``decay_primary``, ``decay_fallback`` or ``actinv_bin`` to override.

How it fits OpenMC's own code path:

- Step 1 is OpenMC's mesh/cell setup, with a flux-only tally on the ACTINV library's exact group
  boundaries, so the spectrum reaches ACTINV without rebinning and no depletion chain is needed.
- Step 2 writes one ACTINV flux record per activation region (a mesh element x material
  combination, or a cell), in the same order OpenMC enumerates its activation materials, and
  solves them all with a single ``actinv mesh`` call: the library is loaded once and each region
  gets its own material and absolute mass.
- Step 3 is OpenMC's own code, unmodified. It asks ``results[t].get_material(id)
  .get_decay_photon_energy()`` for each region, and a small results shim answers from ACTINV's
  24-group decay-photon source (photons/s at the group centroids). Cell-based and mesh-based
  R2S both work because OpenMC's step 3 handles both.

Result index 0 is the pre-irradiation state (no source), as in OpenMC; index i is after
schedule step i. Passing ``micro_kwargs={"chain_file": ...}`` (or ``nuclides``/``reactions``)
to step 1 runs OpenMC's own ``get_microxs_and_flux`` instead, as the contrib adapter did.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from . import _defaults

try:
    import numpy as np
    import openmc
    import openmc.deplete
    import openmc.deplete.r2s as _r2s
    import openmc.lib
    from openmc.deplete.microxs import MicroXS
    from openmc.deplete.r2s import get_activation_materials
    from openmc.mpi import comm
    if not hasattr(openmc.deplete, "R2SManager"):
        raise ImportError("this OpenMC has no openmc.deplete.R2SManager")
except ImportError as exc:
    raise ImportError(f"actinv.openmc needs OpenMC 0.15.3 or newer: {exc}. "
                      "Install it (for example `conda install -c conda-forge openmc`).") from exc

_UNITS_S = {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0, "d": 86400.0,
            "a": 365.25 * 86400.0}
# step-1 keywords that mean "run OpenMC's own get_microxs_and_flux" (needs a depletion chain)
_OPENMC_MICRO_KEYS = {"chain_file", "nuclides", "reactions", "reaction_rate_mode"}
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
    ``(schedule, reference)``. Raises ``ValueError`` if any source rate is negative or no timestep has a positive source rate,
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
    if any(r < 0.0 for r in rates):
        raise ValueError("build_schedule: source rates must not be negative")
    if not any(r > 0.0 for r in rates):
        raise ValueError("build_schedule: at least one timestep must have a positive source rate "
                         "(every entry in source_rates was zero)")
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


def flux_only_microxs_and_flux(model, domains, energies=None, path_statepoint=None, path_input=None,
                               run_kwargs=None, include_model_tallies=False):
    """Flux-only stand-in for ``openmc.deplete.get_microxs_and_flux`` (OpenMC 0.15.3, microxs.py).

    OpenMC 0.16 adds ``include_model_tallies``: its step 1 passes True and then reads the model's
    own tallies from the statepoint, so they are kept in the run beside the flux tally.

    ``get_microxs_and_flux`` always resolves a depletion chain (``_get_chain``, line 121) and then
    tallies per-nuclide reaction rates or builds cross sections from the chain; it cannot skip
    that, so ACTINV users would need a chain file ACTINV never reads. This runs the same transport
    (lines 151-154 and 164-203: a flux tally on ``domains`` x ``energies``, statepoint and model
    XML moved to ``path_statepoint``/``path_input``) and returns the same ``(fluxes, micros)`` pair,
    ``fluxes`` in n-cm per source particle per domain. ``micros`` are all-zero one-entry
    placeholders: R2SManager only stores them (``load_results`` expects ``micros.h5`` beside
    ``fluxes.npy``) and ``ActinvR2SManager.step2_activation`` never reads them.
    """
    original_tallies = list(model.tallies)
    energy_filter = openmc.EnergyFilter(energies if energies is not None else [0.0, 100.0e6])
    if isinstance(domains, openmc.Filter):
        domain_filters = [domains]
    elif isinstance(domains[0], openmc.Filter):  # OpenMC 0.16: one mesh-material filter per mesh
        domain_filters = list(domains)
    elif isinstance(domains[0], openmc.Cell):
        domain_filters = [openmc.CellFilter(domains)]
    elif isinstance(domains[0], openmc.Material):
        domain_filters = [openmc.MaterialFilter(domains)]
    else:
        raise ValueError(f"Unsupported domain type: {type(domains[0])}")

    flux_tallies = []
    for i, domain_filter in enumerate(domain_filters):
        flux_tally = openmc.Tally(name=f"ACTINV flux {i}")
        flux_tally.filters = [domain_filter, energy_filter]
        flux_tally.scores = ["flux"]
        flux_tallies.append(flux_tally)
    model.tallies = (original_tallies if include_model_tallies else []) + flux_tallies
    try:
        if openmc.lib.is_initialized:
            openmc.lib.finalize()
            if comm.rank == 0:
                model.export_to_model_xml()
            comm.barrier()
            openmc.lib.init(intracomm=comm)  # reinitialize with the tally
        with TemporaryDirectory() as temp_dir:
            run_kwargs = dict(run_kwargs) if run_kwargs else {}
            if not openmc.lib.is_initialized:
                run_kwargs.setdefault("cwd", temp_dir)
            statepoint_path = model.run(**run_kwargs)
            comm.barrier()
            if comm.rank == 0:
                if path_statepoint is not None:
                    shutil.move(statepoint_path, path_statepoint)
                    statepoint_path = path_statepoint
                if path_input is not None:
                    model.export_to_model_xml(path_input)
            statepoint_path = comm.bcast(statepoint_path)
            with openmc.StatePoint(statepoint_path) as sp:
                flux_tallies = [sp.tallies[tally.id] for tally in flux_tallies]
                for flux_tally in flux_tallies:
                    flux_tally._read_results()
        fluxes = []  # concatenated over the domain filters in order, as OpenMC 0.16 does
        for flux_tally in flux_tallies:
            flux = np.moveaxis(flux_tally.get_reshaped_data(), 1, -1)  # (domains, 1, 1, groups)
            fluxes.extend(flux.squeeze((1, 2)))
    finally:
        model.tallies = original_tallies
    stub = np.zeros((1, 1, fluxes[0].size if fluxes else 1))
    micros = [MicroXS(stub.copy(), ["H1"], ["(n,gamma)"]) for _ in fluxes]
    return fluxes, micros


class ActinvR2SManager(openmc.deplete.R2SManager):
    """R2SManager whose activation step runs ``actinv mesh``.

    ``mesh_timeout_s`` bounds the ``actinv mesh`` subprocess in wall-clock seconds (default one
    day, ``None`` for no limit); on timeout or a non-zero exit the error carries the stderr tail.
    """
    def __init__(self, neutron_model, domains, photon_model=None, *, library=None, decay_primary=None,
                 decay_fallback=None, actinv_bin=None, options=None, photon=None, threads=1,
                 mesh_timeout_s=86400.0):
        super().__init__(neutron_model, domains, photon_model)
        # Data not given comes from the installed catalog's default bundle (see _defaults). The
        # default fallback goes with the default primary only: an explicit decay_primary has no
        # fallback unless given, and decay_fallback=False drops it.
        wanted = []
        if library is None:
            wanted.append("activation-library")
        if decay_primary is None:
            wanted.append("decay-primary")
            if decay_fallback is None:
                wanted.append("decay-fallback")
        found = _defaults.default_data(tuple(wanted)) if wanted else {}
        library = found.get("activation-library", library)
        decay_primary = found.get("decay-primary", decay_primary)
        decay_fallback = found.get("decay-fallback", decay_fallback)
        self.library = str(Path(library).resolve())
        self.decay_primary = str(Path(decay_primary).resolve())
        self.decay_fallback = str(Path(decay_fallback).resolve()) if decay_fallback else None
        # Solver command: the explicit binary, else the CLI of the installed actinv package.
        self.actinv_bin = actinv_bin
        self.actinv_command = [os.fspath(actinv_bin)] if actinv_bin is not None else _defaults.default_command()
        # Wall-clock limit (seconds) for the `actinv mesh` subprocess; None waits indefinitely.
        self.mesh_timeout_s = mesh_timeout_s
        self.options = {"mode": "auto", "prune": "rate", **(options or {})}
        self.options["outputs"] = sorted(set(self.options.get("outputs", [])) | {"photons", "ledger"})
        self.photon = photon or {}
        self.threads = threads

    # -- step 1: OpenMC's, with the tally on ACTINV's exact group boundaries --------------------
    def step1_neutron_transport(self, output_dir="neutron_transport", mat_vol_kwargs=None, micro_kwargs=None):
        micro_kwargs = dict(micro_kwargs or {})
        micro_kwargs.setdefault("energies", list(library_bounds(self.library)))
        if not _OPENMC_MICRO_KEYS & set(micro_kwargs):
            # No chain asked for: OpenMC's own step 1 (mesh material volumes, the mesh-material or
            # cell filter, writing mesh_material_volumes.npz / fluxes.npy / micros.h5, filling
            # self.results) runs unchanged, with only its call to get_microxs_and_flux swapped for
            # the flux-only version above. Patching that one module name keeps every results key
            # and on-disk file exactly as OpenMC writes them.
            original = _r2s.get_microxs_and_flux
            _r2s.get_microxs_and_flux = flux_only_microxs_and_flux
            try:
                return super().step1_neutron_transport(output_dir, mat_vol_kwargs, micro_kwargs)
            finally:
                _r2s.get_microxs_and_flux = original
        # A chain (or nuclides/reactions) was passed: OpenMC's own get_microxs_and_flux, as before.
        # It also tallies per-nuclide reaction rates here for its own depletion solver; ACTINV
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

        # Under MPI one `actinv mesh` (itself multithreaded) runs on rank 0, which alone writes the
        # files; its error, if any, is broadcast so no rank waits on a barrier that never comes.
        result_path = output_dir / "mesh_result.ndjson"
        error = None
        if comm.rank == 0:
            try:
                self._run_mesh(output_dir, mats, fluxes, schedule, reference, result_path)
            except Exception as exc:  # re-raised on every rank below
                error = exc
        failure = comm.bcast(None if error is None else f"{type(error).__name__}: {error}")
        if error is not None:
            raise error
        if failure is not None:
            raise RuntimeError(f"step 2 failed on rank 0: {failure}")

        # photon groups per region and step -> results shim keyed by activation-material id
        self._set_mesh_results(result_path, mats, len(schedule))

    def _run_mesh(self, output_dir, mats, fluxes, schedule, reference, result_path):
        """Write the materials, flux records and mesh spec, then run ``actinv mesh``."""
        # Saved as OpenMC's own step 2 does, so load_results can reopen the run without re-solving.
        mats.export_to_xml(output_dir / "materials.xml")
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
        try:
            proc = subprocess.run([*self.actinv_command, "mesh", str(spec_path), str(result_path)],
                                  capture_output=True, text=True, timeout=self.mesh_timeout_s)
        except subprocess.TimeoutExpired as exc:
            tail = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
            raise RuntimeError(f"actinv mesh timed out after {self.mesh_timeout_s} s:\n{tail[-4000:]}") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"actinv mesh failed:\n{proc.stderr[-4000:]}")

    # -- step 3 (OpenMC >= 0.16): photon sources from ACTINV's spectra ---------------------------
    def _create_photon_sources(self, time_index, work_items):
        """OpenMC 0.16's source builder (``R2SManager._create_photon_sources``), with each
        region's ACTINV photon spectrum in place of ``openmc.stats.DecaySpectrum``, which needs
        nuclide densities and a depletion chain. Same regions, boxes and domain constraints as
        OpenMC's; the strength is the region's photons/s, as OpenMC 0.15.3's step 3 set it.
        """
        step_result = self.results["depletion_results"][time_index]
        materials = self.results["activation_materials"]
        mesh_based = self.method == "mesh-based"
        if mesh_based:
            mat_dict = self.neutron_model._get_all_materials()
        sources = []
        for item in work_items:
            if mesh_based:
                index_mat, domain_id, bbox = item
                original_mat, domain = materials[index_mat], mat_dict[domain_id]
            else:
                domain, original_mat, bbox = item
            energy = step_result.get_material(str(original_mat.id)).get_decay_photon_energy()
            if energy is None:
                continue
            sources.append(openmc.IndependentSource(
                space=openmc.stats.Box(bbox.lower_left, bbox.upper_right), energy=energy,
                particle="photon", strength=energy.integral(), constraints={"domains": [domain]}))
        return sources

    def step4_photon_transport(self, *args, by_parent_nuclide=False, **kwargs):
        if by_parent_nuclide:
            raise ValueError("by_parent_nuclide is not supported: ACTINV gives each region's photon "
                             "spectrum, not one per parent nuclide")
        return super().step4_photon_transport(*args, **kwargs)

    def _set_mesh_results(self, result_path, mats, n_steps):
        per_step = parse_photon_groups(result_path, mats, n_steps)
        self.results["depletion_results"] = build_results_shim(per_step)
        self.results["actinv_mesh_result"] = result_path

    def load_results(self, path):
        """Reload a finished run from ``path`` (the directory holding ``neutron_transport``,
        ``activation`` and ``photon_transport``) without re-solving.

        OpenMC's loader reads what steps 1 and 3 wrote and the activation materials; ACTINV writes
        no ``depletion_results.h5``, so the photon sources are rebuilt from
        ``activation/mesh_result.ndjson`` and the schedule length in ``activation/mesh.json``. A run
        whose activation directory has no ``materials.xml`` (made before it was written) cannot be
        reloaded this way. Without ``mesh_result.ndjson`` the result is OpenMC's loader alone.
        """
        super().load_results(path)
        activation_dir = Path(path) / "activation"
        result_path = activation_dir / "mesh_result.ndjson"
        if not result_path.exists():
            return
        mats = self.results.get("activation_materials")
        if mats is None:
            raise RuntimeError(f"{activation_dir} has mesh_result.ndjson but no materials.xml; "
                               "the activation materials are needed to map regions to photon sources, "
                               "so re-run step 2")
        n_steps = len(json.loads((activation_dir / "mesh.json").read_text())["schedule"])
        self._set_mesh_results(result_path, mats, n_steps)
