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

Photon group values represent source strength integrated over each energy group. Contact gamma dose is a semi-infinite-slab screening proxy; use separate transport for geometry-dependent dose. Photon power below the response's lowest energy (1 keV for the NIST response) is left out of the proxy and shown as `dose_response_power_coverage` below one and as `dose_response_subthreshold_power_W_g`. The proxy is still reported when that power is at most `1e-4` of the step's photon power, because the missing low-energy response then changes it by at most about `1e-3`; above that share it is `null`. Radiological indices use your selected coefficient table (including the `a2` transport index, which needs your A2 table). NRT dpa uses the supplied damage-energy table and displacement energies. [Scope and qualification](qualification.md) explains these boundaries.

Two photon components are emitted only when the specification asks for them. With `photon.point_source`, `photon_source.point_source_dose` holds the dose rate in Sv/h at the configured distance from the step's photon source treated as a point source of `material.mass_g` (1 g by default, FISPACT-II's convention), plus the same dose per gram. It is the manual's Eq. (63): group-centroid energies, air energy absorption, optional air transmission, no build-up. A `null` dose carries an `unavailable_reason`, and `subthreshold_power_W` and `excluded_power_W` show the power it could not use. With `photon.bremsstrahlung`, `photon_source.bremsstrahlung` is a separate source and dose for the listed beta emitters, following the manual's thin-target approximation from each nuclide's mean beta energy and the material's mean atomic number. It is never merged into the decay-photon groups or dose proxies, so the total dose from both is the sum of the two fields. Nuclides whose inputs are missing are named in `missing_inputs`, and `inputs_complete` is then `false`. Both are screening estimates with the limits described under [photon options](specification.md#photon-options), not transport results.

The `pathways` array identifies a source and first product with ranked contributions in trace mode. It does not enumerate every intermediate member of a reaction chain; the opt-in [`pathway_routes`](#read-the-pathway-routes) analysis does.

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
| `paths[]` | Routes ranked by contribution (see below). |
| `generic[]` | Routes grouped by their edge-type sequence, for example `(n,g) b-`, with `n_paths`, `atoms_per_g` and `fraction`. |

Each route in `paths[]` gives its `source`, the `nuclides` from source to target, the `edges` between them (a
reaction such as `(n,g)`, `(n,2n)`, `fission`, or a decay mode: `b-`, `b+/EC`, `IT`, `a`, `n`, `p`), `atoms_per_g`
at the final step, `fraction` of the target's total, and `is_loop`. Loop walks add `loop_gain` and `base_path`;
`by_step: true` adds `atoms_per_g_by_step`. Reaction names follow neutron conventions; other projectiles use the ENDF
`MTnnn` number. An edge that the label tables do not cover is named `edge`.

The residual is the accounting check. It is small when the floors keep the routes that matter and `max_depth` is
long enough; a large residual means the target is fed by many small routes, by chains longer than `max_depth`, or
by loops beyond `loop_floor`. Lower `path_floor` and raise `max_depth` and `max_paths` to chase it, and read
`search_truncated`, `explored_walks` and `candidates_dropped_by_cap` for the search's own limits. In a coupled run a
source that is also the target has a one-node route (the surviving initial atoms).
Contributions are exact for the routes listed, but the dense integration works to the absolute precision of its
largest term, so a route carrying under about 1e-10 of the largest term in its own chain is below the method's
resolution.

## Step summary

When the problem declares [`summary`](specification.md#step-summary), each step gains a `summary` object. Every
quantity is per gram of initial material, except the `absolute` and `per_cm3` totals. A state is counted with the
same population rule as the rest of the output: constant bulk atoms in trace mode (plus the tracked population of a
reservoir nuclide), the evolved state elsewhere, and bulk nuclides absent from the chain at their constant atoms.
The leakage, unit and removal sinks are not nuclide states.

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
