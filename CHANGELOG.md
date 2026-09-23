# Changelog

## Unreleased

**Fixed**

- Self-shielding factors were interpolated in ln σ₀ with the weight mirrored: `sigma0_b` is descending, and
  the weight toward the smaller-σ₀ neighbour was applied toward the larger one. A query exactly on an interior
  grid point returned the neighbouring row (σ₀ = 1e3 b returned the 1e4 b factors) and off-midpoint queries
  were reflected within their interval. Every shielded run whose effective σ₀ lay strictly inside the table
  grid was affected — all `dilution: "composition"` runs and any fixed σ₀ other than the grid ends — including
  the shielded rows folded into MF=33 uncertainty. Runs at σ₀ = 0.1 b or ≥ 1e10 b (the clamped grid ends, which
  every P19 control uses) were unaffected. Regression test added at exact and off-midpoint interior points.
- A misspelt natural-element composition key (`"Fee"`, `"Coo"`) or an element without natural abundance data
  (`"Tc"`, `"Pm"`) passed validation and silently contributed zero atoms; with `atom_fraction` the remaining
  members were renormalised over the survivors. The only trace was `ledger.composition_elements_unknown`. Such keys
  are now validation errors; give abundance-free elements as explicit nuclides.
- Study refinement compared array responses by position and through the `atoms_per_g` field only.
  `photon_source_per_group` groups carry no such field, so every photon criterion compared as 0 and was reported
  `satisfied` regardless of the discrepancy; inventories are ordered by the kept-state set, which differs between
  the declared (default `prune: rate`) and reference (`prune: none`) variants, so positions named different
  nuclides. Inventories now join by nuclide name and photon groups compare `photons_s_g` by group.
- Study robustness summarised the two array responses as a fabricated `0 ± 0` (an empty sum), and comparison
  rules reduced them by summing every numeric leaf (Z + A + LISO + atoms; eV + photons + W). Both consumers now
  require one of the three scalar responses; the schema says the same.
- Study names may no longer contain `__`, the case-id separator: `a__b__c__d` could name two different cases,
  resolve to the wrong triple and overwrite one spec file with the other.
- `robustness.samples` is capped at 4096 per case (`robustness_too_large`), per-sample outputs are reduced to
  their declared responses instead of being held whole in memory, a channel with fewer than two successful
  isolated draws reports a `null` variance (and remainder) instead of `0`, and a study step carrying a per-step
  `spectrum` (absent from the study schema) is refused instead of silently forwarded.
- Library building: a resolved-range NAPS other than 0 or 1 silently used the NAPS=0 computed channel radius.
  NAPS=2 (defined only with an energy-dependent radius) now follows the unresolved shielding path's rule and any
  other value fails closed; an MT carrying MF=6, MF=8 or MF=9 products without an MF=3/MF=10 reaction is now an
  error instead of being skipped with no ledger line. A scan of the local TENDL-2025, TENDL-2017, FENDL-3.2c and
  EAF-2010 corpora found neither case, so no shipped library changes.
- `build-damage`/`build-shielding` checkpoint keys now include the validated projectile and the builder code
  identity (the shielding key also covers the PURR port), so a persistent `--cache` can no longer serve factors
  from older code or skip the projectile check; the embedded-source fingerprint is hashed once per process.
- `build-covariance` refuses an MF=33 LB=0–4 block whose dense form would exceed the 1 GB per-array cap before
  allocating it; a few hundred kB of declared (E, F) pairs could previously demand tens of GB.
- Flux import refuses structured meshes above 100 million cells with overflow-checked arithmetic: an OpenMC
  `dimension` dataset sized the geometry allocation unchecked, and the meshtal cell product could wrap.
- Mesh resume streams the existing output instead of reading it whole into memory, re-reads prefix records
  with buffered I/O instead of one syscall per byte, refuses `memory_limit_bytes` where peak RSS cannot be
  measured (it was silently inert off Linux), and says when a tripped guard discards work because `resume` is off.

