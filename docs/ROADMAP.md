# ACTINV roadmap

*Written 2026-08-26 at the principal's request. This file is the only place scope lives. A phase's scope is fixed when
its protocol is hashed; anything discovered mid-phase goes to `docs/PARKING.md`, not into the phase. One phase open at a
time. Every phase ends with a checker-derived verdict, a session file, a manifest, a commit. Changes to this roadmap are
dated entries in the changelog at the bottom — nothing is edited away.*

**Current status (2026-08-29):** v1.0.1 is publicly released on PyPI, crates.io and GitHub from signed tag `v1.0.1`.
P13 and the initial competitive benchmark are closed **P13-PASS** and **CB1-COMPLETE**. P14 closed honestly below its
frozen threshold. P15 is closed **P15-PASS** with a 2.595× faster and 8.326× lower-RSS prepared warm path. P16 is
closed **P16-CONDITIONAL**: eleven zero-cost physical quantity types are wired through production boundaries, all
seven metamorphic relation families and exact release-compatibility controls pass, and the frozen workload is 3.0%
faster at the median with effectively unchanged memory. The conditional suffix records one CI checkout-history
repair, not a product or scientific failure. P17 is closed **P17-FAIL** under its unchanged procedural rule: all
numerical, identical-data, processor, diagnostic, independent-arithmetic and quality controls pass, but the sole
post-unseal amendment's EOI, product-alias and infinite-dilution assumptions were falsified. P17 changed no production
code or released package. The P17 closure commit passed all 39 workflow steps. P18 is closed **P18-FAIL** under protocol
`002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09`: it replaces heuristic product-state ranking
with auditable ENDF identity, but its complete G2 scan finds the frozen state-partial conservation and mapping rules
violated across the four TENDL corpora. No diagnostic or held-out ratio was read, G3 onward is not authorized, and
v1.1.0 cannot ship from this phase. Source/evidence commit `a460b6e4092d57ff228c6fb04ec41a12f575dd25` passed all
42 substantive steps in GitHub Actions run `33257767713`; that green workflow validates the failure record rather
than converting it into a scientific pass. P18b is open from the green closure under protocol
`69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe`. G0 has frozen provenance for all 11,400
source files plus the 245-file official-checker sample; no checker output, new corpus classification or measurement
value was read before that seal. G1 now fixes the exact-decimal, printed-quantum, interpolation, standard-envelope,
threshold and excitation-boundary oracle: Rust agrees at zero ULP on the generated interpolation cases, and all six
pinned IAEA CHECKR/FIZCON decisions agree. Production and the public v1.0.1 artifacts remain unchanged.
Amendment 1 (`8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0`) quarantines five families whose
dependent rows were accidentally displayed by an incorrect fixed-column redaction before G0. They are diagnostic
only; the remaining held-out partition stays sealed. It had capped a successful close at P18-CONDITIONAL; the later
G2 threshold failure now requires P18-FAIL.

## What v1.0 means (acceptance criteria — all measurable)

| area | v1.0 requirement |
|---|---|
| Data | Full activation library built by ACTINV's own pipeline from TENDL-2025 (latest at the P10 scope freeze) — resolved-resonance reconstruction (SLBW/MLBW/Reich–Moore/R-matrix-limited), unresolved range (LSSF=0 and 1), Doppler at user temperature; EAF-2010 as an alternative library; decay data ENDF/B + JEFF with provenance; fission yields; proton/deuteron/alpha activation from TENDL. No bundled data; every input pinned by hash. |
| Solver | CRAM-16/48; trace (constant-bulk) and coupled (burn-up) modes chosen automatically from the recorded burn-up fraction; arbitrary pulsed irradiation histories; reachable-set and rate-significance pruning with the removed-heat bound in every result; mesh mode (10⁶ cells) parallel. |
| Outputs | Inventory, activity, decay heat split α/β/γ, decay-photon spectra (lines and multigroup), contact γ dose-rate proxy, tritium/helium, pathway analysis (which chain made what), clearance/waste indices against a configurable table, ingestion/inhalation dose (ICRP coefficients). |
| Interfaces | JSON problem spec; CLI; Python API (PyO3) for a full schedule with ledger and certificate; flux import from OpenMC statepoints, MCNP meshtal/mctal, FISPACT fluxes files; decay-photon source export for OpenMC and MCNP (R2S). |
| Validation | FNS decay heat (done); a fission-spectrum decay-heat family (CoNDERC fission set); FNG/ITER shutdown-dose activation step; code-to-code vs FISPACT-II, ALARA and OpenMC on identical data; covariance-propagated uncertainty on collapsed cross sections. |
| Quality | Certificate for every run re-derivable by the checker; ledger categories documented; controls run in CI on a data subset; parser fuzzing; reproducibility across machines demonstrated; versioned releases (wheel + crate); DCO. |

Out of scope for v1.0 (parked, see PARKING.md): neutron transport of any kind; criticality; thermal-hydraulics; GUI.

## Milestones

- **v0.1 — a stranger can install it, run it on public data, and re-derive the validation.** Phases P4–P6.
- **v0.2 — useful in an R2S chain.** Phases P7–P8.
- **v0.5 — activation-grade on every axis the incumbents cover.** Phases P9–P10.
- **v1.0 — something a neutronics team can put in a licensing chain.** Phases P11–P12.
- **post-v1 performance — the complete public workflow is measured, selective and smooth.** Phases P14–P15.
- **post-v1 assurance — dimensional mistakes fail early and accuracy limitations are attributed openly.** Phases P16–P18.
- **post-v1 physics and scale — self-shielding, practical uncertainty and large jobs are independently controlled.** Phases P19–P21.
- **post-v1 scorecard — every improvement and remaining loss is re-measured before release.** Phase P22.

## Phases

Each row is one protocol session. Estimates are working days of the principal's machine at the P0–P3b cadence
(P0–P3b: six sessions in two days). Gates are the controls the phase must pass; they become the protocol's text.

| phase | scope | gates (controls) | depends on | est. |
|---|---|---|---|---|
| **P4** TENDL library | Build the full TENDL-2023 activation library with the P3b reconstruction at 293.6 K (0.1 % linearisation), MF=8/9/10 products, 709-group collapse; ledger every unsupported range (LRF=7, LSSF=0). Rerun the FNS set on equal data vs FISPACT-II/TENDL-2017. | seeded-sample agreement with NJOY where FENDL shares the evaluation; pointwise vs openmc TAB1 for non-resonant files; FNS C/E reproduced by checker; library-difference table vs EAF-2010 | P3b | 2 (+1 background compute) |
| **P5** API & spec | JSON problem spec; **the Rust core owns the whole path from spec to result** — spec parsing and validation, material → atoms per gram, library → one-group rates, trace/coupled selection, schedule stepping, pruning, CRAM, inventory/activity/heat split, pathway analysis, ledger and certificate. `actinv run spec.json`, PyO3 `actinv.run(spec)` and the harness are then the same code path, three entry points. Python keeps library building, the harness and the checkers. | spec round-trip CLI = Python = harness at 0.0 **through one binary**; pathway contributions sum to each nuclide's atoms to 1e-12; every ledger category present through every entry point; planted-failure surfaces identically in all three | P4 | 4 |
| **P6** v0.1 release | Wheels (maturin) and crate metadata; GitHub Actions running the control suite on a data subset; CHANGELOG, versioning, docs pass; reproducibility check on a second machine. Making the repository public is the principal's act. | CI green on a clean runner; certificate matches across two machines; **the known-limitations section of the release notes states every gate a shipped phase failed, with the affected data and the flag that guards it** | P5 | 1–2 |

