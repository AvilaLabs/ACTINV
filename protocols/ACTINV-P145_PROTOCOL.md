# P145 — Waste classification under nuclear-data uncertainty

Registered 2026-10-16, before any source change for this phase. Owner direction 2026-10-16: finish the waste
program. This is the roadmap's separately gated follow-up 1 ("Classification across uncertainty and composition
ranges"), for its uncertainty half; composition ranges were closed by P107, P108 and P110. Uncertainty-aware impurity
budgets need their own validated method and are a later phase (P146).

## Why sampling, and on what

A Part 61 class is decided by sums of nuclide concentrations divided by table limits. The run's first-order
`uncertainty` block reports marginal bands per nuclide and for `activity.total`, but no covariance between nuclides,
so it gives no band on a table sum; adding per-nuclide bands would ignore correlations, and element-run standard
deviations are not additive (roadmap). The qualified `study.robustness` sampler (P30/P43) re-solves the whole
material for each correlated draw of the MF=33 covariance (nearest-PSD factor, mean-preserving lognormal) and of the
declared diagonal channels, so each sample carries a consistent inventory. Classifying every sample with the nominal
evaluator therefore propagates the joint uncertainty to the table sums with their correlations.

## Change under test

A new command `actinv waste uncertainty SPEC [OUT]` (schema `actinv-waste-uncertainty-spec-1`) and its Python twin
`actinv.waste.uncertainty`. The spec names the nominal problem spec, an ordinary waste component block (as in
`actinv-waste-spec-1`, without `input`), the targets, and a sampling block with the `study.robustness` channel
options, `samples` and `seed`, plus `confidence_level` (default 0.95).

1. The nominal problem is solved once; every sample is solved through the same perturbation and prepared-solve path
   as `study.robustness`, with the same per-sample seeds, without its attribution re-draws or first-order comparison.
   Per-sample results stay in memory unless `keep_samples` names a directory. A sample whose solve fails is reported
   by index with its error and counted in every denominator as class `unknown`, so a failure can only weaken an
   established class, never strengthen one.
2. Every sample and target step is classified with `actinv_core::waste::evaluate_component`, with external H-3 merged
   as in the nominal command (declared H-3 is not sampled).
3. Output per component and target step: the nominal result; counts and probabilities of each class (and of unknown);
   a Clopper–Pearson interval at `confidence_level` for each probability, which carries the sampling error; the
   class established at the confidence level, defined as the least restrictive class X with the lower Clopper–Pearson
   bound of P(class at most X) at least the confidence level, or `not_established` with the sample count that would be
   needed if the observed frequency held; per table constraint the 5th, 50th and 95th percentiles of the normalized sum
   and P(normalized sum > 1); and across steps, the earliest target step at which each class is established.
4. Coverage stays explicit and travels with the result: the channels sampled; uncovered MF=33 rows, decay constants and
   yields by name; the run's completeness ledger; and the fixed statement that the probabilities are conditional on
   the sampled channels, excluding MF=32/MF=40 covariance, cross-channel correlation, composition, geometry, model and
   flux-systematic uncertainty, and that measured coverage of these channels on the FNS corpus is below nominal
   (`docs/DATA_LIMITATIONS.md`).
5. Refusals: configurations `study.robustness` or `uncertainty` refuse (gas, schedule library switches, power-driven
   steps); a sampling block with no channel; fewer than 20 samples.

`crates/actinv-core/src/study.rs` may be refactored only to expose the sampling loop to the new command; `study`
outputs must stay byte-identical, which the existing P30/P43/P136 controls check in CI.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
Cases are fixed before any run: C1 `examples/fns_fe_5min.json` (MF=33, flux and decay channels), C2 a
stainless-steel-like material with Co, Nb and Ni impurities near a class boundary at one cooling step, and C3 the
synthetic one-reaction case below. The C2 and C3 inputs (specs, and for C3 the synthetic library, covariance and
closed-form expectations) are committed in the candidate commit, before any evidence run; none is tuned afterwards.

- **U1, same samples.** With `keep_samples`, every per-sample result of C1 and C2 is byte-identical to the
  `rob_<i>.out.json` of a `study.robustness` run with the same channels, samples and seed.
- **U2, exact tallies.** An independent Python checker reads the kept per-sample results, recomputes every table sum
  and class from the bundled rule pack's values, and reproduces every count, probability, percentile and
  Clopper–Pearson bound in the C1 and C2 results exactly (percentiles by the documented nearest-rank rule).
- **U3, calibration.** C3 is a single target, one reaction with one MF=33 group and a declared relative variance, one
  daughter nuclide that alone sets a table sum. Its activity is lognormal with known parameters, so P(normalized sum
  > 1) has a closed form. With 4096 samples, the sampled probability lies within the 99.9 % Clopper–Pearson interval
  around the closed form, for three variances placing the boundary at the 10th, 50th and 90th percentiles.
- **U4, refusals and invariance.** Each refusal in item 5 refuses with its own message and writes nothing; a zero
  variance channel reproduces the nominal class with probability 1; repeating a run gives byte-identical output.
- **U5.** The existing study controls stay green; workspace tests, fmt, clippy `-D warnings` native and wasm, Python
  binding checks, handbook build; CI green on the pushed commit for every triggered workflow.

## Verdict

P145-PASS if U1–U5 hold. A failing gate is preserved under `results/failures/p145/`; one repair round, registered
as an amendment first.

## Not claimed

The probabilities are model-conditional, not total-uncertainty statements and not tolerance limits; with the
FNS-corpus coverage recorded in `DATA_LIMITATIONS.md` they can be overconfident. No budget at a confidence level, no
package class, no disposal acceptance.
