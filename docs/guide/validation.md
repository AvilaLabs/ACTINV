# Validation evidence

ACTINV carries numerical controls, reader and data-processing checks, and comparisons with measured activation histories. Evidence establishes behavior for the recorded inputs, implementation, and acceptance bounds. Assess applicability to your own material, spectrum, data, time range, and response separately.

## What has been checked

| Evidence | What it assesses | Practical limit |
| --- | --- | --- |
| Analytic and independent CRAM controls | Time integration, network accounting, and numerical comparisons | Does not establish evaluated-data accuracy |
| IAEA FNS decay-heat experiments | Recorded irradiation/cooling cases across 73 materials and 132 experiments | Results depend on evaluation choice and the benchmark metric |
| IRDFF-II spectrum-averaged cross sections | 120 measured reaction cross sections in the Cf-252 and U-235 fission spectra (IRDFF-II Tables 18–20), with TENDL-2025: median absolute log error 0.014 to 0.024 per table | Tests cross sections in fission spectra, not 14 MeV fields or irradiation histories |
| Fission decay-heat experiments | Fission-product heat after thermal fission of U-233, U-235 and Pu-239, measured at five laboratories (Oak Ridge, Los Alamos, IRT, UC Berkeley, CEA Cadarache), from 0.75 s to 1.5 × 10⁵ s; see [the benchmark](benchmarks.md#fission-decay-heat-five-laboratories) | Tests fission yields and decay data, not activation cross sections; thermal fission only |
| FNG/ITER cell-620 history | Supplied activation history and selected nuclides over recorded times | Does not validate complete shutdown-dose transport |
| Photon and response controls | Reader agreement, source conservation, unit conversions, and screening responses | Limited reference cases; contact dose remains a slab proxy |
| Import and mesh controls | Supported tally subsets, normalization, rebinning, and independent-cell output | Does not qualify the originating transport calculation |
| Covariance and sensitivity controls | Retained uncertainty propagation and coverage reporting | Does not include all uncertainty sources |

## Compare the same data

Engine comparisons and library comparisons answer different questions. A calculation with EAF-2010 and one with TENDL-2025 can disagree because their evaluated inputs differ. Record the activation and decay data for both sides before assigning a difference to a solver.

The older [validation record](https://github.com/AvilaLabs/ACTINV/blob/master/docs/VALIDATION.md) begins with a historical EAF-2010 run. Its figures are tied to that run and are not the current default-library score. [Understand the competitive benchmark](benchmarks.md) explains the later comparisons, metrics, and data-choice limits, with links to the full report and evidence.

## Follow the evidence

The repository stores protocols, amendments, compact results, and independent checker scripts. Some verdicts are conditional or failed; those records are retained with their stated scope. A later software release does not convert a failed evaluation comparison into a pass.

Use the [recorded validation controls](https://github.com/AvilaLabs/ACTINV/blob/master/docs/VALIDATION.md), [data disclosure](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_LIMITATIONS.md), and relevant release notes together. [Scope and qualification](qualification.md) states the boundary between these checks and approval of an analysis.
