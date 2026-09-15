# Parking lot

Discoveries that are out of the open phase's scope. Dated, append-only. Each item is either scheduled into a phase
of `ROADMAP.md` or explicitly left out of v1.0.

- 2026-08-25 — 18 EAF-2010 products (W-193, Re-195, Os-197…201, Ir-200…202, Pt-203, Au-206, U-243…245, Np-245/246,
  Am-250) have no evaluated decay data in ENDF/B-VIII.0 or JEFF-3.3. Ledgered as such; left out of v1.0 unless a
  library appears (none known).
- 2026-08-25 — fission (MT=18) on actinide targets booked to leakage: no yields yet → P9.
- 2026-08-26 — R-matrix-limited (LRF=7) resolved ranges (FENDL W-186) unsupported → P10.
- 2026-08-26 — unresolved ranges with LSSF=0 (FENDL Ag-107) unsupported → P10.
- 2026-08-26 — abundance and atomic-mass tables copied from openmc.data; independent re-verification → P12.
- 2026-08-26 — ψ-function reference disagrees with the exact kernel by ~1 % beyond ±5 Γ (Gaussian-in-energy
  approximation); information only; no action.
- 2026-08-26 — TENDL-2023 vs EAF-2010 Fe-56 on the FNS spectrum: (n,2n) −19 %, (n,γ) +60 %; investigate which is
  closer to measurement once the TENDL library exists → P4 report.
- 2026-08-26 — CRAM round-off on equilibrium components (2e-11 relative per component; 3e-9 on heat) limits
  cross-network agreement criteria; consider CRAM-48 or substepping for tighter reproducibility → P11 (uncertainty
  budget must include it).
- 2026-08-26 — openmc 0.15.3 wheel lacks its compiled resonance module; do not rely on openmc for reconstruction
  references; NJOY-processed ACE of the same evaluation is the reference.
- 2026-08-26 — TENDL synthetic resonances 1e-7…1e-5 eV wide (Fr-226, Rb-94 class): group values converge to 2e-3–1.5e-2, not 1e-3, between grid densities; flagged in the library index. Refine sampling or treat analytically → P10.
- 2026-08-26 — SIGMA1 cost is dominated by output points in the smooth thermal region, where the exact kernel laws
  (1/v invariant; constant → σ₀(1+1/(2y²))) make the correction analytic. Broadening only where σ departs from
  linear-in-E across the window, or reformulating as per-group kernel weights (≈130 grid points per group), would cut
  the dominant cost by ~10–100× → P10, with the exact-quadrature control as the gate.
- 2026-08-26 — the original P5 draft claimed explicit isotope/isomer material keys (`Fe56`, `Ta180m`), but the shipped
  parser only implements natural elemental compositions. P7 corrected the normative documentation and the three
  elemental bases rather than pretending isotope keys work. Explicit isotope/isomer compositions are needed before
  coupled fuel/fission work and are routed to P9.
- 2026-08-26 — finite-dilution unresolved self-shielding, Bondarenko factors and probability-table use are distinct
  from P10's required infinite-dilution LSSF=0 averages. They remain out of v1.0 unless a later licensing use case
  explicitly requires them; ACTINV must not imply them from the P10 implementation.
- 2026-08-26 — TENDL-2025 also publishes triton, helion and gamma incident-particle sublibraries. The v1.0 roadmap
  names neutron plus proton/deuteron/alpha only, so those three additional projectiles remain post-v1.0 scope.
- 2026-08-28 — CB1 cannot isolate ACTINV/FISPACT solver differences from the public FNS comparison because the
  available rows use ACTINV/TENDL-2025 and FISPACT-II 4.0/TENDL-2017. A many-nuclide run on byte-identical processed
  data is the highest-priority competitive-validation follow-up; it requires lawful FISPACT access or a collaborator.
- 2026-08-28 — In the fresh 132-experiment FNS family, FISPACT's public result leads ACTINV on median point
  `abs(ln(C/E))` (`0.1053` versus `0.1392`), median experiment maximum, and all-points-within-30% coverage (`69/132`
  versus `59/132`). Diagnose reaction/data contributions on held-out evidence; do not tune against the scored family.
- 2026-08-28 — CB1 measured `1.09 GB` peak RSS for the warm-cache public example while hashing/parsing `237.9 MB` of
  input data. Evaluate memory mapping, a hash-bound prepared cache, and narrower decay loading without weakening
  provenance or changing calculated results.
- 2026-08-28 — Physical quantities at ACTINV's Rust boundaries remain unit-named `f64` values rather than distinct
  zero-cost domain types. A scoped units design and broader metamorphic relations (linear scaling, schedule splitting,
  analytic decay, rebin and conservation) are post-CB1 quality work; neither is smuggled into the frozen scorecard.
