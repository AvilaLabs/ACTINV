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
| **P24** corrected benchmark re-validation | Re-derive the P17 measurement definitions that its post-unseal amendment falsified — per-case end-of-irradiation times, evaluated isomer aliases, and shielded-versus-bare treatment — seal a fresh genuinely-unread held-out partition, and re-score through unchanged scoring code. The consumed P17 partition stays public diagnostic evidence; no production physics changes. Scheduled after the relevant P25 fixes freeze, and "unchanged scoring code" applies only after an audit that the scorer implements the corrected definitions. | corrected definitions are hash-pinned from source documents before any new calculation; the new partition is sealed before scoring; every falsified P17 assumption has a named corrected definition; unchanged scorer re-derives the report; failures stay visible | P17, P25 fixes frozen | 5–8 |
| **P25** state identity, construction coverage and predictive qualification | Succeed P18b under a new protocol (P18b's amendment clause authorizes only an otherwise-passing closure). Phase 1 is a bounded cause diagnosis, not a repair commitment: classify every quarantined or dropped outcome — the 1,239 `build_failed_g3` rows, the 29 scored-but-zero-prediction `181Ta(α,n)184Re` rows that `isfinite` filters from metrics, and neutron's 103-of-453 retained coverage — into processing bugs, state-catalog/mapping failures, tiny absolute discrepancies and genuine source inconsistencies, tracing representative cases from original ENDF fields to emitted inventory rows. Investigate the unconditional `inelastic(mt)` same-residual treatment (MT=4/51–91) in the builder and scorer against ENDF charged-particle neutron-emission semantics before attributing failures to TENDL. Phase 2 repairs only demonstrated mechanisms: projectile-aware reaction handling and the state-identity path first; a common rescaling preserves isomer ratios but changes absolute rates, so conservation alone does not justify it — genuine evaluation defects get corrected separately-versioned source data or an explicitly limited support claim. | cause ledger enumerates every dropped/quarantined row's mechanism with independent ENDF-field-to-inventory traces; successor scoring is frozen before further development results — comparable-case accuracy and coverage against the fixed eligible population per projectile, with zero predictions, failed construction and undefined ratios counting against explicit acceptance gates, and family counts reported beside row counts; construction completeness and adequate family coverage are prerequisites before spending fresh measurements; the old P18b metrics stay reproducible as historical evidence; if sufficiently independent unread evidence is unavailable, engineering qualification and retrospective validation are reported as such, and P18b-FAIL remains visible and unamended | P18b | diagnosis milestone first, repair estimate follows evidence |

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

## Draft next-evolution extension — P26–P35 (2026-09-13)

**Status: requested draft; every phase below is unopened and unhashed.** This section is the canonical proposed
scope for the next evolution of ACTINV. It supersedes the advisory E0–E5 phase proposal in
[the competitive research note](COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md), while retaining that note as source
research. It does not change a frozen protocol, prior verdict, evidence record or release authorization. P25 is
the active phase at drafting; its repairs, P24's corrected-definition qualification and the current release hold
retain their existing order and authority. Extension execution begins only after those obligations have a recorded
disposition. Research and drafting may proceed meanwhile. Draft phase numbers and dependencies may be revised by
a dated entry before their protocols freeze; this document does not open ten concurrent phases.

The drafting baseline is ACTINV commit `5049129`; ongoing P25 changes are not assumed complete. The Core interface
review used source `3e6dd8f0eb15509143c4f8e472ee6b899880dc2e`. Both identities must be refreshed and pinned at the
relevant phase opening. No new benchmark, user study, solver job or AI evaluation was executed to write this draft.

### Intended product outcome

**An analyst can complete an activation investigation, understand what controls its conclusions, test the
important alternatives and give a colleague a reproducible result with substantially less effort and waiting.**
Leadership must be demonstrated on complete tasks within a declared activation domain. Feature counts, a fast
CRAM kernel, fluent explanations and phase completion do not establish that outcome.

The proposed flagship is an impurity-sensitive material comparison under a specified neutron spectrum and
irradiation/cooling history. It follows activity, decay heat and photon production at selected cooling times;
explains the important nuclides and pathways; tests whether conclusions survive the declared composition, flux
and nuclear-data uncertainties; and packages the evidence for independent reproduction. Shielding is included
where the chosen physical regime requires it. An associated spatial case exercises the flux-to-photon handoff
through an external transport code. These are candidate workloads for P26 to validate against actual needs.

The candidate users are activation analysts comparing materials and histories, researchers preparing shutdown
source terms, and developers embedding inventories in larger workflows. Early evidence of these needs comes from
application studies and public user discussions, predominantly in fusion. It does not establish representative
demand for AI, automatic error budgets or any particular speed target. P26 must test those priorities against
recent difficult projects before substantial architecture work.

| Outcome | Draft target and evidence required |
|---|---|
| Useful predictive results | Independently controlled calculations with explicit material/projectile/energy/response coverage; no comparable-case regression beyond frozen tolerances and no hidden coverage loss. Predictive superiority requires separate, predeclared measurement comparisons on an adequately covered domain. |
| Investigations previously too expensive | A real user-derived campaign completes within a hardware, elapsed-time and output budget fixed before optimization. Research ambition: at least 10x lower complete campaign wall time on the primary workload and 3x on a second distinct workload against the fastest accessible equivalent-output comparator. P26 must establish plausible headroom. |
| Faster scientific understanding | At least 50% lower median hands-on time on the selected interpretation/comparison/handoff tasks, with no lower observed completion rate and no increase in consequential errors under the frozen evaluation. Predeclare timeout/failure scoring; report all participants, spread and evidence limits. A small study cannot establish a population-wide error-rate guarantee. |
| Reliable assisted work | Every shipped assistant action maps to a maintained contract family and a supported combination; accepted numerical assertions and technical verdicts come from computed evidence. No seeded critical contract-bypass or false-verdict case may be accepted in the frozen battery. Ordinary-language task success is measured separately. |
| Reproduction and interoperability | Another supported installation can regenerate required results and verify their identities/declared numerical equivalence without the original AI session. At least one spatial source handoff is validated through external photon transport. |

The numbers above are proposed design targets, not measurements or frozen acceptance thresholds. P26 must record
whether the ambition is feasible and valuable. If it is not, close with that finding and propose an explicit
revision before opening dependent phases. A missed leadership target cannot silently become a pass by relabeling
it a stretch goal. A scientifically qualified, useful release and a competitive-lead claim have separate verdicts.

### Architecture and scope boundaries

ACTINV owns the scientific calculations, diagnostics and study operations. A versioned study definition records
inputs, variable axes, requested responses, numerical criteria, uncertainty assumptions and required cases. CLI,
Python, desktop and AI use the same validation and study construction path. Study support is a thin layer over
the existing engine and interfaces, not a general project-management platform or a UI rewrite.

Avila Core is an optional, version-pinned external evidence and execution integration. Standard ACTINV calculations
remain usable offline without Core, an AI model or a service account. The supported AI execution feature requires
Core and qualified ACTINV adapters. Core binds the instantiated question, tools, data and outputs, admits claims,
and derives requirement verdicts; ACTINV and independent controls establish the scientific meaning of its outputs.
Core's current MCP interface exposes recorded-result queries, not solver execution. A controlled invocation of its
runner is a distinct integration deliverable. Packaging and distribution compatibility must be resolved in P27
before choosing an embedding or bundling strategy; no new dependency is authorized inside P25.

The application constructs contracts deterministically from maintained releases. The analyst supplies study facts
and meaningful choices, such as cooling times and the response of interest. Documented defaults may supply a setting
only within their justified applicability. The assistant can propose a supported template and allowed values; it
cannot author new evidence rules, change comparators or tolerances to obtain a pass, replace a checker, or remove
cases from an accepted campaign. A consequential change creates a new identified study revision. Execution can
proceed within an explicitly accepted study and resource budget without asking again for every individual case.

Contracts are more than prose. Each release includes the template, parameter schema, compatibility rules, pinned
registry and adapters, executable checkers, required evidence, qualification references and negative controls.
Core does not interpret the scientific truth of question/assumption text. The expected population, tolerances,
claim types and completeness tests must therefore be executable. Review of a template does not by itself qualify
the calculation method or its data. In particular, P18b-style denominator loss needs a direct population check.

The assistant remains optional and model-independent at the tool boundary. It may explain evidence and suggest
follow-ups; the application renders authoritative values, verdicts and qualification status independently of the
generated prose. Its available actions cannot bypass the trusted template/adapter identities or the accepted
study. Files, diagnostics and retrieved text remain data rather than execution instructions. Hosted inference
requires an explicit data-sharing setting; local model support is evaluated for the chosen tasks, not promised
to have the same capabilities. A saved study, its results and verification must remain usable without inference.

**AI delivery decision (maintainer, 2026-09-13): users connect their own AI provider account.** The built-in
assistant connects directly from ACTINV to a supported provider's API using customer-supplied credentials;
the provider runs the model and bills the customer directly. ACTINV supplies the interface, study context and
controlled tools. ACTINV calculations and Core execution/checks remain local. The default assistant requires
internet access and provider API access, but no local model download or GPU for language-model inference.
An optional user-managed local model endpoint may be evaluated separately; it is never an installation requirement.

Avila will not operate an AI backend, request proxy, model-hosting infrastructure, account/billing service or
managed AI subscription for this extension. AI setup and operation must have no runtime dependency on an
Avila-hosted service. This is a fixed delivery constraint for P33–P35, not a choice left for implementation.
Persisted credentials belong in the operating system's credential store; session-only entry is an alternative.
Credentials must never enter study specifications, contracts, execution receipts, logs or exported bundles.
Users explicitly control the study context sent to their provider. Missing credentials, connectivity or provider
quota may make assistance unavailable; ordinary local calculations, saved results and verification remain usable.

Within this extension: deeper qualified activation, response-specific numerical control, practical uncertainty,
efficient campaigns, complete source handoffs, portable evidence and narrowly scoped AI assistance. Additional
projectiles, an internal transport solver, criticality, thermal-hydraulics, general fuel-cycle flowsheets,
multi-tenant services, cluster orchestration and unrestricted autonomous research remain outside this draft.
Existing charged-particle support and documented limitations must be preserved and tested; the flagship's neutron
focus does not authorize silently dropping another supported population. MCNP or other integrations require a
concrete need and lawful validation route. AI-generated nuclear data and replacement of the qualified solver by a
learned predictor are outside scope. Any later surrogate would need its own evidence-directed scope and controls.

### Maintained contract families

The goal is complete coverage of the officially supported assisted workflows through parameterized families.
There is no contract per alloy or spectrum. Supported compositions of families are explicit in a compatibility
matrix: independently qualifying two features does not qualify their combination. All interfaces check the same
matrix, and an unsupported request returns a specific limitation rather than an improvised contract.

| Family | Fixed obligations | Per-study values and boundaries |
|---|---|---|
| **ACT-STUDY-01: activation and cooling** | Identify every input and required case/output; validate units, normalization, data applicability and completion; expose missing channels and numerical limitations. A completion verdict is not a prediction-accuracy verdict. | Material, supplied spectrum, irradiation/cooling history, data selection, supported physics options and requested responses. |
| **ACT-COMPARE-01: controlled comparison** | Declare permitted changes and common reference conditions; preserve the eligible population, zero predictions, failures and undefined metrics; check comparable outputs and predeclared decision rules. | Candidate materials/histories/libraries, response and times, permitted variation axes, justified comparison criterion. |
| **ACT-ROBUST-01: sensitivity and uncertainty** | Enforce parameter constraints, declared correlations, consistent physics perturbations, complete sample accounting and convergence checks; preserve covered and uncovered uncertainty separately. Sampled stability is not a whole-domain proof. | Input ranges or justified distributions, covariance sources, selected responses, sampling plan, supported local or nonlinear method and any ranking criterion. |
| **ACT-REFINE-01: numerical refinement** | Execute the declared refinement and response-comparison procedure; distinguish a proved bound from an estimator or unresolved contribution; fail to establish the requested criterion when evidence is insufficient. | Responses and times, absolute/relative numerical tolerances, refinement limits and work budget within the supported method envelope. |
| **ACT-SOURCE-01: spatial photon handoff** | Verify material/cell mapping, volumes, units, flux normalization, photon totals/energy and spatial distribution; preserve transport and inventory provenance. No dose claim from an unexecuted transport stage. | Supported mesh/cell representation, imported flux/material mapping, cooling times, source representation and external transport configuration. |

Input identity, reproducibility, changed-study lineage and portable evidence are common obligations. Thresholds
that express a user's scientific question are visible study settings fixed before candidate evaluation; numerical
defaults must have a qualified scope. Protocol qualification populations and acceptance thresholds belong to the
maintained checks and cannot be relaxed through an ordinary study setting. Every release records family/version,
instantiated contract identity and checker identity. Updating a release never rewrites past studies or verdicts.

**P26 replan entry (2026-09-14).** P26 closed `P26-FAIL`: the workload derivation, comparison
contract and measurement machinery all hold, but the drafted ambition could not be established —
the 10x headroom leg is unmeasurable on this workstation (ALARA's only local library covers none of
the contract elements; FISPACT-II, SCALE/ORIGEN and OpenMC are unavailable) and the measured
in-process amortization on the executed 1,000-case campaign is 1.33x, not 10x. Undetermined targets
are a phase failure mode under the frozen protocol. Replan before any extension phase opens:
(a) give the comparator leg a real dataset — the on-disk FENDL-3.2c ENDF corpus is ALARA-convertible
and would make at least part of the contract population executable by ALARA; (b) re-scope the
headroom ambition to mechanisms with measured support — shared prepared-network loading is a P31
candidate, not a 10x claim; (c) the 50% hands-on target needs the maintainer's practitioner study
before it can be anything but undetermined; (d) the R2S handoff needs a lawful transport comparator
(collaborator OpenMC/PyNE or local install) to be measurable. No P27+ phase opens on this record.

**P26b replan-execution entry (2026-09-16).** P26b closed `P26b-CONDITIONAL`. Replan item (a)
executed: the on-disk FENDL-3.2c corpus was converted for ALARA (all 36 contract nuclides
accepted) and built for ACTINV (24/36 accepted; 12 rejected across five named validation
classes, including all five W isotopes and all four Cr isotopes — a systematic FENDL-vs-TENDL
encoding difference, not sporadic noise). The comparator leg then executed:
`identical_data` is a total contract_gap (1,016/1,016 — every case needs an ACTINV-rejected
parent), so the executability asymmetry is itself the measured result; `product_plus_data`
executed all 408 executable cases cleanly (ACTINV shipped v1.1.0-patched artifact vs ALARA
FENDL-3.2c), with per-case artifacts, digests and independently re-formed metrics verified.
Response differences are data effects and are reported, never a solver verdict. Item (b) is
dispositioned here: the headroom ambition is re-scoped to mechanisms with measured support —
the 10x draft target is not carried forward; shared prepared-network loading remains a P31
candidate on the measured 1.33x amortization. Item (c) remains unestablished (the
practitioner study is the maintainer's; the 50% hands-on target is undetermined and not
claimed). Item (d) remains unmeasurable (no lawful transport comparator on this workstation;
the R2S handoff stays closed and is inherited by P32). P27 may open only on these recorded
terms.

**P27 execution entry (2026-09-16).** P27 closed `P27-PASS`. The shared-study layer is
delivered and executed: versioned `actinv-study-1` schema with deterministic Cartesian
expansion (byte-stable manifests, stable `material__spectrum__schedule` case ids),
`actinv study validate|build|run`, explicit population accounting, predeclared comparison
rules, family gating that names unqualified fields (`robustness`, `refinement`,
`spatial_handoff` refuse `family_not_qualified` with the delivering phase), and template
revocation. The Core interchange is real and end-to-end: a generated case package
(contract, registry snapshot, external-checker adapter, claims) compiles and executes under
`avila.core/semantic/0.2-draft` — all 8 frozen smoke cases ran in staged workspaces with
digest-bound specs, staged data and a package-pinned step runner; every categorical
requirement passed, bindings verified, campaign evaluated. Core `evaluated` is contract
evaluation under a DRAFT profile — staged execution and evidence binding, not scientific
qualification or regulatory acceptance. The frozen 12-class adversarial battery was
executed against the real machinery and every class was rejected; a real defect found in
that battery (duplicate axis names colliding case ids) is now refused at validation with a
regression test. G1/G2/G3 checkers and the G4 closure checker all pass with all planted
mutations rejected (`results/verdict_p27.json`, `controls/check_g{1,2,3,4}_p27.py`).

**P28 execution entry (2026-09-17).** P28 closed `P28-CONDITIONAL` on the neutron /
fispact-709 / 293.6 K regime of the patched TENDL-2025 artifact. All four source-to-rate
traces recover every library group within tolerance (709 single-group unit-flux probes
per channel; Fe56->Mn56 max rel 3.7e-9, Fe54->Mn54 1.3e-12, Co59->Co60 9.7e-8,
Co59->Co60m1 5.5e-8; the artifact stores columns ascending, reversed against the spec's
descending listing). Analytic limits pass: zero flux produces exactly zero; the Mn-56
one-day decay ratio matches the ENDF/B-VIII.0 tabulated half-life to 2e-14; the
production/loss closure residual is exactly zero. Boundary cases measured the envelope:
temperatures other than 293.6 K and the proton projectile fail closed with named errors;
an uncovered shield-table nuclide under `require_shielding_complete` fails closed, and
as passthrough is ledgered by name; a mutated table digest is refused. The
applicability map covers all 113 artifact elements: 103 qualified, 5 qualified with P25c
ledger defects, 5 with dosimetry-critical parents absent (Ag, Au, In, Nb, Ni). One silent
run-ledger coverage absence is recorded: natural nickel executes without Ni-58 with no
ledger entry naming it (the map carries the named record). The verdict stays CONDITIONAL
because frozen boundary combinations landed in contract_gap and coverage gaps remain;
geometry-dependent transport is unchanged and remains external
(`results/verdict_p28.json`, `docs/APPLICABILITY.md`,
`controls/check_g{0,1,2,3,4}_p28.py`).

**P29 execution entry (2026-09-17).** P29 closed `P29-CONDITIONAL`: the ACT-REFINE-01
family now accepts per-(response, time) criteria (`rel`/`abs` bounds, absolute-only near
zero) discharged against a reference solve at `prune: none`, `bmin: 0`, `cram_order: 48`,
`mode: coupled`, with per-component error accounting (solver and population components
empirically estimated from CRAM/pruning variants; processing collapse bounded at the
P25c/P28 measured 1e-6 tolerance) and a finite escalation ladder inside a declared
`resource_limit_runs`. All 48 frozen criteria on the 8-case population recorded
`satisfied` — the inventory tail only after escalating to reference settings, which is
the measured evidence that default pruning drops nuclides the reference retains.
Analytic controls: Mn-56 impulse decay matches the ENDF/B-VIII.0 half-life to 9.4e-15;
the Mo-99 -> Tc-99m -> Tc-99 chain recovers the tabulated branch (0.879) from a short
hold and independently predicts the one-day daughter within tolerance; the stiff
1 y cooling case agrees declared-vs-reference to 4e-13; the zero-flux case reports
exactly zero. Conformance: a criterion at a non-emitted time is `unestablished`, an
unattainable bound under `resource_limit_runs: 0` is `unmet` with zero escalations,
and an unqualified response is refused at validation. One amendment was used
(schedule-name repair) and one limitation was discovered: under `auto`/`trace` a
radioactive feed nuclide is an undepleted constant source — over a 1 d hold the
daughter is overproduced ~6.45x and mass is not conserved; radioactive initial
composition is qualified only under `coupled`. The verdict is CONDITIONAL per the
closure rule; computational error is controlled within the demonstrated envelope only,
and no physical-accuracy or convergence claim follows (`results/verdict_p29.json`,
`docs/STUDY.md`, `controls/check_g{0,1,2,3,4}_p29.py`).

**P30 execution entry (2026-09-17).** P30 closed `P30-CONDITIONAL`: the ACT-ROBUST-01
family runs a declared number of perturbed solves per case alongside the nominal solve —
correlated MF=33 collapsed-covariance draws (Cholesky with a diagonal-ridge ladder),
flux-normalization draws and element weight-percent draws renormalized to the declared
total — with per-(response, time) sample statistics, per-sample spec/output artifacts,
and separate accounting for sampling error, covered rows, uncovered rows, failed
samples, clamping and covariance regularization. On the frozen two-case qualification
population all 32 samples executed with zero failures under correlated sampling
(coverage 166/211 active rows Fe, 210/266 Fe+Co; ridge ~7-8e-8 barn^2 of mean
diagonal). Explanation views are delivered with the sample record: a
local-vs-nonlinear check compares the sampled spread with P11 first-order propagation
(sampled/first-order 0.76-0.84 on the population, the deficit being measured clamp
truncation), channel attribution reports isolated per-channel variances plus the
unexplained interaction remainder, and a pathway view names the dominant nuclides and
their production legs per cooling time with the unattributed closure. All five frozen
controls pass (flux linearity to 1e-6, exact zero-variance reproduction, fixed-seed
content determinism, coverage accounting, composition-sum preservation) and all five
negative controls fail closed with named errors; invalid `rate_scale` rows are
rejected at run time and the ledger names the applied count. The verdict is
CONDITIONAL: MF=33 coverage is partial (uncovered active rows carry no declared
uncertainty), a nonzero covariance ridge was required, hundreds of draws were clamped
nonpositive and are reported rather than hidden, the total-activity local comparison
uses a root-sum-square approximation over per-nuclide bands, and a sample spread is a
sensitivity over the declared input distributions — not a domain bound, not a
rigorous confidence interval, not an evaluation comparison; no blind experimental
validation was performed (`results/verdict_p30.json`, `docs/STUDY.md`,
`controls/check_g{0,1,2,3,4}_p30.py`).

**P31 execution entry (2026-09-17).** P31 closed `P31-CONDITIONAL`: the study
executor now shares a `PreparedRun` context across every case and robustness
sample whose spec carries the same data-and-option signature (library, decay,
photon response, fission yields, projectile, spectrum shape, temperature and
the uncertainty/radiological/damage/self-shielding options); material,
schedule and `rate_scale` are runtime inputs. The collapsed activation library
is flux-bound, so each spectrum is its own signature — the sealed smoke
population of 8 cases uses exactly 2 prepared runs, and the robustness
workload 2 (one nominal + one local-check signature shared across both cases).
Per-case-prepared baseline runs produce bit-identical results (modulo volatile
timing), so reuse changes preparation cost only. The study record is streamed
after every case (`status: partial` until complete) and `study run` resumes
into an existing output directory: a case is skipped only when its recorded
spec digest, output digest and every sample artifact re-verify byte-for-byte;
skipped cases are listed under `resumed_cases`. Warm resume of the frozen
workload measured 0.069 s vs 2.6 s cold (~38x, local measurement on the frozen
workload — not a competitive claim). All six controls pass: reuse
correctness, torn/corrupt/stale-spec resume, distinct-signature accounting and
kill-resume completion; all five negative controls fail closed (declared-hash
mismatch, torn record, phantom record entry, option drift, directory
pollution). The verdict is CONDITIONAL: two G0 amendments were used (the
signature had to include the spectrum; the robustness prepared-run target was
corrected after measuring signature sharing), and reuse is exact preparation
sharing only — no solver-result caching, approximate reuse or cross-hardware
headroom claim (`results/verdict_p31.json`, `docs/STUDY.md`,
`controls/check_g{0,1,2,3,4}_p31.py`).

**P32–P34 dispositions (2026-09-17).** P32 closed `P32-CONDITIONAL`: OpenMC
0.15.3 was installed (conda-forge) and the full chain executed on the frozen
voxelized-Fe study — neutron transport with a 4×4×4-mesh × 709-group flux
tally, `actinv import-flux openmc`, a 64-cell mesh activation, the new
`actinv export-openmc-mesh` distributed source (one `openmc.stats.Box`
`IndependentSource` per voxel per cooling step), executed OpenMC photon
transport for both cooling steps, and an `openmc.deplete` comparison leg
(median relative deviation 2.1e-7 across 704 cell–nuclide pairs). Conditions:
self-produced geometry only (no external benchmark), the photon leg is a flux
proxy not a qualified dose prediction, and tally statistical error is not
propagated into the comparison (`results/verdict_p32.json`,
`controls/check_g{0,1,2,3,4}_p32.py`). P33 remains `P33-BLOCKED`: it requires
a direct connection to the user's own AI provider account plus independent
human evaluators, neither available to the agent. P34 is `P34-BLOCKED`,
inheriting P33's blocker (`results/verdict_p33.json`,
`results/verdict_p34.json`).

