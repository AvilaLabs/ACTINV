# actinv optimize — chance-constrained design search

`actinv optimize` answers *what should I build* rather than *what does this
do*. Given a base activation problem and a bounded design box, it searches
composition, flux and schedule axes with a seeded, derivative-free optimizer,
solves **every** candidate through the identical `actinv run` solver path,
and ranks candidates by feasibility and objective — where feasibility can be
declared on propagated nuclear-data **band edges** (chance constraints), not
just nominal values.

```
actinv optimize OPTSPEC.json [OUTDIR] [--resume]
```

Outputs in `OUTDIR` (default `optimize_out/` next to the optspec):

- `optimize_ledger.jsonl` — append-only, one row per attempted evaluation
  (parameter vector, generated spec SHA-256, status, objective, per-constraint
  edges and margins, feasibility, wall time). Every attempt is recorded;
  failures are never dropped.
- `candidates/eval_NNNN.json` — the generated `actinv-spec-1` document for
  each executed evaluation.
- `optimize_result.json` — ranked candidate table, `best_feasible`,
  `infeasible` flag, and the winner re-execution record (the winning spec is
  re-solved and must reproduce the ledgered objective bit-for-bit).

## optspec (`actinv-optimize-1`)

```json
{
  "schema": "actinv-optimize-1",
  "base_spec": "base_spec.json",
  "design_axes": [
    {"kind": "composition_fraction", "element": "NI", "bounds": [0.0, 3.0]},
    {"kind": "flux_scale", "bounds": [0.5, 2.0]},
    {"kind": "step_dt", "step": 0, "bounds": [60.0, 3600.0]}
  ],
  "objective": {"response": "activity.total", "time_s": 3180727000.0,
                "edge": "nominal", "direction": "min"},
  "constraints": [
    {"name": "heat_1y_upper95", "response": "heat.total",
     "time_s": 30727000.0, "edge": "normal_upper", "sense": "le",
     "limit": 1.65e-12}
  ],
  "optimizer": {"algorithm": "lhs_coordinate", "seed": 49,
                "init_points": 16, "refine_points": 8}
}
```

- `base_spec` — path relative to the optspec file; an ordinary
  `actinv-spec-1` document.
- `design_axes` (1–6):
  - `composition_fraction` sets the element's wt% and renormalizes all
    *non-axed* elements proportionally so the composition sums to 100.
  - `flux_scale` multiplies `spectrum.total`.
  - `step_dt` sets `schedule[step].dt` in seconds.
- `objective.response` — an uncertainty-response selector: `heat.total`,
  `heat.alpha|beta|gamma`, `activity.total`, `activity:<nuclide>`.
  `time_s` selects the step whose cumulative `t_s` is nearest (1e-3 relative
  tolerance). `edge` ∈ `nominal | normal_lower | normal_upper |
  conservative_lower | conservative_upper`; `direction` ∈ `min|max`.
- `constraints` — each entry is one of two kinds:
  - `kind: "response"` (default): `response`/`time_s`/`edge` selector fields
    plus `sense: le|ge` and `limit`. A band edge requires the base spec's
    `uncertainty` block; without it the constraint is ledgered
    `constraint_not_computable` and the candidate is infeasible — never
    silently skipped.
  - `kind: "axis"`: `{"name","kind":"axis","axis":i,"sense","limit"}` bounds
    design-axis `i` directly (e.g. `ge 1.0` forces Ni ≥ 1 wt%). Evaluated on
    the parameter vector before solving — a violated axis constraint is
    ledgered `infeasible_by_axis` with the candidate spec still written, but
    consumes no solver run and leaves response constraints
    `constraint_not_evaluated`.
- `optimizer` — `lhs_coordinate`: seeded Latin-hypercube fill
  (`init_points`) then coordinate-descent refinement from the best-ranked
  point (`refine_points`, initial step `refine_step_fraction` × axis width,
  default 0.25). `lhs_corners_coordinate` (post-P49 variant) spends the
  first `min(2^d, init_points)` evaluations on the box corners in
  binary-counting order before LHS-filling the remainder — prefer it when
  optima may sit on bounds (e.g. zero-dopant corners). Total evaluations
  ≤ 64.

Ranking: feasible candidates first, then total positive constraint
violation, then objective value. A candidate is feasible iff every
constraint violation ≤ 0.

## Semantics worth knowing

- **Chance constraints**: `edge: normal_upper` on a `le` constraint means
  "the 95% (or declared `confidence_level`) upper edge must satisfy the
  limit" — a nominal-feasible point can be band-infeasible. This is the
  differentiator versus point-valued optimization.
- **Identical solver path**: candidates run `actinv_core::run::run`
  in-process, the same function `actinv run` calls — no surrogate, no
  reduced numerics.
- **Determinism**: identical seed + optspec ⇒ identical candidate sequence
  and outcomes (wall times aside).
- **Resume**: `--resume` skips parameter tuples already present in the
  ledger, so a killed campaign can be continued.
- **Honest infeasibility**: if no candidate satisfies the constraints the
  result is `infeasible: true` with no `best_feasible` — the optimizer
  never returns the least-bad violator as a winner.
- The search is bounded and derivative-free — it reports the best ledgered
  candidate, not a proof of global optimality.

## Cost

Each candidate with `uncertainty` bands costs a full solve plus covariance
propagation — the demonstration campaign (~24 evals on the 13-step RA-steel
problem) runs in hours, not seconds. Optimization is a **batch** tool until
the persistent-worker path lands; do not wrap it in an interactive loop.

A complete worked example lives in `examples/optimize_ra_steel/`:

- `opt.json` — the P49 demonstration spec (frozen): minimize 100-year
  activity under a 95%-edge decay-heat limit at 1 year and a nominal Nb-94
  limit at ~100 years. 24 evaluations; the winner sits at Ni=0/Mo=0/Nb=0.113
  wt%. Note the zero-dopant corner beats it — `lhs_coordinate` never
  sampled the corner within its budget, an honest illustration that the
  ledger, not a claimed optimum, is the deliverable.
- `opt_v2.json` — the realistic variant: axis constraints force Ni ≥ 1.0
  and Mo ≥ 0.3 wt% (austenitic-stability / strength proxies) and the
  `lhs_corners_coordinate` algorithm covers the box corners first, so the
  constrained optimum (Ni=1.0/Mo=0.3 boundary with Nb set by the Nb-94
  response constraint) is found instead of merely bounded.