## v1.2.0 — 2026-09-21

**Added**

- Per-step spectra on the activation schedule: each step may declare its own spectrum (same structure
  and group count as the base spectrum); the step's flux multiplier scales that spectrum's total.
  Distinct override spectra collapse once each on the groupwise rows and deduplicate against the base
  spectrum; physical fluence accumulates each step's own spectrum total and the schedule ledger reports
  the override count as `step_spectra`. Per-step spectra force the groupwise library path and are
  rejected at validation on a group-count mismatch.
- MF=33 uncertainty propagation across per-step spectra: covariance blocks collapse jointly over
  (spectrum, row) parameters, reaction and fission-yield tangent directions assemble per spectrum, and
  decay-constant directions remain shared. Identical overrides deduplicate to the bitwise-identical
  single-spectrum path; sensitivity parameters report their spectrum index.
- `activity.total` as a first-class uncertainty response, propagated sᵀΣs through the full covariance;
  the per-nuclide RSS combination is removed.
- `actinv export-openmc-mesh`: decay-photon source handoff for downstream transport — one
  IndependentSource per mesh cell, box-sampled inside recorded bounds, discrete spectrum from the
  cell's group structure, absolute per-cell strengths, with TOTAL_PHOTONS ledgered.
- `actinv build-library --decay-fallback`: a second decay sublibrary for nuclides absent from the
  primary file, plus a sole-candidate loose-ELIS tier resolving cross-library level-scheme drift
  (recovers Sb-120m, Cu-68m, Cs-135m, Dy-147m, Hf-178n, Au-189m to real decay homes); both table
  SHA-256 values are recorded in the artifact index.
- Emitted-state products resolve to decay-library isomer levels through the MF=1 state table, with
  rank-compressed LFS mapping for products lacking an explicit state catalog; the LIS label fallback
  recovers 1,347 isomer production channels on TENDL-2017.
- Finite self-shielding factors fold through the MF=33 covariance collapse, so shielded runs carry
  consistent propagated uncertainties.

**Changed**

- CRAM solves apply a selective iterative-refinement gate with a convergence floor; rows at subnormal
  scale are exempt from residual/convergence gates rather than forcing refinement that cannot
  converge.
- Robustness sampling draws every perturbation channel as a mean-preserving lognormal factor (never
  nonpositive, never uncorrelated); the relative covariance is eigendecomposed and eigen-clipped to
  the nearest positive-semidefinite matrix, replacing the ridge-ladder Cholesky path.
- Governed ENDF-6 legality relaxation in the library builder accepts LRX=0 competitive-width and
  sentinel-excitation rows from real evaluations; 31 of 36 FENDL-3.2c parents now build.

**Fixed**

- A spectrum with no positive total flux (an unreached mesh cell, a layer no tallied neutron reached)
  failed in the default collapsed mode with "collapsed activation cache does not match the run
  spectrum", a message naming the cache rather than the cause, after writing an unusable collapsed
  artifact. The run now uses the groupwise data for such a spectrum, which is exact (pure decay), and
  the collapsed-cache validator names a zero run spectrum, a zero cached spectrum and a group-count
  mismatch separately. Found through an external per-layer activation harness; regression tests added.

## v1.1.2 — 2026-09-16

Release-tooling patch. No functional changes to the solver, schemas, data catalog, or public interfaces;
the embedded catalog remains v1.1.0.

**Fixed**

- `scripts/smoke_python_wheel.py` expected embedded data catalog v1.0.0 and rejected the correct v1.1.0
  catalog, which blocked the v1.1.1 PyPI publish on the frozen tag. The constant now matches the shipped
  catalog, restoring the PyPI channel.
- Post-release controls updated for published-tag reality: the P18/P18b release boundary accepts the
  published v1.1.* tags while still rejecting tags ahead of the workspace version, and the P22 post-bump
  digest check scopes to the 1.1.0 release window.
