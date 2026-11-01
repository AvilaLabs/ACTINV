# ACTINV as the activation engine of OpenMC's R2S workflow

The adapter is now part of the `actinv` Python package: after `pip install actinv`, write
`from actinv.openmc import ActinvR2SManager` (source: `python/pysrc/actinv/openmc.py`). Use that module.
The file `actinv_openmc_r2s.py` in this folder is the single-file adapter P163 measured, kept unchanged
because `controls/check_p163.py` pins it; it needs explicit data paths and a chain file, and the
defaults described below apply only to `actinv.openmc`.

`ActinvR2SManager` is a subclass of `openmc.deplete.R2SManager`
(OpenMC >= 0.15.3) that keeps OpenMC's neutron transport (step 1) and photon transport (step 3),
and replaces the activation solve (step 2) with an `actinv mesh` call. It exists so that OpenMC's
rigorous two-step (R2S) shutdown-dose workflow can use ACTINV's activation/decay solver instead of
`openmc.deplete`'s own `IndependentOperator`/`PredictorIntegrator`, without touching any other
part of R2S: `step3_photon_transport`, `get_decay_photon_source_mesh`, and everything downstream
of `self.results['depletion_results']` is OpenMC's own, unmodified code.

## Requirements

- OpenMC 0.15.3 (the `openmc.deplete.r2s` module `ActinvR2SManager` subclasses; earlier versions
  do not have `R2SManager`).
- The `actinv` package (`pip install actinv`). Its `actinv` command is the default solver;
  pass `actinv_bin=` with an explicit path to use another build that supports `actinv mesh`.
- The standard ACTINV data (`actinv data fetch`, which installs under `./actinv-data` or
  `$ACTINV_DATA_DIR`). The default bundle's 709-group activation library and decay data are used
  unless you pass `library=`, `decay_primary=` or `decay_fallback=`. The default fallback goes
  with the default primary; with an explicit `decay_primary` there is no fallback unless given.
- No depletion chain. Step 1 tallies only the flux. Pass `micro_kwargs={"chain_file": ...}` (or
  `nuclides`/`reactions`) to run OpenMC's own `get_microxs_and_flux` instead.

## Minimal usage

```python
import openmc
from actinv.openmc import ActinvR2SManager

r2s = ActinvR2SManager(model, mesh)
r2s.step1_neutron_transport()
r2s.step2_activation(timesteps=[3.15e7, 1.0e6], source_rates=[1.0e17, 0.0])
r2s.step3_photon_transport(time_indices=[2])
```

`contrib/openmc_r2s/demo_steel_cube.py` is a complete end-to-end example (steel cube, point
source, mesh-based R2S) that runs both `ActinvR2SManager` and stock `openmc.deplete.R2SManager`
side by side and diffs the shutdown dose rate they predict.

## What differs from OpenMC's own `R2SManager`

**Step 2 (activation) is a different engine entirely.** OpenMC's `R2SManager.step2_activation`
builds an `IndependentOperator` and runs `PredictorIntegrator` (matrix-exponential depletion over
the chain file). `ActinvR2SManager.step2_activation` instead:

1. Builds the list of activation materials the same way OpenMC does (`get_activation_materials`
   for mesh-based R2S, or one material clone per cell for cell-based R2S).
2. Writes every region's flux as one `actinv-flux-1` NDJSON file, using the exact same group
   boundaries as the ACTINV library (so ACTINV never has to rebin OpenMC's spectrum). The
   per-group flux is `openmc's track-length-per-source-particle / region_volume * reference_rate`,
   where `reference_rate` is the first positive entry in `source_rates`.
3. Converts each region's OpenMC material to ACTINV's explicit-nuclide `atoms_per_g` form
   (`material_to_actinv`), with `mass_g = volume_cm3 * density`.
4. Runs **one** `actinv mesh` subprocess call for the whole schedule and every region (the
   library and decay data load once; each region gets its own material and absolute mass).
5. Builds a small in-memory shim (`_Results` / `_Step` / `_PhotonMaterial`) so OpenMC's own step 3
   can ask `results[t].get_material(id).get_decay_photon_energy()` exactly as it would for a real
   `openmc.deplete.Results` object, and get back an `openmc.stats.Discrete` distribution built
   from ACTINV's photon-source groups.

**Step 1 (neutron transport) is OpenMC's, but flux-only.** `get_microxs_and_flux` always loads a
depletion chain and tallies reaction rates, which ACTINV never reads. By default the subclass runs
OpenMC's own `step1_neutron_transport` unchanged (mesh material volumes, the mesh-material or cell
filter, `mesh_material_volumes.npz`, `fluxes.npy`, `micros.h5`, the same `self.results` keys) with
its `get_microxs_and_flux` call replaced by `flux_only_microxs_and_flux`: the same transport with
only a flux tally, and all-zero placeholder `micros` that OpenMC stores but ACTINV does not use.
No chain file is needed. Whichever way step 1 runs, `energies` defaults to the ACTINV library's
exact group boundaries (`library_bounds`), so the flux ACTINV receives needs no rebinning.

If the caller passes `chain_file`, `nuclides`, `reactions` or `reaction_rate_mode` in
`micro_kwargs`, OpenMC's own `get_microxs_and_flux` runs as before, with this difference from its
defaults:

