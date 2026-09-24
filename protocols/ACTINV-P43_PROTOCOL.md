# ACTINV-P43 protocol — qualified uncertainty campaign driver

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P43 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** P30-CONDITIONAL
(`results/verdict_p30.json`), P31-CONDITIONAL
(`results/verdict_p31.json`)

Frozen population sizes, seed values and the campaign resource envelope
are placeholders to be fixed at the freeze; this document authorizes no
execution.

## Intent

P30 qualified the ACT-ROBUST-01 machinery on a two-case smoke
population; P31 qualified prepared-run amortization and case-granular
resume. P43 promotes the sampling path to the supported product path:
declared channel combinations at campaign scale, a complete per-channel
coverage table, sample-granular streaming and resume, and
conclusion-survival semantics for study decision rules. It changes no
solver physics; it qualifies combinations and delivers the campaign
surface the P26–P35 machinery implied.

## Scope

- New sampling channels, each sourcing only *declared* uncertainty —
  ACTINV supplies the mechanism, the user supplies the values:
  - `channels.decay_constant`: hash-pinned table mapping nuclide →
    relative standard uncertainty on the decay constant. Perturbations
    are mean-preserving lognormal factors applied per solve; radioactive
    nuclides absent from the table get factor 1.0 and are ledgered in
    the channel's uncovered set — never silent zero-uncertainty.
  - `channels.fission_yield`: hash-pinned table mapping (parent,
    product) or product → relative standard uncertainty on independent
    fission yields, same lognormal convention and uncovered accounting.
    ENDF-6 carries no yield covariance and no licensed TMC/GEF source is
    assumed; without a declared table the channel refuses
    `family_not_qualified`.
- New spec options `options.decay_scale` / `options.yield_scale`,
  mirror images of `options.rate_scale`: input-declared perturbation
  maps consumed by the solver at run time, recorded in the run ledger,
  valid outside `robustness` as ordinary spec options.
- `decision_rules` gain optional `survival`: the rule is evaluated on
  every successful sample, not only the nominal. The record carries
  `fraction_satisfied`, `successful_samples`, `failed_samples` and the
  nominal verdict; a rule over zero successful samples is `undefined`,
  never satisfied.
- Sample-granular streaming and resume: per-sample records are appended
  as samples complete; `study run` into an interrupted case completes
  the declared sample count, re-verifying each existing sample artifact
  byte-for-byte and re-executing only torn or missing samples.
- Per-channel coverage table per case: covered/uncovered named sets for
  every enabled channel (MF=33 rows, decay constants, yields, flux,
  composition elements) emitted beside the existing MF=33 row split.

## Out of scope

- MF=33 covariance coverage completion — data-bound; uncovered rows
  stay ledgered as under P30.
- Blind or sealed experimental validation of bands — that is P44's
  entire scope and shares no code path with this phase.
- Uncertainty *values*: ACTINV never invents decay or yield
  uncertainties. Where no lawful declared source exists the channel
  refuses rather than defaults.
- Model-form, processing or predictive discrepancy — a sample spread
  remains a sensitivity over declared input distributions, exactly as
  P30 defined it.
- New physics, new responses, or relaxation of the 1024-case /
  4096-sample caps (a raise is a separately-frozen scope decision).

## Frozen controls (G2, draft)

- `decay_perturbation_trace`: on a single-nuclide decay fixture, a
  declared `decay_scale` factor on λ reproduces the analytically
  recomputed inventory at the frozen tolerance (Mn-56-class half-life
  check, in the P29 tradition).
- `yield_perturbation_trace`: on a linear-regime fission-feed fixture,
  a declared `yield_scale` factor scales the daughter's production
  linearly within the frozen tolerance.
- `channel_isolation`: a run with only channel X enabled perturbs no
  other channel's inputs — decay-only samples carry identity
  `rate_scale`/`yield_scale`, XS-only samples carry identity decay
  factors.
- `coverage_accounting`: for every enabled channel, covered + uncovered
  named sets partition the channel's active input set.
- `sample_resume`: a run interrupted mid-case resumes to the declared
  count; a planted torn sample artifact re-executes exactly that sample;
  every pre-existing artifact survives resume byte-identically.
- `survival_accounting`: `fraction_satisfied` and the sample counts are
  re-derived by the independent checker from raw sample records; a rule
  over zero successful samples is `undefined`.
- Carried from P30 unchanged: `zero_variance`, `flux_linear`,
  `seed_reproducibility`, `composition_sum`.

## Frozen conformance probes (G3, draft)

- `decay_constant` / `fission_yield` enabled without a declared table:
  `family_not_qualified`, not a nominal run.
- A radioactive nuclide absent from the decay table: factor 1.0, named
  in the uncovered set — a silent drop fails.
- `decay_scale`/`yield_scale` on a non-robustness spec: recorded in the
  run ledger like `rate_scale`.
- Unknown channel name, negative std, `samples` outside [2, 4096]:
  refused at validation.
- An unsupported channel *combination* (e.g. yield sampling on a
  fission-free network): `family_not_qualified` with the reason named.
- A mutated coverage table or sample-artifact digest fails resume
  closed.

## Frozen population (G1, draft — sized at freeze)

- Minimum gate input: two qualified-envelope cases at small `samples`
  proving mechanics end-to-end (P30 smoke lineage).
- Campaign population: a materials × spectra grid with all enabled
  channels at campaign `samples`, sized after one profiled unit per
  standing rule 7; the wall-time envelope is frozen alongside it.
- The P36 W-MATCMP record is the workload anchor but is not re-scored;
  this phase's population is frozen fresh.

## Closure rule (G4, draft)

PASS only if every frozen control holds, the campaign population
completes within its frozen envelope with zero unaccounted samples
across every channel, and the independent checker re-derives coverage,
sample statistics and survival accounting from raw records.
CONDITIONAL if an amendment was used or a named gap changes downstream
scope. FAIL otherwise — the ledger survives either way.
