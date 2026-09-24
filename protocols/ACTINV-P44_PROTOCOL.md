# ACTINV-P44 protocol — measured band coverage

**Status:** FROZEN — sealed at G0 before any coverage value exists |
**Frozen:** 2026-09-24 | **Parent:** roadmap draft innovation extension
P44 (`docs/ROADMAP.md`, commit `6db406d`) | **Depends on:** P43
(sampled-band leg, closed `P43-CONDITIONAL`); P11 machinery
(first-order leg); CB2 corpus machinery (spec generation, alignment)

This document is the freeze: partitions, band definitions, scoring-code
identity, coverage metrics and envelope are concrete below. Evidence
collection is authorized only under these values.

## Intent

P44 produces the extension's central new claim: *measured* coverage of
ACTINV's declared uncertainty bands against the experimental corpus —
the fraction of measured points that fall inside a stated band,
reported per experiment, per material family and pooled, separately for
the first-order (P11) and sampled (P43) band types. No competitor
publishes coverage-tested uncertainty for activation. The deliverable
is the honest number, including where coverage is poor: poor coverage
names the missing channel and is itself the result.

## Honesty boundary (read first)

The FNS corpus is public and has been scored repeatedly for C/E — it is
consumed diagnostic evidence, not unread data. Sealing here therefore
means something narrower and still real: the *band definitions, scoring
code, partition assignment and coverage metrics* freeze before any
coverage value is computed. The seal prevents tuning a band, a
partition or a metric to a coverage outcome; it does not manufacture a
blind corpus. Every published claim must say exactly this. No lawful
genuinely-unread family was identified before the freeze: the IRDFF-II
SACS arm named in the draft is excluded as out of scope — its scoring
unit is a folded spectrum-averaged cross section, not an activation
response, and no processed SACS corpus exists on this host; routed to
`docs/PARKING.md` for a dedicated phase.

## Frozen population

### Corpus

`~/nuclear-data/conderc-fns/fns/` — 132 experiments across 73
materials, all parsed cleanly: 73× `2000exp_5min`, 30× `1996exp_5min`,
29× `1996exp_7hour`. 2,376 measured decay-heat points (`heat_uW_g`),
every point carrying a positive reported sigma (`sigma_uW_g`).

### Development partition (10 experiments; labeled retrospective)

Used for G1 machinery qualification and channel diagnosis; consumed C/E
data from CB2:

```
Fe/1996exp_5min   Fe/1996exp_7hour   Fe/2000exp_5min
Ni/2000exp_5min   Ti/2000exp_5min    Al/2000exp_5min
W/2000exp_5min    V/2000exp_5min     Cu/2000exp_5min
SS316/2000exp_5min
```

### Sealed partition (122 experiments)

Every remaining experiment — the full corpus minus the development
partition — scored exactly once through unchanged scoring code at G4.

### Band inputs (all hash-pinned at G0)

- activation library: `actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz`
  (`fb13c16c703c71a862ff82c78bc7fbd0761902264cf45efb97aa1a7e5e43b48d`)
- MF=33 covariance sidecar: `~/nuclear-data/p43-work/p43.cov.npz`
  (`18b8afaf…`, built over the full 1,679-target index during P43)
- decay archives: `~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat`
  primary, `~/nuclear-data/jeff-3.3-decay/bulk/jeff-3-3_decay.dat`
  fallback (the CB2-pinned pair)
- per-experiment inputs: `TENDL-2017_<exp>.i` (mass, wt% composition,
  irradiation time, cumulative cooling schedule, flux total),
  `<exp>_fluxes` (measured 709-group flux), `<exp>.exp` (measured
  points)
- release binary: `target/release/actinv`, hash-pinned

## Frozen band definitions

Both legs cover **nuclear-data uncertainty only**: MF=33 cross-section
covariance (covered rows) plus declared decay-constant sigmas
(`d_half_life`). Fission-yield uncertainty is not a channel here — the
FNS materials are non-fissile structural materials; the channel is
declared absent by construction, not silently dropped. Flux
normalization and composition are treated as exact: their uncertainty
is a measurement-side input and lives in the `combined_sigma` metric's
measurement term, not in the calculation band.

Confidence level for both legs: **0.6827**.

- **First-order band** (`first_order`): the spec's `uncertainty` block
  with `channels: ["cross_section_mf33", "decay_constants"]`,
  `responses: ["heat.total"]`, `confidence_level: 0.6827`; the band per
  post-shutdown step is the emitted `normal_interval`
  (nominal ± σ_propagated at the frozen level, `k = normal_multiplier`).
- **Sampled band** (`sampled`): a per-experiment study (1 case) with
  `robustness.samples = 32`, `robustness.seed = 287444822`,
  `channels: {cross_section_mf33: true, decay_constants: true,
  flux_rel_std: 0.0, composition_rel_std: {}, fission_yields: false}`,
  `first_order_comparison: false`, `robustness.responses:
  ["decay_heat_w_per_g"]`, and restricted `options.outputs:
  [inventory, activity, heat, ledger, certificate]`. The band per
  post-shutdown step is the central quantile interval
  `[Q(0.15865), Q(0.84135)]` of the 32 sample values, linear
  interpolation (numpy/percentile type-7) on raw values.

## Frozen scoring rules

