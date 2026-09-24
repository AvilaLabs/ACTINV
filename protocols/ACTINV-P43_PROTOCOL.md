# ACTINV-P43 protocol — qualified uncertainty campaign driver

**Status:** frozen | **Opened:** 2026-09-23 | **Parent:** roadmap draft
innovation extension P43 (`docs/ROADMAP.md`, commit `6db406d`)
| **Depends on:** P30-CONDITIONAL (`results/verdict_p30.json`),
P31-CONDITIONAL (`results/verdict_p31.json`)

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

- New sampling channels, each drawing only the *declared* uncertainty
  already propagated by the P11 first-order path — ACTINV supplies the
  mechanism, the pinned evaluations supply the values:
  - `channels.decay_constants` (bool): per-radioactive-nuclide
    mean-preserving lognormal factors with relative sigma
    `d_half_life / half_life` from the pinned decay archives — the same
    declared source as the first-order `decay_constants` channel.
    Chain members with no declared sigma get factor 1.0 and are named
    in the channel's uncovered set; the evaluations carry no
    correlation data, so draws are independent (stated in the record).
  - `channels.fission_yields` (bool): per-(parent, product) independent
    lognormal factors with sigma from `EffectiveYields.uncertainties`
    (the declared endpoint sigmas propagated through the same linear
    interpolation as the values), requiring `fission_yields` files on
    the spec. Pairs with zero declared sigma get factor 1.0 and are
    named uncovered; draws independent, same caveat.
- New spec options `options.decay_scale` / `options.yield_scale`,
  mirror images of `options.rate_scale`:
  - `decay_scale`: `{"<nuclide>": factor}` keyed by explicit-nuclide
    name (`material_key` semantics, e.g. `"Mn56"`, `"Co60m1"`),
    multiplying that nuclide's decay constant everywhere it enters the
    run — decay edges, activity, heat, photon and dose responses —
    never only the matrix. A key absent from the case's decay chain or
    naming a stable state is a named error, not a silent no-op.
  - `yield_scale`: `{"<parent>:<product>": factor}` with both keys
    explicit nuclides, multiplying that independent yield (and its
    declared sigma, keeping relative uncertainty invariant). A pair
    absent from the effective yields is a named error.
  - Both are ordinary spec options outside `robustness`, recorded in
    the run ledger like `rate_scale`, and excluded from the
    prepared-run signature so perturbed samples share preparation.
