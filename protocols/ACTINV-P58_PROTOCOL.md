# ACTINV-P58 protocol — isomer-resolved uncertainty + channel introspection

Status: draft → sealed at first G0 run. Lane class: **light compute** —
pure emit-layer aggregation over quantities the run already computes
(covariance quadratic forms, pathway attributions). No new solves, no new
collapses.

Approved 2026-09-26 in the "light lanes only" direction: the head-to-head
work (results/FNS_ENDF8_HEADTOHEAD.md) showed ACTINV's demonstrated
structural advantage is isomer-channel completeness — Ta-180m, W-185m1,
Y-89m1, Pb isomer branches where the incumbent chain is ground-state-only.
This lane makes that advantage *self-describing*: every banded run can
emit how much of each response's value and band is carried by isomer
channels, and which channels. Nobody ships isomer-resolved UQ.

## Input

Any `actinv run` spec carrying an `uncertainty` block. A new optional
field rides beside `voi`:

```json
"uncertainty": {
  "covariance": {...}, "responses": [...], "channels": [...],
  "voi": {"top": 20},
  "isomer": {}
}
```

`"isomer": {}` (or an object later carrying knobs) opts in; absent →
emitted record byte-identical to pre-P58 output, same discipline as P50's
`voi` option.

## Output — per requested response, per step

Inside `uncertainty.responses[response].isomer`:

```json
{
  "variance_shares": {
    "isomer_product_channels": <f64>,   // Σ variance_share over LFS>0 xs
                                        //   params + yield params with
                                        //   product_LISO>0
    "isomer_target_channels":  <f64>,   // Σ over target_LISO>0 xs params
    "isomer_decay_constants":  <f64>,   // Σ over decay params on liso>0 nuclides
    "ground_channels":         <f64>,   // all remaining covered xs params
    "unranked_l2_sensitivity": <f64>    // L2 sensitivity mass outside covariance coverage
  },
  "top_isomer_channels": [
    {"parameter": {…full SensitivityParameter…},
     "channel_label": "W186(n,2n)→W185m1",
     "sensitivity": …, "variance_share": …, "share_fraction": …}
  ]
}
```

- Shares are fractions of the response's total propagated variance —
  `variance_share / total_variance`, identical arithmetic to `voi`'s
  `share_fraction` (P50 machinery: `sᵢ · (Σs)ᵢ` per covered parameter).
  Denominators and rounding match `voi` exactly; when the response has
  no finite positive total variance, shares are null and the ranking
  falls back to |sensitivity| exactly as `voi` does.
- `top_isomer_channels` is capped at `isomer.top` when the spec sets it,
  else `voi.top` when `voi` is present, else 20; `top` validates 1–256.
  Only parameters in the isomer buckets appear.
- `channel_label` renders target nuclide + MT + product isomer from the
  parameter record only (`name_of(target_za, target_liso)`, `MT`,
  `name_of` on the residual when resolvable; ZAP/LFS carried raw).

## Output — step-level pathway summary (value side)

When the run emits `pathways` (trace mode), the document gains a
step-aligned top-level array `isomer_pathway_shares` — one record per
step, emitted only when `uncertainty.isomer` is requested:

```json
"isomer_pathway_shares": [{
  "status": "emitted",
  "atoms_through_isomer_products_per_g": <f64>,
  "share": <f64>,
  "top_isomer_products": [{"first_product": "W185m1", "atoms_per_g": …,
                           "share_of_isomer_flow": …}]
}]
```

Chains are aggregated by `first_product` isomer-ness (name suffix `mN`,
N>0) — the exact convention `name_of` uses. `share` is against the summed
pathway attribution at that step; `pathway_closure` already bounds the
attribution total against the main solve. Runs without pathways emit the
field as `"unavailable"` with a named reason, never silence.

## What this lane is NOT

- Not per-isomer σ (a separate solve per channel — not light).
- Not new propagation machinery: the partition sums `variance_share`s
  already computed for `voi`; when `voi` is absent the same quadratic
  forms are computed for the partition only.
- Not decay-data or chain changes.

## Rejections (fail closed)

- `uncertainty.isomer` on a spec without `uncertainty` — schema parse
  rejects (field lives inside the uncertainty block).
- Non-object `isomer` value → parse error.
- Unbanded spec → field never reaches emit; nothing emitted.

## Gates

- **G0** — seal artifacts + protocol hash.
- **G1 mechanics** — banded fixture spec with isomer channels: shares
  present, sum to ≤1 within fp tolerance; isomer buckets disjoint;
  unbanded spec unchanged byte-identical; parse rejections fire.
- **G2 exactness** — independent re-derivation of every share from the
  emitted sensitivity maps + the same Σ convention (checker rebuilds the
  covered covariance from the spec's sidecar); channel labels recomputed.
- **G3 demo** — corpus banded run on the isomer-heavy FNS cases from the
  head-to-head (W, Pb, Y): emitted shares on the unit simplex, isomer
  channels ranked where isomer physics is known to matter.
- **G4 determinism** — byte-identical reruns.
- **G5 checker** — reparse the emitted record; recompute shares
  independently; planted mutations (share scaled, bucket mislabelled,
  pathway share moved) detected.

## Cost statement

Pure aggregation over the existing `voi` quadratic forms — O(covered) per
response, µs-scale. Pathway aggregation O(pathways). The corpus run is
the same wall time as the P50–P55 banded runs (~30 s class).