- The P10 neutron-output identity pin was re-seated to the post-refinement deterministic value, and the
  committed CI result records were refreshed for the 1.1.x line.

## v1.1.1 — 2026-09-15

**Data**

- Embedded data catalog v1.1.0: adds the `tendl-2025-patched` neutron activation library and its matching
  rebuilt MF=33 covariance sidecar — an Avila Labs remediation derivative of TENDL-2025 that zeroes the 44
  upstream-confirmed leaked leading ordinates across the 28 confirmed-signature files. Not an official TENDL
  release; scope and remaining defect classes are disclosed in `docs/DATA_LIMITATIONS.md`.
- `tendl-2025-patched-neutron` is the new default data bundle; `tendl-2025-patched-neutron-covariance` adds the
  sidecar. The legacy `tendl-2025-neutron` bundles are superseded but preserved for byte-compatible reproduction;
  all v1.0.0 artifact IDs resolve to exactly the same bytes.
- `actinv new` now emits `catalog:tendl-2025-patched-neutron-709g` references; data installs under
  `actinv-data/v1.1.0/`.

**Fixed**

- CRAM linear solves now use iterative refinement (`Lu::solve_refined`) with compensated residual
  evaluation: row pivoting can mix a tiny state with a large background, so a small normwise residual
  alone is insufficient. Refinement is bounded at five iterations (the LAPACK GERFS cap) with stagnation
  stop, and any non-finite solution, residual or correction is a hard error rather than silent data.
- The certificate's `numerical_floor` note now states plainly that `alpha0 * max(N)` is the legacy CRAM
  asymptotic scale, not a bound on total numerical or solve error; below-scale populations remain reported,
  never removed.

## v1.1.0 — 2026-09-14

**Added**

- Finite-dilution self-shielding: `self_shielding` on `actinv-spec-1`/`actinv-mesh-spec-1` applies
  unresolved-range Bondarenko shielding from hash-pinned `actinv-shield-table-1` tables built by
  `actinv build-shielding`. Infinite dilution remains the explicit default; resolved-region pointwise
  shielding, escape corrections and probability-table transport remain out of scope.
- Schedule-driven feed and removal on `actinv-spec-1` steps (constant feed rates, first-order removal
  constants), a `removed` sink state, and named-ledger reporting rather than silent omission.
- `actinv reverse` / `actinv.reverse`: linear-regime flux estimation — recovers the flux normalization
  or segment multipliers from measured activities by weighted least squares, with residuals,
  chi-square and explicit method limits.
- Damage observables: a `damage` spec section folds hash-pinned `actinv-damage-table-1` tables (built
  by `actinv build-damage` from TENDL MF=3/MT=444) into damage-energy and NRT-dpa outputs.
- Practical uncertainty channels: propagated response bands now carry a per-channel breakdown across
  cross-section (MF=33), decay-constant (MF=8/MT=457) and independent fission-yield (MF=8/MT=454)
  uncertainty plus a named uncovered remainder; malformed covariance blocks are excluded with named
  reasons rather than silently clipped or symmetrized.
- Mesh scaling surface: signature-keyed workload grouping (reuse across cells sharing an identical
  rebinned flux), `cell_result_fields` selection, the post-hoc `memory_limit_bytes` guard, and
  output-as-checkpoint resume — a resumed run is byte-identical to an uninterrupted run modulo footer
  timing.

**Validation**

- Opened the evidence-directed P18 state-identity phase under a pre-evidence hash. It freezes physical ENDF product
  mapping, state-partial conservation, fail-loud missing-isomer handling and a family-level held-out public
  isomeric-ratio score before any production behavior changes.
- Recorded P18's sole procedural amendment after an incorrect fixed-column redaction exposed five early source
  families. Those families are permanently diagnostic; the remaining held-out partition, product behavior and all
  acceptance thresholds are unchanged.
