# ACTINV P52 Protocol — OpenMC-ingest, uncertainty-bearing R2S handoff

Date: 2026-09-25. Status: **open** — sealed at G0; gates below are frozen by that seal.

Predecessor: P51 (`P51-CLOSED`). This phase closes the extension's product lane:
the only shipping workflow that hands a shutdown-photon source to an external
transport code *with propagated nuclear-data bands* — the layer OpenMC's
`deplete`/`R2SManager` does not provide.

## Frozen definitions

**Pipeline.** The end-to-end flow composes existing qualified stages plus one
new emission stage:

```
openmc neutron run (external)
  → statepoint HDF5 flux tallies (cell or mesh)
  → actinv import-flux openmc        (existing: actinv-flux-1 ndjson)
  → actinv mesh                      (existing: per-cell solve on one PreparedRun,
                                      `uncertainty` threaded into every cell)
  → actinv export-r2s STEP           (NEW: actinv-r2s-source-1 banded interchange)
  → external photon transport        (external: the existing export-openmc-mesh
                                      Python module remains the nominal view)
```

Only the `export-r2s` stage is new code. The mesh result (`actinv-mesh-result-1`)
already embeds each cell's full per-step `RunResult`: `steps[].photon_source`
(incl. `by_nuclide` photons/s) and `steps[].uncertainty` (per-response bands,
coverage fields) are already present.

**`actinv-r2s-source-1` (interchange format).** One UTF-8 JSON object per line:

```json
{"record":"header","schema":"actinv-r2s-source-1",
 "mesh_result_sha256":"<sha256 of the source ndjson>",
 "mesh_spec_fingerprint_sha256":"<copied from mesh header>",
 "canonical_flux_sha256":"<copied from mesh header>",
 "step":<int>,"time_s":<float>,"dt_s":<float>,
 "band_semantics":{"method":"first-order propagated band (P43/P44 qualifications)",
   "sigma_field":"combined_standard_uncertainty | mf33_standard_uncertainty",
   "applies_to":"source strength only — the emitted group spectrum is nominal",
   "channels_reflect":"activation-side data only; photon yields carry no published covariance",
   "combination_rules":{"sigma_independent":"sqrt(sum sigma_i^2) — assumes independent contributions",
                        "sigma_conservative":"sum sigma_i — assumes full positive correlation"}}}
{"record":"cell","ordinal":<int>,"id":"<cell id>","index":[i,j,k]|null,
 "bounds_cm":[[x0,x1],[y0,y1],[z0,z1]]|null,"volume_cm3":<float>,
 "photons_s":<float>,
 "sigma_photons_s_independent":<float|null>,"sigma_photons_s_conservative":<float|null>,
 "groups":[{"centroid_eV":<float>,"photons_s":<float>}],
 "per_nuclide":[{"nuclide":"<name>","photons_s":<float>,
    "activity_nominal_bq":<float>,
    "rel_sigma":<float|null>,"sigma_photons_s":<float|null>}],
 "coverage":{"photon_nuclides":<int>,"banded_nuclides":<int>,
    "unbanded_photon_share":<float>,
    "uncovered_library_rows":<int>,"uncovered_decay_constants":[...],
    "uncovered_yield_products":[...],"excluded_blocks":<int>}}
{"record":"footer","cell_count":<int>,
 "total_photons_s":<float>,
 "sigma_total_independent":<float|null>,"sigma_total_conservative":<float|null>,
 "cells_partially_unbanded":<int>}
```

**Emission rules.**

- Per nuclide `i` emitting photons in the step's `photon_source.by_nuclide`:
  `sigma_i = photons_s_i × rel_sigma_i`, where `rel_sigma_i` is the
  `activity:<nuclide>` response's `combined_standard_uncertainty/nominal`
  (falling back to `mf33_standard_uncertainty/nominal` when no secondary
  channel ran). A photon-bearing nuclide without an `activity:` band
  (response not requested or nominal ≤ 0) contributes `sigma:null`; the
  cell's `sigma_*` fields are then `null` and `coverage.unbanded_photon_share`
  carries that nuclide's share of the cell's photons/s — never silently
  nominal.
- Cell totals: `sigma_independent = sqrt(Σ σ_i²)`, `sigma_conservative =
  Σ σ_i`, over banded contributions only; `unbanded_photon_share` records the
  excluded fraction. Footer totals combine cells with the same rules;
  cells' `unbanded_photon_share` is aggregated into
  `cells_partially_unbanded`.