| **P7** Photon source & dose | Parse decay spectra (MF=8 continuous/discrete); line and multigroup decay-photon sources; contact γ dose-rate proxy (semi-infinite slab) and specific γ constants; export as OpenMC source and MCNP SDEF. | spectrum energy integral = Ē_EM per nuclide to 1e-6; known-nuclide dose constants (Co-60, Cs-137) within 2 % of tabulated | P5 | 2 |
| **P8** Flux import & mesh | Readers for OpenMC statepoint tallies, MCNP meshtal/mctal, FISPACT fluxes; mesh mode: independent cells in parallel (rayon), pruned per cell; sizing table to 10⁶ cells. | imported flux reproduces the file's totals to 1e-12; mesh result = per-cell single runs at 0.0; timing table | P5 | 3 |
| **P9** Fission & coupled mode | Fission yields (ENDF/B nfpy) as products; coupled bulk mode for burn-up > 1e-6 with automatic selection; pulsed histories; second validation family: CoNDERC fission decay-heat set; code-to-code vs ALARA and OpenMC on identical data. | yield sums = ν_f-consistent to 1e-6; coupled vs trace agree where burn-up ≪ 1; fission set C/E reported with FISPACT/ORIGEN references | P4 | 3–4 |
| **P10** Data completeness | R-matrix-limited (LRF=7) reconstruction; unresolved range with LSSF=0 (infinite-dilution averages from parameters); Doppler at arbitrary temperature; TENDL-2025 p/d/α activation; ultra-narrow-resonance treatment (the P4 G2c limitation); **`actinv-data`: library building moves to Rust, after which ACTINV is a single binary with an optional Python API**. | LRF=7 vs NJOY on FENDL W-186; URR averages vs NJOY UNRESR on Ag-107; identical-data TENDL-2017 charged values vs official FISPACT-II processed rows plus TENDL-2025 MF6 vs official residual tables | P4 | 4–5 |
| **P11** Uncertainty | Covariance (MF=33) propagation to collapsed one-group cross sections; sensitivity of heat/activity to each collapsed σ; uncertainty bands in reports and certificates. | propagated variance = sampled variance on a 2×2 case to 1e-3; sensitivities vs finite differences to 1e-4 | P5 | 3 |
| **P12** v1.0 hardening | Clearance/waste indices (configurable table), ICRP dose coefficients, independent re-verification of abundance/mass tables from a primary source, parser fuzzing, FNG/ITER shutdown-dose activation step with provided fluxes, docs for use in licensing chains, v1.0 release. | all prior controls green in CI; fuzzing finds no crash in 10⁶ cases; FNG activation step vs reference | P7–P11 | 3–4 |
| **P13** verified distribution | Embedded versioned data catalog; one-command, atomic, SHA-256-verified setup for exact P10/P11 TENDL artifacts and official decay archives; attribution, release staging, quick start and CI controls. No physics changes and no bulk data in Git. | strict manifest and path rejection; direct/archive download fault regressions; staged assets match P10/P11 evidence; required Rust gates and independent control green | P12 | 1 |

### Post-v1 phases

The post-v1 program does not depend on access to a licensed FISPACT executable. Solver controls use analytic Bateman
solutions, dense exponentials and OpenMC; processing controls use NJOY or another lawful independent processor;
identical-data end-to-end controls use ALARA and OpenMC; predictive claims use protocol-frozen measurements. Public
FISPACT results remain different-data context. A blind public runner may later accept results from a licensed
collaborator, but no phase gate depends on that optional route.

| phase | scope | gates (controls) | depends on | est. |
|---|---|---|---|---|
| **P14** performance anatomy | Attribute wall time, allocations, peak RSS, I/O and copied bytes across the released public path; remove only measured redundant work that requires no data-format, physics or public-interface change. | frozen CB1 baseline and inputs reverified; stage accounting reproducible; scientific result/provenance unchanged; candidate improves one primary resource metric without materially regressing the other; Rust and prior-release gates green | CB1 | 2–3 |
| **P15** prepared selective data | Add a deterministic schema-versioned prepared artifact, indexed reachable-target loading, compact sparse storage and safe cache reuse; evaluate immutable-array mapping and remove redundant Rust/Python copies. | cache deletion changes performance only; corrupt/stale/cross-version caches fail closed; at least 2× lower public-example peak RSS with a 512 MiB stretch goal; at least 1.5× faster warm path with a one-second stretch goal; provenance and results preserved | P14 | 5–8 |
| **P16** typed boundaries and metamorphic suite | Introduce zero-cost physical quantity types incrementally at Rust parsing/core boundaries while retaining convenient compatible JSON/Python inputs; broaden relation-based tests for scaling, decay, schedules, rebinning, modes and mesh identity. | incompatible quantities rejected at compile time in fixtures; exactly one documented conversion per boundary; legacy APIs/results remain compatible; release runtime has no material regression; all frozen relations pass | P15 | 4–6 |
| **P17** open validation and attribution | Freeze new diagnostic and genuinely held-out public evidence before calculation; expand identical-data ALARA/OpenMC networks and NJOY processing controls; separate solver, processor, evaluation, decay/yield and measurement-definition effects. No production physics is changed in this phase. | no post-result exclusions or metric changes; all inputs hashed; independent arithmetic rederives the report; cause ledger names every material mismatch; held-out evidence remains sealed until scoring | P16 | 7–10 plus bounded background compute |
| **P18** evidence-directed accuracy | Repair only cause classes demonstrated by P17, one narrowly frozen repair session at a time; diagnostic evidence guides work and held-out evidence decides whether changed default behavior ships. | independent control per behavior change; conservation/numerical controls remain green; held-out typical and tail measures do not regress; the already-seen FNS family is reporting evidence, never the acceptance oracle | P17 | discovery-dependent |
| **P18b** evaluated state semantics | Resolve the P18 G2 failure without using measurement values: distinguish strict ENDF decimal/source conformance, the official IAEA checker convention and conservation against ACTINV's actual processed runtime total; use only ratio-preserving reconciliation inside the independent ceiling and fail loudly beyond it. | independent 80/120-digit decimal oracle and pinned CHECKR/FIZCON fixtures; complete four-corpus classification reproduces P18 counts; unchanged rows remain bit-identical; runtime sums close; boundary/gross plants fail; no changed default before compatibility and held-out gates | P18 | 3--5 |
| **P19** finite-dilution self-shielding | Add explicit finite-dilution treatment from lawful open probability-table or independently processed data, while preserving infinite dilution as an explicit mode and recording all model/data choices. Initially opt-in. | infinite-dilution limit reproduces v1.0; dilution and temperature limits are physical; selected rates match an independent open processing path; missing inputs fail visibly; resonance-sensitive held-out cases improve or remain consistent | P17; P18 if opened | 10–15 |
| **P20** practical uncertainty | Audit P11 covariance coverage; propagate relevant correlations into usable observable bands; compare linear propagation with deterministic correlated sampling; distinguish cross-section, decay, yield and uncovered model uncertainty. | synthetic analytic and sampled controls agree in their shared regime; covariance validity and fixed-seed reproduction pass; partial coverage is explicit through CLI/Python/JSON/certificates; no interval is labeled total uncertainty without total coverage | P16 | 7–10 |
| **P21** large-scale execution | Reuse prepared networks across compatible mesh cells, group common workloads, stream selectable outputs, bound memory by chunk size and add interruption/checkpoint support; replace extrapolation with an executed large case. | mesh equals independent cell runs; thread-count identity holds; memory excludes total-cell scaling apart from requested output; scaling evidence records hardware/work/output/cache state; no unexecuted million-cell claim | P15 | 5–8 |
| **P22** public re-score and release | Rerun frozen CB1 against v1.0.0 and the candidate; score P17 held-out evidence; repeat open-code, install, memory, runtime and mesh exercises; publish raw machine-readable evidence, limitations and narrowly supported claims. | checker rederives every table; clean clone and all release gates green; source and data artifacts remain versioned and hash-pinned; any superlative names the exact executed workload and comparator set | P18–P21 | 3–5 |