**P35 execution entry (2026-09-17).** P35 closed `P35-CONDITIONAL`: the claim/
limitation matrix was derived from the on-disk verdict files and the release
recommendation scoped to what is actually qualified. All six phase closure
checkers (P26b–P31) re-verified evidence-chain integrity on the candidate
tree; the reproduction leg re-executed the frozen smoke population into fresh
directories with semantically identical outputs and runtime-verified data
digests. All four controls pass (matrix consistency, battery completeness,
reproduction identity, blocked families named) and all four negative controls
fail closed (upgraded verdict, omitted family, forged reproduction,
unsupported claim). The verdict is CONDITIONAL: the release recommendation is
`conditional_release` — ship the activation/study/uncertainty/campaign
envelope under its recorded conditions, and explicitly do not claim spatial
handoff, AI-assisted capability, experimental validation or competitive
results beyond the locally measured preparation amortization
(`results/verdict_p35.json`, `controls/check_g{0,1,2,3,4}_p35.py`).

**P36 execution entry (2026-09-17).** P36 (flagship W-MATCMP, non-AI leg)
closed `P36-CONDITIONAL`: the frozen three-case RAFM impurity study executed
end-to-end under the sealed FNS 709-group spectrum and two-year schedule —
activity, decay heat, grouped photon sources, inventories and trace-mode
pathways at five cooling times, plus 24 robustness samples per case with
zero failures. All three declared decision rules pass at the scoped 100 y
cooling time (ranking high > base > low, impurity span 10.12x); the
high-vs-low difference is driven by Ni-63 (96.4% share) via Ni-64(n,gamma),
and Nb-94 attributes to Mo-98 feed rather than the Nb impurity. The flagship
forced two machinery changes: `DecisionRule.times_s` scopes comparison
rules to declared cooling times (early-time ordering genuinely differs),
and `CollapsedLibrary::validate_flux` now compares normalized spectrum
shapes rather than raw flux bits — collapsed rates are
flux-denominator-normalized, so bit-exact validation wrongly rejected every
flux-normalization sample (24/24 failed on first execution; one G1 repair
round). The AI leg of W-MATCMP remains open under P33/P34's blockers; this
phase does not constitute experimental validation
(`results/verdict_p36.json`, `controls/check_g{0,1,2,3,4}_p36.py`,
`protocols/ACTINV-P36_PROTOCOL.md`).

**P37 execution entry (2026-09-18).** P37 (identical-data rejection
classification) closed `P37-CONDITIONAL`: all twelve FENDL-3.2c evaluations
ACTINV rejected during the P26b artifact build were classified by sealed
file-level forensics — **4 true_defect, 8 actinv_strictness, 0 ambiguous**.
True defects (file contradicts ENDF-6 or itself): Ni62 (Breit-Wigner GT
exactly 1000x below its component sum — a keV/eV unit mix), W182 and W184
(MF=1 and MF=2 headers carry different AWR values), W186 (MF=10 state
partials exceed its own MF=3 total by 5.09e-2 on the shared grid).
ACTINV-strictness (legal ENDF-6, stricter-than-format contract): the four
Cr isotopes, Mn55 and W180 (MF=10 activation channels shipped without MF=3
totals — products verified physically consistent), Fe57 and W183 (legal
negative-spin parity encoding in LRF=7 particle pairs). Consequence: the
P26b identical-data arm stays blocked — the strictness class is at most
admissible under a separate, protocol-governed relaxation decision; the
four defective sources cannot be lawfully admitted without upstream
correction or a governed normalization pass. No validation behavior
changed; no comparator or solver claim follows
(`results/verdict_p37.json`, `results/g1_p37_classification.json`,
`controls/g{2,3}_p37_controls.py`, `protocols/ACTINV-P37_PROTOCOL.md`).

**P38 execution entry (2026-09-18).** P38 (governed ENDF-6 legality
relaxation + identical-data leg re-execution) closed `P38-CONDITIONAL`:
three bounded format-legal relaxations landed — MF=10-only sections admit
with the MF=10 partial sum as an explicitly ledgered internal comparator
(`missing_total_self_comparator`, 20 MTs); negative IA/IB spins admitted
(ENDF-6 parity encoding); zero-NRS spin groups tolerate an all-zero
SAMMY placeholder LIST row. Result: **31/36 FENDL-3.2c parents build**
(764 rows); the P37 strictness class narrows to 7 after W-183 surfaced a
deeper true defect under the fixes (MT28 state excess 2.1e-3); all five
defects (Ni-62, W-182/183/184/186) still fail their original gates. The
identical-data arm is now structurally executable — 912/1016 cases, 40
sampled end-to-end with zero arm failures — **but it is not yet a solver
comparison**: ALARA/ACTINV diverge 1.2x-200x at shutdown (worst under
IRDFF, driven by lumped-channel MTs 600-849 that FENDL uses for
charged-particle production and ACTINV skips) while agreeing 0.995-1.011
at 9y cooling on ~90% of cases. The frozen 5e-4 tolerance fails
comprehensively and honestly records the coverage gap; lumped-MT
coverage is the named next blocker for the identical-data arm
(`results/verdict_p38.json`, `results/g2_p38_leg.json`,
`controls/g2_p38_leg.py`, `controls/check_g{3,4}_p38.py`,
`protocols/ACTINV-P38_PROTOCOL.md`).

