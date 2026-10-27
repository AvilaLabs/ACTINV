# Ledger semantics

Every ACTINV result carries a `ledger` object. Empty arrays are intentional: a category is always present so callers
can distinguish “checked and empty” from “not evaluated.” Rates are per target atom unless a field states otherwise.

## Model and material

- `projectile` — present for proton, deuteron and alpha results; omission is the byte-compatible neutron identity.
- `mode`, `max_burnup_fraction`, `max_burnup_optical_depth`, `max_burnup_nuclide` — selected trace/coupled
  formulation and the largest initial-nuclide reaction loss over multiplier-weighted exposure used by `auto`.
- `composition_basis`, `composition_input_total`, `composition_weight_percent_total`,
  `composition_not_summing_to_100` — how the input composition was interpreted. The weight-percent total is `null`
  for other bases.
- `composition_elements_unknown` — material keys without natural-isotope mass/abundance data. Specification
  validation now rejects such keys, so a validated run always reports this list empty.
- `composition_isotopes_absent_from_decay_library` — initial isotopes which cannot enter the decay network.
- `explicit_isotope_masses` — canonical nuclide, ZA/LISO, evaluated AWR, neutron-mass conversion, molar mass, resulting
  atoms per gram and primary/fallback source for each explicit isotope key. A literal unsolved `atoms_per_g` key may
  carry null AWR/source and zero diagnostic molar mass because no mass conversion was needed.
- `bulk_production_dropped` — count of reactions whose product is held as part of the constant bulk in trace mode.
- `bulk_background_heat_W_per_g` — decay heat from a radioactive constant-bulk component.

## Nuclear-data loss and fallback

- `products_no_evaluated_decay_data` — reaction products absent from primary and fallback decay data; production is
  sent to the explicit leakage state.
- `products_unmapped_to_leakage` — activation-library rows whose product could not be mapped.
- `fission_no_yields_to_leakage` — active-parent fission rate sent to leakage because no matching yield evaluation was
  supplied.
- `fission_unselected_to_leakage` — present only with `fission_yields.parents`: per parent with a yield file that the
  selection leaves out, its active fission rate (per atom, 1/s) sent whole to leakage. Never counted in
  `fission_no_yields_to_leakage`.
- `fission_yield_weights` — present only with `fission_yields.energy: "rate_weighted"`: one entry per selected parent
  and distinct spectrum: `parent`, `parent_ZA`, `parent_LISO`, `yields`, `energies_eV` (the tabulated incident
  energies), `weights` (the rate-weighted `w_k`, summing to 1; `null` with `fallback: "spectrum_average"` when the
  parent has no fission in the spectrum), `spectrum` (0 = base spectrum, `n` = the n-th distinct step spectrum),
  `steps` (1-based schedule steps using it) and `library_slot`.
- `fission_cumulative_overlap` — present only with `fission_yields.yields: "cumulative"`: per active parent (key
  `ZA_LISO`), `summed_cumulative_yield`, `overlap_yield` (summed yield of products with a direct decay precursor that
  also receives a positive yield from the parent), `overlap_share` (their ratio), `overlap_products` and
  `products_with_yield`. It uses the base spectrum's effective yields.
- `fission_yield_selection` — per-parent effective incident energy, evaluated bracket, interpolation weight, clamp
  decision, product count and effective independent-yield sum.
- `fission_yield_balance` — per-parent fission rate plus raw, chain-mapped and leakage yield sums. Mapped plus leakage
  equals the unnormalized MF=8/MT=454 source.
- `fission_yield_products_to_leakage` — each independent-yield product absent from the decay chain, with parent,
  product, yield and production rate per parent atom.
- `isomer_state_absent_from_decay_library_used_ground` — requested product isomer replaced by evaluated ground state.
- `targets_absent_from_decay_library` — activation-library targets absent from the assembled decay network.
- `decay_daughters_missing`, `spontaneous_fission_branches_to_leakage` — missing/unsupported decay daughters routed to
  leakage. A branch whose daughter resolves to the parent itself (an isomeric transition from the ground state to an
  isomer the library lacks) is counted here too.
- `unmodelled_decay_branches_to_leakage` — present only when nonzero; decay branches with an elementary mode the chain
  does not model, routed to leakage. A fission after another transition (RTYP 2.6, β-delayed fission) is counted here, not as spontaneous
  fission of the parent.
- `decay_branching_sums_off_unity` — present only when a radioactive state's positive branching ratios sum further than
  1e-5 from 1; maps the state to that sum. A shortfall is routed to leakage and an excess is scaled away, so decay
  neither destroys nor creates atoms. The default ENDF/B-VIII.0 data never triggers it; JEFF-3.3 and UKDD-2020 used
  as the primary decay library do.
