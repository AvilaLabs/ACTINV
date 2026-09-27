# ACTINV-P67 — Pareto frontier under declared band edges (C5)

## Question

P56 certifies a single-objective winner at band edges. Design trade-offs
are multi-objective (cost vs margin vs dose). Can the optimizer emit a
*nondominated certified frontier* — a set of designs, each feasible at
its declared constraint edges, no one better than another on all named
objectives — for free per evaluation (extra objectives cost a field
lookup, not a solve)?

## Deliverable

`optspec.objectives` (≥2 entries) names additional
`{response, time_s, edge, direction}` objectives alongside the primary
`objective` (which still drives the seeded LHS + coordinate-descent
ranking). Every evaluated candidate gains an `objectives` map in its
ledger row — read off the same solve, zero extra runs. The result gains:

```
"pareto": {
  "objectives": [{name, response, time_s, edge, direction}, ...],
  "n_evals_feasible": f, "n_front": n,
  "front": [{x, param_digest,
             "objectives": {"name@t": value, ...},
             "certified": true}]    // certified ⇔ violation_sum ≤ 0 at
}                                   // declared edges (same P56 rule)
```

A front point is nondominated among *feasible* evals: no feasible eval
is better-or-equal on every listed objective and strictly better on one.
Infeasible evals never reach the front — that is the band-edge honesty:
the frontier is the certified feasible set's boundary, not a nominal
envelope.

## Honesty rules

- `objective` remains the sole search driver; `objectives` only
  *measure* and *filter*. The front is a property of the evaluated set —
  the result honestly states `n_evals` coverage.
- Named `edge` per objective — a banded edge on the front carries the
  same meaning as a banded constraint (covariance set named in
  `certification`).
- No extrapolation between evals: the front is the evals' nondominated
  subset, not a fitted curve.

## Gates

- **G1 mechanics** — fixture optspec with 2 objectives: ledger rows carry
  `objectives`, front members nondominated, certified=feasible, invalid
  configs (len<2, bad direction) rejected.
- **G2 exactness** — checker re-derives the front from ledgered
  objectives + violations bit-exactly.
- **G3 demo** — real-data two-objective run (heat vs an activity
  constraint-line objective), front emitted with certification.
- **G4 determinism**, **G5 checker** — byte-identical; planted front
  mutations caught.