**P39 execution entry (2026-09-18).** P39 (lumped-channel MT 600-849
synthesis) closed `P39-CONDITIONAL`: where a summary MT (103-107) has no
MF3/processed/MF10 coverage, the family's collapsed MF3 tables now sum
into one canonical-MT row (`lumped_channel_synthesis`, lfs=0, REAC-equivalent
semantics); where coverage exists the lumped sections are ledgered
redundant and skipped. Artifact: 31 targets, 777 rows (+13 synthesized
including Fe56(n,p)->Mn-56 and Mn55(n,alpha)->V-52; zero removals; no new
duplicates). Identical-data effect measured on the 36-case sample:
shutdown ALARA/ACTINV ratios collapsed from 1.05x-200x to **0.98x-1.10x**;
late-cooling unchanged at ~1%. The frozen 5e-4 tolerance still fails
everywhere — the leg now measures a smaller residual divergence class
(uniform ~9-10% under the IRDFF fast spectrum; a 17.5x Mo-impurity
late-time outlier on a non-lumped mechanism), not the coverage void.
All five defect evaluations still reject; no solver or equivalence claim
follows (`results/verdict_p39.json`, `results/g2_p39_leg.json`,
`controls/g2_p38_leg.py --p39`, `controls/check_g{3,4}_p39.py`,
`protocols/ACTINV-P39_PROTOCOL.md`).

**P39 residual-classification addendum (2026-09-18).** Both sampled
residual classes are data-REPRESENTATION differences, not ACTINV
production gaps: (1) the ~9-10% uniform IRDFF shutdown residual is
ALARA's Mn-58m1 (~11% of shutdown activity) from REAC-library
isomer-split heritage — FENDL-3.2c Mn-55 carries no (n,2n)->Mn-58m
carrier (MF8/10 isomers exist only for MT30/37; MF6 MT16 subsections
are all emitted-neutron), so the residual is a representation floor the
identical-data arm cannot cross without non-FENDL isomer data; (2) the
fe_mo100000wppm late-time 17.5x outlier is a decay-convention
difference — ALARA's idx assigns Mo-98 t_half=3.15569e21 s (phantom
0.033 Bq at 9y) while ACTINV treats it as stable; both libraries produce
the Mo-98 atoms. Recorded in `results/verdict_p39.json`
`residual_classification`. The identical-data arm's remaining
divergence is a named representation floor, not an open coverage gap.

**P40 execution entry (2026-09-18).** P40 (scoped identical-data
equivalence under named representation floors) closed `P40-CONDITIONAL`:
the complete **912-case executable census** (a 184-case stratified
stratum — every impurity element x wppm{1000,100000} x both spectra x
all five irradiations + base cases — was found fully representative;
extension to the full census left every bound and class unchanged)
executed with **zero arm failures**. Full per-nuclide activity vectors were recovered per arm and
every divergence class-tagged: `isomer_branching` (Mn58m1 in all 184,
Nb93m1, Co60m1, Tc99m1...) and `quasi_stable_convention` (V50, Mo92,
Mo98, Zr96, Cr50) are representational floors — ALARA's REAC-heritage
isomer splits and quasi-stable half-life conventions are absent from the
identical FENDL input and cannot be closed without breaking the
identical-data premise. The pre-registered `other` rule fired (8.7% of
cases exceed 1%), forcing two named OPEN classes:
`short_lived_products_absent_in_actinv` (Ti55/V55/Ti53/Cr57/Mn59) and
`common_nuclide_magnitude` (Cr51, Fe59, Cr55, Mn57, Fe53 — 2-30% gaps on
shared channels). Scoped result: on class-cleaned channels the arms'
total activity agrees to a median of 1.06%, p90 3.5%, worst 6.1% —
versus 16.5x worst-case uncleaned. The coverage-asymmetry mechanism was
then **confirmed predictively**: rerunning the classifier on the
product_plus_data arm (ACTINV-TENDL ~2800 targets vs ALARA, 408 cases)
collapsed the second-order 'other' share 3.17% -> 0.18% while the
isomer/quasi-stable floors persisted — coverage, not channel defects
(`results/g2_p40_ppd_check.json`). This is a measured residual over the
census, not solver validation (`results/verdict_p40.json`,
`results/g2_p40_classes.json`, `controls/g2_p40_classes.py`,
`controls/check_g{3,4}_p40.py`, `protocols/ACTINV-P40_PROTOCOL.md`).

**P40 mechanism addendum (2026-09-18).** Root mechanisms for both
open classes resolved to structure, not channel defects: ALARA's
library covers **4036 nuclides vs ACTINV's 31-target artifact** —
product-on-product reactions and product burn-out exist only in
ALARA's chain-complete library. `short_lived_products_absent` splits
into decay-file gaps (channel rows exist — Cr54 MT111->Ti-53, Fe58
MT111->Cr-57 — but Cr-57 is absent from BOTH decay files and Ti-53/V-55
from ENDF-B primary; products without decay data are dropped
fail-closed at solve time) and second-order channels on non-artifact
parents (Fe-59(n,p)->Mn-59), and threshold-blocked channels — the only
artifact rows for Ti-53/Cr-57 are MT111 (n,2p) at ~20 MeV, above both
spectra, so ACTINV correctly produces none. `common_nuclide_magnitude`
reflects burn-out/decay-feed asymmetries (Cr-55 systematically
ACTINV-low; Mn-58/Fe-53/Mn-57 systematically ACTINV-high). The identical-data arm
is saturated: remaining divergence is bounded by chain coverage
(FENDL-3.2c does not evaluate the product nuclides) and decay-file
coverage — data-availability limits, not ACTINV channel defects.

**P42 execution entry (2026-09-21).** P42 (mechanism closure of P40's
open classes) closed `P42-PASS`: all 13 member nuclides carry a
hash-pinned primary class under the frozen vocabulary, re-derived by an
independent checker with 6/6 mutation rejections. Ten members are
`alara_heritage`: ALARA's converted REAC rows emit to mass-impossible
daughters (e.g. Fe-57 `p*`→Mn-59, gaining 2 amu), and Fe-59/Mn-57/Cr-56/
Mn-58 inherit through `*D` decay feeds of phantom-produced parents —
verified quantitatively (ALARA's Mn-59 feed ≈ 20% of its Fe-59 atoms,
matching the observed 13% deficit). Three members are
`conversion_content` on shared channels: ALARA under-reads FENDL's
lumped content (50.4%/96.4%/38.7% for Cr-51/Fe-53/V-52) while ACTINV's
synthesized MT107/MT16 rows match the ENDF lumped sums within 0.3%;
ALARA's α-label totals conserve the cross section (~101%) but scatter
it across impossible daughters. Zero `true_defect` — no ACTINV row
deviated from FENDL ground truth — so G2 ledgered zero repairs and G3's
re-census reproduced P40 byte-identically. Two corrections to the P40
record: Cr-57 is present in both pinned decay archives (MAT 471 in
ENDF-B-VIII.0), superseding the "absent from both decay files" note;
and the P26b stdout resolver drops isobar-ambiguous rows (Ti-53/Ti-55),
corrected by t_1/2-matched extraction in the G1 census
(`results/verdict_p42.json`, `results/g1_p42_mechanisms.json`,
`results/g2_p42_repairs.json`, `results/g3_p42_recensus.json`,
`controls/check_g{0,1,2,4}_p42.py`, `controls/g{1,2,3}_p42*.py`,
`protocols/ACTINV-P42_PROTOCOL.md`).

### Phase sequence and acceptance gates

The default execution order is P26 through P35, one phase at a time. Each phase inherits all applicable earlier
controls and updates the support matrix and contract evidence for capabilities it actually qualifies. The rows
below are bounded planning scopes; the detailed protocol, minimum gate input, tolerances, resource envelope,
independent checker and stop rule must be frozen before each phase executes. No calendar commitment is made here:
P26 supplies estimates based on measured bottlenecks, method feasibility, evidence access and user availability.

| Phase | Deliverable | Entry dependency | Decisive gate |
|---|---|---|---|
| **P26 — user workloads and feasibility** | User-derived flagship, current competitive baseline, evidence partitions and bounded method prototypes. | Recorded P25/P24 and release-hold disposition. | A consequential user problem, complete comparison contract and a credible technical route to the proposed advantage; otherwise explicit replan. |
| **P27 — shared studies and Core contracts** | Versioned study model, deterministic template construction, supported operations and initial Core adapters. | P26. | Every initially supported action has a tested contract/adapter; mutated populations, policies, identities and evidence cannot obtain an accepted result. |
| **P28 — qualified physics combinations** | The processing and shielding regimes needed by the selected studies, with a complete applicability map. | P27. | Independent rate and response controls across the declared regime and its boundaries, with missing coverage counted. |
| **P29 — response-specific numerical control** | Requested numerical criteria on selected outputs, refinement and explicit error accounting. | P28. | Bounds/estimators meet their stated claims on independent and difficult controls; unresolved error cannot masquerade as satisfied accuracy. |
| **P30 — uncertainty and scientific explanation** | Consistent nuclear/input uncertainty, sensitivities, pathways and controlled result comparisons. | P29. | Independent local/nonlinear controls, complete coverage/sample accounting and evidence-backed attribution in the supported combinations. |
| **P31 — efficient complete campaigns** | Reusable work, streaming and resumable execution across realistic variations, including uncertainty costs. | P30. | Frozen complete-workload speed/resource targets at qualified accuracy, including distinct inputs and failure recovery. |
| **P32 — spatial handoff and portable studies** | External-transport source integration and reproducible studies across CLI, Python and desktop. | P31. | Source conservation/spatial controls, an executed open R2S chain and independent handoff/reproduction tasks. |
| **P33 — assisted setup and interpretation** | Optional evidence-linked explanations and validated study drafts, using a direct connection to the user's own AI provider account. | P32. | Correct task completion, credential handling and provider-failure recovery; no Avila-hosted service dependency; official values/verdicts stay independent of narration. |
| **P34 — bounded assisted investigations** | AI-initiated execution and follow-ups through the maintained contracts and Core runner. | P33. | Accepted task intent, bounded execution, immutable rules, complete evidence and recovery hold under repeated and adversarial evaluations. |
| **P35 — independent product qualification** | Exact-candidate scientific, competitive, user and assisted-workflow qualification; release recommendation. | P34 and every shipped combination's controls. | Independent verdicts on scientific fitness, workflow benefit, AI reliability and competitive claims, with all prior failures preserved. |

**P26 — define the work users need and test the difficult ideas.** Examine recent projects with a proposed five
to eight practitioners across the candidate user groups, using existing work products where they can lawfully be
shared. Record preparation, computation, debugging, interpretation and handoff time; identify studies abandoned or
simplified and the practical reason. Seek repeat use of an actual case as adoption evidence. Outreach requires
separate authorization; drafting this roadmap does not contact anyone. When participants are unavailable, label
public studies/internal exercises as proxies and keep demand and usability conclusions unestablished.

Freeze two campaign workloads and one spatial handoff, required output/evidence parity, resource limits, comparator
versions and baseline configuration before optimization. Candidate scales are 1,000 material/history variants and
the existing 20,000 distinct-spectrum case; validate the scales rather than treating them as demand evidence.
Match physical assumptions, uncertainty scope and requested numerical accuracy across the timed paths; a capability
mismatch is reported as such and cannot become a speed ratio. Enable each comparator's appropriate reusable path.
Profile complete preparation, processing, solve, uncertainty and output costs. Test the smallest useful prototypes
for response error control and reuse across genuinely changing inputs. Declare a compute budget and a go/no-go
criterion for each prototype; promote a method only when it has independent numerical support and a useful cost
case. This is permission for bounded feasibility work, not unrestricted engine redesign.

Select separate development and final-evaluation tasks. Secure and seal suitable new measurement populations
before their values are examined; if none are available, plan an honestly retrospective/engineering qualification.
Opening deliverables are a workload manifest, user-evidence summary, combination matrix, baseline report, evidence
access plan and revised per-phase estimates. A missing comparator is unmeasured, not an assumed win. No required
development gate depends on obtaining a FISPACT/SCALE licence; optional lawful collaborators can widen claims later.

**P27 — make study construction and contracts a shared product capability.** Deliver the study schema and
deterministic generator, with initial executable ACT-STUDY-01 and ACT-COMPARE-01 paths limited to already qualified
operations. Define the interfaces and obligations for the remaining families, leaving unsupported combinations
explicitly unavailable until their delivering phase qualifies them. The maintained package, not generated model
text, supplies workflow topology, evidence extraction, population accounting and verdict logic. Structured settings
and inputs remain editable through the ordinary interfaces; users do not author Core JSON.

Pin the Core semantic profile and supported version range, define an external-process interchange and failure
contract, and qualify a minimum calculation/comparison end to end. Distinguish recorded attestations, current
artifact verification, reused execution and fresh execution. Make qualification policy explicit: Core's default
display of an unqualified-evidence warning is insufficient for an ACTINV result advertised as qualified. Test
incorrect units, changed data/executables, missing/duplicate cases, zero/undefined metrics, forged or stale evidence,
missing qualification, altered limits and unexpected schema fields. Independent checks must exercise substantive
metric/coverage semantics, not only reproduce a hash or validate JSON. Establish template migration and revocation
behavior without editing historical evidence. ACTINV's base calculation path stays independent of this integration.

**P28 — qualify the combinations the investigation needs.** Starting from P25's recorded disposition, identify
remaining method and coverage limitations that prevent the chosen studies. Qualify consistent rates, product
identity, production/loss accounting and shielding within the selected regimes. Add resolved or unresolved
treatment only where the workload requires it and an independent processing route exists. Check composition,
temperature, dilution, spectrum and supported data combinations at regime boundaries as well as ordinary cases.
Geometry-dependent transport remains an external input responsibility; a dilution approximation cannot claim to
replace it. Freeze the intended validation population before construction so unsupported cases cannot disappear.