- `decay_nuclides_from_fallback` — number of records supplied by the fallback sublibrary.
- `library_convergence_flags` — grid-sensitive activation evaluations affecting an initial material isotope.
- `library_target_limitations` — per-target builder ledger entries (for example unsupported resonance formalisms)
  affecting an initial isotope. These two categories repair the v0.1 promise that library-index guards propagate.

`steps[].leakage_atoms_per_g` is the accumulated amount routed to leakage; the categories above explain why.
`steps[].total_atoms_per_g` sums every state, leakage and removal sinks included. It also includes the unit source
state, which holds exactly 1.0 atom/g in trace mode and in coupled runs with `feed` (and adds one to
`n_states_populated`). Subtract it before comparing a trace-mode total with a coupled total.

The certificate's `inputs.fission_yields` records every declared and recomputed file hash, parent, AWR and independent
table size/sum; `certificate.fission_yields.selection` repeats the effective selection (with `yields` added for cumulative mode;
under `rate_weighted` its energy fields are the weight-mean energy and the lowest and highest tabulated energy with
nonzero weight). `certificate.fission_yields.weights`, `.yields` and `.parents` appear only when the matching option
is used, and `weights` repeats `fission_yield_weights`. Cumulative MT=459 tables are
counted for inspection only and never appear as production sources.

## Numerical resolution and pruning

- `negative_atoms_zeroed_per_step` — negative CRAM round-off set to zero, with the removed amount.
- `numerical_floor` — CRAM's absolute floor (`alpha0 × max population`), the maximum number of sub-floor states and a
  conservative heat fraction for them. Each step also carries the underlying counts and heat bound.
- `rate_pruning` — the threshold actually used (the `screen` threshold when one is set), every dropped nuclide's
  population bound (`atoms_per_g_bound`) and inflow-rate bound (`feed_atoms_per_g_s_bound`), the activity and heat the
  dropped states can carry (`dropped_activity_Bq_per_g_bound`, `dropped_heat_W_per_g_bound`), the shortfall of kept
  states fed by dropped ones (`kept_activity_Bq_per_g_bound`, `kept_heat_W_per_g_bound`), their sums
  (`activity_Bq_per_g_bound`, `heat_W_per_g_bound`: bounds on the change in total activity and total heat at every
  step), and `unbounded_components`, states kept because no bound could be established. A bound that is infinite is
  written as `null`. Reachability and `none` pruning produce an empty dropped list and zero bounds. Before P148 the
  heat field was `removed_heat_W_per_g_bound` and covered dropped states only; see `docs/METHOD.md`.

## MF=33 uncertainty accounting

When an `uncertainty` object is present, `ledger.uncertainty` names the method and band (the **MF=33 nuclear-data
band**, or — in flux-only mode, `channels: ["flux"]` with `covariance` omitted — the **transport-tally
statistical-error band**), records the confidence/multiplier, selected and comparison CRAM orders, exact collapse
convention, builder/source identities and all excluded uncertainty sources. In flux-only mode the coverage
diagnostics below are empty (there is no MF=33 propagation to report on) and `flux_channel_requested`,
`flux_parameters` and `flux_held_at_nominal` are added instead, naming the choices (self-shielding row scale,
`rate_scale`, fission-yield selection, mode and pruning) held at the nominal run's values while the channel is
propagated. Its coverage diagnostics are:

- `active_parameters`, `covered_parameters`, `uncovered_library_rows` — the pruned matrix rows with local influence,
  which of them have valid MF=33 self-covariance, and the exact uncovered library-row indices;
- `absent_cross_parameter_pairs` — pairs of covered parameters for which the evaluation supplies no cross component;
  they contribute zero evaluated covariance and are counted rather than fabricated;
- `maximum_covariance_asymmetry_barn2` — the largest assembled orientation difference before propagation;
- `excluded_blocks` — each collapsed `(target, MT, MT1)` block rejected as defective (asymmetric beyond the frozen
  `1e-9`-relative bound, or non-positive-semidefinite symmetric part), with its measured defect; excluded blocks
  contribute zero and count against coverage.

Every `steps[].uncertainty.responses[]` record contains the nominal value, unit, complete parameter metadata and
sensitivity, MF=33 standard/relative uncertainty, normal multiplier/interval, separately estimated CRAM-order bound,
expanded conservative interval, round-off-negative variance removed, and `complete` or `partial` coverage. Coverage
depends only on nonzero sensitivities for that response. `require_complete` fails before reporting a partial result.