Triton/helion/gamma activation, feed/removal, reverse calculation, damage observables and an internal transport solver
remain demand-led candidates rather than automatic post-v1 scope. Performance-only work may ship as a patch release
when behavior and interfaces are unchanged; additive physics or API work uses a minor release and release candidate.

### Known limitations carried into v0.1
Written here so they cannot be forgotten at release. Each must appear in the v0.1 release notes with its guard.

| limitation | affected data | guard | scheduled |
|---|---|---|---|
| Group values for evaluations with synthetic resonances far narrower than the Doppler width do not converge to 1e-3 between grid densities (worst 1.5e-2) | TENDL-2023 Fr-226, Rb-94; no FNS benchmark material contains either | `convergence_flag` in the library index; propagates to every run's ledger as `library_convergence_flags` | P10 (`protocols/ACTINV-P10_PROTOCOL.md`) |
| R-matrix-limited (LRF=7) resolved ranges and unresolved ranges with LSSF=0 are not reconstructed | e.g. FENDL W-186 | ledgered per target as unsupported; MF=3 background used, never silently approximated | P10 |
| Fission products are not followed (no yields) | actinide targets | explicit leakage state, `fission_no_yields_to_leakage` with rates | P9 |
| 18 reaction products have no evaluated decay data in ENDF/B-VIII.0 or JEFF-3.3 | exotic products, nil realised in FNS | `products_no_evaluated_decay_data_ENDFB80_JEFF33`, booked to leakage | none known |

**P9 status update:** the fission-products row is retained verbatim because it records what v0.1 shipped and is checked
against those release notes. P9 resolves it when a matching hash-pinned NFPY evaluation is supplied; an active parent
without yields deliberately retains the named leakage guard.

**P10 status update:** the first two rows are also retained verbatim as the checked v0.1 release record. P10 resolves
them in the current Rust builder: isolated ultra-narrow lines use certified analytic area treatment; limited R-matrix
and infinite-dilution `LSSF=0` unresolved ranges are reconstructed and independently controlled. Unsupported features
continue to fail closed rather than use the historical fallback. Finite-dilution self-shielding remains out of scope.

**Total:** ~25–30 working days → at two to three sessions a week, roughly three months to a v1.0 candidate. The
part that does not compress: users, issues, and the validation record accumulating afterwards.

## Standing rules (from P0–P3b, binding on every phase)

1. Protocol hashed before evidence; verdict by checker; ledger append-only; manifest once at close; commit and push
   at close, authored by the principal's git identity, no assistant attribution.
2. Scope is frozen at the hash. Discoveries go to `docs/PARKING.md` with a dated line; they enter a later phase's
   protocol or not at all. A phase that fails its gate after one repair round closes FAIL and gets a successor session
   with a new protocol — never a silent retry.
3. Memory guard: every heavy job under `ulimit -v 12000000`; no array beyond ~1 GB without a ledger reason; kernels
   chunked and windowed; no concurrent heavy jobs.
4. Data never in the repository; every input pinned by SHA-256 in the certificate; terms of use recorded in DATA.md.
5. Controls are exact where the physics allows exactness (kernel laws, quadrature, identities) and reference-bound
   where it does not (NJOY at its stated tolerances); a control whose premise turns out wrong is corrected by
   amendment with the numbers that showed it.
6. External acts — publishing the repository, releases, contact, filings — are the principal's.
7. **Cost is designed, not discovered.** Before any computation expected to exceed ~10 minutes: (a) state the smallest
   input set that settles the gate and use it — a gate's prerequisite is not the phase's deliverable, and the two are
   scheduled separately; (b) profile one representative unit and record where the time goes; (c) checkpoint per unit of
   work so an interruption resumes. A phase's protocol names its minimum gate input alongside its gates.

   *Learned the expensive way in P4 (2026-08-26): the gate needed 255 of 2,847 library targets, but the phase was
   executed in the order it was written, so a four-hour build blocked a twenty-minute validation. Profiling afterwards
   showed 91–97 % of the cost in one kernel.*

## Changelog
- 2026-08-26 — roadmap written after P3b (v0.1 = P4–P6, v0.2 = P7–P8, v0.5 = P9–P10, v1.0 = P11–P12).
- 2026-08-26 — standing rule 7 added (cost is designed, not discovered): minimum gate input, profile first, checkpoint.
  Phases P7–P12 are re-read under it — each now states its minimum gate input before its full deliverable.
- 2026-08-26 — P5 scope set by the principal ("do it the right way, not cut corners"): the Rust core owns spec → result,
  so the three entry points are one code path and the certificate's solver hash is meaningful. Estimate 2–3 → 4 days.
  P10 completes the move by porting library building (`actinv-data`), leaving a single binary.
- 2026-08-26 — P7 closed P7-CONDITIONAL: decay photons, NIST-response dose proxy and OpenMC/MCNP exports delivered;
  one G5 repair round is recorded in P7 Amendment A. P8 is next and remains unopened.
- 2026-08-26 — P8 opened under protocol `bd3111cd…`: a hashed streaming flux interchange, fail-closed supported
  subsets for OpenMC statepoint/MCNP meshtal+mctal/FISPACT fluxes, conservative lethargy rebinning, deterministic
  chunked Rayon cells, and measured/extrapolated sizing through 10^6 cells.
- 2026-08-26 — P8 closed P8-CONDITIONAL: all six gates pass after the single repair pass recorded in P8 Amendment A;
  four source formats, exact mesh/single-cell identity, thread-deterministic records and bounded-memory sizing are
  delivered. v0.2 is complete. P9 is next but remains unopened and unhashed.
