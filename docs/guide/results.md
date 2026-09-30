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

Photon group values represent source strength integrated over each energy group. Contact gamma dose is a semi-infinite-slab screening proxy; use separate transport for geometry-dependent dose. Radiological indices use your selected coefficient table. NRT dpa uses the supplied damage-energy table and displacement energies. [Scope and qualification](qualification.md) explains these boundaries.

Pathways identify a source and first product with ranked contributions in trace mode. They do not enumerate every intermediate member of a reaction chain.

## Check the ledger

The top-level `ledger` records missing data, composition issues, leakage, pruning, mode selection, numerical diagnostics, and optional response coverage. Inspect it before interpreting a small value as physical absence.

Positive activity without a radiological coefficient and material targets without damage data are reported as incomplete coverage. For features that support it, `require_complete` turns missing coverage into a calculation error.

The `numerical_floor_atoms_per_g` field is a CRAM asymptotic scale, **not a bound on total numerical error**. Below-floor counts and heat bounds help identify small populations that need closer review.

## Read uncertainty with its coverage

An uncertainty response contains its nominal value, propagated standard uncertainty, interval, coverage, and a separate CRAM-order comparison. The default channel is MF=33 cross-section covariance. Half-life and independent fission-yield uncertainties can be enabled explicitly.

Incomplete evaluated covariance does not become complete by requesting a confidence level. Reported intervals omit flux, composition, response-coefficient, geometry, and model uncertainties, among others. Aggregate activity uncertainty must be propagated as `activity.total`; summing individual standard uncertainties does not preserve correlations.

You can declare an additional unmodeled relative term directly or through a hash-pinned calibration/evaluation-spread table. That declared contribution supplements the band; it cannot account for a missing reaction channel or establish complete uncertainty coverage. See [additional uncertainty reporting](specification.md#additional-uncertainty-reporting).

## Check input identity and comparisons

The `certificate` records the selected inputs and computed hashes. It establishes which files were used; it does not establish their physical suitability.

Before comparing two results, check the material basis, spectrum normalization, schedule, projectile, library, and decay data. Compare at the same physical time. Record missing-data and mode differences alongside numerical differences.