When `uncertainty.channels` requests `decay_constants` and/or `fission_yields`, each response additionally reports
per-channel sensitivity lists (`decay_sensitivities`, `yield_sensitivities`) with full parameter metadata and
declared standard uncertainty, a `channels` report of each channel's own coverage, and
`combined_standard_uncertainty` — the independent root-sum-of-squares across channels. The step record names
`uncovered_decay_constants` and `uncovered_yield_products` (parameters with no declared uncertainty). Requested
channels extend `require_complete`; `uncovered_remainder` is always named and never propagated.

When `uncertainty.channels` requests `flux`, each response additionally reports `flux_sensitivities` (one entry per
input group with a positive sensitivity and a declared `relative_error`; each parameter carries the group's energy
bounds, flux and declared relative error) and `flux_fully_correlated_bound` (`sum(|s_g| * e_g)`, an alternative,
worst-case fully-correlated bound alongside the channel's own diagonal variance). The flux channel is diagonal by
construction (group-to-group tally errors are uncorrelated) and carries no coverage gap of its own to name — every
group with a declared error is used. It is excluded from `voi`, `isomer` and `design`: it is not a nuclear-data
parameter, so it does not belong in a "which measurement buys down this band" ranking.

The excluded list is substantive: MF=32 resonance-parameter covariance, MF=40 production covariance, decay-yield
and cross-channel correlation, material-composition uncertainty, response-coefficient uncertainty and model
discrepancy are not included. Absent a requested `flux` channel, incident-flux uncertainty is excluded too; with
`flux` requested, the excluded entry instead names the narrower remainder the channel does not cover — systematic
flux uncertainty from the transport model, geometry and transport nuclear data, as opposed to the tally's own
statistical error, which the channel does cover. Decay-constant, independent-yield and flux uncertainties are
included only when their channels are requested. Neither reported interval is a licensing safety margin.

## Photon accounting

`photon_spectra` is one entry per result step and contains:

- `energy_normalized_spectra` — nuclide, evaluated energy moment, `E_EM`, relative raw discrepancy and the explicit
  transport-source scale;
- `nuclides_with_em_energy_but_no_photon_spectrum` — no invented photons, plus the omitted gamma-power bound;
- `group_underflow_*` and `group_overflow_*` — photon rates and powers outside the selected group structure;
- `response_excluded_power_W_g`, `response_missing_elements` — why a contact-dose response is incomplete;
- `response_subthreshold_power_W_g` — power in groups with centroid below the response's lowest energy, left out of the
  proxy and not counted in `response_excluded_power_W_g`. Present only when non-zero.

The step source repeats the user-facing consequences as `unrepresented_gamma_power_W_g`,
`represented_gamma_power_fraction`, `ungrouped_power_W_g` and `dose_response_power_coverage`, plus `dose_response_subthreshold_power_W_g` when it is non-zero. The contact proxy
stays available while that sub-threshold power is at most `1e-4` of the step's response power.

## Mesh rebin accounting

Every `actinv-mesh-result-1` cell carries a separate `rebin` object before its ordinary run result:

- `method` — `copy` for byte-identical boundaries or `equal-flux-per-unit-lethargy` for logarithmic overlap;
- `source_total`, `destination_total` — compensated input and in-library flux totals;
- `underflow`, `overflow` — physical flux outside the activation-library energy range, excluded rather than folded;
- `relative_closure` — relative disagreement of `destination + underflow + overflow` with `source` (bounded to 1e-12).

The mesh footer sums these quantities again in canonical cell order and reports the minimum/maximum independently
pruned state counts. Import-source total checks remain in the canonical flux footer and its upstream provenance.
The footer also records `cells_served_from_reuse`: cells whose byte-identical rebinned flux shared an earlier cell's
solve instead of re-executing. Reuse is a scheduling record only — the emitted cell bytes are identical with or
without grouping, and a resumed run reproduces the same count.

## Assembly diagnostic

`assembly` records the bulk isotope count, matrix triplet counts, activation-library row count, decay-chain size and
total neutron flux. A ledger with entries is not intrinsically a bad result; it is a result whose limitations are
machine-readable rather than hidden.

`schedule` records segment count, total multiplier-weighted seconds and physical fluence. The corresponding
`steps[]` fields are cumulative elapsed `t_s`, current multiplier `flux`, `flux_weighted_time_s` and either neutron
`fluence_n_cm2` or charged-projectile `fluence_particles_cm2`, so decay gaps and pulse normalization remain
independently auditable. Charged prepared runs, mesh headers/results and certificates repeat the projectile; a
projectile, group, temperature or source-hash mismatch fails before matrix assembly.