- Sealed 962 public isomeric-ratio reaction families at family granularity before calculation, leaving 180 families
  and 1,945 rows genuinely held out. The committed metadata contains no dependent measurement, and an independent
  checker rederives the split and rejects identity, partition, quarantine and value-leak plants.
- Completed P18 G1 physical product-state identity: TENDL builds now retain ENDF target and product excitation
  metadata, map raw levels only through evaluated `LISO`, serialize the complete decision provenance in a v2 index,
  and account unsupported states explicitly. Real Ag-110 and two-isomer Ag-116 cases plus generated fixtures pass an
  independent mutation-tested checker; held-out measurements remain sealed.
- Completed the bounded P18 G2 audit of all 11,400 frozen TENDL evaluations and 1,810,499 MF=8/9/10 declarations.
  Catalog order, inventory, duplicate and total-availability checks pass, but the unchanged state-partial rule finds
  2,647,615 comparison violations and 143 MF8-versus-Q identity conflicts. G2 therefore fails, no held-out value is
  unsealed, and the unreleased P18 candidate cannot authorize v1.1.0.
- Closed P18 with the checker-derived `P18-FAIL` verdict after its exact source/evidence checkpoint passed all 42
  substantive CI steps. G3--G7 were not authorized, v1.0.1 remains the public release, and the unfinished evaluated
  precision/domain work moves to a separately frozen P18b successor rather than weakening the observed gate.
- Opened P18b after the green P18 closure to separate strict ENDF source conformance, the pinned IAEA FIZCON 0.1%
  compatibility convention and exact groupwise runtime conservation. Its pre-evidence protocol permits only a
  ratio-preserving bounded reconciliation and fails closed beyond that independent ceiling; held-out values remain
  sealed and v1.0.1 is unchanged.
- Sealed P18b G0 provenance for all 11,400 source files and a deterministic 245-file official-checker sample before
  reading checker output or new corpus classifications. The independent checker rederives the sample, binds both
  green opening workflows and rejects seven authority, inventory and quarantine mutations.
- Completed P18b G1 without reading corpus classifications or measurements. An exact 80/120-digit Decimal oracle,
  the checked Rust parser/interpolator and six unmodified pinned IAEA CHECKR/FIZCON runs agree across all ENDF real
  forms, interpolation laws, repeated-energy sides, printed-field classes, exact 0.1%/zero-total boundaries,
  threshold rules and excitation cancellation. Seven independent mutations fail; production behavior is unchanged.
- Closed the post-release P17 open-validation phase with its frozen `P17-FAIL` verdict intact. Same-operator,
  identical-data, processing, provenance, independent-arithmetic and quality controls pass; all 94 held-out rows and
  every unsupported case remain visible. The failure records three falsified benchmark assumptions and changes no
  production behavior, public interface, default, package or nuclear-data artifact.
- Completed P18b G2--G4. The complete four-corpus classification reproduced P18's defect counts under the separated
  conformance model; runtime conservation enforcement with fail-closed quarantine replaced the retired stress gate;
  and the diagnostic scoring leg, compatibility/performance leg and independent checker all passed, authorizing the
  one-time held-out unseal.
- Closed P18b with a `P18b-FAIL` verdict at G5. On the 180 held-out families the candidate's overall metrics pass
  with 6,912 provable rank-artifact identity corrections, but the frozen per-stratum gate fails: proton regresses on
  median and within-30% coverage, alpha regresses on p90, and deuteron has zero candidate-scored rows because
  genuine TENDL-2025 state-partial defects fail closed at construction. The coverage-limited first execution is
  preserved in the record; G6 release artifacts are not authorized and v1.0.1 remains the public release.
