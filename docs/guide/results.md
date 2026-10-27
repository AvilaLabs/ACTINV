# Read your results

`actinv run problem.json result.json` saves one JSON document with every computed step, calculation diagnostics, and the input certificate. Keep that file alongside the problem and selected data.

## Time steps and units

Each entry in `steps` reports the end of a schedule segment. `step` is one-based; `t_s` is cumulative seconds from the start, including irradiation and cooling.

| Field | Meaning | Unit |
| --- | --- | --- |
| `inventory[].atoms_per_g` | Nuclide population | atoms/g |
| `activity_Bq_per_g` | Activity by nuclide | Bq/g |
| `heat_W_per_g.total` | Total decay heat | W/g |
| `heat_W_per_g.alpha`, `.beta`, `.gamma` | Decay-heat components | W/g |
| `leakage_atoms_per_g` | Atoms routed outside the represented evaluated chain | atoms/g |
| `removed_atoms_per_g` | Atoms in the removal sink, when requested | atoms/g |

For a sample of known mass in grams, multiply the per-gram activity or heat by that mass to obtain Bq or W. Check that the composition and its basis describe the material you intend.

`total_atoms_per_g` is an accounting diagnostic across the solver state vector. It includes sinks and, where present, a unit source state. Use the per-nuclide inventory for physical nuclide populations.

## Optional results

Photon sources, dose-response quantities, pathways, uncertainty, radiological responses, and damage observables depend on the requested options and required data. An absent field means that output was not emitted; it is not a zero result.