Minimum gates include independent source-to-rate traces, analytic limits, an independently processed reference,
and complete inventory/response comparisons for the selected cases. Update family applicability and ledger
categories across all interfaces. Separate processing correctness from experimental prediction. If required
coverage cannot be established, record the gap and revise unopened scope; do not qualify a narrower easy subset
under the original broad claim.

**P29 — control numerical error in the response the user asked for.** Deliver numerical criteria for selected
activity, heat and photon responses at specified times. Account for solver, pruning, collapse/processing and
other relevant approximations individually; distinguish rigorously bounded, empirically estimated and unresolved
contributions. Demonstrate how combined error is justified, rather than adding unrelated estimates as though they
were a proved bound. Use absolute criteria near zero and declared relative criteria elsewhere. Missing support
must produce an unmet or unestablished criterion, with an exact/full calculation fallback where supported.

Adaptive refinement may spend work where the response requires it and must stop at a declared resource limit.
Reference controls include analytic chains, independent dense/high-accuracy solves, stiff/long histories,
near-zero outputs, and independent processing/refinement cases. A two-resolution agreement alone does not prove
convergence or a whole-pipeline error bound. Qualify ACT-REFINE-01 only for the demonstrated envelope. Computational
error, nuclear/input uncertainty and predictive discrepancy remain separate in every report. This phase cannot
promise a user-selected total physical accuracy that the data cannot support.

**P30 — make uncertainty and explanation work together.** Deliver a supported nonlinear sampling path alongside
local sensitivities, including the nuclear-data channels available to the selected studies and user-supplied flux
normalization/spectral and composition/impurity uncertainty where justified. Preserve positivity, composition sums,
correlations and schedule constraints; document independence assumptions when correlations are unavailable.
Recompute shielding and rates consistently when perturbed inputs affect them. Local methods require an explicit
applicability check against nonlinear controls; a fixed seed supplies repeatability, not convergence evidence.

Report sampling error, covered uncertainty, missing covariance and model remainder separately, with every sample
and failure accounted. Ranking claims name whether they concern nominal values, supplied distributions or a proved
domain bound. A spread across data evaluations is a sensitivity result unless a statistical interpretation is
justified. Build response-linked pathway/contribution views and controlled comparisons with explicit interactions
and unexplained remainder; sensitivities do not automatically prove causation. Independent analytic/finite-
difference and sampled controls check the numbers and interpretation. Qualify ACT-ROBUST-01 and the corresponding
comparison combinations; no LLM is needed to produce the underlying explanations or diagnostics.

**P31 — make the full investigation affordable.** Optimize measured work across changed compositions, histories
and distinct spectra: preparation, rate construction, network assembly, sensitivity/uncertainty execution and
output. Candidate mechanisms include reusable operator structure, batched responses and qualified response-aware
reduction. Select mechanisms from P26 evidence; no algorithm is mandatory merely because it is sophisticated.
Approximate reuse must carry the P29 error checks and revert to a supported full calculation when it cannot satisfy
them. Exact-repeat cache hits alone do not establish the primary advantage.

Measure elapsed time to all required results and evidence, including Core/checker overhead, preparation amortization,
sampling and output. Report cold and warm states and realistic distinct-input workloads; give competitors their
documented caches and suitable APIs. Include memory and artifact size at fixed output parity. Existing P21
checkpointing is a starting point; exercise cancellation during all expensive stages, termination/reaping, resume
after torn output, cache identity changes and resource exhaustion. The OS resource boundary must enforce limits;
a post-hoc memory counter is not an allocation limit. The frozen competitive target and scientific equivalence have
separate checker results, both including failed cases.

**P32 — deliver source handoffs and studies another person can use.** Qualify at least one open OpenMC workflow
from supplied neutron flux/material mapping through ACTINV activation to a spatial decay-photon source and external
photon transport. Compare with the pinned native OpenMC R2S path where outputs and data permit. Check cell IDs,
volumes, source normalization, spectra, cooling-time separation and spatial sampling independently; a point at the
origin cannot satisfy a distributed source contract. Account for transport statistical error separately and keep
source validation distinct from an unqualified dose prediction.

Package the study definition, template release, data/tool identities, results, diagnostics and verification route
into a portable record. Resolve bulky/licensed inputs by lawful references and hashes. Expose setup, requested
outputs, comparisons, limitations and reproduction through the existing CLI/Python/desktop using shared operations.
A new installation must reproduce a supplied study, diagnose a planted normalization/mapping error, and explain a
controlled change. Test missing data, relocation, changed versions, interrupted export and Core-unavailable basic
use. Qualify ACT-SOURCE-01 and complete all non-AI families needed by the flagship. The product should already be
useful and testable with AI disabled.

**P33 — assist setup and interpretation from evidence.** Introduce optional assistance for investigating recorded
results, finding documentation and preparing study drafts. Tools return structured outputs, units, applicable
limitations and source identities. Numerical answers are selected/computed by trusted tools; generated explanation
links to that evidence and marks hypotheses. The model cannot manufacture a pathway contribution or claim that a
saved receipt freshly verifies current files. A draft exposes material assumptions and unresolved inputs before
execution; matching a template's schema does not prove that the intended scientific question was understood.

Deliver provider/model selection and customer API credential setup inside ACTINV under the delivery decision
above. Show usage and bounded request/token budgets, provide cancellation and bounded retries, and identify which
study context is shared. Verify direct provider access, credential redaction, missing/invalid credentials, quota
and rate-limit responses, timeouts and outages. The assistant must preserve local studies and remain optional
through these failures. Qualify installation and operation without an Avila-hosted AI service or a local model.

This phase grants no autonomous solver execution. Evaluate unseen natural-language tasks, ambiguous normalization,
incompatible options, missing data, contradictory records and misleading instructions inside imported text.
Include a strong conventional UI/documentation baseline. Measure task correctness, unsupported factual claims,
hands-on time, model latency/cost and repeated-trial variability for each pinned supported model/configuration.
Use deterministic grading for numerical/record assertions and independent domain assessment for intent and
interpretation; an LLM judge alone cannot establish correctness. Freeze development/evaluation partitions before
tuning prompts or tool descriptions. A provider/model change triggers the relevant evaluation again.

**P34 — conduct bounded investigations through Core.** Enable the assistant to instantiate and execute only
qualified study operations within the selected contract families. The user accepts a concrete study scope and
resource budget; a trusted dispatcher binds the template, inputs, permitted variations and execution policy.
Core runs the bound capabilities and the application renders its authoritative report. The AI has no write
authority over trusted templates, checker executables, qualification records, receipts or technical verdicts.
Use process and file permissions to enforce that separation; conversational instructions and MCP path checks
alone are not an OS isolation boundary.

Follow-up actions may vary only accepted axes and must preserve attempt lineage. A new scientific question or
weakened criterion requires an explicit study revision; depleted budgets, unsupported requests and unresolved
inputs return a specific incomplete state. Test duplicate requests, idempotency, interruption/cancellation,
partial failures, retry exhaustion, invalid parameters, changed evidence and attempted contract bypass. Retain
all attempts, including failed samples, while avoiding duplicate solver work through verified reuse. Final
free-text conclusions must be checked against the structured result and cannot override its qualification limits.
Compare a contracted assistant with an ordinary tool-using assistant and with non-AI ACTINV on the frozen tasks;
measure whether Core actually reduces erroneous accepted claims and unnecessary execution. No statistical claim
of zero AI error follows from a finite passing battery.

**P35 — qualify the exact product and decide which claims it earns.** Re-run the frozen scientific, numerical,
uncertainty, source-handoff, performance, reproduction and AI task batteries on exact candidate artifacts, data,
Core/template versions and supported model configurations. Use new eligible measurements only according to their
sealed protocol; preserve P17/P18/P18b failures and consumed partitions as historical/diagnostic evidence. Report
common-case results and whole eligible-population coverage together, by family/projectile and important regime.
Publish disagreements rather than attributing them to a competitor without matched-data/source evidence.

Independent checkers derive numerical results, completeness and technical verdicts. Human task evaluation records
participant background, task assignment/order, failures, hands-on and elapsed time, and observed errors under
predeclared severity definitions. Unavailable user/comparator evidence remains unmeasured. A scientific release
recommendation can be positive while usability, AI reliability or leadership remains unestablished; the release
must then exclude the unsupported feature/claim and retain the missed gate. A failed phase still follows the
standing successor rule; a required scientific or AI gate cannot be waived to ship its affected feature. Check
final installation, supported-platform reproduction and ordinary operation without
AI/Core. Produce a release recommendation and explicit claim/limitation matrix; tagging, publishing and external
communications retain their existing authorization requirements.

### Evidence, resource and completion rules for this extension

1. Pin and report actual executable/module/data identities for every measured leg. P22's process performance leg
   deliberately used v1.0.0 while its Python kernel leg used the candidate; it is historical evidence, not the
   baseline measurement of this extension's prepared candidate. Add new records and leave CB1/P22 unchanged.
2. Freeze eligible populations, required outputs, metrics, comparison rules and failure accounting before scoring.
   Positive measurements with zero predictions, failed construction, missing samples and undefined ratios never
   disappear through finite-only filtering. Report family counts beside row counts.
3. Distinguish numerical equivalence, processor correctness, data coverage, experimental accuracy, user task
   performance and AI reliability. A green result in one category does not establish another. Treat identical-data
   and each-tool-with-its-data comparisons as separate experiments.
4. Bind contract/checker qualification to the intended output and domain. Core integrity verifies identified
   evidence under stated rules; it cannot repair an incorrect physical premise. Template selection, parameter
   binding and whether the study answers the user's intent are separate evaluation obligations.
5. Count all time relevant to the claimed benefit. Inventory campaign speed cannot stand in for whole R2S speed;
   report external transport, hands-on work and model/network latency separately and in complete task totals.
   No extrapolated million-cell or universal competitor claim. Separate feasibility data from final comparisons.
6. Apply the current [local safety rules](../AGENTS.md): only the coordinating agent runs builds, executable tests,
   benchmarks or solver jobs, one job at a time inside the enforced systemd cgroup. Stop if enforcement fails;
   use disk-backed `target/preflight-tmp` for temporary build work, profile the minimum gate case first, and make
   long work resumable. Historical `ulimit` guidance alone does not replace those protections.
7. Every delivering phase updates the machine-readable family/combination matrix, user-visible limitations,
   independent controls and qualification references. No shipped assistant action relies on a template that exists
   only as a draft. A family has meaningful positive, negative and boundary tests, including attempted evidence
   weakening. Composing supported families requires explicit combination evidence.
8. The final extension has four separately reported outcomes: scientific qualification, user-workflow benefit,
   assisted-workflow reliability and competitive leadership. Its defining demonstration is the complete flagship
   investigation, with and without AI, on a case selected before implementation optimization.

Planning milestones are therefore P26 (established need and feasible mechanism), P27–P30 (scientific investigation
and contract foundation), P31–P32 (efficient, portable non-AI product), P33–P34 (qualified assistance), and P35
(independent product decision). Useful earlier capabilities may have separate qualified release candidates; this
does not confer a later phase's verdict or authorize publication. Uncertainty in estimates is concentrated in the
P28 physics envelope, P29 numerical bounds, P30 covariance coverage and P31 performance headroom; P26 must make
those risks concrete before the implementation sequence is committed.

## Draft innovation extension — P43–P48 (2026-09-23)

**Status: requested draft; every phase below is unopened and unhashed.** This section is the canonical proposed
scope for converting the P26–P35 machinery into product-level claims. It changes no frozen protocol, prior
verdict, evidence record or release authorization. P24's corrected-definition re-validation retains its own
schedule and is not subsumed here; the P17/P18/P18b/P25 failure record stays visible and unamended. P33/P34
remain blocked on external dependencies (user AI-provider credentials, independent human evaluators) and are not
resequenced by this draft. Phase numbers and dependencies may be revised by a dated entry before their protocols
freeze; this document does not open six concurrent phases.

Drafting baseline: commit `cc21d72` (v1.2.1 record). The evidence base is CB1–CB3, the P26b–P42 verdicts, and
the levers analysis in [LEADERSHIP_LEVERS_2026-09-20.md](LEADERSHIP_LEVERS_2026-09-20.md). No new benchmark,
solver job or comparator execution was run for this draft.

### Intent and product outcome

The identical-data accuracy margin over FISPACT-II is measured to sit inside decay-evaluation sensitivity
(ENDF-primary 71/132 vs 69/132; JEFF-primary erases it). Further solver-accuracy work is therefore not the route
to a defensible lead, and the well is recorded as nearly dry. The owner's scoring axes are accuracy, speed and
capability breadth. This extension targets capabilities no competitor currently offers to any user:

**An analyst can state a defensible uncertainty on an activation conclusion, afford the campaign that produces
it, choose a nuclear-data evaluation on evidence, and explore the result interactively — in an open install.**

| Outcome | Draft target and evidence required |
|---|---|
| Uncertainty that means something | Measured per-family coverage of declared uncertainty bands against the sealed experimental corpus, frozen scoring before band computation, failures counted against coverage. A band always names its included channels and uncovered remainder. This yields a calibration claim — "measured coverage on the sealed corpus" — not a universal coverage guarantee, not predictive-superiority. |
| Affordable complete campaigns | Executed end-to-end wall time on frozen campaign workloads against the fastest accessible equivalent-output comparator with its documented amortization enabled. The claim is the executed number at named workload/comparator/resource limits; a miss is published, not relabeled a stretch goal. Capability mismatches are reported, never divided into a ratio. |
| Evaluation intelligence | Per-target and per-family scored agreement for every lawfully buildable evaluation corpus under an identical pipeline and scorer, with construction failures counted per corpus. No recommendation is rendered without its attached evidence row. |
| Interactive exploration | Desktop live re-solve on qualified paths through the identical spec→result machinery, measured interaction latency on the flagship workload, bounded and cancellable per-interaction compute. A second numerics path is not created. |

### Scope boundaries

Within this extension: qualified uncertainty campaigns, measured band coverage, executed campaign benchmarking,
evaluation intelligence, dose-qualified spatial handoff completion, interactive desktop exploration. Each is an
existing machine or partially-executed chain being converted into a product claim, not new physics.

Outside this extension, unchanged and demand-led: gamma/triton/helion projectiles, resolved-region pointwise and
probability-table self-shielding, an internal transport solver, criticality, thermal-hydraulics, MPI/cluster
orchestration, MCNP distributed-source emission (unverifiable without a licensed install), and the blocked
P33/P34 AI legs. None opens because a matrix cell is empty.

External dependencies remain the principal's acts (standing rule 6): the JADE code-target contribution, the
NEA/RSICC submission, and a SINBAD licence. A phase may consume their outcomes where they exist but cannot list
obtaining them as a gate. Where a comparator or corpus stays inaccessible, the cell is `unmeasured` — never a
silent win or loss.