- 2026-08-28 — Feed/removal, reverse calculation and damage observables are confirmed ACTINV capability gaps;
  gamma/triton/helion activation remains parked above. They are demand-led post-v1.0 candidates, not automatic scope
  merely to make the competitive matrix uniformly green.
- 2026-08-28 — the maintainer cannot justify a FISPACT licence cost. P17 therefore uses analytic/dense/OpenMC solver
  controls, NJOY processing controls, identical-data ALARA/OpenMC networks and held-out measurements. A blind runner
  for a future lawfully licensed collaborator is optional; no roadmap gate waits for it.
- 2026-08-28 — CB1's `1.09 GB` public-example finding is routed first to P14 measurement and safe redundant-work
  removal, then to P15's versioned prepared artifact and selective loading. P14 may not smuggle in P15's format or
  architecture changes.
- 2026-08-28 — physical quantity types and the broader metamorphic suite are routed to P16; open accuracy attribution
  to P17; only P17-demonstrated repair classes to P18; finite-dilution self-shielding to P19; practical correlated
  uncertainty to P20; and executed large-scale/streamed mesh evidence to P21. P22 re-scores the complete product.
- 2026-08-28 — P14 measured activation-library read, deflate decode and validation at a roughly 1.79 s median and
  identified the fully decoded 951,393,048-byte cross-section array as the main explanation for the roughly 1.08 GB
  peak RSS. Deterministic prepared storage, target-selective loading, safe cache reuse and mapped immutable arrays
  remain P15 scope; the below-threshold P14 close does not authorize implementing them retroactively.
- 2026-09-14 — standalone material construction should accept provenance-pinned named material definitions and
  explicit user definitions through natural elements/isotopes, enrichment, chemical formulas, mixtures, absolute
  atom densities, density and volume. Named catalog entries must expand to explicit composition/density in the run
  record rather than becoming opaque aliases. Route into the P27/P28 replan after P26's recorded disposition.
- 2026-09-14 — add an optional direct pointwise/continuous-energy activation-collapse path against the supplied
  spectrum, alongside the existing deterministic multigroup-library path. Preserve the current reproducible grouped
  route; qualify the direct path on independently processed data and expose the data/profile identity and applicable
  temperature. Named qualified data profiles may select cross sections, reaction topology/branching, decay and
  fission-yield sources without hiding their exact versions or hashes. Route to P28.
- 2026-09-14 — unresolved-range Bondarenko self-shielding does not cover a geometry-aware resolved-resonance flux
  depression. Add a qualified option that takes explicit mean chord or supported lump shape/dimensions, never guesses
  geometry, treats mixture total/elastic scattering consistently, records what was and was not shielded, and warns
  when an unshielded run contains resonance absorbers for which the omitted correction may matter. Route to P28.
- 2026-09-14 — make nonlinear uncertainty propagation a shipped analysis path rather than only a verification oracle:
  fixed or convergence-controlled replica solves, MF=33 activation-cross-section covariance plus user-supplied
  per-bin flux-spectrum uncertainty, complete sample/failure accounting, coverage diagnostics, and per-replica
  recomputation of nonlinear derived outputs such as contact-photon screening and line spectra. Preserve P20's local
  bands as the efficient linear path and route the nonlinear feature to P30.
- 2026-09-14 — expose analysis-grade reaction introspection needed to explain a result without private internals:
  per-step reaction rates, group-resolved rate contributions, flux-weighted isomeric branching and weighted
  production routes, all tied to the same data identities and ledger. Existing pathway output satisfies part of this;
  only missing surfaces should be added. Route to P30.
- 2026-09-14 — add schedule-aware time correction of externally transported decay-photon tallies into shutdown dose
  responses, including mixed irradiation spectra, selected cooling steps, per-nuclide contributions and propagation
  of tally statistical variance. Keep this distinct from ACTINV's existing source export and from claiming dose
  without an executed transport stage. Route to P32.
- 2026-09-14 — qualify a zero-install browser/WebAssembly route for the supported standalone calculation subset using
  the same scientific Rust core and locally executed solves. Nuclear-data bytes may be fetched by the host but no
  problem or result should require upload to an Avila service; browser/native scientific identity and explicit
  unsupported-feature reporting are required. Route to P32.
- 2026-09-14 — contact-photon screening should support clearly named absorbed-air and configurable effective-dose
  response modes with pinned coefficient provenance and the same model-limit warnings as the existing contact proxy;
  no screening approximation may be presented as transported dose. Route to P28/P32 as appropriate.