Photon group values represent source strength integrated over each energy group. Contact gamma dose is a semi-infinite-slab screening proxy; use separate transport for geometry-dependent dose. Photon power below the response's lowest energy (1 keV for the NIST response) is left out of the proxy and shown as `dose_response_power_coverage` below one and as `dose_response_subthreshold_power_W_g`. The proxy is still reported when that power is at most `1e-4` of the step's photon power, because the missing low-energy response then changes it by at most about `1e-3`; above that share it is `null`. Radiological indices use your selected coefficient table, including the `a2` transport index (A1 and A2 tables are [bundled](specification.md#bundled-regulatory-tables), or supply your own). A nuclide a table lists as unlimited, or a progeny whose listed parent is present, is counted as covered with zero contribution and named in `unlimited_active_nuclides` or `covered_by_parent` instead of `missing_active_nuclides`. NRT dpa uses the supplied damage-energy table and displacement energies. Kerma heating uses the supplied kerma table. [Scope and qualification](qualification.md) explains these boundaries.

Two photon components are emitted only when the specification asks for them. With `photon.point_source`, `photon_source.point_source_dose` holds the absorbed dose rate in air at the configured distance from the step's photon source treated as a point source of `material.mass_g` (1 g by default, FISPACT-II's convention), plus the same dose per gram. The field names say `Sv_h`, but the quantity is the air kerma rate, numerically Gy/h (the contact proxy calls the same quantity `Gy_h`); no quality factor or conversion to ambient dose equivalent H\*(10) is applied, and H\*(10) is roughly 1.1 to 1.4 times the air kerma between 0.1 and 3 MeV. It is the manual's Eq. (63): group-centroid energies, air energy absorption, optional air transmission, no build-up. A `null` dose carries an `unavailable_reason`, and `subthreshold_power_W` and `excluded_power_W` show the power it could not use; decay-record gamma energy with no photon spectrum also makes it `null` and counts as excluded power. With `photon.bremsstrahlung`, `photon_source.bremsstrahlung` is a separate source and dose for the listed beta emitters, following the manual's thin-target approximation from each nuclide's beta-minus electrons per decay (`electrons_per_decay`, from its decay branching), their mean energy and the material's mean atomic number. Group energy follows the manual's bin-midpoint rule, so `energy_closure` (emitted over the analytic `a Z E0^2 / 2` per electron) is not 1, and `zero_lower_edge_energy_fraction` is the share of that energy in the empty zero-lower-edge group. It is never merged into the decay-photon groups or dose proxies, so the total dose from both is the sum of the two fields. Nuclides whose inputs are missing are named in `missing_inputs`, and `inputs_complete` is then `false`. Both are screening estimates with the limits described under [photon options](specification.md#photon-options), not transport results.

Two more photon options add keys only when set. With `photon.missing_spectra: "estimate"`, a nuclide that has a mean electromagnetic energy but no photon spectrum is given an exponential spectrum; `photon_source.estimated_gamma_power_W_g` is its power and `photon_source.estimated_spectra` holds, for the contact proxy and the point-source dose, the value from evaluated spectra alone (`represented_only`), the `estimate` and the `lower` and `upper` bounds, with each estimated nuclide's own range. The estimate is a model; read it with its bounds. With `photon.exclude_xrays`, `photon_source.excluded_xray_power_W_g` is the X-ray power removed from the source.

The `pathways` array identifies a source and first product with ranked contributions in trace mode, including production fed by the decay of a radioactive bulk nuclide. `pathway_closure` is the worst relative difference between a nuclide's listed chains and its inventory, and is 1 when a nuclide in the inventory has no chain at all. The array does not enumerate every intermediate member of a reaction chain; the opt-in [`pathway_routes`](#read-the-pathway-routes) analysis does.

## Read the pathway routes

When the spec carries a [`pathways` section](specification.md#pathways-and-routes-analysis), the result gains a
top-level `pathway_routes` object. It is absent otherwise. (The older `pathways` array identifies a source and
first product only, in trace mode; `pathway_routes` enumerates the whole reaction and decay chain.)

`pathway_routes.targets[]` has one entry per target nuclide:

| Field | Meaning |
| --- | --- |
| `total_atoms_per_g` | The real run's final-step inventory of the target. |
| `paths_atoms_per_g` | Sum of the reported routes. |
| `residual_atoms_per_g`, `residual_fraction` | `total - paths`: the part of the inventory no reported route explains. |
| `explained_fraction` | `paths / total`. |
| `loop_atoms_per_g` | Portion of the reported routes carried by loop walks. |
| `paths_dropped_by_cap` | Routes that passed the floors but fell outside `max_paths`. |
| `candidates_dropped_by_cap` | Candidate routes beyond `8 x max_paths` that were never integrated (smallest upper bound dropped first). Absent when none. |
| `search_truncated`, `truncated_sources` | The route search for this target ran out of its tree-node budget, and the sources whose tree was cut short. Absent when the search finished. |
| `paths[]` | Routes ranked by contribution (see below). |
| `generic[]` | Routes grouped by their edge-type sequence, for example `(n,g) b-`, with `n_paths`, `atoms_per_g` and `fraction`. |

Each route in `paths[]` gives its `source`, the `nuclides` from source to target, the `edges` between them (a
reaction such as `(n,g)`, `(n,2n)`, `fission`, or a decay mode: `b-`, `b+/EC`, `IT`, `a`, `n`, `p`), `atoms_per_g`
at the final step, `fraction` of the target's total, and `is_loop`. Loop walks add `loop_gain` and `base_path`;
`by_step: true` adds `atoms_per_g_by_step`. Reaction names follow neutron conventions; other projectiles use the ENDF
`MTnnn` number. Spontaneous-fission products are `SF`, atoms from a schedule `feed` are `feed`, and an edge into the
removal sink is `removal`; an edge that the label tables do not cover is named `edge`. Labels come from the decay and
reaction tables, not from this run's rates, so a library reaction is named even if its collapsed rate is zero here.

The residual is the accounting check. It is small when the floors keep the routes that matter and `max_depth` is
long enough; a large residual means the target is fed by many small routes, by chains longer than `max_depth`, or
by loops beyond `loop_floor`. Lower `path_floor` and raise `max_depth` and `max_paths` to chase it, and read
`search_truncated`, `explored_walks` and `candidates_dropped_by_cap` (per target, and in the top-level `notes`) for the
search's own limits. A walk that revisits a nuclide must also pass `loop_floor`, so the residual can exceed
`path_floor` unless `loop_floor` is near zero. In a coupled run a
source that is also the target has a one-node route (the surviving initial atoms).
Contributions are exact for the routes listed, but the dense integration works to the absolute precision of its
largest term, so a route carrying under about 1e-10 of the largest term in its own chain is below the method's
resolution.

## Step summary

The desktop **Tables** page shows these tables for the selected step; see [Use the desktop](desktop.md#explore-and-save-results).

When the problem declares [`summary`](specification.md#step-summary), each step gains a `summary` object. Every
quantity is per gram of initial material, except the `absolute` and `per_cm3` totals. A state is counted with the
same population rule as the rest of the output: constant bulk atoms in trace mode (plus the tracked population of a
reservoir nuclide), the evolved state elsewhere, and bulk nuclides absent from the chain at their constant atoms.
The leakage, unit and removal sinks are not nuclide states.

In trace mode the initial material is held constant, so `nuclides` shows each initial nuclide at its constant bulk
atoms: an initial radionuclide keeps a constant activity and heat there, and `mass_g` is not a closure test. The
[depletion table](#depletion-table) gives the analytic view of the same step.

`nuclides` lists one row per nuclide, descending by `atoms`:

| Field | Meaning |
| --- | --- |
| `nuclide`, `Z`, `A`, `LISO` | Name and identity, as in `inventory` |
| `atoms`, `grams` | Population and mass. `grams` uses the decay record's AWR times the neutron mass in u times the atomic mass constant (1.66053906660e-24 g, CODATA 2018). With no AWR it uses A times that constant and sets `mass_from_a` |
| `activity_Bq` | λ·N |
| `alpha_W`, `beta_W`, `gamma_W` | Activity times the record's mean heavy-particle, light-particle and electromagnetic decay energy, the same split as `heat_W_per_g` |
| `half_life_s` | `null` for a stable nuclide or one without a decay record |
| `stable`, `initial`, `decay_data` | A decay record exists with no decay constant; present at t = 0; a decay record exists. A state without one is reported with zero activity and heat, and is not called stable |

`totals` always covers every state, whatever `min_atoms` or `include_stable` remove from the table:

- `activity_Bq`, `activity_Ci` (1 Ci = 3.7e10 Bq exactly), `alpha_W`, `beta_W`, `gamma_W` and `heat_W`.
- `activity_by_mode`: each nuclide's λN split by the branching ratios of its decay-mode records, classed by the
  first digit of RTYP (1 β⁻, 2 EC/β⁺, 3 IT, 4 α, 5 n, 6 SF, 7 p, anything else `other`). The ratios are normalised
  so the eight entries sum to `activity_Bq` to rounding; a record whose ratios fall short of one by more than the
  chain tolerance, or that has no modes, books the shortfall to `other`.
- FISPACT-compatible `alpha_Bq` (the α-mode activity), `beta_Bq` (β⁻, EC/β⁺ and IT modes) and `gamma_Bq` (the
  activity of every nuclide whose mean electromagnetic decay energy is positive). These are FISPACT's report
  categories; `activity_by_mode` is the exact partition.
- `activity_excl_H3_Bq` and `heat_excl_H3_W`: the same totals with H-3 removed.
- `mass_g`: the current total mass in grams per gram of initial material. Departure from 1 is a mass-closure
  diagnostic: it reflects mass the represented chain does not carry and the difference between these AWR-based
  masses and the composition's molar masses.
- `leakage_atoms` and `n_nuclides` (states with a positive population).
- `absolute`: activity (Bq, Ci), heat components and the H-3-excluded values multiplied by `material.mass_g`
  (`multiplier`). `per_cm3` is the same multiplied by `material.density_g_cm3` (Bq/cm³, W/cm³) and is present only
  when a density is given.

`elements` (omitted with `summary.elements: false`) lists one row per Z, ascending, with `symbol`, `atoms`, `grams`,
`activity_Bq` and the three heat components. `dominant` ranks the nuclides for `activity`, `heat`, `alpha_heat`,
`beta_heat` and `gamma_heat`. Each list gives the `total`, the top `dominant_top` entries (`nuclide`, `value`,
`percent`, `cumulative_percent`) and `n_for_90_percent` and `n_for_99_percent`, the number of nuclides needed to
reach those shares of the total, counted over the full ranking rather than the truncated list.

## Depletion table

The desktop **Tables** page shows this table on its **Depletion** tab.

With [`summary.depletion`](specification.md#step-summary) set to `true`, `steps[].summary.depletion` reports how
much of each initial-material nuclide the step removed and which channels removed it. Without it the key is absent.
`model` is `coupled` when the solver evolves the initial nuclides, and `trace_analytic` in trace mode, where the
reservoir is held constant in the inventory and is instead depleted analytically,
N(t) = N(0) exp(−∫ removal rate dt), the same first-order law as the ledger's `max_burnup_fraction`. Analytic
depletion has no ingrowth from other nuclides. An initial nuclide absent from the decay library has no row at
all; the ledger lists it under `ledger.depletion.initial_nuclides_absent_from_decay_library`, written as
`<ZA>_<LISO>` (for example `26056_0` for Fe-56, `47110_1` for Ag-110m), not in the `Fe56` style of the table.

`nuclides` has one row per initial nuclide that is in the decay library, largest first:

| Field | Meaning |
| --- | --- |
| `nuclide`, `Z`, `A`, `LISO` | Name and identity |
| `initial_atoms_per_g`, `atoms_start_per_g`, `atoms_end_per_g` | Atoms per gram of initial material at t = 0, at the start of the step and at its end |
| `depleted_atoms_per_g`, `depleted_fraction` | Start minus end, and that over the start, for this step. Net of ingrowth from other nuclides (a `feed` into an initial nuclide, or production from another nuclide, shows up as negative depletion with no channel) and of a partition applied at the step start |
| `cumulative_depleted_atoms_per_g`, `cumulative_depleted_fraction` | Initial minus end, and that over the initial atoms |
| `removal_rate_per_s` | Total removal rate per atom: decay, the schedule's first-order `removal` and every reaction channel. Exactly `0` when nothing removes the nuclide |
| `channels` | The removal split, descending by rate |
| `removal_rate_relative_standard_uncertainty` | With MF=33 covariance and only when every channel is covered |
| `uncertainty_covered_share` | With MF=33 covariance and a nonzero removal rate: the share of the removal carried by channels that have an uncertainty |

Each channel has `channel` (`decay`, `removal` or `MT<n>`), `kind` (`decay`, `removal` or `reaction`), `mt`, `reaction` (the conventional name such as `(n,g)` for
a neutron projectile), `rate_per_s`, `share` and, with covariance, `relative_standard_uncertainty`.

The rates are the solver's own, not a recomputation. A reaction channel is the sum of the nuclide's loss rows with
that MT, collapsed under the step's spectrum with self-shielding, `options.rate_scale` and named overrides applied,
times the flux multiplier the step ran at (the time-weighted mean for a power-driven step). The decay channel is
the effective decay constant after `decay_scale` and half-life overrides. The `removal` channel is the
first-order rate a schedule step's `removal` map declares for the nuclide (a nuclide or element selector), not
scaled by the flux multiplier and present even in a step with no flux. The channels therefore sum to
`removal_rate_per_s`, the diagonal of the step matrix, and the shares sum to one. A `feed` is a source, not a
removal, so it has no channel. In trace mode the `removal` of an initial nuclide that is not also fed is not applied
to the reservoir (the ledger names these exemptions), so it has no channel; a fed, removable reservoir nuclide
does. A reaction whose loss row is absent from the library is not listed. Fission appears as `MT18`. The analytic
fractions of trace mode are computed with `expm1`, so they keep full precision at the tiny exponents trace mode
runs at.

The uncertainty of a reaction channel is the flux-weighted collapse of the MF=33 covariance for its loss rows, the
same collapse the uncertainty bands use: for weights w<sub>g</sub> = σ<sub>g</sub>φ<sub>g</sub> / Σσφ it is
√(wᵀCw) on the relative covariance. A decay channel takes the half-life uncertainty of the decay data, or a
`half_life_uncertainty` override, whatever `uncertainty.channels` lists (the bands add decay-constant uncertainty
only when `decay_constants` is a listed channel, so the table and the bands can differ there). A `removal` channel
is an exact input and carries an uncertainty of 0. A reaction channel with a loss row the covariance does not cover,
or whose covariance block was excluded as defective (asymmetric or not positive semidefinite, as in the ledger's
`excluded_blocks`), has no uncertainty and is not counted in `uncertainty_covered_share`. The total includes the correlation between a nuclide's reaction channels from the
cross-MT covariance blocks. Fission-yield, flux and composition uncertainty do not enter a removal rate.

## Fission count, burn-up and spontaneous-fission neutrons

Each step gains a `fission` block only when `options.fission` is set (`fission.count`) or
`fission_yields.spontaneous` supplies a neutron multiplicity (`fission.spontaneous_neutron_source`); see the
[specification](specification.md). Absent means not requested.

| Field | Meaning | Unit |
| --- | --- | --- |
| `fission.count.fissions_per_g` | Cumulative fissions since the start of the schedule, per gram of initial material | fissions/g |
| `fission.count.neutron_induced_fissions_per_g` | The part from library fission (MT=18) rates | fissions/g |
| `fission.count.spontaneous_fissions_per_g` | One per spontaneous-fission decay, including states with no yield data | fissions/g |
| `fission.count.initial_heavy_metal_atoms_per_g` | Atoms with Z ≥ 90 in the declared material | atoms/g |
| `fission.count.burnup_percent` | 100 × fissions ÷ initial heavy-metal atoms; absent without heavy metal | % |
| `fission.count.burnup_MWd_per_kg_hm` | Fissions × `energy_per_fission_MeV` ÷ initial heavy-metal mass; absent without heavy metal or when a heavy-metal isotope has no mass in the decay data | MWd/kg |
| `fission.spontaneous_neutron_source.neutrons_per_s_per_g` | Σ λ · BR_SF · N · ν̄ at the end of the step | neutrons/s/g |
| `fission.spontaneous_neutron_source.by_nuclide` | The same sum split by nuclide | neutrons/s/g |
| `fission.spontaneous_neutron_source.uncovered_sf_decays_per_s_per_g` | Spontaneous-fission decays of populated states with no ν̄; their neutrons are not in the total | decays/s/g |

The count integrates each step's fission rate times the evolving population, in the same matrix-exponential
solve family as the inventory, so it is not a rectangle or trapezoid estimate. It is the rate integral and differs from
FISPACT-II's `NUMBER OF FISSIONS`, which counts the change in the number of fissionable nuclides. Burn-up is per gram
of the initial material and per kilogram of initial heavy metal; the 200 MeV default of `energy_per_fission_MeV` is a
stated convention, so state the energy you compare with. The source covers only the states in the decay library that
have both a spontaneous-fission branch and a ν̄; ledger `spontaneous_fission.nubar_uncovered` names the populated
ones that lack it, and `uncovered_to_leakage` names the populated ones whose SF branch still goes to leakage for lack
of a yield evaluation.

## Kerma heating and power-driven steps

A problem with a `kerma` section ([specification](specification.md#kerma-heating-and-power-driven-steps)) adds a
`kerma` block to every step and a `power` block to every power-driven step. Both are absent otherwise; absence is not
a zero result.

| `kerma` field | Meaning | Unit |
| --- | --- | --- |
| `heating_W_per_g` | Instantaneous nuclear heating at the end of the step: `sum_i N_i * k_i * flux_multiplier` | W/g |
| `heating_W_cm3` | The same per volume; present only with `material.density_g_cm3` | W/cm3 |
| `flux_multiplier` | The multiplier the heating is evaluated at (a power step's last sub-interval) | dimensionless |
| `damage_energy_eV_per_g_s` | MT=444 damage-energy production; present only when the table has damage-energy rows | eV/(g s) |
| `top_contributors[]` | The largest contributors by magnitude: `nuclide`, `atoms_per_g`, `heating_W_per_g`, `fraction` of the total | |
| `n_contributing_nuclides`, `other_heating_W_per_g` | Nuclides with nonzero heating, and the heating of those not listed | W/g |
| `covered_atom_fraction` | Share of the step's atoms carried by nuclides with a kerma row; the rest contribute nothing | |

Heating comes only from nuclides with a table row, and an element row (`Fe`) is applied to every isotope and
transmutation product of that element, so a high `covered_atom_fraction` says the rows exist, not that they are
nuclide-specific. A transmutation product without a row adds no heating, so `covered_atom_fraction` below one is a
lower bound on the heating, not a zero.

| `power` field | Meaning |
| --- | --- |
| `requested_W_per_g`, `requested_W_cm3` | The target (the second only when it was written in W/cm3) |
| `tolerance`, `max_substeps` | The `kerma.power_tolerance` and `kerma.max_substeps` in force |
| `substeps`, `substep_duration_s` | Equal sub-intervals the accepted solve used, and their length |
| `flux_multiplier_per_substep`, `flux_per_substep_cm2_s` | The achieved multiplier in each sub-interval and the resulting total flux (particles cm-2 s-1) |
| `mean_flux_multiplier` | Time-weighted mean multiplier; this is the step's top-level `flux`, and drives `flux_weighted_time_s` and fluence |
| `max_power_drift` | Largest relative departure of a sub-interval's end power from the request |
| `converged` | `max_power_drift <= tolerance`. `false` means `max_substeps` was reached first; `actinv run` prints a warning on stderr for each such step |
| `refinement[]` | Each attempt, coarsest first: `substeps` tried and its `max_power_drift` |
| `estimated_flux_multiplier` | The start-of-run estimate used for pruning and the burnup-mode choice before the solve |

The ledger's `kerma` entry records the table hash, `require_complete`, the material nuclides without a row (a
composition isotope with no decay record counts as without one: it is not a chain state and heats nothing), for a
table with damage-energy rows `damage_energy_uncovered_composition_targets` (chain nuclides with a heating row and
no damage row, which add nothing to `damage_energy_eV_per_g_s`), the heating model and units, and for power runs the number of power steps, total sub-intervals, worst drift and the
step numbers that did not converge. The certificate's `kerma` entry carries the table's provenance and
`inputs.kerma_table` its computed hash. The schedule ledger's `flux_weighted_time_s` and `fluence_n_cm2` use the
achieved multipliers of power-driven steps.

## Check the ledger

The top-level `ledger` records missing data, composition issues, leakage, pruning, mode selection, numerical diagnostics, and optional response coverage. Inspect it before interpreting a small value as physical absence.

Positive activity without a radiological coefficient and material targets without damage data are reported as incomplete coverage. For features that support it, `require_complete` turns missing coverage into a calculation error.

The `numerical_floor_atoms_per_g` field is a CRAM asymptotic scale, **not a bound on total numerical error**. Below-floor counts and heat bounds help identify small populations that need closer review.

## Read uncertainty with its coverage

An uncertainty response contains its nominal value, propagated standard uncertainty, interval, coverage, and a separate CRAM-order comparison. The default channel is MF=33 cross-section covariance. Half-life and independent fission-yield uncertainties can be enabled explicitly. Since 1.4.0 a transport-tally statistical `flux` channel can also be requested.

Incomplete evaluated covariance does not become complete by requesting a confidence level. Incident-flux uncertainty is excluded unless the `flux` channel is requested. That channel uses supplied groupwise tally errors and a diagonal model; systematic transport-model, geometry, and nuclear-data errors remain excluded. Composition, response-coefficient, and other model uncertainties also remain outside the band. Aggregate activity uncertainty must be propagated as `activity.total`; summing individual standard uncertainties does not preserve correlations.

You can declare an additional unmodeled relative term directly or through a hash-pinned calibration/evaluation-spread table. That declared contribution supplements the band; it cannot account for a missing reaction channel or establish complete uncertainty coverage. See [additional uncertainty reporting](specification.md#additional-uncertainty-reporting).

## Check input identity and comparisons

The `certificate` records the selected inputs and computed hashes. It establishes which files were used; it does not establish their physical suitability.

Before comparing two results, check the material basis, spectrum normalization, schedule, projectile, library, and decay data. Compare at the same physical time. Record missing-data and mode differences alongside numerical differences.
