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

### Phase sequence and acceptance gates