- 2026-08-26 — P9 opened under protocol `028c5846…`: explicit isotope/isomer materials, independently parsed and
  hashed ENDF/B-VIII.0 MT=454 fission yields, corrected fluence-aware burn-up selection, explicit piecewise pulse
  histories, and CoNDERC/OpenMC/ALARA validation. The roadmap's “nu_f-consistent” shorthand is fixed before evidence
  as the ENDF independent-yield sum of two fission fragments, not prompt-neutron nubar.
- 2026-08-26 — P9 closed P9-CONDITIONAL: all six gates pass after the single repair round recorded in Amendment A.
  Explicit-nuclide materials, independent NFPY matrix feeds, exposure-based auto mode, boundary-level pulse records,
  and OpenMC/ALARA/CoNDERC evidence are delivered. Dickens total pulse and Yarnell 20,000 s geometric-mean C/E are
  1.0070 and 0.9845. P10 is next but remains unopened; v0.5 is not yet claimed.
- 2026-08-26 — P10 opened under protocol `74273ec5…`. “TENDL (latest)” is resolved at scope freeze as TENDL-2025;
  the production builder moves entirely to Rust and must complete full external TENDL-2025 n/p/d/alpha plus
  EAF-2010 builds. Charged validation separates identical-data TENDL-2017/FISPACT processed comparisons from
  TENDL-2025 MF6 checks against official residual tables. The public licensed FISPACT executable is not claimed as a
  run. The P4 Rb-94 residual is correctly assigned to the resolved/unresolved boundary splice, while Fr-226 receives
  the analytic ultra-narrow treatment. P10 is frozen until its checker-derived close.
- 2026-08-27 — P10 closed P10-CONDITIONAL: all seven gates pass after the append-only repair record. The deterministic
  Rust path completes TENDL-2025 neutron/proton/deuteron/alpha and EAF-2010 libraries (12,216 targets, 1,849,479 rows)
  with fresh/cached byte identity and zero target errors, silent fallbacks or convergence flags. Limited R-matrix,
  infinite-dilution unresolved averages, arbitrary-temperature broadening, ultra-narrow treatment and charged runtime
  identity are independently controlled. Technical v0.5 is complete; P11 is next and remains unopened.
- 2026-08-27 — P11 opened under protocol `fb9964d5…`. Its minimum gate uses TENDL-2025 Fe-56/Ni-58 MF=33,
  synthetic 2×2 covariance networks and NJOY2016.79 ERRORR before the complete 2,850-file scan. The frozen scope
  includes strict LB=0--6/8/9 processing, a separate deterministic covariance sidecar, exact differentiation of the
  selected CRAM-16/48 recurrence, MF=33 heat/activity bands and explicit coverage/numerical-method budgets. P12
  remains unopened.
- 2026-08-27 — P11 closed P11-CONDITIONAL: all six gates pass after the append-only Amendments A--E. The complete
  2,850-source scan retains 84,489 sections and 285,023 components with zero errors or silent omissions; current
  fresh/cached sidecar and index bytes match under bounded memory. Heat/activity sensitivities, MF=33 bands,
  coverage, excluded sources and CRAM-order bounds are identical through CLI, Python, prepared and mesh paths. P12
  is next but remains unopened and unhashed; no v1.0 tag or publication is claimed.
- 2026-08-27 — P12 opened under its frozen protocol. The minimum inputs are a two-nuclide response fixture, the
  289-row Meija/AME2020 primary table controls, a 10,000-case fuzz smoke partition and the published FNG/ITER
  campaign-1 cell-620 one-group activation record. External ICRP tables and all nuclear data remain hash-pinned and
  unbundled; the technical release commit does not authorize a tag, registry upload or licensing claim.
- 2026-08-27 — P12 G1--G2 green. A strict hash-pinned response table now produces clearance, waste, ingestion and
  inhalation outputs with exact missing-activity coverage through CLI, Python, prepared and mesh paths. Independent
  parsers reproduce all 289 Meija/AME2020 abundance/mass pairs bit-for-bit from primary files and the embedded Rust
  table byte-for-byte; certificate provenance now names and hashes the primary sources. G3 parser fuzzing is next.
- 2026-08-27 — P12 Amendment A records the first post-G2 CI repair. P10-G6 correctly detected that the required
  primary-source table attribution changed its legacy-result hash. A recursive comparison found that provenance leaf
  to be the sole non-timing/path/version difference; the control now requires it to match the embedded-table record
  exactly before mapping only that leaf to the frozen pre-P12 value. A planted attribution change still fails closed.
- 2026-08-27 — P12 G4 green. The external CC-BY-4.0 FNG/ITER cell-620 archive is transformed reproducibly into
  temporary ACTINV inputs; four selected histories pass all 170 endpoints and independently read rates pass their
  frozen bounds. Archive members and generated nuclear data remain outside Git.
- 2026-08-27 — P12 Amendment B records a pre-publication packaging repair: three compile-time JSON tables moved
  inside the `actinv-data` crate boundary without changing semantic content. Exact `1.0.0` path-dependency versions,
  unpacked-crate compilation, stable-ABI wheel checks and licence inclusion now guard the distributable artifacts.
  User-first installation, release, qualification, specification, method, data and validation documentation is part
  of the G5 release candidate; no registry upload, tag or GitHub Release is authorized by this work.
- 2026-08-27 — P12 Amendment C records a nested clean-clone control-path repair found by the first complete G5 run.
  The inner build inherited the outer temporary target directory while its probe used its own clone path; explicitly
  rooting `CARGO_TARGET_DIR` in the inner clone resolves the mismatch. The already completed package/interface stages,
  product source and all scientific criteria are unchanged; G5 remains open pending the full rerun.
- 2026-08-27 — P12 G5 green on the complete rerun. A fresh clone passes the exact Rust gates, prior-evidence and
  dependency audits, CLI/Python end-to-end comparison and nested self-contained control. Exact unpacked 1.0.0 crate
  packages compile, the standalone binary reports 1.0.0, and the Python 3.9 stable-ABI wheel and source archive pass
  import, metadata, licence and content checks. No artifact was uploaded or publicly released.
- 2026-08-27 — P12 G3 green. Two fixed 10,000-case runs have the same deterministic summary and the fixed
  1,000,000-case partition covers all eleven production-reader families with zero process-level failures below the
  1 GiB ceiling. Amendment D records five pre-full-run bounds/encoding findings and their minimized regressions;
  accepted scientific inputs and results are unchanged. G6 closure and pushed CI confirmation remain.
- 2026-08-27 — P12 Amendment E repairs a closure-control cycle found before G6: the manifest cannot hash reports
  whose content records whether that same manifest reproduces. The exact exclusion set is frozen to the manifest
  itself and the G6/verdict derived reports; the closure commit binds all three while every other file remains
  inventoried. No product or scientific evidence changes.
