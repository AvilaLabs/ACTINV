# P146 — Impurity budgets under nuclear-data uncertainty

Registered 2026-10-16, before any source change for this phase. Owner direction 2026-10-16: finish the waste
program. This is the budget half of the roadmap's follow-up 1, after P145 (classification under nuclear-data
uncertainty).

## Why an order statistic, and what is sampled

The nominal `actinv waste budget` (P105) solves each element of the budget once, uses the linearity of the activation
response in composition, and reports per impurity the interval of weight percent (and the joint specification
scale factor) for which every table constraint at every target stays inside the target class. Under nuclear-data
uncertainty each correlated draw of the data gives its own element responses and therefore its own interval. For a
target class and an impurity, the limit is the per-sample supremum x*_i. An impurity level x keeps the class in a
fraction F(x) of data draws, F(x) = P(x < x*). The budget at confidence γ is the k-th smallest x*_i, with k the
largest index for which the lower end of the two-sided Clopper–Pearson interval at γ of (n − k + 1)/n is at least γ
(P145 Amendment A conventions). Any x below it keeps the class with probability at least γ, at confidence γ, as long
as the samples represent the data uncertainty. This is the order-statistic (Wilks) tolerance bound, exact for any
distribution of x*; it needs no normality and no first-order expansion.

For the element responses of one sample to share one data set, the sample's factors are drawn once, from the
sampling plan of the full budget material (every element of the budget present), and the same factors (cross-section
rate scales by reaction, decay-constant scales by nuclide, yield scales, flux factor) are applied to each element's
solve. The composition channel is refused: composition is what the budget solves for.

## Change under test

A new command `actinv waste budget uncertainty SPEC [OUT]` (schema `actinv-waste-budget-uncertainty-spec-1`) and its
Python twin `actinv.waste.budget_uncertainty`. The spec is an ordinary `actinv-waste-budget-1` document (base spec,
balance, matrix, impurities, targets, target class, rules, component, properties, external tritium) plus a
`sampling` block as in P145 (channels except composition, covariance, `samples` from 20 to 4096, `seed`, optional
`keep_samples`) and `confidence_level` (default 0.95).

1. The nominal budget is computed exactly as `actinv waste budget` computes it and is reported unchanged.
2. Every sample draws its factors from the full-material plan with the per-sample seed `study.robustness` uses, and
   solves each element with those factors. With `keep_samples`, each sample's per-element results and the factor
   block are written.
3. Per sample the nominal budget algorithm is applied to the sample's element responses, giving per impurity the
   lower bound and upper supremum and for the joint specification the scale-factor interval, with binding constraint.
4. Per impurity and for the joint scale factor: the sorted per-sample suprema, the budget at the confidence level
   (the order statistic above, reported as an open bound, not attained), its index k, the minimum sample count for
   which any budget exists at that level (the k = 1 case), the per-sample binding constraints tallied by table,
   column and step, and the nominal supremum for comparison. A sample whose solve fails or whose interval is
   infeasible counts as x* = 0, so it can only lower the budget. The order statistic bounds only the upper end: when
   any sample (or the nominal) has a positive lower bound for an impurity, that impurity's budget is reported as not
   established, with the count of such samples; two-sided budgets are not claimed. A supremum capped by the balance
   (no constraint binds) is reported with that status and enters the order statistic at the cap.
5. Verification: for each sample at sorted indices k − 2 to k + 2 (those that exist), the full material with the
   impurity (each impurity alone, then the joint scale factor) at 0.99 and at 1.01 times that sample's own supremum
   is classified with that sample's own factors; and every sample at index k or above is classified at 0.99 times
   the reported budget. An unbounded supremum (an impurity with no response in any constraint) is reported as such
   and not verified.
6. Coverage and the not-claimed statement as in P145, plus: linearity in composition is assumed for each sample as in
   the nominal budget; budgets are not composition tolerances, and the composition, geometry and model uncertainties
   are not sampled.
7. Refusals: everything the nominal budget refuses; the composition channel; a sampling block with no channel;
   fewer than 20 samples; a confidence level outside (0, 1); a sample count too small for any budget at that level
   (the result of item 4 is computed first and the refusal names the minimum).

`crates/actinv-core/src/study.rs` may change only to let a factor block drawn from one plan be applied to another
spec; `study` outputs stay byte-identical (`controls/check_p136.py` G2–G4 against the P145 candidate release binary, SHA-256 `db2d482f…` from `0ea4a18`).
`actinv waste budget` outputs stay byte-identical (the P105 CI steps).

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
Inputs are committed with the candidate, before any evidence run. D1: a stainless-steel budget (Fe balance, Cr, Ni,
Mn matrix; Co, Nb, N impurities; activated metal; target class A) on the P145 C2 spectrum and schedule, MF=33, decay
and flux channels, 200 samples. D2: the synthetic one-reaction case of P145 C3 (cobalt impurity in a balance with no
response in the governing table sum, thermal spectrum, one day of cooling) at three relative variances, 1024
samples each. Synthetic covariance sidecars are not tracked: the checker generates them with the P145 C3 generator
and verifies their pinned SHA-256 when it stages a case (P145 Amendment C).

- **B1, one data set per sample.** For D1 and D2, the factor block of every sample is identical across that sample's
  element solves, and equal to the factor block a `study.robustness` run of the full material with the same channels
  and seed writes for that sample (the `options` scale entries of `rob_<i>.json`).
- **B2, exact recomputation.** An independent Python checker recomputes, from the kept per-element results and the
  bundled rule pack, every per-sample interval (suprema within 1e-12 relative), the sorted suprema, k, the budget,
  the minimum sample count and the binding tallies (exactly).
- **B3, verification.** Every verification point of item 5 classifies inside the target class at 0.99 times the
  sample's supremum and at 0.99 times the budget, and outside it (in a later class) at 1.01 times the sample's
  supremum.
- **B4, calibration.** In D2 the per-sample supremum is x₀/F with F = exp(σz − σ²/2), so the true probability that
  an impurity level x keeps the class is P(F < x₀/x) = Φ((ln(x₀/x) + σ²/2)/σ) in closed form, with x₀ the nominal
  supremum computed in the same run. The coverage of the k-th order
  statistic of n continuous draws follows Beta(n − k + 1, k). For each variance, the closed-form keep probability at
  the reported budget lies inside the central 99.9 % of that Beta distribution.
- **B5, refusals and invariance.** Each refusal of item 7 refuses with its own message and writes nothing; with every
  channel at zero variance the budget equals the nominal supremum; repeating a run gives byte-identical output.
- **B6.** `check_p136.py` G2–G4 against the P145 candidate release binary (run locally; it is not a CI step); the
  P105 steps of the `controls` workflow, run locally and in CI; the P13 data-distribution step run locally; workspace tests, fmt,
  clippy `-D warnings` native and wasm, Python binding checks, handbook build; CI green on the pushed commit for
  every triggered workflow.

## Verdict

P146-PASS if B1–B6 hold. A failing gate is preserved under `results/failures/p146/`; one repair round, registered
as an amendment first.

## Not claimed

The budget is conditional on the sampled channels and on linearity in composition; with the FNS-corpus coverage
recorded in `docs/DATA_LIMITATIONS.md` it can be optimistic. Two-sided budgets (positive lower bounds) are not claimed. It is not a composition tolerance, a procurement
specification, a package class or a disposal acceptance.