- `study` gains an optional per-material `fission_yields` block
  (`cases.materials[].fission_yields`, same shape as the spec's),
  propagated verbatim into that material's case specs; materials
  without it declare no yield files.
- `decision_rules` survival: when `comparison` and `robustness` are
  both present, each rule is additionally evaluated on paired sample
  outputs — sample index `i` across a group's cases shares the drawn
  factors (common random numbers, see below), so the rule evaluated on
  index `i` is a paired evaluation. The record carries
  `fraction_satisfied`, `paired_samples`, `failed_samples` and the
  nominal verdict per group; a rule over zero paired samples is
  `undefined`.
- Sample-granular draws, streaming and resume:
  - Sample `i` derives its stream as `Rng(seed ^ tag ^ mix(i))` — each
    sample is independently reproducible and identical streams pair
    across cases. This supersedes P30's sequential stream for new
    runs; determinism and the fixed-seed contract are unchanged.
  - Per-sample records are appended as samples complete; `study run`
    into an interrupted case completes the declared sample count:
    each recorded sample artifact is re-verified byte-for-byte,
    verified samples are reused (their recorded per-time responses
    re-read from `rob_<i>.out.json`), and only missing or torn samples
    re-execute.
- Per-channel coverage table per case: for every enabled channel, the
  named covered/uncovered partition of that channel's active input set
  — active library rows (MF=33), radioactive chain members (decay),
  effective (parent, product) pairs (yield), declared elements present
  in the material (composition), total flux (scalar, covered by
  declaration). Emitted beside the existing MF=33 row split.

## Out of scope

- MF=33 covariance coverage completion — data-bound; uncovered rows
  stay ledgered as under P30.
- Blind or sealed experimental validation of bands — that is P44's
  entire scope and shares no code path with this phase.
- Uncertainty *values*: ACTINV never invents decay or yield
  uncertainties. Correlation is not synthesized where the evaluations
  declare none.
- Model-form, processing or predictive discrepancy — a sample spread
  remains a sensitivity over declared input distributions, exactly as
  P30 defined it.
- New physics, new responses, or relaxation of the 1024-case /
  4096-sample caps (a raise is a separately-frozen scope decision).

## Frozen controls (G2)

- `decay_perturbation_trace`: a pure-decay spec — material `{"Mn56":
  100}`, a single zero-flux step — run at `decay_scale {"Mn56": 1.5}`
  and unperturbed. The checker recomputes `N(t) = N0·exp(-f·λ·t)` from
  the pinned decay file's own λ and requires relative agreement
  `<= 1e-9` at the step end, and activity `f·λ·N` consistently.
- `yield_perturbation_trace`: material `{"U235": 100}`, fission_yields
  `nfy-092_U_235.endf` (hash-pinned at G0), FNS spectrum, 300 s pulse.
  `yield_scale` applies factor 2.0 to *every* `(U235, product)` pair
  of the effective yields: every fission-subtree production edge
  scales by 2 and a pure-U235 target feeds the fission-mass range
  only through fission, so `atoms(f)/atoms(1) == 2` exactly for any
  nuclide in the fission subtree. The checker verifies the ratio to
  `<= 1e-9` on the frozen named set `I135`, `Xe135`, `Kr88`, `Cs137`
  and that the activation product `U236` is unchanged to `<= 1e-9`.
- `channel_isolation`: a robustness run with only `decay_constants`
  enabled writes sample specs carrying `decay_scale` and identity
  `rate_scale`/`yield_scale`/flux/composition; likewise each channel
  alone perturbs only its own inputs (sample specs are the evidence).
- `coverage_accounting`: per case and channel, covered + uncovered
  named sets partition the channel's active input set exactly; a
  radioactive member with `d_half_life = 0` appears uncovered, and a
  product with `sigma_y = 0` appears uncovered.
- `sample_resume`: (a) a case whose declared `samples` ran partially
  resumes to the declared count with every pre-existing artifact
  byte-identical; (b) a planted torn sample artifact re-executes
  exactly that sample — all other artifacts unchanged, the re-run
  sample byte-identical to the original under the per-sample seed.
- `survival_accounting`: `fraction_satisfied` and the paired/failed
  counts are re-derived by the independent checker from raw
  `rob_*.out.json` records and the frozen rule semantics; a group with
  zero paired samples is `undefined`, and sample index pairing is
  verified against the recorded per-sample draws.
- Carried from P30 unchanged: `zero_variance`, `flux_linear`,
  `seed_reproducibility` (two runs, same seed → byte-identical sample
  specs; now also per-sample: `rob_i` reproduced by a run of only
  sample `i`), `composition_sum`.

## Frozen conformance probes (G3)

- `decay_scale` on a nuclide absent from the decay chain: named error.
- `decay_scale` on a stable chain member (λ = 0): named error.
- `yield_scale` on a pair absent from the effective yields: named
  error.
- `fission_yields` channel enabled on a study where no case has a
  fission parent: refused at study validation, reason named. Per-case
  zero-parameter coverage is honest accounting, not an error.
- `decay_scale`/`yield_scale` on a non-robustness spec: accepted,
  ledgered like `rate_scale`.
- `rate_scale` + `decay_scale` + `yield_scale` on one spec: all apply,
  all ledgered.
- Unknown channel name refused (`deny_unknown_fields`); negative std
  refused; `samples` outside [2, 4096] refused.
- A mutated coverage table, sample-spec or sample-out digest fails
  resume closed.
- Prepared-run reuse: samples sharing a signature reuse one
  PreparedRun (`prepared_runs` counts distinct signatures, not
  samples).

## Frozen population

- **Minimum gate input (G1 mechanics):** the two trace fixtures above,
  plus a three-case study (`fe`, `fe_co100wppm`, `u235`; FNS-709;
  `pulse_5min`; `samples` 8; all five channels enabled) proving the
  mechanics end-to-end inside 10 minutes.
- **Campaign population (G3):** study `p43-campaign` — materials
  `{fe, fe_co100wppm, u235}`; spectrum `fns_709` (`spec_ref`
  `examples/fns_fe_5min.json`); schedules `pulse_5min` (300 s),
  `cont_1d` (1 d); `cooling_times_s [0.0, 86400.0]`; all five qualified
  responses; `u235` declares `fission_yields` `nfy-092_U_235.endf`;
  `robustness.samples` 64, `seed` 43330177, channels
  `cross_section_mf33` + `flux_rel_std` 0.05 +
  `composition_rel_std {"Co": 0.20}` + `decay_constants` +
  `fission_yields`; covariance sidecar rebuilt minimal over the
  study's active targets (input preparation, hash-pinned at G0);
  `comparison.axes ["material"]` with decision rules:
  - `r1`: `rank_equal`, response `total_activity_bq_per_g`,
    `times_s [86400.0]`, axis `material`, `expected_order`
    `[u235, fe_co100wppm, fe]`.
  - `r2`: `rank_equal`, response `total_activity_bq_per_g`,
    `times_s [0.0]`, axis `material`, `expected_order`
    `[u235, fe, fe_co100wppm]` — the t = 0 fe / fe_co100wppm
    ordering is deliberately marginal (the 100 wppm Co substitution
    moves the shutdown activity by ~1e-4 relative), so r2's nominal
    verdict and survival fraction are measured outcomes of the gate,
    not assumed.
- **Resource envelope:** G3 wall time `<= 45` minutes inside the
  enforced cgroup (`CPUQuota=200%`, `MemoryMax=6G`), one job at a
  time; one representative case is profiled at G1 before the campaign
  (standing rule 7) and an over-envelope profile rescales the
  population by amendment, never silently.

## Frozen authorities and provenance

- P30 verdict `results/verdict_p30.json` and P31 verdict
  `results/verdict_p31.json`, re-verified verbatim at G0.
- Library `actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz`,
  SHA-256 `fb13c16c703c71a862ff82c78bc7fbd0761902264cf45efb97aa1a7e5e43b48d`.
- Decay `actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat` primary,
  `jeff-3-3_decay.dat` fallback (hashes bound at G0).
- Fission yields `~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf`
  (hash bound at G0; terms per `DATA.md`).
- Covariance sidecar: rebuilt minimal over the study's active targets
  by `actinv build-covariance` from the pinned TENDL-2025 MF=33
  sources; the built artifact is an input, hash-pinned at G0.
- Spectrum: `examples/fns_fe_5min.json` spectrum block.

## Gates

### G0 — opening, authority, seal

An independent control binds this protocol's SHA-256, the opening
commit, the P30/P31 verdicts verbatim, every input identity above and
the frozen study/spec documents. G0 is committed and green before any
gate evidence is produced.

### G1 — implementation and minimum gate

The scope items implemented; the two trace fixtures and the two-case
study run end-to-end; one representative campaign case profiled for
the envelope check.

### G2 — frozen controls

Every control in *Frozen controls* holds under the independent
checker.

### G3 — conformance + campaign

Every frozen conformance probe holds; the campaign population executes
within its envelope with zero unaccounted samples across every
channel.

### G4 — independent closure

A checker importing no production, parsing or scoring module rehashes
all inputs, re-derives the coverage tables, sample statistics and
survival accounting from raw artifacts, verifies gate ordering,
re-verifies the prior verdicts verbatim and rejects planted
mutations. Verdict to `results/verdict_p43.json`.

## Closure interpretation

PASS only if every frozen control holds and the campaign population
completes within its envelope with complete per-channel coverage and
survival accounting. CONDITIONAL if an amendment was used or a named
gap changes downstream scope. FAIL otherwise — the ledger survives
either way.