- Unless the caller passes `nuclides=`/`reactions=` explicitly, only a single throwaway nuclide is
  tallied, with `reactions=["(n,gamma)"]`. The nuclide is the first one found in an activation-
  region material (a cell domain's fill material, for cell-based R2S), falling back to the first
  material anywhere in the model that has any nuclide if the domain materials aren't cleanly known
  yet (mesh-based R2S: the mesh/material combinations aren't resolved until `material_volumes`
  runs later in the same step). If no material anywhere in the model has a nuclide,
  `step1_neutron_transport` raises `ValueError` naming `micro_kwargs={"nuclides": [...], "reactions":
  [...]}` as the explicit alternative, rather than failing with a bare `StopIteration`. OpenMC's
  default tally is every chain nuclide times every reaction, which with a 709-group structure
  produces gigabyte-scale tallies that ACTINV never reads — it only uses the flux array, not
  OpenMC's microscopic cross sections.

**Step 3 is untouched.** Both cell-based and mesh-based R2S work because they both go through
`R2SManager.step3_photon_transport`/`get_decay_photon_source_mesh`, which only calls
`get_material(id).get_decay_photon_energy()` — the one method the results shim implements.

## Demo result

`demo_steel_cube.py` runs a 20 cm steel cube with a 14.1 MeV point source, one year of
irradiation at 1e17 n/s, 1e6 s of cooling, and a shutdown photon dose tally 30 cm out, once with
`ActinvR2SManager` and once with stock `openmc.deplete.R2SManager`. The result in
`results/openmc_r2s_demo.json` is a dose ratio (ACTINV / OpenMC) of **1.019 ± 0.011**, with total
wall time **19 s** (ACTINV) vs **850 s** (OpenMC's own depletion) end to end. That OpenMC run used its default
tally mode (per-nuclide reaction-rate tallies), not its fastest one.

Protocol P163 (`results/p163_speed.json`, `results/p163_verdict.json`) then timed the same problem against OpenMC's
flux mode on the library's 709 groups, at 8, 64 and 512 mesh cells with repeated runs. Its allowed claim:

ACTINV is at least 6.5x faster than OpenMC 0.15.3 flux mode at every measured size (8, 64, 512 mesh cells).

The handbook page [Use ACTINV with OpenMC](../../docs/guide/openmc.md) has the per-size times.

This is a single-case engineering demo, not a validation study: one geometry, one nuclide
inventory (SS316-type steel), one irradiation history, and Monte Carlo statistical uncertainty
only on the photon-transport tally (not on activation or on cross-section data). It shows the two
engines agree to about 2% on this problem and that ACTINV's activation step is much faster; it
does not establish agreement across the wider range of materials, spectra, and irradiation
histories a production R2S workflow would see.

## Known limits

Read from the adapter's own code (`python/pysrc/actinv/openmc.py`).

- **Neutron activation only.** `step2_activation` hardcodes `"projectile": "neutron"` in the
  ACTINV mesh spec. Photon-induced or other-projectile activation is not supported.
- **Single reference source rate per schedule.** `build_schedule` picks the *first positive*
  entry in `source_rates` as the reference rate and scales every other step's flux relative to
  it (`flux = rate / reference`). This assumes one constant neutron spectral shape that only
  scales up and down with source strength — irradiation steps at different absolute rates but
  the same *spectrum* are fine, but a schedule that changes the spectrum (e.g. a different source
  position or energy between steps) is not represented; ACTINV would see the same normalized
  spectrum scaled by a different multiplier, not a genuinely different one. (If no timestep has a
  positive source rate at all, `build_schedule` raises `ValueError` rather than guessing at a
  reference — there is no irradiation for ACTINV to solve in that case.)
- **`actinv mesh` region identity is round-tripped through a string index, not the OpenMC
  material ID.** The flux NDJSON writer assigns each region `id: str(i)` by its position in the
  `mats` list; `step2_activation` then reads `actinv mesh`'s result records back with
  `mats[int(rec["id"])]` to recover which OpenMC activation material a result row belongs to.
  This depends on `actinv mesh` echoing the input `id` field unchanged (confirmed for the current
  `actinv-core` mesh writer, which clones `cell.id` verbatim into its output records) and on
  region order being preserved end to end; it is not a property the ACTINV wire format guarantees
  in general.
- **Units and normalization assumptions carried over from OpenMC, not re-validated by ACTINV.**
  The flux written to `actinv-flux-1` is OpenMC's mesh-tally track length per source particle,
  divided by region volume and scaled by the reference source rate, with the region volume taken
  from OpenMC's own material-volume calculation (mesh-based) or `cell.volume` (cell-based). If
  that volume is wrong or a placeholder, ACTINV's activation is silently wrong too — the adapter
  only checks that the volume is positive, not that it is physically correct.
- **Result index 0 is always pre-irradiation with an empty photon source,** matching OpenMC's own
  `Results` convention (`get_material(...).get_decay_photon_energy()` returns `None` for any
  material at index 0). Downstream code that assumes index 0 carries a genuine (zero-strength)
  material, rather than an intentionally-empty stand-in, may behave differently than with a real
  `openmc.deplete.Results` object at that index.
- **`options["outputs"]` always forces `{"photons", "ledger"}`** into whatever the caller passes,
  since step 3 needs the photon source and cell-result parsing needs the per-step ledger; callers
  cannot disable either output.
- **Cell-based R2S materials are cloned per call, not cached.** Every `step2_activation` call
  re-clones each cell's fill material; running it twice for the same manager produces two
  independent sets of activation materials rather than reusing state, unlike OpenMC's own
  behavior in the same code path (this mirrors OpenMC's stock `R2SManager.step2_activation`, so
  it is not adapter-specific, but is worth knowing if you call it more than once).

## Testing

`scripts/test_actinv_openmc.py` covers the adapter's pure logic — material conversion, the flux
NDJSON writer, schedule construction, and the results shim's indexing — with no transport. Those
tests need OpenMC importable and skip cleanly (not failing) if it is not. It also checks that
`import actinv` never imports OpenMC and how the default data and solver command are resolved
(no OpenMC needed). Run it against an installed `actinv`:

```
python scripts/test_actinv_openmc.py -v
```