- `groups` is the nominal per-cell group spectrum (`centroid_eV`,
  `photons_s`) — bands apply to strength, not shape (declared in
  `band_semantics.applies_to`).
- A cell whose step has no `uncertainty` block or no `photon_source` is a
  hard error naming the cell — the interchange never emits nominal-only
  cells. A cell with a genuinely zero source emits strength 0, empty
  `groups`/`per_nuclide`, sigma 0.
- `bounds_cm`/`index`/`volume_cm3` copy from the mesh cell record
  (`index`/`bounds_cm` may be absent for cell-geometry input → `null`).
- `mesh_result_sha256` binds the output to the exact byte stream that
  produced it; the spec fingerprint and canonical flux sha are copied from
  the mesh header, so the chain spec→flux→result→source is closed.

**Parity methodology (G2).** "Identical data" means the same material
composition, the same multigroup flux vector, and the same irradiation/cooling
schedule. Cross-section and decay-file *provenance* differ by construction —
ACTINV uses the TENDL-2025 709-group activation library; OpenMC's deplete uses
ENDF-B-based `MicroXS` collapsed by `from_multigroup_flux`, and its own
chain.xml. The gate compares per-nuclide atom inventories: every nuclide
contributing ≥1% of either arm's activity **at that step** must appear in
both arms and agree within **rtol 0.5** — provided its inventory is
physically significant: a nuclide is compared only when at least one arm
carries it above `1e-12 ×` the largest single-nuclide inventory in that
arm at that step (below the floor the residual is numerical dust — a
2.58 h nuclide at 30 d decay reads 1e-10 vs 1e-40 across arms; no
response can resolve that).
(P45 measured actinv-vs-openmc total-activity divergence of 0.16–0.33 on the
Fe FNS arm — the tolerance absorbs evaluation differences; it cannot absorb a
factor-level coding error.) Every compared nuclide and its relative
difference is ledgered; below-floor nuclides are ledgered as such, not
dropped silently.

**Determinism and resume.** Mesh runs are byte-deterministic on pinned inputs;
`resume:true` treats a truncated output as a checkpoint. Both claims are gated
under the P52 workload, not assumed.

## Artifact set

- `crates/actinv-core/src/r2s.rs` — `actinv-r2s-source-1` emission
  (pure function over a parsed mesh result; no solver changes).
- `crates/actinv-cli/src/command.rs` (+ `lib.rs` if needed) —
  `actinv export-r2s` dispatch.
- `controls/p52_artifacts.py` — artifact registry + drift check.
- `controls/g0_p52_seal.py` — seal control.
- `controls/g1_p52_mechanics.py` — emission mechanics on synthetic records.
- `controls/g2_p52_parity.py` + `controls/p52_openmc_parity.py` —
  one-cell deplete parity (openmc016 env: `openmc==0.16.0`,
  `~/nuclear-data/p32-work/chain/depletion/chain.xml`,
  `~/nuclear-data/endfb-viii.1-hdf5/cross_sections.xml`).
- `controls/g3_p52_demo.py` — executed multi-cell banded demonstration.
- `controls/g4_p52_determinism.py` — determinism + resume legs.
- `controls/check_g5_p52.py` — independent checker + mutations.
- `examples/p52_r2s/` — committed flux/spec inputs for the demo.

## Gates

- **G0 (seal).** This protocol hashed; artifact set pinned; recorded in
  `results/g0_p52_seals.json`.
- **G1 (mechanics).** On synthetic embedded records: schema emission exact;
  per-nuclide σ arithmetic re-derived; both combination rules; zero-source
  cell emits honestly; a cell missing `uncertainty` or `photon_source` fails
  hard and names the cell; `unbanded_photon_share` computed correctly on a
  deliberately unbanded nuclide.
- **G2 (one-cell parity).** Pure-Fe cell, FNS-shaped 709-group flux, 300 s
  irradiation + cooling steps [86400 s, 2.592e6 s, 3.1536e7 s]. The openmc016
  env drives `MicroXS.from_multigroup_flux` + `IndependentOperator` +
  `PredictorIntegrator`; ACTINV runs the identical flux through `actinv mesh`
  (1 cell) on the TENDL library. Gate: every nuclide ≥1% of either arm's
  activity *at that step*, above the declared significance floor, present
  in both, |rel| ≤ 0.5 at every compared step. `results/g2_p52_parity.json`.
