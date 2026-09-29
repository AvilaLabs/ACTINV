# ACTINV-P79 Amendment A — sole-impurity limits and the joint headline

Date: 2026-09-29. Status: **frozen before the amended command is built or run.** The P79 verdict
(`results/p79_verdict.json`, all gates PASS or NOT EXERCISED as specified) stands and is not
re-derived.

## Why

On the P79 EUROFER97 ex-vessel budget every impurity's summary status was "infeasible", because
the single-impurity limit holds the other impurities at spec and cobalt at spec alone gives
CI = 47 at 50 y. That answer is correct but misleading: the material clears with all impurities
scaled to 2.1 % of spec, and each impurity has a well-defined limit when it is the only one present.

## Additions to `actinv budget`

Per target and impurity, with `g_i = dCI/dw_i`:

- **sole-impurity limit** (all other impurities zero, matrix as declared):
  `w_i^sole = (1 − CI_matrix_only) / g_i`, reported when `g_i > 0` and `CI_matrix_only < 1`;
  otherwise a status (`no clearance-index response`, or infeasible because the matrix alone
  exceeds CI = 1).
- `summary` per impurity becomes `{alone: {...}, others_at_spec: {...}}`, each the tightest limit
  across targets with its binding step, or the status of the first target without one.
- `summary.joint`: the smallest positive `k` across targets and its binding step, or the status
  if some target has no positive `k`.

Verification adds a full solve at every positive sole-impurity limit (that impurity alone, others
zero).

## Gates

- **A-G1:** as P79 G1, and `cargo test -p actinv-cli budget` includes a test that a sole-impurity
  limit composition gives composed CI = 1.
- **A-G2:** as P79 G2 (independent re-solve, 1e-6) over all verification points of the amended
  outputs; EUROFER97 must include at least one sole-limit point.
- **A-G3:** every P79 G3 quantity is unchanged in the amended outputs (1e-12 relative).

Checker: `controls/check_p79.py check --amendment A` → `results/p79a_verdict.json`. The P79
outputs and verdict are kept; amended outputs are written as `results/p79a_budget_<material>.json`.