- 2026-08-27 — P12 closed P12-CONDITIONAL with all six gates passing. Release payload
  `0151dd06ee12bc047da34a9e35341d23590a12a9` is pushed to the canonical repository and exact GitHub Actions run
  `33134485488` is green. The closure checker independently re-derives G1--G5, binds that payload and run, and
  reproduces the non-circular source/evidence inventory with Amendment E's exact derived-report exclusions. Technical
  v1.0 is complete; no tag, GitHub Release or registry publication is claimed.
- 2026-08-28 — P13 opened under protocol
  `afbc60cb75411b1f10a558f77f2a512412de2f925bbaccada099ac5fd3c2f92c`. It is a distribution-only extension:
  immutable P10/P11 TENDL artifacts may be released under the CC-BY-4.0 terms recorded in every TENDL-2025 source
  header, while ENDF/B-VIII.0 and JEFF-3.3 decay archives remain direct official-host downloads. The embedded catalog,
  atomic fetch/verify path, release staging, attribution and first-run docs are gated without changing scientific code.
- 2026-08-28 — P13 closed P13-PASS. Source commit `cf862ab90c487be5f2668a2f4f383a2d0dec0869` passed GitHub Actions
  run `33143906452`; release `data-v1.0.0` (GitHub release ID `378273436`) contains exactly the 14 staged assets and
  server-reported SHA-256 identities recorded in `results/session_p13.json`. A clean public default fetch verified the
  GitHub assets and both official IAEA archives, then completed the 21-step FNS iron example with all four certificate
  input hashes matching. No raw evaluation, decay archive, generated payload, cache, or credential entered Git.
- 2026-08-28 — post-release competitive benchmark CB1 opened from `19afc18d…`. Its pre-evidence protocol separates
  identical-operator, identical-data, raw-data, and complete product/data comparisons; labels executed, public-reference,
  documented-only, and unavailable evidence; forbids a composite winner score; and freezes numerical, experimental,
  performance, first-use, and capability measures before any new benchmark result is generated.
- 2026-08-28 — CB1 closed CB1-COMPLETE. ACTINV/OpenMC/dense identical operators agree within `4.18e-15` meaningful
  relative error; the identical-data ALARA shutdown inventory differs by at most `4.12e-8`. In 2,360 FNS pairs,
  FISPACT-II 4.0/TENDL-2017 leads ACTINV/TENDL-2025 in median point error (`0.1053` versus `0.1392`) and whole-family
  30% coverage (`69/132` versus `59/132`), while ACTINV leads the 90th-percentile point error (`0.6637` versus
  `0.6846`). The report forbids cross-data solver claims, records licensed-executable gaps, first-use/resource costs,
  and all capability losses. Scorecard commit `121b35b01eb8a055b071efe7301d07e112269ad1` passed GitHub Actions run
  `33185710084`; all required Rust gates and the isolated clean-clone/end-to-end/P10 legacy controls also pass locally.
- 2026-08-28 — the maintainer approved the P14--P22 post-v1 program after CB1. Licensed FISPACT access is explicitly
  not a dependency: open numerical, processing, end-to-end and measurement controls form the evidence chain, with a
  future blind collaborator runner optional. P14 opens first and is restricted to measured, no-physics performance
  anatomy and safe redundant-work removal; prepared formats and selective loading remain P15 scope.
- 2026-08-28 — P14 closed P14-CLOSED-BELOW-THRESHOLD without relaxing its frozen criterion. The exact-preserving
  candidate lowers warm median wall time by 6.55%, p95 wall time by 7.22% and peak RSS by 1.15% on the recorded
  public-example workload, so G1--G3 and G5 pass while the 10% G4 threshold does not. The source/evidence checkpoint
  is `8e59e6b800d6aaab4ff7add7fb17d4e0e4e77f38`; closure-control checkpoint
  `7cd58fc6fb728e93c8ab5a50fee4b7b5fc688a1f` passed GitHub Actions run `33195930341`. P15 is next but remains
  unopened.
- 2026-08-28 — P15 opened at `5f7289a44c2686505d0e1b40f4b00ef5c8e4a9ab`. It retains every existing result and
  ledger contribution while replacing dense deflate inflation with deterministic sparse prepared data and a
  spectrum-bound collapsed artifact. Frozen required gates are at least 1.5x lower warm wall time, 2x lower peak RSS,
  bounded and visible cold preparation, exact CLI/Python/provenance identity and fail-closed cache reuse. No prepared
  or bulk artifact enters Git.
- 2026-08-28 — P15 closed P15-PASS without threshold relaxation. On the frozen public example, the exact-preserving
  warm path moves from 3,074.85 ms to 1,185.01 ms median wall time and from 1,076,908,032 to 129,343,488 bytes peak
  RSS: 2.595x faster and 8.326x lower. The final one-second warm stretch goal is recorded as missed; every required
  gate passes. All 167,735 source rows, 710 boundaries, 33,597,258 retained values and 167,735 collapsed values match
  exactly; 23 corruption plants fail closed. Source/evidence commit
  `c2c89deab1dcee533414a1e6512d0ff45075c184` passed GitHub Actions run `33207936195`. P16 is next but remains
  unopened and unhashed; no tag or package publication is claimed.
- 2026-08-28 — v1.0.1 publicly released from signed tag `v1.0.1` at
  `0332779401363d2f39722efe7a0b7218afcfb270`. Release-candidate controls run `33217018813`, TestPyPI run
  `33217366561`, production PyPI run `33218184459` and artifact run `33218184465` are green. A pre-upload crates.io
  OIDC environment-name mismatch was corrected without moving the tag; repair commit
  `72fa60e3ab90f84d9cab1bd3aa44bdc3dee5c72c` passed controls run `33218612601`, and protected recovery run
  `33218644967` published all three crates from the exact signed tag source. Fresh PyPI and crates.io installs ran the
  same 21-step public calculation with equal normalized results; public GitHub assets passed their downloaded
  `SHA256SUMS`. P16 remains next and unfrozen until its protocol is committed and hashed.
- 2026-08-28 — v1.0.1 release closeout commit `0624133d3daa5d8440497e06c3d372c8a546a0ed` passed all 34 control
  steps in GitHub Actions run `33220183178`, including the bounded parser and clean self-contained clone. P16 then
  opened without changing a public interface or scientific value. Its frozen scope introduces zero-cost physical
  quantity types behind compatible wire APIs, compile-fail dimensional fixtures, seven analytic/metamorphic relation
  families, exact release-result identity and explicit 10% median/15% p95 runtime ceilings. P17 remains unopened.
- 2026-08-28 — P16 closed P16-CONDITIONAL with every frozen type, scientific, compatibility, runtime, memory and
  quality gate passing. Eleven zero-cost scalar types now guard the validated spec/core boundary; six incompatible
  consumer fixtures fail for the intended type errors; all seven metamorphic relation families pass; candidate,
  signed v1.0.1, CLI, prepared and Python normalized results remain exact. On the frozen public workload, candidate
  median and p95 are 2.95% and 2.41% lower with peak RSS 0.013% higher. Source/evidence commit
  `ede20289ff63951e61db536e2e36dffa5809bd62` passed GitHub Actions run `33223472844`. Amendment A records the sole
  repair: the first clean runner lacked the frozen opening commit under a depth-1 checkout; fetching full history
  changed no product source, result, expectation or threshold. P17 is next but remains unopened pending the P16
  closure commit.
