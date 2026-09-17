# ACTINV-P32 protocol — spatial source handoff

P32 qualifies ACT-SOURCE-01: the spatial decay-photon handoff through an
executed open transport chain. A supplied neutron flux/material mapping
(here: an OpenMC fixed-source neutron transport run over a frozen
geometry) feeds ACTINV activation per spatial cell; ACTINV emits
per-cell decay-photon sources; an external photon transport stage
(OpenMC photon mode) consumes them. Every link is checked
independently. A native OpenMC depletion run on the same geometry is
executed as the comparison leg where outputs and data permit.

## Scope

- Executed chain, one frozen model: concentric spherical iron shells
  around a 14.1 MeV isotropic point source; four material cells with
  distinct volumes. Per-cell neutron flux tallies in the FISPACT-709
  group structure are imported through `actinv import-flux openmc`,
  activated per cell by `actinv run`, and each step's photon source is
  exported via `actinv export-openmc` and executed in a photon-mode
  OpenMC run.
- Independent checks at every link: cell identity, volumes, units,
  source-rate normalization, flux totals, photon totals/energy
  conservation, cooling-time separation, and spatial distribution
  (a single point or collapsed mesh does not satisfy the contract).
- Transport statistical error is reported separately (`std_dev` on
  every tally/dose) and is never folded into source-validation
  tolerances.
- Comparison leg: `openmc.deplete` on the same geometry/materials with
  a chain built locally from ENDF/B-VIII.0 decay + NFY evaluations.
  This is an each-tool-with-its-own-data comparison (ACTINV solves on
  the pinned TENDL-2025-patched artifact; OpenMC depletes on ENDF/B
  data) — agreement bounds are reported as measured values, not as
  identical-data equivalence.

## Out of scope

- A shutdown-dose-rate validation claim. The photon transport stage
  verifies the handoff mechanics (normalization, spectra, spatial
  sampling); it is not an experimental dose benchmark.
- Mesh formats beyond the executed tallies; DAGMC/CAD geometry.
- Any AI-assisted path (P33/P34 remain blocked) and any change to
  production solver behavior.

## Frozen model (sealed at G0)

- Geometry: four concentric Fe shells, r in (0–0.5, 0.5–1.0, 1.0–1.5,
  1.5–2.0) cm, density 7.87 g/cm³, pure elemental iron (wt%).
- Source: isotropic 14.1 MeV point at the origin, strength frozen in
  the seal; fixed-source mode, batch/particle counts frozen.
- Tallies: a regular 4x4x4 mesh + fispact-709 energy-group flux tally
  feeding `actinv import-flux openmc`, plus a per-material-cell flux
  tally feeding the native depletion leg.
- Activation: one `actinv-spec-1` per cell, one irradiation step +
  two cooling steps, natural Fe composition, the pinned
  `tendl-2025-patched-neutron-709g` library and ENDF/B-VIII.0 +
  JEFF-3.3 decay files.
- Photon transport: per-cell OpenMC photon sources from
  `export-openmc`, photon-mode fixed source, dose-proxy tally frozen
  in the seal.
- Depletion: `openmc.deplete` predictor integrator on the same 64
  voxel materials with per-cell fluxes from the cell tally, ENDF/B
  VIII.0 chain built from the pinned decay + NFY files, identical
  irradiation history.

## Controls (G2)

- `cell_identity`: every imported flux row maps to exactly one voxel
  and vice versa; voxel bounds/volumes match the mesh geometry to
  1e-12 and the per-cell mass is rho*V.
- `normalization`: imported flux totals equal the statepoint tally
  means to tally-precision; per-cell photon source integrals equal the
  ACTINV step photon totals to 1e-6.
- `distributed_source`: the exported source occupies every activated
  cell (no single-point collapse); per-cell source weights match
  cell activity fractions.
- `cooling_separation`: photon sources for distinct cooling steps are
  exported and tallied separately; totals differ as physics requires.
- `depletion_comparison`: per-cell end-of-irradiation activity vs the
  native depletion leg is recorded with its measured deviation; the
  deviation is reported, not hidden, wherever data coverage differs.

## Negative controls (G3)

- `planted_normalization`: source-rate scaled 10x between import and
  activation must be diagnosed by the controls, not propagated.
- `planted_mapping`: swapping two cells' fluxes must be diagnosed.
- `interrupted_export`: a photon-source export interrupted mid-write
  must not produce a consumable source file.
- `missing_data`: any required artifact absent must fail closed with
  a named error.
- `point_source`: a source reduced to a single point must be rejected
  by the distributed-source control.

## Closure rule (G4)

P32 closes PASS only if every control and negative control resolves as
declared, the photon transport stage executed end-to-end, and
ACT-SOURCE-01 is qualified. CONDITIONAL if the chain executes but a
measured leg (e.g. depletion agreement) carries named caveats. FAIL
or remain BLOCKED if any link cannot execute.

Prior-blocker record: `results/verdict_p32.json` recorded
`P32-BLOCKED` on 2026-09-17 when no OpenMC installation existed.
OpenMC 0.15.3 (conda-forge, openmpi build) is now installed at
`~/.local/share/mamba/envs/openmc` and a smoke fixed-source run
executed end-to-end; the blocker is lifted and the phase reopens.