- Closed P25b with a `P25b-FAIL` verdict after upstream confirmed the TENDL-2025 defect root cause (an unflushed
  TALYS `channelsout.f90` array leaking thermal (n,p) cross sections into (n,2n) ground-state records; fix lands in
  the next TENDL release). Three hash-pinned alternates — TENDL-2023, FENDL-3.2c and EAF-2010 — were qualified
  against the frozen machinery: all met their frozen coverage floors under the v1.0.1 builder but miss them under
  the release-candidate builder's stricter state validation, and none survives comparable-case nonregression.
  TENDL-2023 carries the same emitted-sum>total defect class; EAF-2010 cannot express isomeric identity under the
  current builder. All scoring was retrospective. Added `docs/DATA_LIMITATIONS.md` as the release-facing disclosure
  for the shipped data artifacts.

**Fixed**

- Scoped P16's historical borrow-workaround assertion to its frozen, already-passed source-evidence commit. Later
  phases may add legitimate owned values without being misclassified as changes to P16's typed-unit implementation;
  current typed boundaries and the repository-wide `unsafe` prohibition remain live checks.
- Removed heuristic per-reaction excited-level rank compression from new TENDL library builds. In particular, the
  sparse Ag-110 raw level 2 now maps by its 117.59 keV physical identity to decay isomer `m1`.
- The crates.io trusted-publisher environment now matches the registry's configured `crates.io` OIDC identity. A
  protected manual recovery path can resume publication from an existing version-matching release tag without moving
  or reusing that tag.

## v1.0.1 — 2026-08-28

**Added**

- Automatic, deterministic prepared activation-data caches bound to the exact source library, index, schema and flux
  bits, with atomic publication, fail-closed integrity checks and safe deletion/recreation.
- Indexed sparse groupwise reads for prepared and mesh workflows, plus an exact spectrum-collapsed path for ordinary
  runs. The Python interface shares the same Rust cache and solver path.
- Independent exhaustive representation, cache-mutation, interface and frozen opening-binary performance controls.

**Changed**

- On the public FNS iron example and recorded x86-64 host, the warm path's median wall time fell from 3.075 s to
  1.185 s and peak RSS from 1.077 GB to 129 MB, while the normalized result and certificate provenance remained exact.
  These measurements describe that workload, host and warm-cache state rather than a general competitor claim.
- Release validation now derives the current software version from package metadata and keeps the independently
  versioned `data-v1.0.0` catalog distinct during wheel smoke tests.

**Fixed**

- Patch-version provenance no longer changes the frozen P15 scientific-result hash. Raw result certificates still
  retain the exact solver version, and the normalized control accepts only the documented solver-semver field.
- Standalone crate validation now resolves temporary package lockfiles offline, proves their external dependency
  records are a subset of the workspace lock, and keeps compiler temporary files inside the selected gate work root.

## v1.0.0 — 2026-08-28

**Added**

- Strict ENDF-6 MF=33 LB=0--6/8/9 parsing and deterministic, checkpointed `actinv-covariance-1` sidecars linked to
  their activation library, group structure and source manifest.
- Selectable CRAM-16/48 plus exact differentiation of the selected incomplete-partial-fraction recurrence for local
  heat/activity sensitivities through irradiation, pulses and cooling.
- Per-response MF=33 standard uncertainties, normal intervals, separate alternate-CRAM bounds, conservative expanded
  intervals, complete parameter metadata and explicit covered/uncovered/absent-cross accounting through CLI, PyO3,
  prepared and mesh entry points.
- Independent Python/dense/NJOY/OpenMC controls and a complete 2,850-source covariance scan with fresh/cached byte
  identity, bounded memory and zero parsing omissions.
- Hash-pinned clearance, waste, ingestion and inhalation response tables with explicit scenario metadata, coefficient
  coverage and identical CLI/Python/prepared/mesh results.
- Independent primary-source regeneration of all 289 embedded natural-abundance and atomic-mass rows.
- A deterministic one-million-case, eleven-family production-reader reliability gate with a repeated CI partition
  and a strict 1 GiB process ceiling.
- FNG/ITER cell-620 activation-history reproduction at 170 endpoints from a fully hash-pinned published archive.
- Stable-ABI Python wheels for Python 3.9+, standalone release binaries, a public artifact workflow and user-facing
  installation, release and qualification documentation.