- 2026-08-28 — P16 closure commit `f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d` passed all steps in GitHub Actions
  run `33224125433`. P17 then opened under protocol `c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f`
  with production code frozen. Public IAEA IRDFF-II inputs are hash-pinned; Tables 18–20 and FNS are diagnostic,
  while SPR-III Tables 21–23, ACRR Tables 24–25 and high-temperature Maxwellian Table 36 remain numerically sealed
  until the parsers, metrics, attribution taxonomy and independent checker pass at a pushed unseal checkpoint.
- 2026-08-29 — P17 closed P17-FAIL without altering its protocol or hiding the failed assumptions. G0--G4 and G6--G7
  pass; all 94 held-out rows are preserved. Twelve supported ACRR threshold responses are all within 10%, and 18 of
  21 Maxwellian responses are within 30%, but Amendment 1's uniform-EOI, `Ag109g` alias and unshielded-`bare`
  assumptions require a forbidden second post-unseal repair. The independent checker rejects 18 total diagnostic
  and held-out evidence plants. Source/evidence commit `0b3a89f5c9953166f1547eb96be56a7bc9d5ff35` passed all 38
  GitHub Actions steps in run `33231786946`. No production source or package changed; P18 remains unopened until the
  closure commit is green.
- 2026-08-29 — P17 closure commit `7a2d1f47b62155c0f7a22a4e0b9ec5d6e6730bc8` passed all 39 steps in GitHub
  Actions run `33232228355`. P18 opened under protocol
  `002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09`. It freezes a physical ENDF product-state
  catalog, state-partial conservation, fail-loud missing-isomer handling, exact unaffected compatibility, bounded
  performance and family-level diagnostic/held-out evidence from the hash-pinned Rodrigo et al. compilation. A
  successful candidate authorizes v1.1.0; after release closeout P19 opens and begins immediately.
- 2026-08-29 — P18 Amendment 1
  (`8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0`) records an attempted redaction that
  checked column 1 instead of the supplement's fixed column 20 and displayed lines 1--140. Two gamma and three
  neutron families are now permanently diagnostic before partitioning; no remaining family, rule, threshold or
  held-out value changed. This consumes P18's sole repair round and caps an otherwise successful verdict at
  P18-CONDITIONAL.
- 2026-08-29 — P18 G0 sealed all 962 reaction families and 12,313 source rows without emitting a dependent value.
  Canonical seal `3c4de15c94fbc39de279fda6a33e68e27dad89626f57172055df90113e81e94b` assigns 561 families diagnostic,
  180 genuinely held out and 221 structurally ineligible; the held-out partition contains 1,945 unread rows. Fresh
  hashes match 10.5 GB of raw TENDL archives plus manifests, released activation/decay data, the ENDF manual, paper
  and supplement. An independent checker rederives every ID and partition and rejects four evidence mutations. The
  apparent 963rd reaction was the preamble word `Reference:`, leaving the publication's 962 families exact.
- 2026-08-29 — P18 G1 replaced per-reaction level-rank compression with evaluated physical identity. The production
  parser retains `LIS`/`LISO`/`ELIS`, `LFS`/`ELFS` and `QM`/`QI`; the v2 index records every catalog source, tolerance
  and decision. Hash-pinned TENDL Ag-109/110 maps sparse raw level 2 to Ag-110m (`LISO=1`), while Ag-116 raw levels 1
  and 4 map to `m1` and `m2` across multiple target states. Generated fixtures cover ambiguity, missing metadata,
  duplicates and ordering, and the independent checker rejects four mapping mutations. G2 is the next gate; no
  diagnostic or held-out ratio was used and no package release is yet authorized.
- 2026-08-29 — P18 G2 completed a bounded one-file-at-a-time audit of all 11,400 frozen neutron, proton, deuteron and
  alpha TENDL evaluations. All 1,810,499 MF=8/9/10 declarations are accounted, all four 2,850-state catalogs are
  file-order invariant, and there are no missing totals, descriptor omissions or conflicting duplicates. The frozen
  conservation rule nevertheless finds 2,128,813 neutron, 175,883 proton, 264,272 deuteron and 78,647 alpha
  comparison violations; 143 MF8-versus-Q excitation conflicts also remain explicit. The checker verifies the
  truthful failure and rejects seven mutations without reading a diagnostic or held-out measurement. G2 therefore
  fails, G3 onward is not authorized and P18 must close P18-FAIL without a v1.1.0 release.
- 2026-08-29 — P18 closed P18-FAIL with its complete G2 failure intact. Source/evidence commit
  `a460b6e4092d57ff228c6fb04ec41a12f575dd25` passed all 42 substantive controls in GitHub Actions run
  `33257767713`; the closure checker binds that run, every G0--G2 artifact, the unchanged package version and the
  absence of G3--G7/unseal/release evidence, and rejects seven closure mutations. Public v1.0.1 is unchanged. P18b is
  planned as a new pre-evidence successor for evaluated decimal precision, threshold-domain interpolation and true
  state-sum classification; it remains unopened and P19's finite-dilution scope remains separate.
- 2026-08-29 — P18b opened after P18 closure run `33258605964` passed all 43 substantive controls. Before any new
  per-file classification, its protocol pinned ENDF-102 and IAEA utility-code commit
  `c2a6718bd831b5c8a6e975beb1946954b1d73c40`, separated strict source quality from runtime conservation, froze the
  official `0.001` compatibility ceiling, allowed only common-factor ratio-preserving reconciliation inside that
  ceiling, and required larger excesses to fail closed. Diagnostic and held-out measurements remain unread.
- 2026-08-29 — P18b G0 sealed all 11,400 external source file identities and the deterministic 245-file IAEA-checker
  sample before any checker output or new per-file result was read. The independent checker reconstructs the hash
  selection and all P18 worst/conflict additions from a compact provenance-only manifest, binds opening run
  `33259343493`, and rejects seven mutations. G1 decimal/checker fixtures are authorized; G2 and all measurements are
  not.
- 2026-08-29 — P18b G1 established the independent numerical oracle before reading a new corpus classification. Its
  18 fixed-width real cases include all three ENDF-102 Table 17 forms and the ±38 exponent limits; seven tables cover
  all five interpolation laws and repeated-energy sides. The 80/120-digit classifications are identical, the Rust
  probe differs by zero ULP on the generated queries, and unmodified pinned IAEA CHECKR/FIZCON agrees on six MF9/10
  inside/boundary/outside fixtures. The independent checker regenerates every official tape and rejects seven
  mutations. Production, measurements and v1.0.1 remain unchanged; G2 starts only after this checkpoint is green.
