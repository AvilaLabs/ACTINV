# Understand the competitive benchmark

The competitive benchmark compares ACTINV with other inventory and depletion codes on **accuracy, speed, and capability**. It helps you judge which measured differences matter for an activation workflow. The [full benchmark report](https://github.com/AvilaLabs/ACTINV/blob/master/COMPETITIVE_BENCHMARK.md) retains the dated results and links to their evidence.

The comparisons below are recorded experiments from September and October 2026, including ACTINV 1.1.2-era runs and the ACTINV 1.7.0 shutdown-dose timing. They are not fresh measurements of every 1.7.0 feature or a ranking for every material, spectrum, and operating regime.

## Three different comparison questions

| Comparison | Question it answers | What to check |
| --- | --- | --- |
| Calculated responses against measurements | How well does this code and selected data predict the experiment? | Material, spectrum, history, response, and data versions |
| Codes using common evaluated cross sections | How much do processing, chain representation, and numerical methods differ? | Decay data and represented product states can still differ |
| The same matrix/time step in numerical kernels | How quickly do the implementations solve this numerical workload? | Matrix size, call overhead, threading, hardware, and omitted setup work |

Using TENDL-2025 in one code and TENDL-2017 in another compares both software and data choices. A common-cross-section comparison removes an important source of difference, but it does not make the two complete input chains identical.

## Read the accuracy metrics

`C/E` is the calculated response divided by the experimental response. A value of 1 is exact agreement; 0.8 is 20% below the measurement and 1.2 is 20% above it.

| Metric | Interpretation |
| --- | --- |
| Median `abs(ln(C/E))` | Typical multiplicative error across scored points; lower is better |
| 90th percentile, or p90, `abs(ln(C/E))` | Error toward the difficult end of the distribution; lower is better |
| Pooled geometric-mean C/E | Overall multiplicative bias; closer to 1 is better |
| Experiments with all points inside the acceptance band | Entire measured cooling curves meeting the stated threshold; higher is better |

The logarithm is natural. It treats a factor-of-two overprediction and a factor-of-two underprediction equally. An absolute log error of about 0.693 corresponds to a factor of two; it is not a 69.3% relative error.

The recorded FNS checker defines its "within 30%" band as `abs(ln(C/E)) <= ln(1.3)`, or approximately **0.769 <= C/E <= 1.3**. This is a multiplicative band, not the ordinary 0.7–1.3 arithmetic interval. An experiment counts only when every scored point meets it.

Read the metrics together. A geometric mean near 1 can hide large overpredictions and underpredictions that cancel in the average. Pooled point metrics give more weight to experiments with more scored time points; experiment-level metrics answer a different question. The checker records nonpositive or unaligned exclusions rather than putting them inside a logarithm.

## FNS accuracy: ACTINV and FISPACT-II

The FNS benchmark contains 132 experiments across 73 materials. In the recorded common-TENDL-2017 comparison, 2,360 positive aligned point pairs were scored. ACTINV used ENDF/B-VIII.0 primary decay data with JEFF-3.3 fallback; the published FISPACT-II 4.0 reference used its own condensed decay dataset.

| Metric | ACTINV / TENDL-2017 | FISPACT-II / TENDL-2017 |
| --- | ---: | ---: |
| Median absolute log error | 0.1030 | 0.1053 |
| p90 absolute log error | 0.6894 | 0.6846 |
| Pooled geometric-mean C/E | 1.0605 | 1.0636 |
| Experiments with all scored points in the band | 71 / 132 | 69 / 132 |

ACTINV has a small advantage on the typical error and whole-curve count in this arm; FISPACT has a slightly lower tail error. Switching ACTINV's primary decay data to JEFF-3.3 changes its median error to 0.1058 and its passing count to 69. The narrow margin therefore depends on the decay-data choice; it does not establish a broad solver advantage independent of evaluation uncertainty.

Evidence: [common-TENDL-2017 result](https://github.com/AvilaLabs/ACTINV/blob/master/results/cb3_fns_tendl2017.json) and [JEFF-primary sensitivity result](https://github.com/AvilaLabs/ACTINV/blob/master/results/cb3_fns_jeff_primary.json).

The recorded ACTINV/TENDL-2025 comparison has median error 0.1392 and 59 passing experiments, while the same FISPACT/TENDL-2017 reference has 0.1053 and 69. That is a software-plus-data comparison. It shows why changing evaluations can matter more than a small numerical-method difference. Later decay-aware TENDL-2023 rebuilding improved a separate arm; its metrics and data identities are in the full report.

## OpenMC: a common ENDF/B-VIII.1 comparison

This arm compares the activation/depletion responses on common evaluated ENDF/B-VIII.1 cross sections over **21 experiments and 424 measured points**. ACTINV and OpenMC 0.15.3 use different chain representations and processing paths.

| Metric | ACTINV / ENDF-8 | OpenMC / ENDF-8 |
| --- | ---: | ---: |
| Median absolute log error | 0.131 | 0.181 |
| p90 absolute log error | 0.970 | 1.640 |
| Pooled geometric-mean C/E | 0.788 | 0.685 |
| Experiments with all scored points in the band | 10 / 21 | 10 / 21 |

ACTINV has smaller median and tail errors and bias closer to 1 in this subset. The whole-curve passing count is tied. The detailed comparison attributes important differences to represented isomer-production channels, including tantalum, tungsten, and yttrium. Those findings describe this activation setup; they do not compare transport accuracy or every OpenMC depletion model.

See the [ENDF-8 comparison](https://github.com/AvilaLabs/ACTINV/blob/master/results/FNS_ENDF8_HEADTOHEAD.md) for per-experiment results and shared data-driven misses.

## Fission decay heat: five laboratories

Fission-product decay heat tests the fission yields and decay data behind every fission inventory. Protocol P166 scored ACTINV against the measured tables of the IAEA CoNDERC fission compilation (Gauld, 2019): Friesenhahn 1976 and 1979 (IRT), Schrock 1978 (UC Berkeley) and Fiche 1976 (CEA Cadarache), for U-233, U-235 and Pu-239 thermal fission, with irradiations from a 1 s pulse to 35 days. ACTINV used ENDF/B-VIII.0 yields and decay data. The compiler's ORIGEN calculations, printed in the same tables, used ENDF/B-VII.0 yields and ENDF/B-VII.1 decay data.

Against 692 measured total decay-heat points from 12 fission decay-heat experiments at IRT, UC Berkeley and CEA Cadarache (U-233, U-235 and Pu-239 thermal fission), ACTINV with ENDF/B-VIII.0 yields and decay data has a median |ln C/E| of 0.046 and a geometric-mean C/E of 0.960.

On the same points, ORIGEN with ENDF/B-VII.0 yields and ENDF/B-VII.1 decay data (Gauld, 2019) has a median |ln C/E| of 0.042 and a geometric-mean C/E of 0.962.

| Laboratory | Points | ACTINV median abs log error | ORIGEN median abs log error |
| --- | ---: | ---: | ---: |
| IRT (Friesenhahn) | 530 | 0.042 | 0.040 |
| CEA Cadarache (Fiche) | 42 | 0.066 | 0.065 |
| UC Berkeley (Schrock) | 120 | 0.091 | 0.083 |

The two codes track each other closely, so the remaining differences come from the data and the measurements rather than the solver. The UC Berkeley scatter is mostly at cooling times the compiler flags as unreliable; limited to 600–10,000 s, the pooled ACTINV figure is 0.040. ACTINV's separately measured gamma and beta heat (Friesenhahn) have median |ln C/E| of 0.042 and 0.057. The earlier P9 comparison covers the Oak Ridge (Dickens) and Los Alamos (Yarnell) U-235 measurements, with geometric-mean C/E 1.007 and 0.985.

Evidence: [scored points](https://github.com/AvilaLabs/ACTINV/blob/master/results/p166_conderc.json) and [verdict](https://github.com/AvilaLabs/ACTINV/blob/master/results/p166_verdict.json).

## Speed: numerical kernel versus a full calculation

### A full shutdown-dose calculation with OpenMC

Protocol P163 timed OpenMC's shutdown-dose (R2S) workflow on the steel-cube problem from model to photon source, with ACTINV doing the activation step against OpenMC 0.15.3 doing it in flux mode, its fastest documented setting. Both codes used 2 threads, the same seeds, and the same transport settings; OpenMC used ENDF/B-VIII.1, ACTINV TENDL-2025.

| Mesh cells | ACTINV | OpenMC flux mode | Worst-case ratio |
| ---: | ---: | ---: | ---: |
| 8 | 10.7–10.8 s | 99.5–102.0 s | 9.2 |
| 64 | 16.4–16.8 s | 138.4–139.2 s | 8.2 |
| 512 | 64.5–66.4 s | 435.1 s | 6.5 |

ACTINV is at least 6.5x faster than OpenMC 0.15.3 flux mode at every measured size (8, 64, 512 mesh cells).

Against OpenMC 0.15.3 in its default tally mode (per-nuclide reaction-rate tallies, one energy group), one run at 8 mesh cells, ACTINV's steps 1 and 2 are at least 42x faster; this is not OpenMC's fastest mode.

The shutdown dose rates agreed within 2.1% at every size. The ratio falls as the mesh grows because ACTINV's per-cell activation cost comes to outweigh OpenMC's fixed start-up cost. Evidence: [timing record](https://github.com/AvilaLabs/ACTINV/blob/master/results/p163_speed.json) and [verdict](https://github.com/AvilaLabs/ACTINV/blob/master/results/p163_verdict.json). See [Use ACTINV with OpenMC](openmc.md) to run it.

### The numerical kernel

The recorded CRAM-48 benchmark uses identical operators at the same Python-call boundary with both implementations limited to one thread. Its ratio is **OpenMC median time / ACTINV median time**: above 1 favors ACTINV and below 1 favors OpenMC.

| Operator states | Recorded time ratio |
| ---: | ---: |
| 32 | 13.8 |
| 256 | 2.64 |
| 1,024 | 1.28 |
| 2,048 | 0.98 |
| 4,096 | 1.83 |

The advantage varies with workload size, with near parity at 2,048 states. Small operators make Python and call overhead a larger fraction of the measured time. These figures do not include the complete cost of installation, library processing, cache preparation, or every response calculation. They cannot be applied as a universal speedup to a desktop run or an uncertainty study.

Evidence: [kernel timings and numerical agreement](https://github.com/AvilaLabs/ACTINV/blob/master/results/cb2_performance.json). Use your own complete workflow and comparable hardware when estimating turnaround time.

## Capability and error-bar comparisons

Capability is a separate axis: product-state handling, uncertainty channels, source exports, self-shielding, reverse calculation, and design search may matter even when nominal heat predictions are similar. Check the current [workflow guide](workflows.md) and [qualification scope](qualification.md) for ACTINV's implemented boundaries. A feature being available does not establish its accuracy for every application, and an unavailable competitor was not measured. The [2026-09-30 capability scorecard refresh](https://github.com/AvilaLabs/ACTINV/blob/master/COMPETITIVE_BENCHMARK.md#capability-scorecard--2026-09-30-refresh-actinv-column-only) updates the ACTINV column for gas production, the transport-tally flux uncertainty channel, lean mesh output, and impurity budgets; the competitor columns there remain the dated CB1 survey.

The report also describes discrepancy calibration on 70 FNS experiments with 62 experiments held out. The recorded two-standard-deviation interval covers 93.1% of holdout points. That is useful evidence about this calibrated corpus, not a guarantee of 95% coverage on a new material or spectrum. See [calibration evidence](https://github.com/AvilaLabs/ACTINV/blob/master/results/d2_calibration.json) and the [uncertainty reference](specification.md#additional-uncertainty-reporting).

## Apply the results to your study

Find the comparison closest to your material, reactions, spectrum, cooling times, and response. Check per-material outliers and represented channels, not just the pooled score. Keep software performance, evaluation accuracy, and feature coverage distinct when making a tool choice.

The benchmark supports the recorded comparisons. Your calculation still needs suitable data and an applicability assessment; see [Validation evidence](validation.md), [Known data limitations](data-limits.md), and [Scope and qualification](qualification.md).