- The PyPI wheel installs both `import actinv` and the full `actinv` terminal command through one compiled Rust-backed
  package, with native wheel smoke tests on Linux, macOS and Windows.
- A strict embedded `actinv-data-catalog-1` manifest and `actinv data list/fetch/verify/manifest` commands.
- Versioned, atomic, SHA-256-verified setup for the exact P10 TENDL-2025 activation libraries, the matching P11
  neutron covariance sidecar, and official ENDF/B-VIII.0/JEFF-3.3 decay archives.
- Data attribution, offline/manual setup guidance, release staging, and independent distribution controls without
  committing nuclear-data payloads to Git.

**Fixed**

- Covariance checkpoint identity is source-local, and full sidecar aggregation retains one grid map and validates the
  combined output once instead of rebuilding growing prefixes quadratically.
- The local CI end-to-end control can load an explicitly built PyO3 extension when no wheel is installed, while still
  preferring and testing the installed package in release CI.
- ENDF, decay, fission-yield and activation-library readers now prove declared payload sizes before reserving memory;
  path and in-memory entry points share the same production implementations.
- CLI and Python package versions now agree at `1.0.0`; `actinv --version` and `actinv --help` are supported and
  regression-tested.

## v0.5.0 — 2026-08-27

**Added**

- Explicit ground-state/isomer material keys across weight-percent, atom-fraction and literal atoms-per-gram bases,
  with evaluated AWR provenance and ambiguity checks.
- Strict, hash-pinned ENDF-6 MF=8/MT=454/459 fission-yield parsing; independent-yield interpolation/selection; and
  yield-expanded MT=18 matrix feeds with complete mapped/leakage balance.
- Per-boundary elapsed time, multiplier-weighted exposure and physical fluence for arbitrary piecewise-constant pulse
  histories, through ordinary and mesh runs.
- Independent P9 controls against dense exponentials, OpenMC CRAM48, ALARA 2.9.2 and the CoNDERC U-235 Dickens pulse
  and Yarnell 20,000 s decay-heat sets.
- A strict, deterministic Rust ENDF-6 activation-library builder with content-addressed per-target checkpoints and
  neutron, proton, deuteron and alpha runtime contracts.
- R-matrix-limited resolved reconstruction, infinite-dilution `LSSF=0` unresolved averages, arbitrary-temperature
  SIGMA1 broadening, analytic ultra-narrow lines and charged-particle MF=6 residual production.
- Complete hash-pinned TENDL-2025 neutron/proton/deuteron/alpha and EAF-2010 builds: 12,216 targets and 1,849,479 rows,
  all fresh/cached byte-identical with zero target errors, silent fallbacks or convergence flags.
- Independent P10 controls against NJOY2016.79, FENDL ACE, official TENDL residual tables and official processed
  FISPACT TENDL-2017 rows; no licensed FISPACT executable run is claimed.

**Fixed**

- Automatic trace/coupled selection now uses each initial isotope's reaction-loss optical depth over the complete
  multiplier-weighted schedule; non-unit pulse multipliers and cooling gaps can no longer be miscounted.
- Scientific-notation durations such as `1e-8 s` no longer treat the exponent marker as a unit suffix.
- Deep but bounded resonance linearization now admits the corpus-observed pass-19 Co-58 kink while retaining the
  unchanged error tolerance and ten-million-point safety cap.
- Rust and independent controls use NJOY component-effective Breit-Wigner widths consistently when evaluator `GT`
  rounding differs from the component sum.

## v0.2.0 — 2026-08-26

**Added**

- Hashed `actinv-flux-1` NDJSON interchange and strict importers for OpenMC statepoint-format-18 mesh flux, traditional
  MCNP neutron MESHTAL, energy-binned F4:N MCTAL, and standard FISPACT-II `fluxes` files.
