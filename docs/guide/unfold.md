# Unfold a neutron spectrum from foils

A set of activation foils irradiated in an unknown field records that field in its activities. `actinv unfold`
turns the measured activities back into the group fluxes that made them, starting from a prior spectrum you supply,
and tells you how well each energy range is now known.

```sh
actinv unfold problem.json measurements.json unfold-result.json
```

Unfolding codes usually expect activities already reduced to saturated reaction rates, leaving the irradiation
history, decay during irradiation, burn-up and multi-step production to you. `actinv unfold` models each foil with a
full ACTINV solve over the irradiation and cooling schedule you declare, so those effects are part of the model. Data
you have already reduced can be given as reaction rates instead, and the two kinds can be mixed.

## An example

`examples/unfold_foils/` holds a nickel and an aluminium foil irradiated for one hour and counted after one hour of
cooling. The prior is the JAEA-FNS iron spectrum of `examples/fns_fe_5min.json`. **The measured values are
illustrative**, chosen to be consistent with a spectrum somewhat harder than the prior.

```sh
actinv data fetch
actinv unfold examples/unfold_foils/problem.json examples/unfold_foils/measurements.json unfold-result.json
```

It runs four solves (one per foil, two passes) in about 20 seconds once the data are installed, and prints
`chi-square 4.074 on 4`. All four measurements respond mostly near the 14 MeV source peak, so the result shows what
foils can and cannot tell you:

| Band | Posterior / prior flux | Standard deviation, prior → posterior |
|---|---|---|
| below 0.5 eV | 1.000 | 18.5% → 18.5% |
| 0.5 eV – 100 keV | 1.003 | 16.3% → 16.3% |
| 0.1 – 1 MeV | 1.047 | 22.7% → 22.6% |
| 1 – 10 MeV | 1.257 | 22.0% → 17.3% |
| above 10 MeV | 1.070 | 29.5% → 1.8% |

Above 10 MeV the flux is now known to 2%. The Co58 and Ni58(n,p) responses start near 5 MeV, which moves and narrows
the 1–10 MeV band a little. Below 1 MeV nothing responds, and the result is still the prior.

## The problem: prior, foils and history

`problem.json` is an ordinary `actinv-spec-1` neutron problem:

- its **spectrum** is the prior φ⁰, on the library's 709 groups (`descending` and `total` are honoured);
- its **schedule** is the foils' irradiation and cooling history, with flux multipliers as usual;
- its **material** is the default foil material (each foil may declare its own);
- its **library** and **decay** data are used for every foil.

Add an `uncertainty` block holding a `covariance` sidecar (and optionally `confidence_level`) to carry the library's
MF=33 cross-section covariance into the fit. Nothing else may appear there: `unfold` sets the responses and channels
itself. Features whose flux is not the spectrum's (power-driven or wall-loading steps, partitions, step spectra,
feed or removal, rebinning) are refused.

## The measurements

`measurements.json` uses the schema `actinv-unfold-input-1`. The example's:

```json
{
 "format": "actinv-unfold-input-1",
 "prior": {
  "relative_uncertainty": 0.3,
  "correlation": {"kind": "lethargy_exponential", "length": 1.0}
 },
 "foils": [
  {"id": "ni", "measurements": [
   {"nuclide": "Co58", "step": 2, "activity_Bq_per_g": 5450.0, "sigma_Bq_per_g": 164.0},
   {"nuclide": "Ni57", "step": 2, "activity_Bq_per_g": 52490.0, "sigma_Bq_per_g": 1575.0}
  ]},
  {"id": "al", "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"AL": 100.0}},
   "measurements": [
   {"nuclide": "Na24", "step": 2, "activity_Bq_per_g": 1112200.0, "sigma_Bq_per_g": 33370.0}
  ]}
 ],
 "reaction_rates": [
  {"id": "ni58-np", "target": "Ni58", "mt": 103,
   "rate_per_atom_s": 3.39e-15, "sigma_per_atom_s": 1.0e-16}
 ]
}
```

- **prior.** `relative_uncertainty` is the standard deviation of ln φ_g: one number, or one per group in the
  spectrum's order. `correlation` is `{"kind": "diagonal"}` or `{"kind": "lethargy_exponential", "length": L}`,
  correlating two groups by exp(−|Δu|/L) with u the logarithm of the group energy. Groups with zero prior flux or
  zero uncertainty are held fixed.
