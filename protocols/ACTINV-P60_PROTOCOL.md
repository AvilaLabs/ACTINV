# ACTINV-P60 protocol — measurement-design report (Kalman-gain VOI)

## Claim

`uncertainty.voi` (P50) ranks parameters by the variance they *carry*.
Experiment design asks a different question: which measurement *removes*
the most response variance? For a correlated covariance the answers
differ — an anticorrelated parameter can carry little marginal share yet
anchor a large conditional reduction. The Kalman-gain (Schur-complement)
quantity

  ΔV_i = (Σ·s)_i² / Σ_ii            (per parameter, perfect measurement)

is the variance removed from the band by fixing parameter i, and for a
reaction block B it is

  ΔV_B = (Σ·s)_Bᵀ Σ_BB⁻¹ (Σ·s)_B    (perfect measurement of the whole
                                     reaction's parameters)

These quantities are computable from the collapsed covariance the run
already builds — zero additional solver or covariance work.

This lane adds `uncertainty.design`, emitting per-parameter and
per-reaction reduction rankings for each banded response. Nobody else
ships a banded measurement-design surface; the claim is a ranked
"measure this and the band shrinks by exactly this much" table.

## Change

`spec.uncertainty.design` (object, optional; absent → byte-identical
output). Fields:

- `top` — ranking size, 1–256; defaults to `voi.top` when present else 20.

Emission: each `ResponseUncertainty` gains `design`:

- `total_propagated_variance` — the variance the band was built on.
- `top_parameters` — ranked entries: channel, parameter record,
  sensitivity, `variance_reduction` (ΔV_i for MF=33 rows;
  `(s_i·σ_i)²` for decay/yield — identical to their share, since diagonal
  parameters have no covariance structure to condition on),
  `reduction_share` = ΔV / total (null when total is zero/nonfinite),
  `posterior_variance` = total − ΔV, and for diagonal channels
  `reduction_at_half_uncertainty` = 0.75·(s_i·σ_i)².
- `top_reactions` — MF=33 parameters grouped by target nuclide
  (`target_za`, `target_liso`), because covariance is per-(mat, MT):
  measuring a nuclide's file conditions its whole correlated block.
  Entries carry `target_za`, `target_liso`, `mts` (sorted distinct MTs),
  `covered_parameters`, `variance_reduction` from the block Schur solve,
  `reduction_share`, `posterior_variance`, and `status`
  (`emitted`/`ill_conditioned`).
- `unranked` — same convention as `voi`: sensitivity-bearing parameters
  with no coverage named honestly by count and L2 magnitude.

### Numerics

- Covered xs parameters form the dense collapsed `covariance_barn2`
  (n×n); per-parameter ΔV needs only row i's dot with s and the diagonal.
- Per-reaction ΔV solves Σ_BB x = (Σs)_B by dense Cholesky; blocks up to
  a few hundred params are trivial. Covered rows with a non-positive or
  non-finite diagonal are excluded from both the per-parameter table and
  the block membership — a zero-diagonal parameter is already perfectly
  known, so conditioning on it removes nothing. A singular/indefinite
  block emits
  `status: "ill_conditioned"` on that reaction entry — never a fake
  number. Round-off may yield tiny negative ΔV; values below
  −1e-9·|total| in magnitude are errors, smaller negatives clamp to 0
  with `negative_reduction_roundoff_removed` on the entry.
- Diagonal-channel (decay/yield) parameters get per-parameter entries
  only — no block grouping (no covariance to condition).

## Rejections (fail closed)

- `design` present without a surrounding `uncertainty` block — schema
  reject (nested, automatic).
- Non-object `design` value, unknown keys (deny_unknown_fields), or
  `top` outside 1–256 — reject.
- Unbanded specs emit no design report (the report needs the run's
  collapsed covariance).

## Gates

- **G0** — seal artifacts + protocol hash.
- **G1 mechanics** — P58-style synthetic fixture, extended so at least
  two MF=33 covered rows share an off-diagonal block (exercises the
  Schur complement over a genuinely correlated pair) plus a diagonal
  decay channel: ranked parameter and reaction entries present,
  reductions positive, `posterior + ΔV = total`, rejections fire.
- **G2 exactness** — independent Python recomputation of every ΔV from
  the emitted covariance inputs: per-parameter via `(Σs)ᵢ²/Σᵢᵢ`, block
  values via `numpy.linalg.solve` on the sub-block; reproduce the
  emitted total and ranking order bit-for-bit under the Rust fold
  convention.
- **G3 demo** — real FNS corpus case (W and Pb legs from P58): report
  the top measurement targets and confirm the anticorrelation effect
  (a parameter whose ΔV exceeds its marginal share).
- **G4** — byte-identical repeated runs.
- **G5 checker** — independently reparse, recompute ΔV values, and catch
  planted mutations in reductions, rankings, and posterior totals.
