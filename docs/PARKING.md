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
- 2026-09-23 — `options.outputs` default `null` computes pathway analysis and pathway closure over the whole decay
  chain at ~90 s per schedule step on a 3873-state fission chain (measured 248 s vs 1.4 s per solve on the P43 U235
  case): output selection is the dominant per-solve cost on wide chains, not the matrix exponential. Study campaigns
  must restrict `outputs` to what the declared responses need; a `pathways` request on fission-bearing material is a
  deliberately expensive diagnostic. Route to P43's campaign record and any future campaign machinery.
- 2026-09-23 — P11 first-order propagation scales its tangent system with covered MF=33 rows, which grow with
  product targets in the chain: on a 1263-product fission chain the covered-row count is far larger than on a
  46-state structural chain, and the augmented tangent solve can dominate campaign cost. If P43's campaign envelope
  needs relief, cap or restrict the local comparison rather than shrinking samples.
- 2026-09-24 — correction to the previous entry's mechanism, measured during P43 G1: the tangent parameter set is
  built from the union of *all active chain reaction rows* (production rows into populated states), not from
  covered covariance rows. The U235 case carried only ~100 covered rows / 23 applied rows yet built ~15k tangent
  parameters (~3.1 GB RSS, >45 min unsolved at kill time vs seconds on the 46-state Fe chain). A
  covered-row-restricted tangent build would collapse the diagnostic's cost on fissile chains by ~150x — the real
  fix if the local-vs-nonlinear comparison is wanted inside campaign envelopes; until then
  `robustness.first_order_comparison=false` is the supported relief (P43 campaign + mechanics both use it).
- 2026-09-24 — P44 measured-coverage finding: the covariance-bound patched activation library
  (`tendl-2025-patched-neutron-709g.npz`, 1679 targets) is a *subset* of full TENDL-2025 (2850 targets) — the
  MF=33-covered working set. Dropped targets include natural stable isotopes for ~26 elements: every natural
  isotope of Ag (107,109), Au (197), Nb (93), Ta (181), Ir (191,193); partial loss for Ba (6/7), Bi (1/1),
  Br, Cd, Cs, Dy, Er, Eu, Ge, Hf, Hg, I, K, La, Ni (1/5), Os, Pd, Te, W (3/5), Yb, Cu. On the sealed FNS
  partition these drive zero or partial nominals (2.9% pooled band coverage on affected materials vs 39.3%
  on isotope-complete materials). Repair options, in order of increasing scope: (a) ship a full-library build
  that keeps uncovered targets with an explicit `uncovered` row flag — the library should never drop a target
  silently because it lacks covariance; (b) extend the covariance sidecar over all available MF=33; (c) treat
  missing-target activation as a model-remainder channel in band definitions. Any future band-coverage
  rescoring on the full library is a new sealed scoring.
- 2026-09-24 — P44 excluded the IRDFF-II SACS arm named in the draft: its scoring unit is a folded
  spectrum-averaged cross section, not an activation response, and no processed SACS activation corpus exists
  on this host (only raw ENDF archives and PDFs). If a SACS arm is wanted, it is a dedicated phase with its own
  corpus build + protocol, not an extension of the activation-response coverage scorer.
- 2026-09-24 — P44 band-definition scope note: measured bands covered MF=33 + decay constants at 68.27% —
  no flux/composition/fission-yield channels (FNS materials are non-fissile; flux/composition treated as
  measurement-side inputs) and no model remainder. The measured ~39% coverage on isotope-complete materials
  quantifies what the missing remainder must supply before a PASS-level calibration claim; options: declared
  uncovered-remainder width, evaluation spread (P46), or a fitted remainder with its own sealed population.

## Feature backlog — consolidated 2026-09-15

The maintainer asked for the remaining feature set in one place. Earlier entries above stay append-only; this
section is the current consolidated backlog. Several originally parked items have since shipped: fission yields and
actinide fission (P9), explicit isotope/isomer material keys (P9 composition keys), R-matrix-limited LRF=7 and
unresolved LSSF=0 reconstruction (P10), prepared artifacts, selective loading and mapped arrays (P15), unit-safe
quantity types and the broader metamorphic suite (P16), finite-dilution self-shielding (P19), practical correlated
uncertainty (P20), streamed mesh execution (P21), and feed/removal, reverse calculation and damage observables
(P23). What remains, including the 2026-09-14 entries above (standalone material definitions, continuous-energy
collapse, geometry-aware self-shielding, nonlinear uncertainty propagation, reaction introspection, decay-photon
tally-to-dose, browser/WebAssembly delivery and dose-response modes, each already routed there):

1. **Additional projectile sublibraries** — triton, helion and gamma TENDL sublibraries (parked 2026-08-26).
   Routed to P28's physics-envelope qualification; no P26+ phase opens it before then.