- **foils.** Each foil has an `id`, an optional `material`, and activities (Bq per gram of foil) at the end of a
  schedule step, each with its one-standard-deviation `sigma_Bq_per_g` and an optional
  `model_relative_uncertainty` for corrections you have not modelled (self-shielding, cover, geometry). A metastable
  state may be written `In115m` or `In115m1`; the result reports the canonical `In115m1`.
- **reaction_rates.** Reactions per target atom per second at the declared spectrum: a `target` nuclide, an ENDF
  `mt`, and optionally the `product` state (without it the reaction's total is used).
- Optional: `report_bands_eV` (energy edges of the reported bands; default 1e-5, 0.5, 1e5, 1e6 and 1e7 eV and the top
  of the structure), `emit_covariance` (write the posterior covariance of ln φ), and `iterations`.

Every measurement needs a positive value and a positive σ. A measurement the prior spectrum does not produce is
refused, since it carries no information the fit can use.

## The result

`unfold-result.json` (`actinv-unfold-1`) holds:

- **groups**: prior and posterior flux per group, the standard deviation of ln φ before and after, and the
  *resolution* 1 − (posterior variance / prior variance): near 1 where the measurements determine the flux, near 0
  where the result is still the prior;
- **bands**: the flux in each report band with its standard deviation and an interval at the confidence level, and
  the correlation between bands;
- **measurements**: each one's prior and posterior prediction, its pull before and after the fit, its cross-section
  uncertainty when a sidecar is given, and its *response window*, the energies below which 5%, 50% and 95% of its
  response lies;
- **consistency**: the χ² at the solution with one degree of freedom per measurement. A χ² far above the number of
  measurements says the measurements and the prior disagree more than their uncertainties allow; look at the pulls.
  Two foils that respond at the same energies but disagree by more than their σ (say Ni57 low and Na24 high, both
  from the 14 MeV peak) show up here, not as a confident spectrum.

## What it does and does not tell you

Unfolding is under-determined. Outside the energies your foils respond to, the posterior is the prior; the
resolution and response windows say where that is. The intervals are only as right as the prior uncertainty you
declare. Self-shielding and flux perturbation by the foils, covers and counting corrections are inputs (through the
foil material, the problem's options or the model uncertainty), not something `unfold` estimates.

## Method and evidence

The fit is generalized least squares on ln φ with the prior's covariance, the measurement uncertainties and, with a
sidecar, the cross-section covariance. It is solved by damped Newton with the local model's exact second derivatives;
every activity and its 709 derivatives come from one solve of the foil. The posterior covariance is the usual
linearized one at the solution.

Protocol P195 and its Amendment A tested it (P195A-PASS):

`actinv unfold` estimates a neutron spectrum from foil activities, modelled with a full ACTINV solve of each foil's irradiation history, and from reaction rates, carrying the library's cross-section covariance into the fit.

On 40 synthetic spectra drawn from its prior, 94% of the true band fluxes fell inside its 95% intervals and 74% inside one standard deviation, and its band fluxes were closer to the truth than the prior's (root-mean-square log error 0.124 against 0.208).

On IRDFF-II's measured spectrum-averaged cross sections in the Cf-252 and U-235 fission fields, predicting each reaction left out of the fit, the median |ln(C/E)| was Cf-252-a prior (the other field): 0.321 → 0.087, UNFOLD CLOSER, p = 3e-08; Cf-252-b prior (a Maxwellian): 0.158 → 0.080, UNFOLD CLOSER, p = 0.00068; U-235-a prior (the other field): 0.246 → 0.044, UNFOLD CLOSER, p = 1.3e-05; U-235-b prior (a Maxwellian): 1.031 → 0.048, UNFOLD CLOSER, p = 2e-09; the Cf-252 reference spectrum used similar measurements in its evaluation.

Evidence: [record](https://github.com/AvilaLabs/ACTINV/blob/master/results/p195a_record.json),
[verdict](https://github.com/AvilaLabs/ACTINV/blob/master/results/p195a_verdict.json),
[protocol](https://github.com/AvilaLabs/ACTINV/blob/master/protocols/ACTINV-P195_PROTOCOL.md) and
[amendment](https://github.com/AvilaLabs/ACTINV/blob/master/protocols/ACTINV-P195_AMENDMENT_A.md).