### Phase sequence and acceptance gates

Default execution order is P43 through P48, one phase at a time. P46 and P47 are machinery-independent of
P43–P45 and may be resequenced by dated entry; P24 retains its own schedule wherever it is slotted. Each phase
inherits all applicable earlier controls and the P26–P35 extension's evidence, resource and completion rules.
The rows below are bounded planning scopes; protocol, minimum gate input, tolerances, resource envelope,
independent checker and stop rule freeze before each phase executes.

| Phase | Deliverable | Entry dependency | Decisive gate |
|---|---|---|---|
| **P43 — qualified uncertainty campaign driver** | The robustness sampling path promoted from verification control to supported product path: ACT-ROBUST-01 qualified across declared channel combinations (cross-section MF=33, flux, composition; decay and yield where coverage exists), complete population accounting, per-channel coverage tables, prepared-run signature sharing, streaming record and digest resume. | Recorded P30/P31 dispositions. | Fixed-seed byte-reproducible campaign on the frozen population with zero unaccounted samples — covered, uncovered, failed, clamped and regularized draws all ledgered; independent local-vs-nonlinear control on the shared regime; unsupported channel combinations refuse `family_not_qualified`. |
| **P44 — measured band coverage** | Sealed scoring of declared bands against the experimental corpus: fraction of measured points inside each declared band per experiment, per family and pooled, for first-order (P11) and sampled (P43) bands. Development partition diagnostic; sealed partition scored once through unchanged code. | P43 (sampled leg); existing P11 machinery (first-order leg). | Coverage table re-derived by independent checker arithmetic; failed constructions, zero predictions and uncovered rows counted against coverage, never filtered; report states measured coverage per band type per family including where coverage is poor — which names the missing channel and is itself the deliverable. |
| **P45 — executed complete-campaign benchmark** | Frozen complete workloads (a material/history variant campaign with declared robustness; the distinct-spectrum mesh case or a scaled subset) executed end-to-end on the release candidate and on every accessible equivalent-output comparator with documented amortization enabled; cold/warm, preparation, solve, sampling, output and evidence time all reported; failed cases counted on both sides. | P43. | Output parity defined and checked per case before timing; checker re-derives every ratio from raw records; the published claim names exact workload, comparator versions, resource limits and host; no extrapolated or universal-competitor figure. |
| **P46 — evaluation intelligence** | A supported comparison surface (CLI, Python, desktop) presenting per-target and per-family scored agreement for every lawfully buildable evaluation corpus — tendl-2025-patched, tendl-2025 legacy, tendl-2017, EAF-2010, FENDL-3.2c — built under an identical pipeline and scored on the frozen measurement partitions; every recommendation carries its evidence row. | Machinery-independent; sequenced after P44 for shared scoring code. | Identical builder/scorer per corpus or the difference named; construction failures and exclusions counted per corpus; scored populations frozen before values are read; a corpus that cannot express a channel (e.g. EAF isomeric identity) is labeled, not silently scored low. |
| **P47 — dose-qualified spatial handoff** | Discharge P32's recorded conditions: execute the implemented EnergyFunctionFilter dose leg under the bounded scope, report photon-dose agreement with its Monte Carlo standard deviation, carry neutron-tally statistical error into the activation comparison band, and consume an external benchmark geometry if and only if one is lawfully available. | Independent; bounded job slots. | Frozen exported sources SHA-re-verified before replay; dose agreement reported with MC std-dev; tally sigma propagated into a stated band; the chain claim upgrades from "flux proxy" to "dose on the executed geometry" only if the dose leg passes its frozen band — otherwise the condition stays named. |
| **P48 — interactive exploration** | Desktop live re-solve on qualified paths: declared sweep axes (composition fraction, flux normalization, cooling time, optionally evaluation selection from P46) rendered with bands from the P43 path; per-interaction compute bounded and cancellable. | P43 (bands), P45 (measured latency envelope). | Interactive-path results identical to CLI runs on the same generated specs; interaction latency measured on the flagship workload with hardware recorded; a cancelled or superseded sweep can never render a stale result under new parameters (planted race regressions pass). |

**P43 — make the uncertainty campaign a product.** The ACT-ROBUST-01 family exists but is a verification
control; this phase ships it as the supported campaign path. Wire the sampling driver over the declared
robustness knobs with complete population accounting and the per-channel coverage table; reuse P31's
prepared-run signatures so perturbed solves amortize preparation; stream the sample record and resume by digest.
Discharge every locally-dischargeable P30 condition; conditions that remain (partial MF=33 coverage, no blind
experimental validation) are carried as named limitations, not silently closed. Rankings claim nominal,
distribution or bound status explicitly. No shipped band may be labeled total uncertainty without total coverage.

**P43 execution entry (2026-09-24).** P43 (qualified uncertainty campaign driver) closed `P43-CONDITIONAL`
— amendments were used and sealed at G0. Five channels qualified end-to-end: correlated MF=33 covariance
draws, flux, renormalized composition, decay constants and fission yields, all mean-preserving lognormal on
declared sigmas. Samples pair across cases through per-channel tagged substreams; per-sample ndjson records
resume on verified spec/output digests, torn samples re-execute, and a recorded `config_sha256` prevents
stale reuse under a changed sampling contract. The frozen 6-case x 64-sample campaign ran in 3.3 min against
the 45-minute envelope — made possible by two documented amendments: restricted study outputs (default
`outputs: null` ran pathway analysis at ~90 s/step on the 3873-state chain) and
`robustness.first_order_comparison` (the first-order tangent system scales with active chain rows — ~15k
parameters, 3.1 GB, >45 min on the fissile case — and is now opt-out, still demonstrated on the structural
cases). Decision rules carry paired survival: r1 nominally passes at 34% survival, r2 nominally fails at 47%.
10/10 frozen controls, all conformance probes, and the independent G4 checker passed with 5/5 planted-mutation
rejections (`results/verdict_p43.json`, `results/g{0,1,2,3,4}_p43*.json`,
`controls/g{0,1,2,3,4}_p43*.py`, `controls/check_g4_p43.py`,
`protocols/ACTINV-P43_PROTOCOL.md`). Carried named conditions: partial MF=33 coverage is ledgered per case
rather than completed, decay/yield coverage is declared-sigma only, and the local-vs-nonlinear diagnostic is
not evaluated on fissile cases pending a covered-row-restricted tangent solve.

**P44 — measure whether the bands are true.** The novel claim of this extension and the cheapest to get wrong.
Freeze the scoring code, the band definitions and a sealed partition of the measurement corpus before any band
is computed against it. Score the fraction of measured points inside each declared band; the development
partition may guide channel work but the sealed partition is scored once through unchanged code. Poor coverage
is a publishable result — it names the missing channel (decay data, yield covariance, model remainder) with
evidence. Under no circumstance is a band widened after seeing its coverage; widening is a new band definition
and a new sealed scoring. This phase produces the calibration claim; P24 produces the complementary predictive
re-validation of point estimates, and neither substitutes for the other.

**P44 execution entry (2026-09-24).** P44 (measured band coverage) closed `P44-CONDITIONAL` — amendments were
used and sealed at G0 and the corpus caveat applies as declared (FNS is consumed C/E evidence; the seal binds
band definitions, scoring code, partition and metrics, not blind data). The sealed partition — 122 of the 132
FNS experiments, 2179 measured decay-heat points — was scored exactly once through unchanged code in 50.3 min
against the 180-minute envelope, both legs complete with zero failed runs. Declared nuclear-data bands (MF=33 +
decay constants, 68.27% level) cover **26.5% first-order / 25.5% sampled** of sealed points band-only, 36.0% /
34.8% with measurement sigma folded in — materially under the declared level, and the decomposition names two
causes: (a) the covariance-bound patched library holds only 1679/2850 full-TENDL-2025 targets, so materials
whose natural isotopes are absent give zero or partial nominals (2.9% pooled coverage on the 26 affected
materials, five all-zero: Ag, Au, Nb, Ta, Ir); (b) on isotope-complete materials the declared bands still
under-cover (39.3% band-only pooled) — the missing channel is band width/remainder, not just library
completeness. Development partition (10 structural experiments) showed 62% — near nominal — so the pooled
deficit is corpus-composition-driven. G1 mechanics 10/10 experiments with complete per-point records; 8/8
frozen controls; all six conformance probes; independent G4 checker re-derived every outcome and aggregate,
verified all 122 band-record file hashes and the 396-file corpus manifest, and rejected 5/5 planted mutations
(`results/verdict_p44.json`, `results/g{0,1,2,3,4}_p44*.json`, `results/p44_sealed_coverage.json`,
`controls/{p44_bands,p44_band_coverage,g0,g1,g2,g3,g4,check_g4}_p44*.py`,
`protocols/ACTINV-P44_PROTOCOL.md`). Carried named conditions: coverage is measured against the
covariance-covered library subset, not the full activation library; band definitions cover MF=33 + decay
constants only (no flux/composition/yield channels, no model remainder); IRDFF-II SACS arm excluded — folded
cross-section scoring unit, no processed corpus on this host, parked.

