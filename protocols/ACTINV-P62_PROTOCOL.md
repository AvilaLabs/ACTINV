# ACTINV-P62 — Persistent preparation in `actinv optimize`

## Question

Can candidate evaluations in an `actinv optimize` session reuse the
file-derived prepared inputs (activation library, decay tables, chain,
covariance preparations) instead of paying preparation per candidate?

Measured driver: on the TENDL-2025 709-group library a cold solve costs
314.5 s where the warm path costs 273.3 s + 1.2 ms fingerprint — ~41 s
(13%) per candidate is preparation + cold-content fingerprinting that the
existing single-slot `PreparedCache` already knows how to skip.

## Deliverable

- `evaluate_candidate` in `crates/actinv-cli/src/optimize.rs` takes a
  `&mut PreparedCache` and calls `run_with_cache`, sharing one cache
  across the whole search (`lhs` fill + coordinate descent).
- Each ledger row gains `constraints.prepared_cache` =
  `{hit, fingerprint_ms}` — an observability field, not a verdict.
- The winner re-verification deliberately keeps a fresh `run()` call:
  it is an independent re-execution check, not part of the search.

## Honesty rules

- `PreparedCache` fingerprints every preparation-relevant input (file
  references + content sha256 + prep options + spectrum vector). A design
  axis that changed any of them misses and reprepares — reuse can never
  serve stale inputs.
- Prepared inputs depend only on data files and non-axis spec fields, so
  composition/material/schedule axes cannot perturb them.
- Cached-vs-uncached results are required to be bit-identical: the cache
  is a reuse path over the identical solver, not an approximation.

## Gates

- **G1 mechanics**: a fixture optimize run (≥2 solved candidates on the
  same data files) — first solved eval misses, subsequent solved evals
  report `hit: true`; ledger rows carry the observability block; resumed
  evals don't fabricate a hit.
- **G2 identity**: run the same two-candidate search twice — once with
  the shared cache, once forcing fresh prepares (per-eval `run`) — every
  objective/violation bit-identical; ledger equality modulo wall_s and
  the prepared_cache field.
- **G3 demo**: real optimize spec (`examples/optimize_ra_steel` reduced
  to 3 points) — evals ≥2 report warm hits; wall_s visibly below the
  cold first eval.
- **G3-measurement**: the cold/warm split itself is measured on the real
  TENDL-2025 709-group library through the `worker` path — cold 314.5 s
  vs warm 273.3 s + 1.2 ms fingerprint (≈41 s, 13%, saved per candidate).
- **G5 checker**: reparse the ledger — every solved eval's
  `prepared_cache` block present and boolean; first solved eval is
  `hit:false`; planted flips caught.
