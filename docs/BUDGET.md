# `actinv budget` — impurity budgets for clearance

`actinv budget BUDGET.json [OUT.json] [--no-verify]`

Given a component's flux and irradiation history, `budget` answers: **how much of each impurity can
the material contain and still clear** (IAEA clearance index `CI = Σ A_n/L_n ≤ 1`) at the cooling
times you care about?

## How it works

At fixed flux, coupled-mode activation is exactly linear in the initial composition (P75b: 1e-11).
So the command solves each element once, as a pure material, and the CI of any mix is the
weighted sum of those runs. The balance element (usually Fe) takes up the remainder to 100 wt%.
With `r_e` the CI of pure element `e`, per target step:

- `CI(w) = Σ_e w_e · r_e / 100`
- `dCI/dw_i = (r_i − r_balance) / 100` (per wt% of impurity `i`)
- **spec margin factor** `k = (1 − CI_matrix_only) / Σ_i w_i^spec · dCI/dw_i`: scaling every
  impurity by `k` puts CI exactly at 1
- **single-impurity limit, others at spec** `w_i* = w_i^spec + (1 − CI_at_spec) / (dCI/dw_i)`:
  how far one impurity may rise while the rest stay as specified
- **sole-impurity limit** `w_i^sole = (1 − CI_matrix_only) / (dCI/dw_i)`: the limit when it is the
  only impurity present. This one exists even when the specification as a whole cannot clear.

A limit is reported only when it exists: a status says `no clearance-index response` (the impurity
is no worse than the balance element) or `infeasible` (the other constituents alone exceed CI = 1).

**Every number is checked.** Unless `--no-verify` is given, the command re-solves the full material
at the as-specified composition, at `spec × k` and at every single-impurity limit, and reports the
predicted and solved CI side by side (`verification.max_rel_dev`, tolerance 1e-6). Sole-impurity
limits are verified the same way. If any point
misses, the command exits 3 after writing its output.

## Input: `actinv-budget-1`

```json
{
  "schema": "actinv-budget-1",
  "base_spec": "component_run.json",
  "balance": "Fe",
  "matrix": {"Cr": 9.0, "W": 1.1, "Mn": 0.4, "V": 0.2, "Ta": 0.12, "C": 0.11, "Si": 0.05},
  "impurities": {"N": 0.03, "Co": 0.005, "Nb": 0.001, "Ni": 0.005},
  "targets": [6, 7],
  "limits": "optional/actinv-clearance-limits-1.json"
}
```

- `base_spec`: an `actinv-spec-1` run (path relative to this file, or an embedded object). Its
  flux, schedule, data and temperature are used; its `material` is replaced. The command forces
  `mode: coupled`, `prune: reach` (the configuration proven additive) and records that.
- `targets`: one-based result step numbers, as in `actinv clearance`.
- Refused because the response would not be linear in composition: `self_shielding`,
  `uncertainty`, `options.screen`, schedule `feed`.

## Output: `actinv-budget-result-1`

Per target: `ci_matrix_only`, `ci_at_spec`, `spec_margin_factor_k` and its status, per impurity
the gradient, contribution at spec and single limit with status, the top CI nuclides, and the
**uncovered activity** (nuclides with no clearance limit in the table, e.g. Ag-108m in the bundled
IAEA table), which is reported, never dropped. `summary` gives, per impurity, the tightest limit
across targets `alone` and with `others_at_spec`, each with the step that binds it, and
`summary.joint`, the smallest spec margin factor `k` across targets: scale every impurity by `k`
and the material clears at every target.

Reading it: when `others_at_spec` says infeasible, the specification as written cannot clear. Use
`joint` to see how far it must tighten, and `alone` to see which impurity dominates.
`verification` holds the full-solve checks.

Cost: one solve per element plus the verification solves; the prepared data are shared across
them. See `results/p79_verdict.json` and `results/p79a_verdict.json` for the acceptance record.

**Limitation:** an impurity whose products have no limit in the table (silver: Ag-108m is not in the
bundled IAEA table) shows `no clearance-index response`; its activity is listed as uncovered, not
cleared. Check `top_uncovered_nuclides_Bq_g_at_spec` before trusting such a status.
