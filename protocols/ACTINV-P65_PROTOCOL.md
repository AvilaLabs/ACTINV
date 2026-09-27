# ACTINV-P65 — certified screening (`options.screen`)

## Question

A full banded solve on a real 709-group case costs ~5 s cold (~1.6 s
warm), dominated by the CRAM/sensitivity solve over the reachable
chain. Reachability pruning already bounds every dropped state's
possible atom inventory (`rate_pruning.dropped`), but the bound is
ledgered, not *certified back onto the response*. Can a screening tier
raise the prune threshold, propagate an honest bound onto every emitted
response edge, and produce a result that is *provably* a superset of
the full-fidelity band — at a measured fraction of the cost?

## Deliverable

New spec option `options.screen = {"bmin_atoms_per_g": X}` (X finite,
≥ 0, required inside the block). When present the prune stage uses X
as the atom bound (overriding `options.bmin_atoms_per_g`), and the
result gains a `screen` block — the screening certificate:

```
"screen": {
  "bmin_atoms_per_g": X,
  "dropped_states": n, "kept_states": m,
  "removed_heat_W_per_g_bound": H,        // Σ dropped_atoms_bound·λ·E
  "removed_activity_Bq_per_g_bound": A,   // Σ dropped_atoms_bound·λ
  "certified": {                          // per step index
    "3": {"heat.total":   {"lower": lo − H, "upper": hi + H + floor_i},
          "activity:Mn57":{"lower": lo − A_n, "upper": hi + A_n},
          ...}
  }
}
```

For each `uncertainty.responses` entry, the certified interval takes
the emitted band edges (or nominal when the spec is unbanded — honestly
labelled `edge: "nominal"`) and widens by the dropped-state bound for
that response, plus the step's `heat_bound_from_below_floor_W_per_g`
on heat. Per-nuclide `activity:NAME` bounds count only dropped states
that *are* NAME. Responses outside the supported set get no certified
edge — the certificate never silently asserts a bound it did not
compute.

## Honesty rules

- The bound is the same quantity the ledger already emits
  (`rate_pruning.dropped`); the certificate just maps it onto
  responses. `screen` with `prune: "none"` is an error — a zero bound
  from disabled pruning is not a certificate.
- The certificate makes no accuracy claim about what the prune *kept*;
  kept-state fidelity is the solver's normal machinery.
- `screen` emits `certified` intervals only; it does not alter the
  band edges themselves, so the screened run's own numbers stay
  attributable.

## Gates

- **G1 mechanics** — fixture screened spec: block fields present,
  certified intervals = edge ± bound bit-exact, rejected with
  `prune: none`, larger `bmin` ⇒ fewer kept states, larger bound.
- **G2 exactness** — checker rebuilds the dropped bounds from the
  `rate_pruning` ledger + decay table and verifies every certified
  interval arithmetic.
- **G3 demo** — real W FNS case: screen at `bmin=1e-4` vs the
  `bmin=1e-8` full run — every certified upper ≥ the full solve's
  band upper at every step (the superset claim, empirically), solve
  stage speedup measured and recorded.
- **G4 determinism** — byte-identical screened result.
- **G5 checker** — planted mutations (widened bound, swapped edge,
  forged certified value) caught on reparse.
