# Ledger semantics

Every ACTINV result carries a `ledger` object. Empty arrays are intentional: a category is always present so callers
can distinguish “checked and empty” from “not evaluated.” Rates are per target atom unless a field states otherwise.

## Model and material

- `mode`, `max_burnup_fraction` — selected trace/coupled formulation and the value used by `auto`.
- `composition_basis`, `composition_input_total`, `composition_weight_percent_total`,
  `composition_not_summing_to_100` — how the input composition was interpreted. The weight-percent total is `null`
  for other bases.
- `composition_elements_unknown` — material keys without natural-isotope mass/abundance data.
- `composition_isotopes_absent_from_decay_library` — initial isotopes which cannot enter the decay network.
- `bulk_production_dropped` — count of reactions whose product is held as part of the constant bulk in trace mode.
- `bulk_background_heat_W_per_g` — decay heat from a radioactive constant-bulk component.

## Nuclear-data loss and fallback

- `products_no_evaluated_decay_data` — reaction products absent from primary and fallback decay data; production is
  sent to the explicit leakage state.
- `products_unmapped_to_leakage` — activation-library rows whose product could not be mapped.
- `fission_no_yields_to_leakage` — fission rate sent to leakage because fission yields are P9.
- `isomer_state_absent_from_decay_library_used_ground` — requested product isomer replaced by evaluated ground state.
- `targets_absent_from_decay_library` — activation-library targets absent from the assembled decay network.
- `decay_daughters_missing`, `spontaneous_fission_branches_to_leakage` — missing/unsupported decay daughters routed to
  leakage.
- `decay_nuclides_from_fallback` — number of records supplied by the fallback sublibrary.
- `library_convergence_flags` — grid-sensitive activation evaluations affecting an initial material isotope.
- `library_target_limitations` — per-target builder ledger entries (for example unsupported resonance formalisms)
  affecting an initial isotope. These two categories repair the v0.1 promise that library-index guards propagate.

`steps[].leakage_atoms_per_g` is the accumulated amount routed to leakage; the categories above explain why.

## Numerical resolution and pruning

- `negative_atoms_zeroed_per_step` — negative CRAM round-off set to zero, with the removed amount.
- `numerical_floor` — CRAM's absolute floor (`alpha0 × max population`), the maximum number of sub-floor states and a
  conservative heat fraction for them. Each step also carries the underlying counts and heat bound.
- `rate_pruning` — threshold, every dropped nuclide's atom/feed bound, and `removed_heat_W_per_g_bound`. Reachability
  and `none` pruning produce an empty dropped list.

## Photon accounting

`photon_spectra` is one entry per result step and contains:

- `energy_normalized_spectra` — nuclide, evaluated energy moment, `E_EM`, relative raw discrepancy and the explicit
  transport-source scale;
- `nuclides_with_em_energy_but_no_photon_spectrum` — no invented photons, plus the omitted gamma-power bound;
- `group_underflow_*` and `group_overflow_*` — photon rates and powers outside the selected group structure;
- `response_excluded_power_W_g`, `response_missing_elements` — why a contact-dose response is incomplete.

The step source repeats the user-facing consequences as `unrepresented_gamma_power_W_g`,
`represented_gamma_power_fraction`, `ungrouped_power_W_g` and `dose_response_power_coverage`.

## Mesh rebin accounting

Every `actinv-mesh-result-1` cell carries a separate `rebin` object before its ordinary run result:

- `method` — `copy` for byte-identical boundaries or `equal-flux-per-unit-lethargy` for logarithmic overlap;
- `source_total`, `destination_total` — compensated input and in-library flux totals;
- `underflow`, `overflow` — physical flux outside the activation-library energy range, excluded rather than folded;
- `relative_closure` — relative disagreement of `destination + underflow + overflow` with `source` (bounded to 1e-12).

The mesh footer sums these quantities again in canonical cell order and reports the minimum/maximum independently
pruned state counts. Import-source total checks remain in the canonical flux footer and its upstream provenance.

## Assembly diagnostic

`assembly` records the bulk isotope count, matrix triplet counts, activation-library row count, decay-chain size and
total neutron flux. A ledger with entries is not intrinsically a bad result; it is a result whose limitations are
machine-readable rather than hidden.
