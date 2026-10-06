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
- 2026-09-26 — **measured refutation of the SIGMA1 linear-window claim** (P59 exploration, reverted
  uncommitted): a sparse-table linear-window collapse of `doppler::broaden` — algebraically exact where it
  fires (max rel deviation 1.5e-15), tolerance-insensitive — fired on 216/209,724 windows (0.1%) on
  W-182's zero-K-refined table at tol=1e-12 **and identically at tol=1e-6**, and 114/1,542 (7.4%) on
  N-14, netting 0.93–1.11× (a loss to parity, before O(n log n) table-build overhead). The premise —
  "output points ≫ input in smooth windows" — does not occur: `process_reaction` builds the output grid
  from the refined input itself (≈1:1), and every 8σ window spans genuine slope structure by construction
  of the refinement. The 10–100× claim dies against the real pipeline shape; the remaining avenue, if
  revisited, is the per-group-kernel-weight reformulation at the *unprocessed* MF3 stage where a coarse
  input meets a dense evaluator grid — no production path exercises that today. Do not re-open without
  a pipeline shape that actually emits output≫input.
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

## ENDF-8 head-to-head parked items (2026-09-26)

From the identical-data FNS exercise (`results/FNS_ENDF8_HEADTOHEAD.md`,
`~/nuclear-data/endfb-viii.1-fns-arm/`). Ordered by the principal's
priority — each needs its own protocol before any code changes.

1. **Normalization layer — SHIPPED 2026-09-26.** `--profile endfb8`
   ingests PRISTINE ENDF/B-VIII.1 tapes: the 269-file FNS arm builds
   269/269 — full coverage, matching the hand-patched arm plus the
   three feature-gap files closed same day. Normalization classes:
   LRF=0 degenerate range CONT, BW total-width deficit/omission under
   LRX=0, unresolved case-A/B/C dof rounding (AMUX/AMUN/AMUF),
   unresolved zero-width eps under log laws, RML photon-pair
   PNT/SHF=-1, MF=2 TAB1 abscissa ordering, ZA/AWR/TITLE comment scrub,
   plus collapse-level MF9/MF10 state-sum reconciliation via
   `normalize_state_sums`. Every action lands in the per-source ledger
   in the index; `NormalizeProfile::None` is byte-identical and all
   normalization is gated on MF2 range context (LRU/LRF) to prevent
   cross-structure corruption. **RML feature support shipped the same
   day**: charged-channel Coulomb penetrability/shift (Steed's
   continued-fraction method per DLMF 33.8 with propagation fallback,
   ENDF-6 D.80–D.85 conventions, verified against mpmath references),
   KBK background R-matrix elements (LBK=0/1/2 including the SAMMY
   logarithmic form D.77 used by Sr88), and KPS tabulated phase shifts
   (LPS=0/1, positional channel order). `inspect_projectile` now uses
   the state-audit parse so defective MF2 payloads can't break
   projectile detection before normalization runs. LBK=3 (Fröhner)
   remains a clear not-implemented error, not silent wrong physics.

2. **Workflow breadth** — OpenMC does transport→deplete→R2S end-to-end;
   ACTINV needs a handed spectrum. Explicitly not pursued as a gap to
   close by re-implementation; the strategy question is whether
   complementing OpenMC (import-flux → solve → export source) is the
   lane instead.