- Unit of coverage: one measured (experiment, post-shutdown time) point
  with `heat_uW_g > 0`.
- Alignment: raw `.exp` times carry no unit column; the scorer infers
  the unit from {s, min, h, d, y} by minimum median relative mismatch
  against the experiment's cumulative cooling schedule, then matches
  each positive-time positive-measurement row to the nearest cooling
  step within `max(2% of the time, 1 s)`. Rows failing the match are
  outcome `undefined` with the named reason
  (`nonpositive_time`, `nonpositive_measurement`,
  `no_cooling_step_within_2_percent`); `nonpositive_time` and
  `nonpositive_measurement` rows are not measured points and are
  counted in `excluded`, not in coverage denominators.
  `no_cooling_step_within_2_percent` rows ARE valid measured points and
  count in denominators as `undefined`.
- Band extraction: per experiment, per band type, per post-shutdown
  step time `t`, the record carries `nominal`, `lo`, `hi`. A measured
  point maps to its matched step's band.
- `band_only`: `covered` iff `lo <= measured <= hi` — the boundary is
  **inclusive** both ways.
- `combined_sigma`: let `c = (lo+hi)/2`, `w = (hi-lo)/2`; `covered` iff
  `c - sqrt(w² + σ_meas²) <= measured <= c + sqrt(w² + σ_meas²)`
  (inclusive). Every corpus point carries a positive sigma; a point
  without one would contribute to `band_only` only.
- `not_covered` outcomes: measured outside the band; nominal ≤ 0
  (`zero_prediction`); band absent (`band_unavailable` — failed solve,
  failed sample set, missing step). All count in denominators.
- Reported aggregates: per-experiment coverage, per-material-family
  coverage (family = corpus directory name), per-experiment-type
  coverage (`1996exp_5min`, `1996exp_7hour`, `2000exp_5min`), and
  pooled coverage — separately per band type and per metric, never
  blended.
- Headline denominator: `covered / (covered + not_covered + undefined)`
  over valid measured points.
- Independence caveat in every report: points within an experiment
  share data and measurement systematics; pooled coverage is a
  descriptive statistic, not a binomial confidence statement.

## Frozen scoring artifacts

- `controls/p44_bands.py` — band-production driver: builds the frozen
  spec/study per experiment, runs them under the bounded scope, emits
  `bands.json` per experiment. Hash-pinned.
- `controls/p44_band_coverage.py` — the scorer: consumes band records +
  `.exp` points, emits per-point outcomes and all aggregates.
  Hash-pinned; unchanged after sealed scoring.
- `controls/harness/fispact_io.py` — corpus readers; hash-pinned as a
  dependency.
- `controls/g0_p44_seal.py`, `controls/g2_p44_controls.py`,
  `controls/g3_p44_conformance.py`, `controls/g4_p44_verdict.py`,
  `controls/check_g4_p44.py` — gate artifacts.

## Envelope

- Development (G1): both legs over the 10 development experiments.
- Sealed scoring (G4): both legs over the 122 sealed experiments —
  first-order ~1 solve each, sampled ~1+32 solves each — declared
  envelope **180 minutes** wall for the sealed scoring run, measured by
  the producer. The run is resumable at case/sample granularity; a
  resumed run restarts the wall clock only for a fresh run — the
  producer reports `wall_minutes` against the envelope only for a
  complete fresh pass.

## Frozen controls

- Synthetic corpus: planted points at known offsets inside/outside a
  known band score exactly `covered`/`not_covered`; a planted
  sigma-only inclusion flips `combined_sigma` without touching
  `band_only`.
- Boundary: a point exactly on a band edge scores `covered` under the
  frozen inclusive rule — exercised on `lo`, on `hi`, and on the
  `combined_sigma` widened edge.
- Arithmetic: the checker re-forms every family, type and pooled
  coverage figure from the per-point records without importing the
  scorer.
- Denominator: planted `zero_prediction`, `band_unavailable` and
  `undefined` points appear in `not_covered`/`undefined` and shrink the
  headline coverage accordingly.
- Alignment: a planted unit-shifted `.exp` column (minutes written as
  hours) is either correctly inferred or produces named `undefined`
  outcomes — never a silent mismatch.
- Resume: a torn sampled band record is re-derived, not trusted; a
  mutated band record hash fails closed.

## Gates

- **G0** — seal: protocol hash, opening commit, partition assignment,
  scoring-code hashes, band definitions, input identities, envelope —
  all bound by an independent control before any coverage value exists.
- **G1** — mechanics: bands produced per definition on the development
  partition; per-point records complete; both legs exercised.
- **G2** — frozen controls above.
- **G3** — conformance: the scorer refuses a band record, partition
  list, or metric identity that differs from the sealed values; an
  unsealed rescore is impossible without a new seal record.
- **G4** — sealed scoring + independent closure: one pass, checker
  re-derives every figure, planted mutations rejected, verdict emitted.

## Closure rule

PASS only if the sealed partition is scored exactly once through the
sealed code with complete denominator accounting and the checker
re-derives the report. CONDITIONAL if an amendment was used or the
corpus caveat weakens (it applies here: consumed C/E data — the seal
covers code/definitions/partition, not blind data). FAIL otherwise. The
verdict records the measured coverage either way; a low number is a
fork to channel-repair scope, not a failed phase.
