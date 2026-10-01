# Numerical method

ACTINV solves a nuclide network under irradiation and during cooling. For a fixed spectrum and schedule segment, the network is a linear system built from decay constants and spectrum-collapsed reaction rates. Optional feed adds a constant source and removal adds first-order losses with a recorded sink.

## Data and rates

The Rust data library reads supported ENDF-6 evaluations and builds deterministic activation libraries. Standard neutron data use 709 groups; standard proton, deuteron, and alpha data use 162 groups at 0 K. A calculation must use the projectile, temperature, and group structure its library declares.

Decay data provide half-lives, branching, energies, and available radiation spectra. Natural elements are expanded using tabulated abundances. Products absent from the configured data remain explicit leakage.

## Time integration

The solver uses the Chebyshev Rational Approximation Method, with CRAM orders 16 and 48. The default order is 16. Uncertainty runs evaluate the alternate order as a separately reported method comparison; this comparison is not a proof of total numerical error.

`options.mode` selects `auto`, `trace`, or `coupled`. Trace mode retains an undepleted initial-material reservoir and a linear production system. Coupled mode evolves the material network. Auto uses an estimated initial-material reaction-loss fraction to select the path and records its decision in the ledger. See the [options reference](specification.md#options-and-result) for the exact criterion and pruning choices.

## Responses and uncertainty

Activity follows radioactive populations and decay constants. Decay heat combines evaluated alpha, beta, and gamma energy contributions. Photon sources use evaluated decay spectra; response quantities use the explicit photon or radiological tables supplied by the analyst.

First-order sensitivities propagate retained MF=33 covariance into selected responses. Optional decay-constant and independent fission-yield channels use the uncertainties in their evaluated records. Coverage and excluded terms remain explicit; see [Results](results.md#read-uncertainty-with-its-coverage).

## Self-shielding and damage

A separate neutron table supplies finite-dilution unresolved-range Bondarenko factors. Fixed or composition-derived dilution selects factors on the declared grid, and compatibility is checked against the library. This bounded method does not provide resolved-region pointwise shielding or a geometry-dependent transport solution.

NRT damage uses separate damage-energy production data and declared displacement energies. It reports coverage of the material targets; it does not model damage covariance, recombination, or general material evolution.

For equations, data-processing details, and historical controls, consult the [technical method record](https://github.com/AvilaLabs/ACTINV/blob/master/docs/METHOD.md). [Validation evidence](validation.md) explains what the recorded checks establish.