2. **External schema interoperability** — versioned intake adapters that detect common handoff formats and convert
   into the strict `actinv-spec-1` canonical schema with a conversion receipt (2026-09-15 HYPERION intake note in
   `docs/ROADMAP.md`). Routed to P27.
3. **Spatial/R2S handoff** — external-transport source integration and an executed open R2S chain against OpenMC.
   Routed to P32.
4. **FNS C/E gap — diagnosed** (`docs/FNS_GAP_DIAGNOSIS.md`, 2026-09-24): on identical data
   (TENDL-2017) ACTINV matches FISPACT (0.1043 vs 0.1053 median |ln C/E|, 2,279 joined points); the
   published deficit was a TENDL-2025 evaluation regression on ~8–10 materials' short-lived channels
   (Cr-55/V-52 from Mn-55 (n,p)/(n,α) +33%/+20%; W-185m1/W-183m1 isomer branches; Pb-207m1). Remaining
   open sub-questions are upstream-data ones: the all-corpora W isomer overprediction and the shared
   Pb late-time production gap (Pb-210→Po-210 chain candidate). A quantified TENDL-2025-vs-2017
   regression report to upstream is a candidate but unscheduled.
5. **Blind many-nuclide FISPACT comparison** — needs lawfully licensed FISPACT access or a collaborator; optional
   runner design stays recorded, no gate waits for it. Routed to P35's competitive qualification.
6. **Missing EAF-2010 decay products** — 18 products (W-193, Re-195, Os-197…201, Ir-200…202, Pt-203, Au-206,
   U-243…245, Np-245/246, Am-250) have no evaluated decay data in ENDF/B-VIII.0 or JEFF-3.3. No known library
   supplies them; stays out unless one appears.
7. **Remaining TENDL-2025 defect classes** — the 46 non-signature defect files and 5 unrecovered
   dosimetry-critical targets (Ni-58, Nb-93, Ag-109, In-113, Au-197) documented in `docs/DATA_LIMITATIONS.md`.
   Remediation waits on corrected upstream TENDL; a further repair phase would need its own protocol.
8. **AI-assisted setup and bounded investigations** — provider-credential setup, validated study drafts,
   AI-initiated runs through Core contracts. Routed to P33–P34 under the no-hosting constraint.

## P48 parked items (2026-09-24)

9. **Library/evaluation-selection sweep axis** — declared in the P48 scope but not shipped; swapping the
   library path/hash per point is a discrete-selection axis that needs a UI for choosing among the P46
   qualified corpora plus per-corpus sha resolution. Deferred; the three numeric axes shipped carry the
   measured interactive-latency claim.
10. **Amortized interactive latency** — P48's measured 4.6 s/point includes a fresh isolated worker
    (spawn + library load) per point. A persistent-worker design could amortize library load across
    points; deferred until the claim "interactive" needs to be faster, and any such design must keep
    the identical solver path and per-point spec binding.
11. **Full-coverage patched corpus rebuild** — the `tendl-2025-patched` NPZ carries only the 1,678 targets that
    survived the fail-closed conservation check; 1,172 files (incl. natural Ag/Au/Nb/Ta/Ir isotopes) are evicted
    with ledgered residual defect classes. A rebuild that emits those targets with the residual defects
    documented-but-present (rather than fail-closed) would give a corpus with both the confirmed-signature repair
    *and* full coverage — the "best of both" default. Deferred: it changes what the solver emits for defective
    evaluations, a data-policy decision that needs its own protocol. Default flipped to full `tendl-2025` in the
    meantime (P46 evidence).

## P49 parked items (2026-09-24)

12. **Per-candidate band cost** — a 13-step spec with 4 propagated responses costs ~8–15 min of wall per
    candidate (the full 24-evaluation RA-steel campaign ≈ 3.3 h solve wall). The optimizer is therefore a
    batch tool by design; any *interactive* optimization surface needs parked item 10 (persistent worker)
    first, and even then only for nominal-edge objectives — band propagation itself is the dominant cost,
    not process spawn. Possible accelerations within the identical solver path: early-exit once a
    constraint is violated, coarse-step prescreen, or cheaper response sets — each is a numerics-policy
    decision needing its own protocol.

## P50–P52 extension parked items (2026-09-25)

13. **Probabilistic clearance classification** — a "probability this component clears free-release"
    classifier with quantified misclassification risk. Existing clearance tools (clearance-finder,
    F4E-radwaste) are deterministic; nobody ships band-driven classification. Deferred from the
    P50–P52 draft as a later candidate — narrow but money-adjacent. **Promoted 2026-09-25 to
    candidate lane P54** in the strategic positioning assessment (decommissioning reach + moat).

14. **Inverse irradiation-history estimation** — measured sample inventories → inferred exposure
    history/composition (decommissioning and assay forensics). Nobody ships it as a product; the
    validation story is harder and the market narrower than the P50–P52 lanes. **Promoted
    2026-09-25 to candidate lane P55** in the strategic positioning assessment (structural moat:
    requires many-query solves no MC-coupled solver can host).