**P45 execution entry (2026-09-24).** P45 (executed complete-campaign benchmark) closed `P45-CONDITIONAL` —
amendments were used and re-sealed (population regeneration + sealed-code fixes), and the identical-data leg
closed fully divergent under the declared FENDL conversion gap. The sealed campaign executed all 147 ledger
rows with zero failures in 12.8 min against the 240-minute envelope: 28-case campaign × {ALARA 2.9.2,
ACTINV-FENDL, ACTINV-TENDL} at 3 invocations plus a one-batch OpenMC 0.16 deplete leg; 16-cell
distinct-spectrum mesh through the real `actinv mesh` path plus per-cell ALARA and a 16-spectrum OpenMC batch;
and the 24-case × 16-sample robustness capability leg in 129.8 s with 0 failed samples (~0.34 s/case-sample —
the uncertainty product costs about a nominal solve each). Measured medians per invocation: ALARA 0.027 s,
ACTINV-FENDL 0.83 s, ACTINV-TENDL 1.21 s; OpenMC batch 273 s over 28 cases (~9.8 s/case incl. 11.3 s union
micro-XS amortization); mesh `actinv mesh` 16 cells in ~5.2 s (3.1 cells/s) vs OpenMC 152.6 s batch vs ALARA
0.019 s/cell. Identical-data parity (ALARA-vs-ACTINV on the converted FENDL-3.2c, |rel| ≤ 0.10): **28/28
parity_divergence** — all attributable to the declared conversion gap (12 targets failed ENDF parsing
outright; Fe56 lost its (n,p) channel), physics-coherent: short-lived products (Mn56/Mn57/Cr55/Fe53) diverge
at t=0 (rel 1.4–94) and converge at late cooling times; timing bias direction (toward ACTINV's sparser chain)
is declared. Data-mismatch leg is descriptive only: OpenMC/ENDF-B-VIII.1 vs ACTINV/TENDL-2025 median rel
−2.6% at t=0 rising to +33% at 3.15e8 s (evaluation spread on long-lived products). G1 mechanics 9/9 probes;
9/9 frozen controls; all conformance probes; independent G4 checker re-derived coverage, timing and tallies
from raw rows and rejected 3/3 planted mutations (`results/verdict_p45.json`, `results/p45_parity.json`,
`results/g{0,1,2,3,4}_p45*.json`, `results/check_g4_p45.json`,
`controls/{p45_*,g0,g1,g2,g3,g4,check_g4}_p45*.py`, `protocols/ACTINV-P45_PROTOCOL.md`). Carried named
conditions: identical-data parity is scored on a minimal 24-target converted subset — conversion repair is
parked, not silently patched; OpenMC leg carries the declared evaluation mismatch; FISPACT-II and
SCALE/ORIGEN stay unmeasured (no lawful executable); the robustness leg is a capability cost, never a speed
ratio.

**P45 — prove the campaign is affordable.** Replace the kernel-ratio headline with an executed whole-workload
claim. Freeze workloads, output parity, comparator versions, resource limits and host before timing. Give each
comparator its documented preparation and caches; where no lawful comparator exists the leg is unmeasured.
Report preparation amortization separately so the win (or loss) is attributable. The P26 lesson applies: an
undetermined target is a failure mode — every ambition here is an executed measurement, and a measured loss is
published as one.

**P46 — sell the evaluation choice.** Productize what the defect censuses already proved: ACTINV can say which
evaluation is best for a target on evidence. Build every lawful corpus through the identical pipeline, score on
the frozen partitions, and render per-target/per-family tables with a recommendation surface that carries its
evidence row. This converts the TENDL-2025 defect record from a liability disclosure into a selection capability
FISPACT's take-it-or-leave-it condensed libraries do not offer, and produces the decision evidence for whether
an EAF state-catalog phase or the corrected upstream TENDL release is worth a later phase.

**P46 execution entry (2026-09-24).** P46 (evaluation intelligence) closed `P46-CONDITIONAL` — the IRDFF-II
SACS arm was declared `unmeasured` at freeze (folded-XS scoring unit needing dedicated per-corpus machinery;
parked, not silently dropped) and corpus builder provenance is declared per artifact rather than one revision.
The FNS arm scored fully: all five admitted corpora × 132 experiments = 660 solves, zero failures, 10.3 min
against the 90-minute envelope under the identical spec→solve→C/E pipeline with decay data held fixed.
Measured pooled mean |ln C/E| over scored points: **eaf-2010 0.267** (2,360 pts), tendl-2025 0.280 (2,360),
tendl-2017 0.293 (2,360), fendl-3.2c 1.82 (609 — 24-target converted subset, 95/132 experiments `uncovered`),
**tendl-2025-patched 2.07** (2,201 — the shipped covariance working set's missing-isotope gaps, quantified:
it is the only corpus losing entire experiments' denominators). Per-material recommendations: tendl-2017 wins
30 materials, eaf-2010 22, tendl-2025 13, patched 9, fendl 1 — no single corpus dominates; per-target
selection is the only defensible answer (Ag→tendl-2017, Fe/SS316/Cu/Ta/Nb→eaf-2010, Au/Ba→tendl-2025,
W→tendl-2017 as least-bad at |ln| 0.41 — all corpora diverge on W). Every recommendation carries corpus sha,
scorer sha, ledger sha, n_scored and median C/E; construction refuses on missing evidence. G1 7/7, G2 7/7,
G3 conformance all pass, G4 independent checker 10/10 with 4/4 planted mutations rejected
(`results/verdict_p46.json`, `results/p46_eval_tables.json`, `results/g{0,1,2,3,4}_p46*.json`,
`results/check_g4_p46.json`, `docs/P46_EVALUATION_RECOMMENDATIONS.md`,
`controls/{p46_*,g0,g1,g2,g3,g4,check_g4}_p46*.py`, `protocols/ACTINV-P46_PROTOCOL.md`). Carried named
conditions: IRDFF arm unmeasured (parked); per-artifact builder provenance (legacy indexes) rather than a
rebuilt uniform revision; the FENDL-3.2c conversion gap is a measured score, not a patched input.

**P47 — finish the R2S claim.** The executed chain is currently a flux proxy on a self-produced geometry.
Execute the implemented dose leg, propagate tally statistical error, and report dose agreement with its MC
uncertainty. An external benchmark geometry (SINBAD) upgrades the claim only if a licence arrives through the
principal's channels; the self-produced condition otherwise stays named. MCNP distributed-source emission
remains a documented placeholder until a lawful verification route exists.

**P47 execution entry (2026-09-24).** P47 (dose-qualified spatial handoff) closed `P47-CONDITIONAL` — no lawful
external benchmark geometry was available, so the self-produced condition stays named verbatim; MCNP emission
remains a placeholder. All 25 frozen P32 artifacts re-verified byte-identical at G0 (OpenMC 0.15.3 pinned).
Two real findings: (1) **P32 dose normalization defect corrected** — verified empirically (void, void+volume,
material+volume cells) that OpenMC 0.15.3's EnergyFunctionFilter flux tally returns the f-weighted track-length
integral `Σ strength·f(E)·L` with NO cell-volume division, so `p32_dose.json`'s values overstated dose by the
detector volume (4124.79 cm³, ~4125×); the corrected transported dose is **2.24e-3 Gy/h** (step 2, MC rel-std
0.18%) and **5.86e-5 Gy/h** (step 3, 0.51%). (2) The contact-proxy ratio is **unmeasurable** under the frozen
P32 spec — no photon response was declared so the proxy is null per nuclide; reported as such, no dose claim
made from the proxy. G2 analytic control: 1 MeV point source in void reproduces the exact uncollided
track-length tally to **rel_dev 3.9e-13** (the energy-function + transport machinery is exact); linearity
fixture verified σ-scaling of written perturbations (2.04×, band [1.7,2.3]) and response monotonicity.
G3 issued the propagated-band comparison: all top-50 (cell,nuclide) ACTINV-vs-deplete deviations (median
2.1e-7) sit inside the propagated tally band (2·~2%). G4 checker re-derived the dose table from sealed
statepoints, verified band membership from raw records, and rejected 4/4 planted mutations including a real
h5py-injected tally corruption (`results/verdict_p47.json`, `results/g{0,1,2,3,4}_p47*.json`,
`results/check_g4_p47.json`, `controls/{p47_*,g0,g1,g2,g3,g4,check_g4}_p47*.py`,
`protocols/ACTINV-P47_PROTOCOL.md`).



**P48 — make speed visible.** Deliver interactive exploration in the desktop through the identical qualified
solver path — never a second numerics implementation. Declared sweep axes only; per-interaction compute bounded
and cancellable; a displayed result is always bound to the parameters that produced it. Regression coverage for
cancellation races and stale results is required by the local safety rules on process/worker launching. Latency
is a measured claim on the flagship workload, not an aspiration.

**P48 execution entry (2026-09-24).** Verdict `P48-CONDITIONAL`. The desktop gains a parameter sweep panel
(`crates/actinv-gui/src/sweep.rs` + `sweep_panel` in `app.rs`): three declared axes — composition fraction
(wt%, rest renormalized), flux normalization (× `spectrum.total`), cooling-step duration (s) — each point a
complete generated spec solved through `worker::spawn`, the identical isolated-worker protocol as a manual
Run. Bounded: `MAX_SWEEP_POINTS=32`, one sweep at a time, supersession cancels the running sweep and a new
generation makes its late results inadmissible; every rendered point carries its spec sha256. G1 measured the
identity claim on the sealed release binary — all 3 sweep points' worker results byte-equal the direct solver
on identical specs. G2: 5/5 controls — supersession staleness rejected, mid-sweep cancellation clean, artifact
freeze intact, 6/6 sweep unit regressions, stale-generation plant inadmissible. **G3 measured latency: median
4579 ms per interactive point** (n=3: 4866/4492/4579 ms) on the flagship FNS Fe 5-minute workload, release
binary, cgroup-bounded — the honest per-point number includes process spawn + library load + CRAM solve, no
amortization claimed. G4: 10/10 independent checks, 3 planted mutations rejected. Conditions named verbatim:
verification ran through the shipped binary's headless smoke mode (identical machinery, no on-screen
click-through); the library/evaluation-selection axis is declared-but-unshipped; debug builds are slower and
were not claimed. Evidence: `results/{g0_p48_seals,g1_p48_identity,g2_p48_controls,g3_p48_latency,
verdict_p48,check_g4_p48}.json`; protocol `protocols/ACTINV-P48_PROTOCOL.md` (sha `852f14fe…`); opening commit
`bb34acb`.

### Evidence and completion rules for this extension

1. The P26–P35 evidence, resource and completion rules (1–8 above) and the standing rules apply in full.
2. A calibration band is never tuned to the corpus it is scored against. Development and sealed partitions are
   distinct; scoring code freezes before sealed values are read.
3. Comparator fairness is a gate: documented competitor amortization is enabled, capability mismatches are
   reported as such, and unmeasured cells stay unmeasured.
4. Interactivity cannot create a second numerics path, and a rendered result is always bound to the inputs that
   produced it.
5. Every claim names its executed workload, comparator set, corpus identity and measured value. The deliverable
   is the honest number; "best in class" is claimed per named workflow or not at all.

Planning milestones are P43–P44 (uncertainty as a calibrated product), P45–P46 (affordability and evaluation
intelligence as measured claims), P47–P48 (the R2S claim completed and speed made visible). The largest
uncertainties are MF=33 coverage depth in P43, what the sealed coverage score actually is in P44 — a poor number
is a fork to channel work, not a failed phase — and whether any equivalent-output comparator is lawfully
executable for P45. P44's outcome should be treated as the extension's first real decision point: it determines
whether "calibrated uncertainty" ships as a claim or returns to channel-repair scope.

## Draft product-innovation extension — P49 (2026-09-24) — **closed P49-CONDITIONAL 2026-09-24**

P49 delivered `actinv optimize`: a bounded, seeded, derivative-free design
search (`actinv-optimize-1` documents; `lhs_coordinate` — seeded
Latin-hypercube fill plus coordinate-descent refinement, ≤64 evaluations).
Design axes: `composition_fraction` (joint renormalization of non-axed
elements), `flux_scale`, `step_dt`. Objectives and constraints evaluate on
nominal or propagated `normal`/`conservative` band edges — chance
constraints ("95th-percentile decay heat ≤ limit") are first-class.
Every attempted candidate is solved through the identical
`actinv_core::run::run` path and appended to `optimize_ledger.jsonl` with
its generated spec's sha256, parameter vector, objective, constraint
margins, feasibility and wall time; `--resume` replays the seeded sequence
and skips ledgered parameter digests. `optimize_result.json` ranks
feasible-first, flags `infeasible` boxes honestly, and re-executes the
winner in a fresh evaluation (bit-identical objective required).

Demonstration: reduced-activation steel box (Fe balance, Cr 9.0 wt%;
axes NI [0,3], MO [0,1], NB [0,0.2] wt%; FNS 5-min shot + cooling to
~100 y) — minimize `activity.total` nominal at ~100 y under
`heat.total` 95%-upper ≤ 1.65e-12 W/g at 1 y and `activity:Nb94` nominal
≤ 1e-6 Bq/g at ~100 y. 24 evaluations (12 feasible, 12 infeasible);
winner Ni=0/Mo=0/Nb=0.113 wt% at 2.75e-5 Bq/g — the refinement found that
removing Ni and Mo dominates the Nb-94 penalty of retained Nb. The
showcase case exists in the ledger: nominally feasible points rejected by
their 95% band edge. Total solve wall ≈ 3.3 h over two sessions (an
OOM repair mid-campaign exercised `--resume` end-to-end).

Verdict: **P49-CONDITIONAL** — one append-only mechanical amendment used
(Amendment 1: post-seal G3 OOM — candidate evaluation serialized each
RunResult to a serde_json::Value DOM, ~4 GB at this problem size; repaired
by reading edges from typed `StepOut`/`ResponseUncertainty`, identical
values). Independent checker re-derived ranking and feasibility from raw
ledger rows, verified all 24 candidate spec hashes, and rejected all three
planted mutations.

Conditions: (a) batch tool only — ~8–15 min per band-propagated evaluation
makes interactive optimization meaningless until the persistent-worker
path lands (parked item 10 becomes a prerequisite for any interactive
optimizer surface); (b) the optimizer reports the best ledgered candidate
under the seeded bounded search — it does not claim global optimality;
(c) band-edge constraints inherit the P43/P44 qualifications — bands are
first-order normal (plus conservative floor bound) on the covariance
subset, not full-quantile guarantees.

**Status: closed — verdict `P49-CONDITIONAL` recorded in `results/verdict_p49.json`.** The text below is the
pre-execution proposal, retained for provenance. This phase delivered ACTINV's first capability that no
competitor ships: optimization, not just evaluation. It changed no frozen protocol, prior verdict, evidence
record or release authorization. P33/P34 remain blocked on external
dependencies; the standing rules and the P26–P35 evidence rules applied in full.

Competitive landscape check (2026-09-24): FISPACT-II ships MC sensitivity and pathways but no optimizer;
SCALE/ORSEN is a prototype adjoint-DPT module, not a product; ACTYS-1-GO (2019) did composition optimization
without uncertainty; STAYSL-PNNL owns standards-lane spectrum unfolding; ISOTOPIA lists neutron production and
yield uncertainty as unimplemented future work; existing clearance classifiers (clearance-finder, F4E-radwaste)
are deterministic. **No shipping tool couples design optimization to nuclear-data uncertainty.**

### Intent and product outcome

The first three extensions proved ACTINV can *answer* accurately, quickly, broadly. This phase makes it
*decide*: an analyst states a design space and a chance-constrained objective, and gets back a ranked,
fully-evidenced candidate table — "what should I build", not "what does this do". The differentiator is that
objectives and constraints are evaluated on propagated nuclear-data bands, so a candidate can be rejected for
failing *probabilistically*, not just nominally — something no competitor can express.

### Scope boundaries

Within this phase: a bounded, seeded optimizer over declared design axes (composition wt% bounds; schedule
times; optionally evaluation corpus choice) evaluated through the unchanged qualified worker path; chance
constraints on propagated-band quantiles (e.g. 95th-percentile contact-dose proxy ≤ limit); a complete
evaluated-candidate ledger (no cherry-picking — every evaluation recorded, feasible or not); honest infeasibility
reporting.

Outside this phase: gradient/adjoint methods (the optimizer is derivative-free); multi-physics coupling
(no thermal feedback); value-of-information / measurement-priority ranking (separate candidate lane); spectrum
inference; isotope-production campaign design as a *productized workflow* (the optimizer primitives serve it but
the Tb-149-class workflow is not packaged here); and interactivity — the deliverable is a batch campaign
(`actinv optimize`), not a live GUI loop, since per-candidate band propagation at ~70 s rules out
interactive latency until the persistent-worker item ships.

### Phase sequence and acceptance gates

| Phase | Deliverable | Entry dependency | Decisive gate |
|---|---|---|---|
| **P49 — chance-constrained activation design optimization** | `actinv optimize` over a declared design box: a base spec + bounded axes (composition wt%, irradiation/cooling times, corpus) + a scalar objective (min or max a named response at a named time) + chance constraints (band quantile vs limit). Output is a ranked candidate ledger — every evaluated spec, its response, its band, its feasibility verdict, and spec sha; the best feasible candidate re-executed and re-verified as a fresh independent run. | P43 (bands), P48 (sweep machinery as the evaluation kernel), the full-corpus default (2026-09-24 flip). | Fixed-seed determinism (identical rerun = identical ledger); every candidate evaluated through the identical solver path — no surrogate, no reduced numerics; the optimizer never sees a value it could not re-derive from its own ledger; an infeasible-box control must report infeasible, never return the least-bad violator silently; a synthetic known-optimum control must recover the planted vertex; independent checker re-derives the ranking and the feasibility verdicts from the raw ledger; measured wall time reported with hardware; no candidate rendered without its bound spec. |

**Demonstration target (declared, not yet scoped to a spec):** minimize contact-dose proxy at a named
cooling time over a reduced-activation steel composition box — the canonical "design for decommissioning"
problem the fusion materials program runs by hand today. A second candidate demonstration, maximizing
Tb-149 radionuclidic purity against the shipped feedstock example, exercises the maximize direction and a
real published problem.

## Draft uncertainty-differentiation extension — P50–P52 (2026-09-25) — **P50 closed P50-CONDITIONAL 2026-09-25; P51 closed P51-CLOSED 2026-09-25; P52 closed P52-CLOSED 2026-09-25**

Competitive landscape check (2026-09-25): OpenMC now ships `openmc.deplete` with an automated
cell/mesh `R2SManager` plus D1S, validated against the FNG ITER SINBAD shutdown-dose benchmark —
real geometry and spatial dose maps ACTINV does not pursue. The maintainers state directly that
uncertainties are *not* propagated through depletion: R2S produces point estimates only, and no
shipped code couples nuclear-data covariance to the activation step, the decay photon source, or
the dose. Shipped SDR-uncertainty work exists only in research code (R2S-UNED propagates Monte-Carlo
*statistical* flux uncertainty; MS-CADIS is adjoint variance reduction) — a different uncertainty axis
than data covariance, and neither is a product. FISPACT-II 5.x retains pathways UQ and MC sensitivity
on full covariance but no decision layer. P49 already made ACTINV the only shipping code that couples
design decisions to propagated bands. This extension deepens that moat: **uncertainty is the
differentiator, and ACTINV is positioned as the missing layer of other pipelines rather than a
competitor for transport geometry.**

### Intent and product outcome

P50–P52 extend "evaluate → decide" into "decide what to measure" and "decide inside other people's
pipelines". The analyst who asks OpenMC-or-FISPACT for a shutdown dose gets a number; with ACTINV in
the loop they get the band, the dominant uncertain reactions, and the measurement that would shrink it.
Capability breadth on the three axes that matter (accuracy, speed, breadth) — provenance remains
internal QA.

**P50 — value-of-information / measurement-priority ranking.** Given a spec with declared uncertainty,
a named response and a decision limit, rank the contributing nuclear-data uncertainties — reaction,
decay constant, yield — by how much each buys down the response band. Deliverable is a ranked,
fully-evidenced measurement-priority table: per-channel sensitivity times propagated variance, the
share of total band width each channel carries, and the marginal reduction implied by a declared
measurement improvement. This is the lane the P49 scope explicitly deferred; the P43 band machinery,
P11 sensitivity paths and the qualified worker carry it. Nobody ships measurement-priority ranking as
a product; the output is also independently publishable. Demonstration target (declared): rank
uncertainty contributors to the P49 RA-steel constraint responses — the Nb-94 clearance arm and the
heat-band edge — naming the two or three measurements that would most tighten the feasible region.

**P50 closed `P50-CONDITIONAL` 2026-09-25** (`results/verdict_p50.json`, protocol
`protocols/ACTINV-P50_PROTOCOL.md` at five append-only mechanical amendments — all in the checking
layer; the emit path needed none). Delivered: `uncertainty.voi {top}` on the existing spec block —
each requested response band now carries a ranked `voi` table (per-parameter `variance_share`,
`share_fraction`, `sensitivity`), MF=33 shares are the exact row-wise decomposition
`s_i·(Σs)_i` of the propagated variance (negative shares under anticorrelation preserved; Σshare_i ≡ V),
decay/yield channels contribute `(s·σ)²`, and sensitivity-bearing parameters outside covariance
coverage are summarized under `unranked` — never silently ranked at zero. Flag absent: byte-identical
results. G3 executed the P49 winner spec on the full corpus (222 s, 52 tables): irradiation-step heat
is carried by Cr-52(n,p) (92%), the 1-y heat band and the Nb-94 arms by Nb-93(n,γ) (~97%). G4
re-derived every emitted share in Python — per-target block-diagonal collapse, locally re-diagnosed
exclusions — and rejected the planted mutations (reordered ranking, zeroed share, dropped unranked
family). Conditions: shares are within-band accounting under the P43/P44 qualifications, not posterior
model-error probabilities; the draft's marginal band-reduction column under declared improvements was
narrowed to share ranking by the frozen protocol (a natural follow-on); `unranked` names the coverage
boundary, it does not quantify it.

**P51 — amortized latency / persistent worker.** The extension's speed gate and the prerequisite for
voxel-scale work. Deliverable: a bounded persistent worker that amortizes library load and
fixed-cost setup across evaluations, so sweeps, optimization and per-cell activation run at warm cost.
Every claim is measured: cold vs warm wall time ledgered per entry point, hot-path results bit-identical
to the qualified cold path (same worker semantics, no reduced numerics), memory ceiling enforced and
recorded. Retires parked items 10 and 12's root cause; it does not change solver semantics, and the
gate requires it provably not to.

**P51 closed `P51-CLOSED` 2026-09-25** (`results/verdict_p51.json`, protocol
`protocols/ACTINV-P51_PROTOCOL.md`, zero amendments). Delivered: `PreparedCache` in `actinv-core` —
a single-slot `PreparedRun` holder keyed by a fingerprint of every input that determines it
(spec-side references and options plus the resolved sha256 of each referenced file) — and
`actinv worker`, a bounded NDJSON subprocess serving `actinv-worker-request-1` lines sequentially.
`run()` delegates to `run_with_cache` with a one-shot cache, so cold and warm execute literally the
same preparation code on a miss and the same solve code always; a fingerprint re-hashes every input
file per request, so no qualification step is skipped and a changed file is a miss. G2: worker
results byte-identical to fresh `actinv run` on the three-member battery (synthetic nominal
Groupwise, synthetic uncertainty Dense, full-corpus probe) modulo the declared `ms`/`entry_point`
fields. G3: full-corpus solve 4.69 s cold median vs 1.74 s warm client wall (0.37x; ~3 s of
data-file load amortized per request) — ledgered with hardware. G4: 20-request alternating-corpora
battery thrashed the slot honestly, kill-mid-solve → reaped → respawned → re-issued solve
byte-identical, RSS peak 1.56 GiB under the 4 GiB ceiling across 1134 samples; no zombie or orphan.
G5 independently re-diffed the saved documents, recomputed the timing arithmetic and the event
order, and rejected all three planted mutations. Conditions: warm path skips data-file *reload*
only; single-slot thrash on corpus alternation is honest and measured; cancellation is by process
termination with demonstrated restart.

**P52 — OpenMC-ingest, uncertainty-bearing R2S handoff.** Execute the parked spatial-handoff item
(3): ingest OpenMC `get_microxs_and_flux()` exports — multigroup fluxes and microscopic cross sections
per cell or mesh element — run the identical activation chain per spatial bin *with propagated bands*,
and emit per-bin decay photon sources with uncertainty. The photon-transport step stays external
(OpenMC's own step or an equivalent); ACTINV supplies the activation+UQ layer OpenMC's deplete does
not have. Result: the only shipping workflow producing shutdown-dose-relevant quantities with honest
nuclear-data bands — and it lands *inside* the incumbent's pipeline, which is the adoption path.
Gates must include one-cell parity against OpenMC's own activation step on identical data, a small
executed mesh demonstration with bands ledgered per bin, and a determinism/resume contract identical in
kind to P49's. Candidate demonstration (declared): a published SDR benchmark geometry at reduced
mesh resolution, reporting dose-relevant bands and the dominant uncertainty-bearing nuclides.

**Out of scope for this extension:** neutron or photon transport of any kind inside ACTINV
(the transport engines stay external), spectrum unfolding (STAYSL-PNNL owns the standards lane),
gradient/adjoint optimization methods (P50/P52 may motivate it; it enters a protocol only through a
future phase), probabilistic clearance classification and inverse irradiation-history estimation
(parked lanes; logged in PARKING.md with dated lines).

### Phase sequence and acceptance gates

| Phase | Deliverable | Entry dependency | Decisive gate |
|---|---|---|---|
| **P50 — measurement-priority (VoI) ranking** | `actinv` subcommand or study mode producing a ranked channel table for a named response at a named time: per-channel variance share of the propagated band, sensitivity column checkable against the P11 machinery, and marginal band reduction under declared measurement improvements. | P43 (bands), P11 (sensitivity), qualified worker path. | Fixed-seed determinism; ranks re-derived independently by a checker from raw sensitivity+covariance records; variance shares sum consistently with the propagated band (no double counting across correlated channels — correlations ledgered or excluded by declaration); a planted-dominant-channel control must rank it first; missing-covariance channels appear honestly as unranked, never zero. |
| **P51 — persistent worker / amortized latency** | A bounded long-lived worker serving identical-path evaluations: library load amortized, per-call setup eliminated, results bit-identical to the cold path on a pinned corpus. | Qualified worker path; P48 sweep machinery as the first consumer. | Hot/cold bit-identity on a representative battery; measured cold vs warm wall time ledgered with hardware; memory ceiling enforced and demonstrated under sustained load; cancellation and crash-restart semantics verified; no path may answer faster by skipping qualification steps. |
| **P52 — OpenMC-ingest UQ'd R2S handoff** | `actinv` intake of OpenMC microXS+flux exports → per-bin activation with bands → per-bin decay photon sources with bands, emitted in a documented interchange format for the external photon step. | P51 (per-bin band cost), P43, parked item 3. | One-cell parity vs `openmc.deplete` on identical data within declared tolerance; executed multi-bin demonstration with per-bin ledgers; determinism + resume like P49; an independent checker re-derives band and source tables from raw records; bands flagged honestly where covariance coverage is partial, never silently nominal. |

Standing rules 1–7 apply unchanged. Each phase opens with its own hashed protocol naming its minimum
gate input; nothing in this draft is frozen until sealed.

## Strategic positioning assessment — 2026-09-25 (draft, unhashed)

**Competitive reality.** OpenMC owns the transport-coupled lane: continuous-energy MC, geometry,
and a shipped FNG-validated `R2SManager` doing cell/mesh depletion → decay photon source → photon
transport in-tree. It is expanding into activation breadth and will keep doing so. Two things it
cannot reach without a multi-year re-architecture: a nuclear-data covariance channel (no MF33
ingestion, no collapse operator, no sensitivity machinery in `deplete` — the only UQ work in their
ecosystem is a PHYSOR-2026 preprint propagating *stochastic* tally noise via adjoint PT, the
orthogonal axis), and many-query workloads (MC-coupled depletion is hours/eval with tally noise —
gradients and probability integrals over its outputs are not well-defined). FISPACT-II is the nearer
UQ rival (real MF33/40 propagation: pathways, depletion, MC sampling); our edge there is
deterministic propagation speed, the decision layer, and the data-quality ledger (the patched-TENDL
corpus and its defect eviction are an *accuracy* advantage no incumbent ships).

**The category reframe.** ACTINV does not win by out-featuring OpenMC on transport — it wins by
owning the adjacent category: **activation decisions under uncertainty on prescribed spectra**.
OpenMC computes activation; ACTINV computes *decisions about* activation — what to design, what to
measure, what clears regulatory limits, and how confident each answer is. That category requires
three properties an MC transport code cannot host by construction: (1) nuclear-data covariance
propagation end-to-end, (2) deterministic, bit-reproducible solves cheap enough for 10³–10⁶-query
workloads, (3) confidence-bearing outputs (bands → probability statements, not point estimates).
For the large class of problems where the spectrum is already known (irradiation facilities, fusion
first walls with precomputed flux maps, isotope production, decommissioning assay, clearance),
transport is not the bottleneck at all — and there ACTINV should be the obvious choice outright.

**The embeddability play.** The strongest adoption path is not replacement but indispensability:
ACTINV as the activation+UQ layer *inside* incumbent pipelines (P52's interchange already feeds
banded sources into an external photon step — OpenMC's own included). A tool that every transport
code calls wins differently than a rival every transport code routes around.

**Deprecation caveat.** "Can't do" for an open-source incumbent means "can't do inside ~2–3 years of
re-architecture plus a data program." The moat widens only while we build on it.

**Candidate next lanes (P53, P54, P55, P57 approved for the next pass 2026-09-25; P56 deferred —
bands inside the optimizer loop are the heaviest compute on the board; each opens with its own
hashed protocol, one lane at a time, in the order listed):**

| Lane | Structural claim | Builds on |
|---|---|---|
| **P53 — correlated spatial dose bands** | Cells share nuclide covariances → per-bin bands are correlated; a system-level SDR band needs the joint structure. Nobody ships this; only ACTINV has the machinery. | P52 |
| **P54 — certified probabilistic clearance** | `P(component clears free-release) ≥ x` with quantified misclassification risk. Needs bands + many queries; deterministic clearance tools can't emit confidence. Money-adjacent (decommissioning). | P43, P51, parked 13 |
| **P55 — inverse irradiation-history estimation** | Measured assay → inferred exposure history/composition. Needs thousands of solves; MC can't host it. Forensics/decommissioning lane. | P49, P51, parked 14 |
| **P56 — optimization under uncertainty** | The P49 optimizer with banded constraints: "design that certifies dose < X at 95% over the covariance set." Requires speed + bands + optimizer together — no incumbent has all three. | P49, P43 |
| **P57 — activation oracle / platform hardening** | Stable interchange surface + adapters (OpenMC, MCNP-format inputs, SERPENT) so ACTINV is the layer other codes call. | P51, P52 |

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

- 2026-09-12 — P21 closes with `P21-PASS` (verdict `results/verdict_p21.json`, independent closure
  `results/p21_closure_check.json`) under frozen protocol `871c9650`. G0 re-ran the four-surface identity battery
  (CLI cold/warm, Python extension, one-cell mesh: unchanged normalized hashes) and recorded the baseline mesh
  profile on the pinned TENDL-2025 709-group library. G1 adds signature-keyed workload grouping (SHA-256 of the
  rebinned f64 flux bytes; memo bounded by 256 entries and 512 MiB), `cell_result_fields` selection, and the
  post-hoc `memory_limit_bytes` guard: grouped and ungrouped runs emit bit-identical cell records with the header
  differing only in `spec_fingerprint_sha256`, reuse recounts exactly from the recorded signatures, and the guard
  fails loudly carrying observed and configured bytes (`controls/g1_p21_scaling.py`, checker
  `controls/check_g1_p21.py`, 7/7 mutations rejected). G2 delivers checkpoint/resume: the output file is its own
  checkpoint — a validated header fingerprint plus complete in-order cell records stand, a torn tail is truncated,
  only unfinished cells re-solve, and the result is byte-identical to an uninterrupted run modulo footer timing
  (`controls/g2_p21_resume.py`, checker `controls/check_g2_p21.py`, 7/7 mutations rejected). G3 executed a real
  20,000-cell case on the pinned library: 2,943 s wall at 6.80 cells/s, 402 MB peak RSS, 219 MB output; peak RSS
  stayed flat within 0.27 MB across 1,000/5,000/20,000 cells while the chunk 16→256 leg moved peak RSS by 1.59 GB,
  and the repeated-spectrum grouping leg served 975/1,000 cells from reuse at 197.8 cells/s vs 6.9 ungrouped
  (`controls/g3_p21_executed.py`, checker `controls/check_g3_p21.py`, 7/7 mutations rejected). G4 re-proved
  absent-option identity on the post-change binaries across all four surfaces and superseded the CB1 million-cell
  extrapolation with the executed record in `docs/COMPETITIVE_BENCHMARK.md` (checker `controls/check_g4_p21.py`).
  The closure checker `controls/check_p21.py` re-runs all five gate checkers, recounts reuse from signatures,
  re-verifies resumed-vs-reference line digests, recomputes both memory gates, re-asserts all 24 prior verdicts
  including P18b-FAIL, and rejects 5/5 evidence mutations. Scope is honest: no distributed or cluster execution, no
  million-cell claim (not executed), checkpoints resume the same spec only (not an interchange format), and the
  memory guard is post-hoc — it cannot pre-empt a single oversized allocation.

- 2026-09-13 — P22 re-scores the post-improvement candidate under frozen protocol `86f8509f` and
  Amendment A (the frozen G4 premise "the version string is absent from normalized results" was
  falsified on observation — `certificate.solver` is the documented solver-semver leaf, so the gate
  became strictly stronger: the hash-pinned pre-bump 1.0.1 artifact must reproduce the G0 baseline
  exactly AND the rebuilt 1.1.0 artifacts must match it under solver-semver normalization). G0 sealed
  25 prior verdicts and 9 CB1 digests and bound the P21 executed 20,000-cell evidence to the
  candidate. G1 re-ran the frozen CB1 battery against the candidate: worst-vs-SciPy 4.0960e-15,
  identical-input ALARA agreement exact on collapsed rate/timeline and <=4.12e-8 on shutdown
  inventory, FNS pooled geometric-mean C/E 1.0313 with 59/132 experiments wholly within 30% — every
  sealed metric reproduced, integer counts exact (`controls/g1_p22_battery.py`, checker
  `controls/check_g1_p22.py`, 6/6 mutations rejected). G2 re-exercised the released 1.0.0 binary for
  the frozen performance path (startup 1.29 ms, example 2.27 s at 1.09 GB, kernel ratios 165x/19x/
  4.2x/2.6x at 2/32/256/1024 states), re-verified `pip install actinv==1.0.0` first-use end-to-end
  (~8.2 s to first result; ALARA 2.9.2 source to passing sample ~37 s), built a clean clone of HEAD,
  and re-ran a fresh 1,000-cell mesh at 8.7 cells/s with 402.1 MB peak RSS inside the P21 bound
  (`controls/g2_p22_exercises.py`, checker 7/7 mutations). G3 re-scored the sealed P17 held-out
  partition (94 rows) once through unchanged scoring code — all family metrics reproduce within
  1e-12 and `P17-FAIL` stands, as does `P18b-FAIL` (`controls/g3_p22_heldout.py`, checker 6/6
  mutations). G4 updated `docs/COMPETITIVE_BENCHMARK.md` (P22 candidate re-score section, scoped
  P19/P21/P23 capability cells, every remaining loss preserved), bumped the workspace to 1.1.0,
  rebuilt the CLI binary/Python extension/wheel, and proved the bump solver-inert under the amended
  gate (`controls/g4_p22_release.py`, checker 7/7 mutations); `release_ready` is recorded but
  tagging, GitHub release and PyPI upload remain separate maintainer actions. The closure checker
  `controls/check_p22.py` re-runs every gate checker, re-derives the CB1 comparisons and held-out
  metrics, recomputes the release decision, re-asserts all 25 prior verdicts, verifies the manifest,
  and rejects planted mutations.

- 2026-09-13 — P22 closes with `P22-PASS` (verdict `results/verdict_p22.json`, closure
  `results/p22_closure_check.json`) under frozen protocol `86f8509f` plus Amendment A (the recorded
  correction to the G4 identity gate: `certificate.solver` is the documented solver-semver leaf, so the
  gate is two-stage — pre-bump artifact reproduces the baseline exactly, rebuilt artifacts match under
  solver-semver normalization). The maintainer directed that the 1.1.0 release **remain untagged and
  unpublished** pending repair of the two standing failures: P24 re-validates the corrected benchmark
  definitions P17's amendment falsified, and P25 repairs the isomeric coverage gap P18b's stratum gate
  exposed. The P18/P18b closure checkers' release boundary is scoped accordingly: the workspace may
  carry the 1.1.0 candidate version only while the green P22 RC record exists and no `v1.1*` tag does.
  Independent read-only review of the P18b evidence then established that the candidate's headline
  improvement is a coverage-selection artifact — the 356 paired rows span only 13 families with maximum
  |Δln(C/M)| = 5.7e-7 — that 29 scored `181Ta(α,n)184Re` rows with zero candidate predictions are
  silently filtered from metrics by the `isfinite` gate, and that the builder and scorer both apply the
  neutron-file `inelastic(mt)` same-residual treatment unconditionally for charged-particle files.
  P25 was rewritten accordingly: it is a new protocol (not a P18b amendment, which only authorizes
  otherwise-passing closures) whose first milestone is a bounded cause diagnosis, with acceptance gates
  that count coverage, zero predictions and construction failures explicitly.

- 2026-09-13 — at the maintainer's request, draft the next-evolution extension P26–P35: validate complete user
  investigations and competitive headroom; deliver shared studies and predefined Avila Core contract families,
  qualified physics combinations, response-specific numerical control, practical uncertainty and explanation,
  efficient campaigns, spatial handoffs and portable evidence; then qualify optional AI setup/interpretation and
  bounded execution through Core before an independent product decision. The extension is unopened/unhashed;
  P25/P24 obligations and the current release hold retain their existing authority. The advisory E0–E5 proposal
  is superseded as a phase plan, with its research retained. No scientific evidence or prior verdict is changed.

- 2026-09-13 — the maintainer fixes AI delivery to customer-supplied provider API credentials, with direct
  ACTINV-to-provider requests and provider-to-customer billing. Avila hosts no AI backend, proxy, model or
  account/billing service. P33 now explicitly delivers credential setup, usage controls and provider-failure
  handling; P33–P35 inherit the no-hosting constraint. Local language-model inference is optional, and ordinary
  ACTINV/Core calculation and verification remain local and independent of provider availability.

- 2026-09-14 — **P25 closes P25-FAIL** (`results/verdict_p25.json`, independent G6 checker
  `controls/check_g6_p25.py`). The phase executed its full G0–G6 protocol: a 397-file quarantine census
  (`results/g1_p25_census.json`), decimal-oracle mechanism classification (`results/g2_p25_traces.json`),
  a diagnosis with coverage floors frozen before repaired scoring (Amendment B), and bounded repairs that
  legitimately recovered 287 of 397 quarantined files (neutron 40, proton 129, deuteron 73, alpha 45) —
  including the projectile-aware fix for the charged-particle MT=4 same-residual defect behind the
  `181Ta(α,n)184Re` wrong-nuclide rows. G5 then failed honestly under the frozen gates: all four
  Amendment-B coverage floors miss (n 162/188, p 358/417, d 70/72, a 288/304 status-scored rows) and
  stratum nonregression fails, while outcome accounting, the no-empty-stratum rule and byte-exact
  historical reproducibility pass. The failure is structural, not a repair miss: the ~110 still-quarantined
  files are genuine TENDL-2025 source inconsistencies that correctly fail closed, all 485 candidate
  `zero_denominator` rows are also zero on the baseline library, and the composition-controlled paired
  bootstrap shows ~zero per-row change (median Δ ≈ 2e-8) — the proton aggregate breach is population
  composition, not physics. The census produced a hash-pinned per-file defect catalog of TENDL-2025
  (`docs/P25_TENDL2025_DEFECT_REPORT.md`, qualified by absolute magnitude in `docs/DATA_TRAPS.md` traps
  10–15). P17-FAIL, P18-FAIL and P18b-FAIL remain visible and unamended; the v1.1.0 release hold stands;
  P24's corrected-definition re-validation retains its order and is the next scheduled phase.

- 2026-09-14 — **P25b closes P25-FAIL analog `P25b-FAIL`** (`results/verdict_p25b.json`,
  `results/g5_p25b_accounting.json`, independent checker `results/g5_p25b_check.json` green,
  6/6 planted mutations rejected, gate ordering verified by git ancestry). Three hash-pinned
  alternate neutron evaluations were qualified against the frozen machinery: TENDL-2023,
  FENDL-3.2c and EAF-2010. Under the v1.0.1 builder all three met their Amendment-1 coverage
  floors (47/44/35 IRDFF isotopic targets); under the 1.1.0 release-candidate builder — whose
  added state-catalog and emitted-state validation is the stricter, release-relevant
  instrument — all three fall below floor (37/34/31) and no candidate survives nonregression:
  TENDL-2023 passes the IRDFF partition on 21 comparable rows but fails the isomeric
  partition (median |ln C/E| 0.250 vs baseline 0.162 over 253 paired rows); EAF-2010 and
  FENDL-3.2c fail IRDFF nonregression and cannot express isomeric identity at all (the EAF
  format path emits no `state_catalog`; FENDL ships none of the 32 required product-isomer
  anchor files). Decisive findings: TENDL-2023 carries the same TALYS emitted-sum>total
  defect class as TENDL-2025 (upstream-confirmed, fixed in the next TENDL release);
  EAF-2010 carries negative MF=8 ELFS excitation energies the 1.1.0 builder correctly
  rejects. ACTINV 1.1.0 stays data-blocked; the recorded paths are the corrected upstream
  TENDL release, or a new phase for EAF state-catalog emission under explicit isomeric
  limitations. All scoring was retrospective; no blind evidence exists or was claimed.

- 2026-09-14 — **P25c closes `P25c-PASS`** (`results/verdict_p25c.json`,
  `results/g5_p25c_accounting.json`, independent checker `results/g5_p25c_check.json`
  green, 7/7 planted mutations rejected, gate ordering verified by git ancestry). The
  phase produced `tendl-2025-patched`, a clearly labeled derived corpus that mechanically
  zeroes the 44 enumerated leaked leading ordinates across the 28 confirmed-signature
  files while copying all 2,822 other files byte-for-byte; G2 replay verified surgical
  integrity. The post-patch census shows zero residual leak-signature hits and recovered
  three union targets (n-Sc045, n-Y088, n-Br080m) with zero regressions; coverage moved
  IRDFF 29→30, union 43→45, anchors 41→42. The union+anchor artifact builds clean (87
  targets, `state_catalog` liso {0,1}) and passes all frozen nonregression gates on both
  partitions (IRDFF 17 comparable rows; isomeric 162 paired of 469 eligible neutron
  rows). The honest boundary is equally clear: the five dosimetry-critical targets
  (Ni-58, Nb-93, Ag-109, In-113, Au-197) remain unrecovered — their blockers are
  non-signature defect classes, not the confirmed leak — and 46 non-signature
  conservation-excess files plus 7 self-channel-only files stay ledgered and unpatched.
  The verdict qualifies the derived corpus as a *candidate* for a separately labeled
  data release; publishing it as a `data-v1.1.x` artifact remains the maintainer's
  decision and must carry the defect scope verbatim. All scoring was retrospective; no
  blind evidence exists or was claimed.

- 2026-09-23 — at the maintainer's direction, draft the innovation extension P43–P48: convert the P26–P35
  machinery into product-level claims on the owner's axes (accuracy, speed, capability breadth). Proposed scope:
  a qualified uncertainty-campaign driver (P43), measured band coverage against the sealed experimental corpus
  (P44), an executed complete-campaign comparator benchmark (P45), evaluation intelligence (P46), dose-qualified
  spatial handoff completion (P47) and interactive desktop exploration (P48). The draft opens no phase and
  changes no frozen protocol or verdict; P24's schedule, the P17/P18/P18b/P25 failure record and the P33/P34
  blockers are unchanged. Gamma/triton/helion projectiles, probability-table shielding, internal transport, MPI
  and the AI legs remain demand-led and unopened. JADE/NEA/SINBAD routes stay external maintainer acts that
  phases may consume but cannot gate on.
- 2026-09-25 — at the maintainer's direction, draft the uncertainty-differentiation extension P50–P52:
  measurement-priority (value-of-information) ranking (P50), amortized latency through a persistent
  worker (P51), and OpenMC-ingest uncertainty-bearing R2S handoff (P52, retiring parked item 3).
  Motivation: OpenMC ships automated R2S/D1S (FNG-validated, real geometry) but propagates no
  nuclear-data uncertainty through depletion; ACTINV's bands→decisions path is the open moat and the
  complementary layer inside incumbent pipelines. Clearance classification and inverse exposure-history
  lanes are logged in PARKING.md items 13–14. The draft opens no phase and changes no frozen protocol
  or verdict.
- 2026-09-25 — **P50 closes `P50-CONDITIONAL`** (`results/verdict_p50.json`; protocol at five
  append-only mechanical amendments, all in the checking layer — the emit path needed none).
  `uncertainty.voi {top}` emits per-band ranked variance-share tables: MF=33 shares are the exact
  row-wise decomposition s_i·(Σs)_i (anticorrelated channels can carry negative shares; shares sum to
  the propagated variance), decay/yield contribute (s·σ)², uncovered sensitivity-bearing parameters
  are summarized under `unranked`, and flag-absent output stays byte-identical. Demonstration on the
  P49 RA-steel winner: irradiation heat carried by Cr-52(n,p) (92% share), the 1-y heat band and
  Nb-94 arms by Nb-93(n,γ) (~97%). The G4 checker re-derived every share independently from the pinned
  sidecar and rejected all planted mutations. Conditions and the P51/P52 opening order stand in the
  extension text; P51 is next and remains unopened.
- 2026-09-25 — **P51 closes `P51-CLOSED`** (`results/verdict_p51.json`; protocol sealed at
  `9065e48a…`, zero amendments). `PreparedCache` (single slot, fingerprint = spec inputs + resolved
  content sha256 of every referenced file — re-verified per request) plus `actinv worker`, a bounded
  NDJSON request/response subprocess. `run()` delegates to `run_with_cache`, so the cold and hot
  paths are the same code; a warm request pays only the ~ms fingerprint. Evidence: bit-identity vs
  `actinv run` on synthetic nominal/Dense/corpus members modulo `ms`/`entry_point`; full-corpus
  4.69 s cold vs 1.74 s warm (0.37x); 20-request alternating-corpora thrash, kill-mid-solve →
  reaped → respawned → byte-identical, RSS 1.56 GiB under a 4 GiB ceiling; G5 re-verified all
  ledgers independently and rejected three planted mutations. The qualified path — not a shortcut —
  is what got faster.
- 2026-09-25 — **P52 closes `P52-CLOSED`** (`results/verdict_p52.json`; protocol sealed at
  `7d12f615…`, 10 amendments — all mechanical/control-layer repairs plus two format-methodology
  tightenings discovered by executing the gates). `actinv export-r2s` emits the
  `actinv-r2s-source-1` interchange: per-cell banded decay-photon sources — per-nuclide photons/s
  with σ from each `activity:` band (`σ_i = source_i × rel_σ_i`), cell totals under both
  declared rules (independent sqrt-Σσ², conservative Σσ over banded contributions), and a coverage
  ledger (`partially_unbanded`, `unbanded_photon_share`, uncovered rows/decays/yields) so no band
  is silently nominal. Executed evidence: one-cell parity vs `openmc.deplete` on identical
  Fe/FNS input — Mn-56 (n,p) at rel 0.0009, Fe-55 (n,γ) at rel 0.25 (known TENDL↔ENDF evaluation
  divergence, inside the declared 0.5); 8-cell banded mesh at ~15 s/cell inside the 6 GB envelope;
  byte-identical reruns modulo the declared wall-clock footer fields; mid-output resume converges
  identically; G5 re-derived every cell's per-nuclide σ, both combination rules, coverage
  fractions, and footer aggregates from the raw ndjson and rejected all three planted mutations.
  The capability claim: uncertainty now propagates through the R2S handoff into the photon
  transport source — the lane no incumbent offers.
- 2026-09-25 — repository hygiene: `results/p50_voi_result.json` (408 MB, over GitHub's 100 MB push
  limit) was excised from unpushed local history (`c91d0af`→`0af91c6`, `8753255`→`1c7fb68`,
  `ebb7810`→`b6f9afa`, `fbfaaeb`→`5b562ff`); the file remains on disk, .gitignored, and its sealed
  sha256 in `results/verdict_p50.json` (`7a1c548c…`) is unaffected — it regenerates deterministically
  via `controls/g3_p50_demonstration.py`. Seal `opening_commit` fields predate the excision and now
  point to superseded local SHAs; artifact sha256s they bind are unchanged.
