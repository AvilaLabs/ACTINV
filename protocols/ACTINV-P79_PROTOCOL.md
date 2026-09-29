# ACTINV-P79 — `actinv budget`: impurity budgets as a verified command

Date: 2026-09-29. Status: **frozen before the command is first executed.**

## Purpose

P75b showed that coupled-mode activation at fixed flux is exactly linear in composition (1.06e-11).
P76 and Amendment A used that to derive impurity budgets for the IAEA clearance index
`CI = Σ A_n/L_n` from one run per element, and verified them with full solves to ≤ 3e-15. This
protocol turns the prototype into a CLI command whose every emitted limit is checked by a full solve
in the same invocation.

## Command under test

`actinv budget BUDGET.json [OUT.json] [--no-verify]`, `crates/actinv-cli/src/budget.rs`, branch
`budget-command` from `41dd5b4`. Input schema `actinv-budget-1`:

- `base_spec`: a run spec (path relative to the budget file, or embedded). Its `material` is
  replaced; its flux, schedule, data and temperature are used as given.
- `balance`: the element that takes up the remainder to 100 wt% (e.g. `Fe`).
- `matrix`: fixed constituents, wt%. `impurities`: declared impurity specification, wt%.
- `targets`: one-based result step numbers at which CI must be ≤ 1 (same convention as
  `actinv clearance`).
- optional `limits`: an `actinv-clearance-limits-1` file (default: the bundled table).

Solver settings are forced to `mode: coupled`, `prune: reach` (the configuration P75b proved
additive) and recorded in the output. Inputs whose response is not linear in composition are
refused: `self_shielding`, `uncertainty`, `options.screen`, per-step `material` changes (if any).

Per target step k, with `r_e` the CI of pure element e (per 100 wt%):
`CI(w) = Σ_e w_e r_e / 100`; `dCI/dw_e = (r_e − r_balance)/100`; spec margin factor
`k = (1 − CI_matrix_only) / Σ_i w_i^spec·dCI/dw_i`; single-impurity limit with the others at spec
`w_i* = w_i^spec + (1 − CI_at_spec)/(dCI/dw_i)`, reported only when the gradient is positive and
`w_i* > 0` (otherwise a status says why). Uncovered activity (nuclides without a limit) is
reported, never silently dropped.

Verification (default on): full solves at the at-spec composition, at `spec × k` for every target
with `k > 0`, and at every positive single-impurity limit; each solve is compared with the composed
prediction at every target.

## Population

`controls/p79/base_exvessel.json`: the P76a ex-vessel field (`mix` shape, 1e9 n/cm²/s, 5 y, then
cooling to 100 y), data as in P76a. Budget documents `controls/p79/eurofer97_exvessel.json` and
`controls/p79/ss316ln_exvessel.json`: matrix and impurity specification exactly as P76 Amendment A,
targets = the 50 y and 100 y steps.

## Gates (checker `controls/check_p79.py`)

- **G0** protocol hash registered before the first execution; both budget runs exit 0.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-cli --all-targets --
  -D warnings`; `cargo test -p actinv-cli budget` passes and contains at least one test each for:
  the composition algebra on a synthetic table, the infeasible and no-response statuses, and the
  refusal of a non-linear input.
- **G2 exactness (independent):** for every verification composition the command emits, the checker
  runs `actinv run` itself and recomputes CI in Python (the P76 `ci_of` arithmetic). At every target,
  `|CI_solved − CI_predicted| / max(CI_predicted, 1e-300) ≤ 1e-6`. At least one verification point
  per material must have predicted CI = 1 (an edge), or G2 is reported NOT EXERCISED for that
  material.
- **G3 parity with the prototype:** against `results/p76a_verdict.json` `report`, per material and
  target: `ci_matrix_only`, `ci_at_spec`, `spec_margin_factor_k`, each impurity's CI contribution at
  spec, and every single-impurity limit that is positive in both, agree to 1e-9 relative. Limits the
  prototype emitted as non-positive numbers must be reported by the command as not existing
  (status), not as numbers.
- **G4 descriptive:** command wall time, number of solves, per-solve time.

Builds and runs use the repository cgroup cap (6G, `CARGO_BUILD_JOBS=1`).
