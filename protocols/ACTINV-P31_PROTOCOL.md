# ACTINV-P31 protocol — efficient complete campaigns

P31 qualifies the efficiency claims of the study executor: shared
preparation, resumable execution and measured workload cost. This
protocol freezes the workload, the targets, the recovery controls and
the closure rule. P31 does not re-open scientific qualification: every
mechanism must produce bit-identical physics to the sequential path it
replaces, or fail closed.

## Scope

- Shared preparation: `PreparedRun` instances are keyed by the
  data-and-option signature they verify against (`library`, `decay`,
  `photon`, `fission_yields`, `projectile`, temperature, `uncertainty`,
  `radiological`, `damage`, `self_shielding`) and reused across cases
  and robustness samples whose specs are compatible. A spec that does
  not verify against the prepared context prepares its own — never
  silently reuses.
- Streaming + resumable execution: the study record is written after
  every completed case (`status: partial` until all cases resolve).
  `actinv study run` into an existing output directory resumes: a case
  is skipped only when its recorded `spec_sha256`, `out_sha256` and
  status match the artifacts on disk byte-for-byte; any divergence or
  corruption re-executes that case. A torn record (truncated/malformed
  `study_record.json`) discards the record and re-verifies all case
  artifacts.
- Measured workload: wall-clock cost of the frozen campaign is recorded
  per case and in total, split into preparation and solve; the number
  of `PreparedRun` constructions is reported. Cold (fresh outdir) and
  warm-resume (second identical invocation) are both measured.

## Out of scope

- A cross-case competitive speedup claim or any competitor comparison
  (P26b established that the comparator software is absent; no
  headroom claim is made).
- Approximate reuse, caching of solver results across distinct inputs,
  or response-aware reduction. Reuse here is exact: identical inputs
  prepared once; distinct inputs prepared separately.
- GUI/desktop streaming.

## Frozen workload (G1)

The P27 smoke population, unchanged: 8 cases
(`{fe, fe_co100wppm} x {fns_709, irdff_sp_mat9861_709} x
{pulse_5min, cont_1d}`), same library/decay/spectrum assets. Robustness
is off for this workload (sampling cost is already P30-scoped); a
second frozen workload with robustness (the P30 2-case study,
samples 4) exercises shared preparation across sample solves.

Targets, frozen at seal:

- `prepared_runs` == number of distinct data-and-option signatures in
  the campaign (exactly 1 for the smoke population; the robustness
  workload admits 1 nominal-signature + 1 uncertainty-signature per
  case for the local check = 2 per case, 4 total).
- Resumed run writes `resumed_cases` naming each skipped case; every
  skipped case's artifacts verify against the record.
- Results equivalence: every resumed/skipped case's `out_sha256` is
  identical to the cold run's.

## Controls (G2)

- `reuse_correctness`: the shared-preparation campaign produces
  out.json files bit-identical to a per-case-prepared baseline run
  (the baseline uses `PreparedRun::prepare` per case; physics must be
  identical — only preparation is shared).
- `resume_torn`: delete one case's `out.json`, resume; only that case
  re-executes; final results identical to the original run.
- `resume_corrupt`: corrupt one byte of a case `out.json` (truncate),
  resume; the case re-executes and the final record is clean.
- `resume_stale_spec`: edit a case spec on disk (change a schedule
  dt), resume; the case re-executes (spec hash mismatch), never
  silently accepted.
- `distinct_signatures`: a study whose cases carry different fission
  options (or different uncertainty blocks) constructs one PreparedRun
  per signature — reported count verified.
- `interrupted_run`: kill the executor mid-campaign; resume completes
  with a clean record (the partial record never counts a case that did
  not finish).

## Negative controls (G3)

- `spec_mismatch_accepted`: a case spec whose declared library sha
  diverges from the prepared context is rejected by
  `ensure_compatible`, not run.
- `corrupt_record_swallowed`: a `study_record.json` that is not valid
  JSON is diagnosed and re-run, never read as authority.
- `phantom_resume`: a record claiming a case `executed` whose artifact
  is absent re-executes that case.
- `option_drift`: a study edited after partial execution (different
  material composition for a case) re-executes the drifted case on
  resume.
- `directory_pollution`: foreign files in the cases directory do not
  alter the manifest or results.

## Gate structure

- G0: seal — binary digest, prior verdicts, frozen workload and
  targets, tolerances (`analytic_rel` 1e-6 for the reuse-equality
  checks which are exact so the tolerance is belt-and-suspenders).
- G1: implement shared preparation + streaming/resumable record; run
  the frozen workload; measure cold/resume wall times and
  prepared-run counts.
- G2: execute the six controls above.
- G3: execute the five negative controls above.
- G4: verdict + independent closure checker + roadmap entry.

## Closure rule

PASS requires all gates green and results equivalence on every skipped
case. CONDITIONAL when a frozen control or accounting item remains
unmet (e.g. interrupted-resume semantics partial) but the measured
claims hold. FAIL on any silently-accepted stale/corrupt artifact or
any non-identical physics under reuse.
