# P136 — Sampling distributions and correlation tables (FISPACT-II `SENSITIVITY`/`MCSAMPLE`, row 28)

Registered 2026-10-15, after implementation (merged in `e9a37c2`) and before any evidence.

## Change under test

Opt-in `study.robustness.statistics` adds `robustness.sampling_statistics`: per response and time, the sample
distribution (n, mean, std, skewness, min, max, nearest-rank percentiles, optional histogram), a fixed-seed bootstrap
standard error of the std and of each percentile, the first-order prediction beside the sampled std when computed,
and input–output Pearson and Spearman correlations ranked by |Spearman|; plus a response–response Pearson matrix.

Beyond FISPACT-II: correlations against every sampled parameter with identity (nuclide, MT, product), the
convergence diagnostic for N, and the linear-vs-sampled comparison.

## Gates

Checker: `controls/check_p136.py`.

- **G0.** Protocol hash registered. **G1.** fmt, clippy, workspace tests (including `sample_stats`).
- **G2, default unchanged.** A robustness study without `statistics` (the P30 sampling study, repointed to the
  v1.1.0 TENDL-2025 library and covariance) gives case files identical to the reference apart from timing.
- **G3, recomputation.** With `statistics`, every distribution field, percentile, histogram count and the response
  matrix are recomputed independently from the record's per-sample response values, exact for counts and order
  statistics and within 1e-12 relative otherwise. Input correlations are recomputed from the per-sample inputs if
  the record exposes them; if it does not, that gap is reported and G3 covers the response-side statistics only.
- **G4, determinism.** Two runs with the same seed give identical `sampling_statistics`; a different bootstrap seed
  is not exposed, so only the repeat is gated.
- **G5.** CI green on the pushed commit; committed baselines unchanged.
