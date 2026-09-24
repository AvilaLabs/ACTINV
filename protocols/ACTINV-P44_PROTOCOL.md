# ACTINV-P44 protocol — measured band coverage

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P44 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** P43 (sampled-band leg); P11
machinery (first-order leg)

Frozen partitions, band definitions, scoring-code identity and the
coverage metrics are placeholders to be fixed at the freeze; this
document authorizes no execution.

## Intent

P44 produces the extension's central new claim: *measured* coverage of
ACTINV's declared uncertainty bands against the experimental corpus —
the fraction of measured points that fall inside a stated band,
reported per experiment, per family and pooled, separately for the
first-order (P11) and sampled (P43) band types. No competitor publishes
coverage-tested uncertainty for activation. The deliverable is the
honest number, including where coverage is poor: poor coverage names
the missing channel and is itself the result.

## Honesty boundary (read first)

The FNS corpus is public and has been scored repeatedly for C/E — it is
consumed diagnostic evidence, not unread data. Sealing here therefore
means something narrower and still real: the *band definitions, scoring
code, partition assignment and coverage metrics* freeze before any
coverage value is computed. The seal prevents tuning a band, a
partition or a metric to a coverage outcome; it does not manufacture a
blind corpus. Every published claim must say exactly this. If a lawful
genuinely-unread family is identified before the freeze, it joins the
sealed partition; none is assumed.

## Scope

- Two frozen coverage metrics per measured point, reported separately
  and never blended:
  - `band_only`: measured value inside the declared calculation band
    about the calculated value;
  - `combined_sigma`: measured value inside the band widened by the
    point's reported measurement sigma (root-sum-square with the band
    half-width at the frozen confidence level).
- Coverage accounting with failures counted *against* coverage: a point
  whose band cannot be computed (uncovered channel, failed sample,
  failed construction, zero prediction) is scored `not_covered`, never
  filtered. Report family counts beside point counts.
- Partition discipline: a frozen development partition may guide
  channel diagnosis; a frozen sealed partition is scored exactly once
  through unchanged scoring code. A band changed after sealed scoring
  is a new band definition requiring a new sealed scoring.
- Per-band-type, per-channel-combination reporting: every emitted
  coverage figure names the band type, the channels included, the
  declared distribution inputs and the uncovered remainder.
- Out of scope: tuning band widths to coverage outcomes; any
  recalibration shipped to users (a widened "calibrated band" product
  is a separate, later decision requiring its own frozen validation).

## Frozen scoring rules (draft)

- Unit of coverage: one measured (experiment, time) point.
- Band definition per band type, frozen before scoring: first-order
  ±k·σ_propagated and sampled quantile/normal-CI at the frozen level
  (k and the level are protocol parameters, not chosen post-hoc).
- A measured point with no reported sigma contributes to `band_only`
  only, and counts in that metric's denominator.
- `undefined`, `not_covered`, `covered` are the only point outcomes;
  every outcome is named per point in the scored record.
- Independence caveat stated in the report: points within an experiment
  share data and measurement systematics; pooled coverage is a
  descriptive statistic, not a binomial confidence statement.

## Frozen controls (draft)

- Synthetic corpus: planted points at known offsets inside/outside a
  known band score exactly `covered`/`not_covered`; a planted
  sigma-only inclusion flips `combined_sigma` without touching
  `band_only`.
- Boundary: a point exactly on a band edge uses the frozen inclusive/
  exclusive rule — whichever is declared — and is exercised both ways.
- Arithmetic: the checker re-forms every family and pooled coverage
  figure from the per-point records without importing the scorer.
- Denominator: planted failed-band and zero-prediction points appear
  in `not_covered` and shrink the headline coverage accordingly.

## Frozen population (draft)

- Development partition: a named subset of the 132 FNS experiments
  plus the IRDFF-II SACS arm (retrospective, labeled as such).
- Sealed partition: the remaining FNS experiments, sealed at G0 with
  the scoring code hash and band definitions.
- Band inputs: the shipped tendl-2025-patched library, pinned decay
  archives, the shipped MF=33 covariance sidecar, and the P43 channel
  tables where enabled — all hash-pinned at G0.

## Gates (draft)

- **G0** — seal: protocol hash, opening commit, partition assignment,
  scoring code hash, band definitions and input identities bound by an
  independent control before any coverage value exists.
- **G1** — machinery: bands produced per definition on the development
  partition; per-point records complete.
- **G2** — frozen controls above.
- **G3** — conformance: scorer refuses a band/partition/metric that
  differs from the sealed identities; an unsealed rescore is impossible
  without a new seal record.
- **G4** — sealed scoring + independent closure: one pass, checker
  re-derives every figure, planted mutations rejected, verdict emitted.

## Closure rule (draft)

PASS only if the sealed partition is scored exactly once through the
sealed code with complete denominator accounting and the checker
re-derives the report. CONDITIONAL if an amendment was used or the
corpus caveat above weakens (e.g. no unread family existed and coverage
is materially low — reported, not hidden). FAIL otherwise. The verdict
records the measured coverage either way; a low number is a fork to
channel-repair scope, not a failed phase.