3. **Accuracy tail vs FISPACT-TENDL — CLOSED (2026-09-26).** Root
   cause was the legacy Python builder's rank-compressed LFS→LISO.
   The Rust builder's decay-aware path (physical LIS/ELIS resolution
   against ENDF-8.0 + JEFF-3.3 fallback) fixed it; the new TENDL-2023
   decay-aware library (`actinv_tendl2023_fns_decay_709g.npz`,
   519 targets incl. 266 isomer evals, zero build failures) scores
   gm 1.097 across FNS vs old rank-mapped arm 1.249 and FISPACT-T17
   1.244. Standouts: In 29.2→1.9 (old arm's catastrophic misroute),
   W 1.84→2.45 and Zn 0.90→0.63 regressed — TENDL-2023-vs-2017 data
   differences, now physically routed; Sn ~1.33 and Ta ~0.85 are
   unchanged (their residual is decay-data/XS-version, not routing —
   TENDL-2023's Ta-181 lacks the 15.8-min Ta-182m capture channel
   entirely; TENDL-2025 declares it as LFS=29). Enabling work landed
   this session:
   `NormalizeProfile::Tendl` (state-sum reconciliation + fix_bw_gt +
   fix_nan_fields, orphan MF6/MF8/descriptor tolerance; confirmed
   misroute exemplar Tb-156, 35 rows raw-LFS 3→LISO 2), RML direct
   γ-capture reconstruction via Woodbury (kills the 1−P cancellation
   noise that linearization could never resolve), generic
   Riccati–Bessel L≤∞ path (TENDL O-16 uses L=5), RML channel-threshold
   boundary seeding, `--continue-on-error`. Remaining tail is
   decay-data disagreement (ENDF-8.0 vs EAF-2017 deposited energies
   differ up to ~30× on individual isomers, e.g. Ta-182m 506 vs
   ~16 keV/decay) — not closable while arms use different decay
   libraries.

4. **W overshoot — RESOLVED (not an ACTINV defect).** All
   isomer-carrying arms overshoot ~1.9x early (ACTINV-E8 1.83,
   ACTINV-T17 1.94, FISPACT-T17 1.97 at 50 s); ACTINV is within ~3% of
   FISPACT on identical XS. OpenMC's low gm is a mis-shaped curve
   (0.03 at 50 s → ~1.7 late) from the missing W-185m channel.
   Root cause is W-185m evaluation-vs-measurement tension, shared by
   every code.

5. **Coverage dropouts** — `products_no_evaluated_decay_data`,
   `fission_no_yields_to_leakage`, isomer→ground rerouting, negative
   atoms zeroed: small documented leakages on every run. FISPACT's
   reference-data coverage is broader.

- 2026-09-27 — **Banded re-run of the decay-aware TENDL-2023 corpus is
  blocked on a matched covariance artifact.** `results/fns_tendl_decay/`
  is deterministic (no `uncertainty` blocks); the P63 coverage instrument
  needs banded runs, and `p43.cov.npz` was built against the TENDL-2025
  activation library. Producing `*.cov.npz` for
  `actinv_tendl2023_fns_decay_709g.npz` needs TENDL-2023 MF33 covariance
  evaluations (the `p43-work/cov-inputs` set is TENDL-2025 vintage).
  Interim evidence: `controls/d2_empirical_calibrate.py` fits the
  declared unmodeled term empirically on the deterministic corpus with a
  fit/holdout split — `results/d2_calibration.json` (u_pooled = 0.437,
  held-out u@68% = 0.306). The banded leg lands when the cov inputs are
  fetched/built → re-run corpus with `uncertainty` → re-emit
  `actinv-calibration-1` → compare coverage vs the sealed 0.360 baseline.

- 2026-10-03 — **Proposed extension: `actinv waste` (U.S. Part 61 classification + fusion intrusion screen +
  impurity limits per waste class).** Not scheduled. Classification rules alone are not novel (RADMAN, 1982); the
  value is reuse of the `budget` composition linearity for class limits and band-aware classification. Design,
  sources and two discrepancies found in draft NUREG-1556 Vol. 22 (Cs-137 Class C 460 vs §61.55's 4,600; table
  numbering) in `WASTE_CLASSIFICATION_EXTENSION.md`.
- 2026-10-03 — **`actinv waste` promoted to the planned roadmap at the owner's direction.** See
  [ROADMAP.md — planned component waste classification extension](ROADMAP.md#planned-extension--component-waste-classification-and-material-impurity-budgets-2026-10-03).
  The initial phase covers nominal single-component U.S. Part 61 classification and verified class impurity
  budgets. Uncertainty/composition-range classification and the opt-in draft fusion screen are separately gated;
  mixed packages, scaling factors and additional jurisdictions remain later scope. No phase is open or hashed.
- 2026-10-03 — **Nominal waste layer closed P105-PASS.** `actinv waste` and `actinv waste budget` are implemented
  and all six workflows on `a1d2dc3397ace8b3d22c2e8ade8127cf4d5086e2` are green. The earlier entries above
  describe the promotion-time status. Conservative activity bounds, composition ranges and the draft fusion
  screen still require separate protocols; P103/P104 failures remain unchanged.
- 2026-10-03 — **Native/budget modeled-inventory audit coverage.** Review after P108 found
  `budget::solve_doc` forces outputs to `ledger`, omitting the optional P61 `audit` report;
  the waste-budget evaluator's coverage checks concern rule-property coverage and do not establish
  reaction/decay completeness. P109's separate native composition path explicitly requests audit,
  checks reached activation-target metadata and downgrades modeled coverage. A future separately
  frozen budget coverage phase must address the older helper, preserve P105's historical evidence
  and distinguish arithmetic qualification from physical completeness. No P105 behavior/verdict
  is silently changed in P109.
- 2026-10-03 — **P108 intermediate stdout emission.** Static native integration review found
  the existing P108 wrapper calls the P107 output-producing helper internally, so it can emit an
  intermediate P107 JSON report before its final P108 report. P109 adds quiet internal evaluator
  routes needed to avoid preliminary native output, preserving historical P107/P108 wrapper
  side effects during this frozen phase. A small separate follow-up should make the existing
  composition CLI emit exactly one final JSON document and test stdout as well as file output.
  No class arithmetic, rule pack, projected interval or historical verdict is changed.
- 2026-10-03 — **Independent public benchmark feasibility.** Luna read-only review found
  unrestricted SINBAD FNG-SS/Bulk foil reaction-rate metadata and an FNG Dose Rate file listing
  containing a small local cell-620 flux; wider foil spectra still need exact mapping or transport.
  Public IRDFF-II benchmark spectra and measured SACS may support a separately frozen P46 direct-
  fold comparison without licensed transport. These are candidates, not executed/qualified
  evidence. Preserve consumed partitions and freeze observable, lawful input bytes, thresholds
  and data exposure before any new scoring/calibration phase.
- 2026-10-04 — **Local preflight/self-contained child bounds.** Read-only review while waiting for P112 closure CI found unbounded subprocess calls in `controls/g1_self_contained.py` and `scripts/preflight.py`; the self-contained helper also changes HOME. Neither was executed locally in this review. Before future local execution, separately scope bounded child lifecycles, disk-backed temporary paths and task-specific home/cache variables with regression coverage. Existing remote CI success does not override workstation cgroup and child-lifecycle requirements. This is outside P113's nominal twin scope.
- 2026-10-04 — **Dedicated-row missing-property oracle gap.** P113 static review found the frozen P105 independent oracle includes active dedicated-row nuclides in row arithmetic even when their properties are absent. The production core excludes those nuclides and reports incomplete coverage with the known-subtotal class. Preserve the historical P105 source and verdict bytes. P113 independently filters the known-property inventory before delegating row arithmetic and restores unknown metadata from the full inventory; literal missing-property regressions pin this behavior. A separately scoped audit should identify other consumers of the frozen oracle that need the same distinction.

- 2026-10-05 — **Gaps found while scoping the FARIS coupling** (FARIS release 0.2 plan: per-component activation
  and decay heat at each outage and replacement of a 30-year tokamak operating history). Not scheduled at that
  time; each needs its own protocol before code changes.
  1. **`actinv run` had no opt-in rebinning.** A single-point neutron spectrum had to have exactly 709 values or
     `custom` boundaries matching the library to 1e-12 (specification.md "Projectile and spectrum"). The
     equal-flux-per-unit-lethargy rebin existed only in the mesh path (`mesh.rs`, `rebin_equal_lethargy`), so a
     user with a VITAMIN-J 175, CCFE 315 or arbitrary-boundary spectrum had to wrap it as a one-cell mesh or rebin
     it outside ACTINV. FISPACT-II users expect GRPCONVERT here. Proposed: an explicit `rebin` option on the
     ordinary spec and the Python `Spectrum`, reusing the mesh rule, reporting `source_total`,
     `destination_total`, underflow and overflow as the mesh path does, and marking the result as assuming a
     within-group shape (threshold and resonance reaction rates depend on it). Refusing silent rebinning stays
     the default. Acceptance sketch: flux integral conserved to 1e-12 relative on the covered range; results
     bit-identical to the one-cell mesh route; a spectrum with energy outside the library range or a 0 eV lower
     edge refused unless an explicit floor/cap is given, as in mesh mode.
  2. **The mesh guide said one material and one schedule for all cells.** Mixed-material tallies (steel, water, coil
     in one mesh) had been described as requiring separate runs. Proposed: an optional per-cell material reference
     (a material table keyed by id), keeping prepared-data reuse per (material, rebinned flux) pair. The `materials`
     override map was implemented earlier in `bb2e8e5` (2026-09-27); this old note and the guide wording were
     stale. The schedule is shared by all cells; per-cell schedules remain unsupported.
  Historical note on flux uncertainties: P93 supports statistical groupwise tally relative errors when the
  `flux` uncertainty channel is requested. Systematic transport-model and transport-data uncertainty remain
  outside that band, and transport ensembles remain the caller's mechanism for sample-wide spectrum variation.
- 2026-10-06 — **P120 registered for opt-in single-case neutron spectrum rebinning.** Implementation and
  documentation now describe `spectrum.rebin: "equal_lethargy"`, its within-group assumption, strict source-range
  requirement and absent-by-default behavior. Local analytic, one-cell-mesh, uncertainty, Python/native/CLI,
  evaluated-data mapping, and workstation gates passed; see the
  [P120 verification record](../results/quality/p120/local_verification.json).
  The frozen protocol also requires green candidate workflows before publication. Per-cell schedules remain
  unsupported, and the broader waste qualification remains open.
