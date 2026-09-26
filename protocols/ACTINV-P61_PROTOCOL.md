# ACTINV-P61 — Chain-completeness audit surface

## Question

Can the run emit a single consolidated verdict on how complete the
assembled depletion chain is — every defect class the builder/chain layer
already detects, each with its count, its summed rate coefficient, its
fraction of the channel's total production flow, and the top contributors —
so a user reads one block instead of hand-aggregating ~10 scattered ledger
fields?

This is an emit-layer lane over existing ledger machinery: every defect
map (`products_no_decay_data`, `products_unmapped`, `isomer_fell_back_to_ground`,
`fission_no_yields`, `fission_product_leakage`, `daughters_missing`,
`sf_branches`, `branching_sums_off_unity`, `targets_absent_from_decay_lib`,
`bulk_production_dropped`, `n_fallback`, `negative_atoms_zeroed`) is
already quantified where it is detected. Nothing is recomputed;
denominators are the channel's own summed triplet rates.

## Deliverable

- `options.outputs` accepts `"audit"`.
- When `outputs` explicitly lists `"audit"` (explicit membership only —
  an absent `outputs` list emits everything *except* audit, keeping the
  byte-identical contract), the ledger gains a `completeness` object:

```json
"completeness": {
  "status": "complete" | "incomplete",
  "reaction_channel": {
    "total_production_rate_coefficient_per_s": <Σ positive reaction triplet rates>,
    "defects": [
      {"class": "<name>", "instances": n,
       "rate_coefficient_per_s": x,
       "fraction_of_channel_flow": x/total (null if total <= 0 or nonfinite),
       "largest": [{"name": "...", "rate_coefficient_per_s": ...} ≤3]}
    ]
  },
  "decay_channel": {
    "total_decay_rate_coefficient_per_s": <Σ positive decay triplet rates>,
    "defects": [ same shape; rate is λ·BR coefficients ]
  },
  "unquantified": [{"class": ..., "instances"/"count"/"names": ...}]
}
```

- `status` is `"complete"` only when every quantified defect list is
  empty and every unquantified count is zero.
- Defect classes emitted (reaction side): `products_no_evaluated_decay_data`,
  `products_unmapped_to_leakage`, `isomer_fell_back_to_ground`,
  `fission_no_yields_to_leakage`, `fission_yield_products_to_leakage`
  (fraction on yield-sum basis), `decay_nuclides_from_fallback` (count
  only — from fallback is coverage-by-substitution, named not quantified).
- Decay side: `decay_daughters_missing`, `spontaneous_fission_branches_to_leakage`
  (count), `decay_branching_sums_off_unity` (instances + worst sum),
  `targets_absent_from_decay_library` (names, unquantified).
- `unquantified` names classes present as counts/names with no rate
  coefficient: `bulk_production_dropped` count, `negative_atoms_zeroed`
  per-step counts, `composition_isotopes_absent_from_decay_library` names.
- Defects sorted by `rate_coefficient_per_s` descending; `largest` holds
  the top 3 entries of each map by rate.

## Honesty rules

- Rates are production/decay *coefficients* (per parent atom per second),
  not actual step flows — the emitted key names say so. Channel totals
  count only positive-rate triplets (channel inflow); the diagonal
  outflow terms (−λ) live in the same vectors and would cancel it.
- `fraction_of_channel_flow` is null when the channel total is
  nonpositive or nonfinite — never a fabricated share.
- Absent `"audit"`: byte-identical output (G4).
- No new computation is invented; every number comes from the same ledger
  maps the existing `ledger` block already emits.

## Gates

- **G1 mechanics**: fixture run with `"audit"` — completeness block
  present; fixture's planted defect (no-decay daughter, isomer fallback,
  fission leak as applicable) appears with the right class/rate; sorting
  order; status honest; parse rejection of `"audit"`-adjacent unknown
  output values still fires.
- **G2 exactness**: independent rebuild of every emitted coefficient from
  the emitted ledger maps and the run's own triplets denominators.
- **G3 real data**: FNS W/Y real runs — completeness present, status
  reflects the real defect inventory of those libraries.
- **G4 byte-identical** without `"audit"`.
- **G5 checker**: full reparse; planted mutations caught (status flip,
  rate change, fraction change, missing largest entry).
