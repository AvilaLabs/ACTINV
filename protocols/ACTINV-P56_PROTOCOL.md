# ACTINV-P56 — Certified optimization under uncertainty

## Question

P49 already ran banded constraints (the RA-steel campaign ledger shows
candidates flipping feasible→infeasible on `normal_upper` edges). What
the result never says formally is the thing no incumbent can emit:
*the winner satisfies every response constraint at its declared band
edge under the named covariance set* — and whether the nominal search
would have over-certified it.

## Deliverable

Two additions to `actinv optimize`, both over the identical run path:

1. **Per-constraint nominal margin for free** — when a response
   constraint selects a banded edge, the ledger detail gains
   `nominal_edge` (and `nominal_violation`), read from the same solved
   step. Zero extra solves: the nominal value sits beside the band.

2. **`certification` block in `optimize_result.json`**:

```json
"certification": {
  "statement": "eval N satisfies <k> response constraints at their
                declared band edges under <confidence> over <covariance>",
  "confidence_level": <from base spec uncertainty, or null>,
  "covariance": {"path": ..., "sha256": ...} | null,
  "winner": {
    "eval_id": n,
    "objective": {"value": ..., "edge": "...", "response": "..."},
    "constraints": [
      {"name": ..., "edge_rule": "normal_upper", "edge_value": ...,
       "nominal_edge": ..., "limit": ..., "sense": ...,
       "margin_fraction": (limit - edge)/|limit| }
    ]
  },
  "nominal_would_overcertify": <count of ledgered candidates whose
    nominal edge satisfies every banded response constraint while the
    banded edge violates at least one>
}
```

`winner` is `null` (and `statement` says so) when no candidate is
feasible under the declared edges — an honest "the box contains no
certifiable design" outcome.

## Honesty rules

- The certification names the edge rule and confidence from the spec —
  no invented guarantees; `conservative_*` edges stay labelled
  conservative.
- `nominal_edge` is emitted only when the constraint's edge is banded
  (nominal-edge constraints already carry the same number).
- `margin_fraction` uses the banded edge, never the nominal.
- Ledger schema is additive; rows without `nominal_edge` are pre-P56.
- Winner re-verification stays a fresh `run()` and now checks the
  constraint edges too, not just the objective.

## Gates

- **G1 mechanics**: fixture banded search — `nominal_edge` present iff
  edge is banded; certification block shape; winner null on an
  all-infeasible spec; statement text accurate.
- **G2 exactness**: independently recompute every certified margin and
  violation from the emitted edge/limit/sense; `nominal_would_
  overcertify` recomputed from raw rows.
- **G3 real data**: reduced RA-steel banded run (3 evals, release build)
  — certification carries the real covariance identity; margins are
  finite, honest values.
- **G4 determinism**: two identical optimize runs → identical ledgers
  modulo `wall_s`/`fingerprint_ms`.
- **G5 checker**: reparse ledger + result; planted mutations (margin
  flip, overcertify count, certification statement) caught.