- Conservative equal-flux-per-lethargy rebinning with exact-grid identity and explicit underflow/overflow closure.
- `actinv-mesh-spec-1` and a bounded, ordered Rayon mesh runner that prepares immutable nuclear data once and invokes
  the ordinary independently pruned solver for every streamed cell.
- Independent P8 controls for format values and rejection paths, provenance, mesh identity/thread determinism,
  bounded HDF5 reads and measured/extrapolated sizing through one million cells.
- ENDF-6 MF=8/MT=457 discrete and continuous decay-photon parsing, including ENDF interpolation metadata.
- Per-step evaluated line and FISPACT-24/custom multigroup photon sources with explicit `E_EM` energy normalization,
  missing-spectrum bounds and per-gram/total strengths.
- NIST-response specific gamma constants and a clearly labelled FISPACT semi-infinite-slab contact air-dose proxy.
- OpenMC `IndependentSource` and MCNP `SDEF` photon-source exports.
- Reproducible external NIST photon-response builder and independent P7 G1–G6 controls.

**Fixed**

- Canonical flux streams now reject duplicate cell IDs as well as malformed ordering/counts, standard FISPACT input
  rejects blank identifying titles, and mesh-output path aliases cannot replace their canonical input. Import and mesh
  outputs publish atomically only after a validated footer.
- Truncated fixed-width activation-library records now fail instead of silently dropping the incomplete tail.
- Prepared execution removes repeated activation-library/decay-chain parsing from mesh cells without changing any
  pre-v0.2 ordinary result field.
- Certificates now compute and verify hashes for the activation library, its index, primary/fallback decay files and
  photon response; declarations are no longer repeated without verification.
- `atom_fraction` and `atoms_per_g` material bases now follow their documented meanings, including response mass
  fractions; non-finite specification values and mismatched custom/library boundaries fail validation.
- Rate-pruning bounds and relevant activation-library convergence/unsupported-feature guards now propagate to each
  run ledger as promised by the v0.1 documentation.
- Radioactive constant-bulk components now contribute to split alpha/beta/gamma heat and photon activity in trace mode.
- PyO3 updated from 0.22 to 0.29 so the binding builds normally with Python 3.14.

## v0.1.0 — 2026-08-27

First release. ACTINV computes nuclide inventories, activity and decay heat from any neutron flux spectrum.

**Added**
- Own ENDF-6 data pipeline: parsing, resolved-resonance reconstruction (SLBW, MLBW, Reich–Moore), SIGMA1 Doppler
  broadening, 709-group collapse — verified against IAEA's NJOY-processed cross sections to 2.3e-3.
- Activation libraries built by that pipeline from EAF-2010 (816 targets) and TENDL-2023 (2,847 targets).
- Decay data from ENDF/B-VIII.0 with a JEFF-3.3 fallback; the source of each nuclide's data is recorded.
- Rust solver: CRAM-16 with an in-house sparse complex LU; trace and coupled formulations selected from the recorded
  burn-up fraction; reachable-set and rate-significance pruning with bounds on what was removed.
- `actinv-spec-1` problem specification; `actinv run`, a Python module, and the validation harness as three entry
  points to one binary.
- Pathway analysis: ranked production chains per nuclide, exact by linearity.
- A missing-data ledger and an input-hashing certificate on every run, including the method's numerical floor.
- Validation against the 132-experiment FNS decay-heat benchmark, re-derivable by the shipped checkers.

**Known limitations** — see `docs/RELEASE_NOTES_v0.1.md`; each is reported by the code rather than hidden.

**Not included** — neutron transport, criticality, fission yields, decay-photon transport, covariance uncertainty.

**Fixed**

- Scoped the P18 and P18b release-boundary checks to their actual frozen guarantee — no tagged or
  published 1.1.0 release may exist — rather than the workspace version string. The workspace may carry
  the 1.1.0 release-candidate version only while the green P22 release-candidate record stands and no
  `v1.1*` tag does; P18-FAIL and P18b-FAIL remain unchanged.