- 2026-09-10 — the maintainer directed closing the CB1 demand-led capability gaps now rather than after the
  post-v1 sequence: P23 (continuous feed/removal, reverse calculation, damage observables) opens under frozen
  protocol `fa0df3411e7e2d1d8c5777810db03e76563d6dec1f695fb9219dc0ce7ee59dd5`. P18b remains open — G0/G1 committed
  and green, G2 controls staged but uncommitted since 2026-08-29 — and the one-phase-at-a-time rule is relaxed for
  this ordering by explicit direction; P18b's gates, seals and staged controls are unchanged and nothing in P23
  reads or depends on P18b evidence. P19–P22 remain scheduled behind it.
- 2026-09-10 — P23 G3 lands damage observables: `actinv build-damage` collapses ENDF-6 MF=3/MT=444
  damage-energy sections through the build-library pipeline into a hash-pinned `actinv-damage-table-1`, and a
  spec `damage` section folds those rows over the composition-resolved target inventories to NRT dpa per element
  and in aggregate. Coverage is honest: nuclides without a row are named, `require_complete` fails closed, and the
  certificate carries the table hash and provenance. Amendment A records that no local evaluation store carries
  MT=444 — the control battery therefore qualifies the builder on a persisted synthetic ENDF mini-corpus with an
  independent re-collapse, and separately verifies that a real TENDL-2025 subset yields an honest zero-coverage
  table. Users must source `heatr`-processed or equivalent damage-energy evaluations until a distributed MT=444
  library is available.
- 2026-09-10 — P23 closes with `P23-PASS` (session `results/session_p23.json`, verdict `results/verdict_p23.json`,
  independent closure `results/p23_closure_check.json`): feed/removal, linear-regime reverse calculation, and NRT
  damage observables are delivered with all gate batteries green and the frozen identity intact. Same day, a
  usability pass landed outside the phase: problem files can reference installed data symbolically as
  `catalog:<artifact-id>` (resolved against `$ACTINV_DATA_DIR` or `./actinv-data`, hash-filled from the embedded
  catalog, conflicting hashes rejected), `actinv new` emits catalog references by default, and the examples
  gallery grew to pulsed, feed/removal, damage, reverse, and two-cell mesh walkthroughs indexed by
  `examples/README.md`. Next capability work: P18b completion or P19 finite-dilution self-shielding, whichever
  the maintainer directs.
- 2026-09-10 — P19 opens under frozen protocol: finite-dilution self-shielding via independently processed
  Bondarenko factors (deterministic PURR-equivalent from ENDF-6 MF=2 LRU=2 blocks), with NJOY2016.79 as the
  independent processing oracle and the oracle-comparison campaign run as an Avila Core case. Opt-in
  `self_shielding` spec section; absent section preserves byte identity. P18b stays open and dormant.
- 2026-09-10 — P19 G0 sealed: the frozen protocol's opening gate is green (`results/g0_p19_check.json`).
  The TENDL-2025 unresolved-resonance inventory covers 2715/2850 neutron files including all six test-set
  materials; the NJOY2016.79 oracle produced complete MT=152 Bondarenko data over the declared sigma0 x
  temperature grid (792 energy rows, deterministic across runs); the Avila Core case at controls/p19_core
  compiled, executed the oracle step under a verified receipt, and recorded PASS on both G0 requirements.
  The four-surface identity baseline reproduces the P23-era hash exactly. Next: G1 build-shielding.
- 2026-09-11 — P19 G1/G2 sealed: `build-shielding` emits the deterministic `actinv-shield-table-1` Bondarenko
  artifact (PURR-equivalent ptable pipeline, six materials x 4 temperatures x 10 sigma0), and `self_shielding`
  folds it into group rates with composition/fixed dilution, ledger+certificate provenance, fail-closed coverage,
  and byte-identical absent-section output (`ebc307ff`).
- 2026-09-11 — P19 G3 seals the oracle battery: the group fold was corrected to the full-group Bondarenko form
  (covered segments carry probability-table weight moments; the uncovered part is suppressed by
  sigma0/(sigma0+background)), after the flat `(1-c)+c*f` blend was shown to under-shield edge groups ~2x at
  sigma0=0.1. Independent NJOY2016.79 PURR (node) and GROUPR (FISPACT-709 group) batteries pass all six materials
  under measured per-material tolerances; raw GENDF tapes persist under `results/gendf/`. Held-out rates
  (Ta-181 deep dilution, sigma0=1e10 byte-identical physics, run/mesh parity) and the FENDL-3.2c sanity leg land
  inside the declared scope. Convention differences (GROUPR's pointwise weight on total/elastic; the ladder-mean
  vs pointwise unshielded column) are measured, reported as diagnostics, and documented as limitations.
- 2026-09-11 — P19 G4/G5 close the phase: `examples/shielding_demo.json` walkthrough, absent-feature performance
  unchanged by construction (opt-in branch, byte-identical payloads) with feature cost recorded
  (`results/g4_p19_perf.json`), and the independent closure checker `controls/check_g5_p19.py` re-verifies the
  gate chain and artifact invariants without production imports (3/3 mutations rejected). Verdict
  `results/verdict_p19.json`: **P19-PASS**. Next: P18b completion or the P20-P22 gap list.
- 2026-09-12 — P18b G2 completes the complete four-corpus classification. A schema-v2 Rust probe and the
  independent 80/120-digit decimal oracle audited all 11,400 evaluations (84.5M source comparisons plus 24.2M
  runtime group comparisons): every frozen P18 inventory and violation count is reproduced exactly
  (2,128,813 / 175,883 / 264,272 / 78,647 for neutron/proton/deuteron/alpha), 35,033 binary64 preliminary
  labels are corrected by the authoritative exact-decimal classes, and the runtime comparator counts bound the
  G3 reconciliation envelope (793,520 / 173,579 / 282,975 / 73,230 standard-compatible vs
  1,769,831 / 140,423 / 339,399 / 54,580 outside-standard group excesses). A deterministic sub-resolution bound
  (1e-30 relative, twenty orders below the finest printed ENDF difference) resolves true-zero differences whose
  stable-integral residues flip sign across precisions; the bound is pinned in the emitted checkpoint header.
  The pinned CHECKR/FIZCON sample explains all 245 official runs. Evidence `results/g2_p18b_corpus_classification.json`,
  independent checker `controls/check_g2_p18b.py` replays all four checkpoints and rejects 3/3 mutations
  (`results/g2_p18b_check.json`). Measurements and held-out values remain unread; G3 is authorized.
