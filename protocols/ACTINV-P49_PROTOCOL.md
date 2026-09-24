# ACTINV P49 — chance-constrained activation design optimization

Opened 2026-09-24. Executes the roadmap's P49 row: `actinv optimize` — a bounded,
seeded, derivative-free optimizer over a declared design box, evaluated through
the unchanged qualified solver path, with objectives and feasibility constraints
evaluated on propagated nuclear-data bands. The deliverable is a ranked,
fully-ledgered candidate table, not a point answer.

This phase adds a new product path (`actinv optimize`) and a new spec document
kind (`actinv-optimize-1`). It changes no solver numerics, no existing spec
schema, and no default data. Prior verdicts are preserved byte-for-byte.

## Why this phase exists

P43–P48 made ACTINV answer accurately, quickly and broadly. P49 makes it
*decide*: a design question ("which composition minimizes dose at 1 y while
staying inside the activity band limit") is answered by a ranked candidate
ledger where every evaluation is recorded. The differentiating capability —
feasibility decided on a propagated band edge rather than a nominal value —
has no shipping equivalent in FISPACT-II, ALARA, OpenMC or SCALE.

## Frozen scope

P49 may:

- add `actinv optimize OPTSPEC.json [OUTDIR]` to the CLI, a new
  `actinv-optimize-1` document kind, the optimizer engine, controls, examples,
  tests and documentation under `crates/actinv-cli/`, `examples/`,
  `controls/`, `results/`, `protocols/` and `docs/`;
- read the base spec's `uncertainty` block to produce band edges per
  candidate; candidates are generated spec documents solved by the identical
  `run::run` path used by `actinv run`;
- record a complete append-only evaluation ledger (`optimize_ledger.jsonl`)
  with per-candidate spec sha256, parameters, responses, constraint margins
  and wall time.

P49 may not:

- use a surrogate model, emulator, reduced-precision solve, or any second
  numerics path — every candidate is a full solve of its generated spec;
- silently drop a failed or violated candidate from the ledger;
- return a constraint-violating candidate as "best" without the
  `infeasible` flag set and the violation margin reported;
- change solver defaults, tolerances, data files or the `actinv-spec-1`
  schema; the optimizer emits ordinary `actinv-spec-1` documents only;
- claim the search converged to a global optimum — the optimizer is a
  bounded seeded search; the report names it exactly that.

## Frozen definitions

**Design axes** (`design_axes`, 1–6 entries):

- `{"kind":"composition_fraction","element":"NI","bounds":[lo,hi]}` — sets the
  element to `x` wt%; every *non-axed* element is rescaled by
  `(100 − Σ axed)/Σ non-axed` so the composition sums to 100. Errors if no
  non-axed element exists or `Σ axed > 100`.
- `{"kind":"flux_scale","bounds":[lo,hi]}` — multiplies `spectrum.total`.
- `{"kind":"step_dt","step":i,"bounds":[lo,hi]}` — sets `schedule[i].dt`
  seconds; `flux` semantics unchanged (a cooling step if flux = 0).

**Objective** (`objective`): `{"response": R, "time_s": T, "edge": E,
"direction": "min"|"max"}` where `R` is an `uncertainty.responses` selector
(`heat.total`, `heat.alpha|beta|gamma`, `activity.total`, `activity:<nuclide>`,
`activity:*` rejected for objectives), `T` selects the step with cumulative
`t_s` nearest `T` (relative tolerance 1e-3, else error), `E ∈ {nominal,
normal_lower, normal_upper, conservative_lower, conservative_upper}`.

**Constraints** (`constraints`, 0–8 entries): `{"name": S, "response": R,
"time_s": T, "edge": E, "sense": "le"|"ge", "limit": L}`. `le` requires
edge ≤ L; `ge` requires edge ≥ L. A band edge requires the candidate spec to
carry the `uncertainty` block and the response in `uncertainty.responses`;
otherwise the constraint evaluation fails `constraint_not_computable` and the
candidate is ledgered infeasible, never silently skipped.

**Feasibility**: `violation_i` for `le` = (edge − L)/max(|L|, tiny); for `ge`
= (L − edge)/max(|L|, tiny). Candidate is feasible iff every violation ≤ 0.
Ranking key: (feasible first, then Σ positive violations ascending, then
objective value ascending for `min` / descending for `max`).

**Optimizer** (`optimizer`): `{"algorithm":"lhs_coordinate","seed":u64,
"init_points":N1,"refine_points":N2,"refine_step_fraction":F}`.

- Stage 1: seeded Latin-hypercube fill — `N1` points, per-axis strata
  permuted by a xorshift64* RNG seeded once from `seed`; the PRNG is defined
  in `optimize.rs` and is the only randomness in the phase.
- Stage 2: from the best-ranked point, coordinate descent — for each axis in
  declared order, probe `x ± step` clamped to bounds (step = F × axis width,
  F=0.25 initial), accept the best feasible-or-less-violating improvement,
  halve the axis step when neither probe improves, until `N2` evaluations are
  consumed or every axis step < 1e-3 × width.
- Total evaluations ≤ N1 + N2 (frozen cap 64).

**Ledger**: one JSON object per evaluated candidate, appended before the next
evaluation starts; `eval_id` monotone from 0; carries the full parameter
vector, the generated spec's sha256 (canonical JSON), run status, the
objective components, every constraint edge and margin, feasibility, and
`wall_s`. `actinv optimize --resume` skips parameter tuples already present
in the ledger (digest of the rounded parameter vector).

**Result**: `optimize_result.json` — optspec sha256, optimizer echo, ledger
row count, ranked candidate table (all evaluated candidates, best feasible
first), `best_feasible` block, `infeasible` flag, winner re-execution record:
the winning spec is re-run once in a fresh process evaluation and its
objective must reproduce the ledger value bit-for-bit.

## Gates

- **G0 seal**: this protocol, `optimize.rs`, command wiring, unit tests, the
  demonstration optspec and base spec, and all control scripts are committed
  and hash-pinned before any campaign evaluation runs.
- **G1 mechanics**: seeded determinism — two identical invocations produce
  byte-identical ledgers; axis application produces a spec that decodes as
  `actinv-spec-1` with sum-100 composition; ledger covers every attempted
  evaluation including failures; resume reproduces a partial ledger.
- **G2 controls**: (a) planted-optimum — the engine over a synthetic
  evaluator with a known vertex must rank it first; (b) infeasible box — all
  constraints unsatisfiable must produce `infeasible:true` and no candidate
  labeled best; (c) a band-edge constraint on a spec lacking `uncertainty`
  ledgered `constraint_not_computable`; (d) kill-mid-campaign then `--resume`
  produces a complete ledger with no duplicate eval_ids.
- **G3 campaign**: the frozen demonstration problem executed end-to-end on
  the release binary under the bounded scope; measured wall time recorded;
  winner re-run bit-identical.
- **G4 verdict**: producer emits `verdict_p49.json`; independent checker
  re-derives the ranking and feasibility from the raw ledger, re-verifies
  every candidate spec sha against its ledger row, and rejects planted
  mutations (reordered ranking, dropped violator, forged feasibility).

## Demonstration (frozen at G0)

Base spec `examples/optimize_ra_steel/base_spec.json`: reduced-activation
steel box — Fe balance, fixed Cr 9.0 wt%, axes `NI ∈ [0,3.0]`,
`MO ∈ [0,1.0]`, `NB ∈ [0,0.2]` wt%. FNS 5-minute irradiation schedule
extended with cooling steps to ~100 years (13 steps; cumulative
t_s = 3.0727e7 s at the 1-year leg and 3.1807e9 s at the final leg).
Full TENDL-2025 library + full covariance sidecar; `uncertainty.responses`
= `["heat.total","activity.total","activity:Ni63","activity:Nb94"]`,
confidence 0.95.

Objective: minimize `activity.total` at T = 3.180727e9 s, `edge: nominal`.

Constraints (both selected so the feasible set is nontrivial and the band
edge, not the nominal, does the work):

- `heat_1y_upper95`: `heat.total` at T = 3.0727e7 s, `edge: normal_upper`,
  `le 1.65e-12` W/g. Corner measurements: zero-dopant upper95 = 1.533e-12
  (feasible); max-dopant nominal = 1.108e-12 *passes nominally* but
  upper95 = 1.743e-12 fails the band — a nominal-feasible/band-infeasible
  corner, the showcase case.
- `nb94_100y`: `activity:Nb94` at T = 3.1807e9 s, `edge: nominal`,
  `le 1.0e-6` Bq/g. Zero-dopant = 0 (feasible); max-dopant = 1.968e-6
  (infeasible — Nb-94 is the long-lived clearance driver).

Landscape: `activity.total` at ~100 y spans 6.6e-6 (zero-dopant) to
1.9e-3 (max-dopant) nominal — ~200× dynamic range dominated by
Nb-91/Ni-63/Nb-93m/Mo-93, i.e., the dopant axes.

Optimizer: `seed=49`, `init_points=16`, `refine_points=8`,
`refine_step_fraction=0.25` — ≤24 candidate evaluations.

## Amendment clause

One append-only repair round is permitted for mechanical defects (input
formats, path plumbing, record completeness). No definition, ranking rule,
constraint semantics, axis semantics, optimizer algorithm, seed, bound or
inclusion predicate may change after the G0 seal; such a change fails the
phase.