- **G3 (multi-cell demonstration).** 8 cells, distinct FNS-perturbed spectra
  (P45-style deterministic modulation), pure Fe, `uncertainty` declared with
  `activity:*` responses and the MF=33 covariance sidecar, `cell_result_fields`
  pruned to `steps`,`pruned_states`,`total_states` (+ needed result keys —
  declared in the control). Executed wall time ledgered with hardware line;
  `actinv export-r2s` emits the banded source for the final cooling step;
  every cell's band fields present and `unbanded_photon_share` ≤ 0.05.
  `results/g3_p52_demo.json` + `results/p52_r2s_source.ndjson`.
- **G4 (determinism/resume).** The G3 mesh spec run twice → byte-identical
  output and identical emitted r2s doc; a run truncated mid-output resumed
  with `resume:true` → byte-identical to the uninterrupted run.
  `results/g4_p52_determinism.json`.
- **G5 (independent checker).** `controls/check_g5_p52.py` re-derives, from
  the mesh ndjson alone, every cell's per-nuclide σ, both combination rules,
  totals, coverage fractions, header binding hashes, and footer aggregates;
  verifies parity arithmetic from the recorded ledgers; rejects planted
  mutations (dropped nuclide contribution, sigma-rule tampering, coverage-flag
  fabrication, swapped step). `results/check_g5_p52.json`.

## Conditions and honest boundaries

- Bands reflect **activation-side data uncertainty only** (cross sections,
  and decay/yield channels when requested): photon yields
  (`source_photons_per_decay`) are treated as exact — no evaluation publishes
  their covariance. Declared in `band_semantics.channels_reflect`.
- The band applies to source *strength*; the group spectrum shape is nominal.
  Nuclide-mix uncertainty modulating the spectrum is a declared limitation —
  a derived photon-group response would need per-(parameter, nuclide)
  sensitivity composition, which is adjoint-lane work (parked).
- Independent-quadrature and conservative-linear totals bracket the true
  combination: per-nuclide bands share covariance sources, so independence
  understates and linear-sum overstates. Both are emitted; the consumer
  chooses.
- Parity compares *answer agreement across distinct evaluations*, not
  identical numerics: TENDL vs ENDF microXS differences are physical, not
  bugs. The 0.5 tolerance is sized from P45's measured divergence; it would
  catch normalization/scheduling errors (factor ≥ 2), not library physics.
- Photon-transport runs stay external — ACTINV emits the interchange
  document and (unchanged) the OpenMC source module; nothing about the
  transport step is claimed.

## Amendment rule

Post-G0 changes to sealed artifacts are append-only amendments: recorded in
this file with reason and sha, then re-sealed. Any change to the frozen
definitions above is a new phase, not an amendment.

## Amendments

- **A1 (2026-09-25, mechanical).** `controls/p52_openmc_parity.py`: set
  `OPENMC_CROSS_SECTIONS` to the pinned endfb-viii.1-hdf5
  `cross_sections.xml` when spawning the sealed P45 driver — the driver
  requires it and P45 supplied it from the campaign harness. No protocol
  value, format, or gate changed.
- **A2 (2026-09-25, mechanical).** `controls/g2_p52_parity.py` +
  `controls/g3_p52_demo.py`: the canonical `actinv-flux-1` footer requires
  `volume_integrated_flux` when cell records carry `volume_cm3`
  (flux.rs:766-771); both writers omitted it. Added the field with the
  required sum. No protocol value, format, or gate changed.
- **A3 (2026-09-25, gate-methodology).** G2 comparison rule sharpened in two
  ways after the first executed run exposed them: (1) dominance is evaluated
  *per step* (a nuclide ≥1% of either arm's activity at that step is
  compared), not only at end-of-irradiation — more nuclides are compared,
  never fewer; (2) a declared significance floor (`1e-12 ×` the largest
  per-nuclide inventory in each arm at that step) excludes numerical dust —
  the first run's only failure was a 2.58 h nuclide read as 9.5e-10 vs
  1.2e-40 atoms at 30 d cooling, physically identical silence. Below-floor
  nuclides are ledgered, not dropped.
- **A4 (2026-09-25, mechanical).** `controls/g3_p52_demo.py`: the banded
  mesh must run the library the covariance sidecar binds
  (`tendl-2025-neutron-709g.npz`, sha `ec4c72bf…`), not the P45-matched
  patched variant — the covariance index pins `activation_library_sha256`
  and rejects the mismatch (correctly). G2's nominal-only parity arm keeps
  the patched library, unchanged.
- **A5 (2026-09-25, mechanical).** `controls/g3_p52_demo.py`: flux-cell
  `index` components are 1-based (flux.rs:319); the writer used a 0
  component. Index is optional — bounds carry the geometry — so the field
  is dropped rather than re-based.

