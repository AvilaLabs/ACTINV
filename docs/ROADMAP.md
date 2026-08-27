# ACTINV roadmap to v1.0

*Written 2026-08-26 at the principal's request. This file is the only place scope lives. A phase's scope is fixed when
its protocol is hashed; anything discovered mid-phase goes to `docs/PARKING.md`, not into the phase. One phase open at a
time. Every phase ends with a checker-derived verdict, a session file, a manifest, a commit. Changes to this roadmap are
dated entries in the changelog at the bottom — nothing is edited away.*

**Current status (2026-08-27):** P11 is closed **P11-CONDITIONAL** with all six gates passing. P12 — v1.0 hardening
is open under its frozen protocol; G1 configurable radiological responses and G2 primary abundance/mass-table
re-verification are green. Deterministic parser fuzzing is next, followed by the published FNG/ITER cell-620
activation step, qualification-boundary documentation and the technical v1.0 release candidate. Public tagging and
publication remain principal acts.

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