- 2026-09-12 — P18b G3 replaces the retired P18 stress gate with runtime conservation enforcement. The raw
  pointwise/collapsed MF10-vs-MF3 validators become non-fatal source diagnostics whose counts ride the target
  ledger; the fatal gate moves after processing to `reconcile_emitted_states`, which compares each emitted
  `(MT, ZAP)` state vector group-by-group against the runtime total row (processed MF2+MF3 where resonance
  reconstruction applies, collapsed MF3 otherwise, the MF10/MT18 `IZAP=-1` fission sentinel where the manual
  supplies that representation). Conformant sums stay byte-identical; sums inside the frozen 0.001 standard
  envelope scale by the common factor T/S with at most a bounded one-ULP downward closure correction; anything
  outside fails construction closed with MT/MF/ZAP/group, both sums, relative excess, MAT, ZA and
  `source_sha256` in the diagnostic. MF=9 collapsed production rows obey the same rule. A `--strict-states`
  option rejects every positive excess and is hashed into build checkpoint keys; legacy v1.0.1 libraries stay
  readable because readers never gated on the builder fingerprint. Real fixtures exercise every branch:
  n-At198 fails closed on the genuine MT16 defect (13.39 barns of emitted states against a 3.6e-6 barn
  runtime total), n-Ag112 reconciles 624 group excesses including processed-resonance MF9 production and real
  one-ULP corrections, p-Ac223 builds through the sentinel comparator, p-Ag096 fails closed outside the
  envelope, and strict mode rejects a 1.3e-16 relative excess. The independent checker
  `controls/check_g3_p18b.py` re-derives every emitted fixture value through its own lethargy collapse,
  re-checks emitted-row closure on the real corpus build, verifies the diagnostic context contract, and
  rejects 3/3 mutations (`results/g3_p18b_check.json`). Measurements and held-out values remain unread; G4 is
  authorized.

- 2026-09-12 — P18b G4 completes the diagnostic scoring leg and unseal authorization package. The scorer
  `controls/g4_p18b_diagnostics.py` reads the frozen Rodrigo supplement once, reproduces every seal row id,
  resolves all seven printed ratio forms against MF3-collapsed inelastic totals and MF9/MF10 state partials,
  and applies the frozen eligibility predicates — with predicate 4 evaluated against the corpus-wide evaluated
  target-header catalog (the deterministic evaluation-wide residual-state catalog), so a row is ineligible only
  when no evaluated isomer exists, while candidate rows whose anchoring evaluation was quarantined are reported
  `build_failed_g3` rather than silently scored through leakage-routed partials. Candidate diagnostic
  libraries were built per-file under the released parameters with G3 fail-closed quarantine: 259 of 672 staged
  evaluations constructed (85/200 neutron, 84/197 proton, 10/72 deuteron, 80/118 alpha); every quarantine is
  ledgered with its source diagnostic. Of 6,600 diagnostic ledger rows, 6,134 are eligible: the v1.0.1 baseline
  scores 4,212 rows (median |ln C/M| 0.175, p90 0.851, within-30% 65.3%) and the candidate 966 rows with 926
  family-paired; the paired bootstrap reports median |ln| change +0.0007 (p95 +0.003) and p90 change +0.007
  (p95 +0.032). The dominant candidate outcome is `build_failed_g3` (4,602 rows) — honest construction
  coverage under genuine TENDL-2025 state-partial defects, concentrated in deuteron (62/72 files). The
  compatibility/performance leg is unchanged: normalized results bit-identical to the signed v1.0.1 binary,
  median ratio 0.998, p95 1.022, RSS 1.015. The independent checker `controls/check_g4_p18b.py` re-derives
  every scored row's arithmetic, all metrics and the bootstrap plus an independent NPZ row-filter spot-check,
  and rejects 4/4 mutations. A green workflow on this commit is the sole G5 held-out unseal authorization.

- 2026-09-13 — P18b G5 executes the one-time held-out decision and **fails the frozen stratum gate**
  (`results/verdict_p18b.json`: **P18b-FAIL**). The green controls run `34696870664` on commit `976e260`
  authorized unseal; held-out values were then read through the unchanged frozen scorer. A first
  execution scored only 128 of 1,859 eligible rows because diagnostic staging had never included the
  held-out families' target files — a staging defect, not a physics result — and is preserved as
  `results/g5_p18b_heldout_run1_coverage_limited.json` and cited inside the final report. Candidate
  artifacts were rebuilt over all sealed targets and products (296 of 686 staged files constructed;
  the newly staged deuteron evaluations all failed closed) and re-scored through the same unchanged
  code. Final outcome on 180 held-out families / 1,859 eligible rows: the overall gate **passes**
  (candidate median |ln C/M| 0.194 vs 0.222 ceiling, p90 0.962 vs 1.259, coverage improved, benefit
  satisfied via +10.4% median improvement and 6,912 provable rank-artifact identity corrections with
  zero provably-valid violations), but the frozen rule additionally requires every populated
  projectile stratum to pass. Neutron passes; proton fails median and within-30% coverage
  (0.291 vs 0.266 ceiling, 50.0% vs 55.2%); alpha fails p90 (1.189 vs 1.126); deuteron has zero
  candidate-scored rows of 143 eligible because 86% of its corpus failed closed under genuine
  TENDL-2025 state-partial defects. The result is therefore a coverage/stratum-qualification
  failure, not a scoring error: the identity repair is measurably beneficial overall but cannot be
  released while construction coverage leaves strata undemonstrated. G6 release artifacts are not
  authorized; v1.0.1 remains the public release. A permitted append-only repair amendment could
  reopen a corrected phase without weakening these frozen thresholds.

- 2026-09-12 — P20 closes with `P20-PASS` (verdict `results/verdict_p20.json`, independent closure
  `results/p20_closure_check.json`) under frozen protocol `76c2ca2f`. G0 sealed the opening gate: a
  complete TENDL-2025 MF=33 census (`results/g0_p20_mf33_census.json.gz`) inventories 285,023
  components across 200,534 blocks (LB5/LB8 paired short-range + relative, LB6 multi-column) with
  zero parse failures, reproduced independently by `controls/check_g1_p20.py`, and the four-surface
  identity baseline is intact. G2 classifies defective blocks — asymmetric sections, non-positive
  eigenvalues, singular weights — and excludes them with named reasons per response
  (`controls/g2_p20_defects.py`), adding per-channel band reporting. G3 delivers the deterministic
  correlated-sampling oracle (`controls/g3_p20_sampling.py`): nine midpoint-normal quantiles, 192
  perturbed solves on the richest FNS target's eigenbasis, every resolvable comparison inside 5% of
  the linear band, with solver-floor gating so sub-CRAM-noise comparisons are evidence rather than
  gates (checker `controls/check_g3_p20.py`, 6/6 mutations rejected). G4 adds the decay-constant
  (MF=8/MT=457) and independent-yield (MF=8/MT=454) channels: synthetic tangents machine-exact,
  real Mn56 half-life finite-difference agreement 3.7e-3, real Kr92 yield agreement 5.9e-5 with a
  sum-to-two-preserving compensated perturbation, channel cost measured (+0.14 s for 34 decay
  parameters, +1.92 s for 1,016 yield parameters) (`controls/g4_p20_channels.py`, checker
  `controls/check_g4_p20.py`, 8/8 mutations rejected). The closure checker `controls/check_p20.py`
  re-runs all five gate checkers, re-derives census aggregates, sampling variance and channel
  arithmetic, re-walks the pinned MF=8 records, re-asserts all 23 prior verdicts including
  P18b-FAIL, and rejects 5/5 evidence mutations. Scope is honest: no MF=32/34/35/40 covariance,
  no cross-channel correlation, no flux or composition uncertainty, no tolerance limits; the
  uncovered remainder is always named.
