# P145 Amendment A — definitions fixed before implementation

Registered 2026-10-16, after the protocol and before any source change, evidence input or run for the phase. It
fixes three definitions the protocol leaves open. No gate is relaxed.

## 1. Clopper–Pearson interval and the established class

For k of n samples, the interval at confidence level γ (α = 1 − γ) is the two-sided equal-tailed Clopper–Pearson
interval: lower end the α/2 quantile of Beta(k, n − k + 1) (0 when k = 0), upper end the 1 − α/2 quantile of
Beta(k + 1, n − k) (1 when k = n). The classes are ordered A, B, C, above Class C. A sample whose class is unknown,
and a sample whose solve failed, counts in n and is at most no class.

The class established at γ is the first class X in that order for which the lower end of this interval for
P(class at most X) is at least γ. When every sample is at most X this needs (α/2)^(1/n) ≥ γ, i.e. at least 72
samples at γ = 0.95; the result states the minimum for its γ. When no class is established, the result reports, for
the first class whose observed frequency p̂ exceeds γ, the smallest n′ ≤ 1 000 000 for which k′ = ⌊p̂·n′⌋ would
establish it, and otherwise states that the observed frequency cannot establish a class.

## 2. What "exactly" means in U2

Counts, probabilities (k/n), percentiles (nearest rank, Hyndman–Fan type 1, as `sample_stats::percentile_sorted`),
P(normalized sum > 1) and every class must be equal as values. Clopper–Pearson ends are roots of a regularized
incomplete beta function; the independent checker computes them with its own implementation and they must agree
within 1e-12 absolute.

## 3. The C3 closed form

The sampler's MF=33 factor on a row is exp(σz − σ²/2) with z standard normal and σ² the relative-variance diagonal
entry (`study.rs`, nearest-PSD factor). C3 uses one reaction and a rank-one synthetic covariance, fully correlated
across its groups with one relative variance σ², so every group takes the same factor and, with burn-up negligible
and the target nuclide alone in the governing table sum, the normalized sum is s₀·F. Then
P(s > 1) = Φ((ln s₀ − σ²/2)/σ), with s₀ the nominal normalized sum computed in the same run.

P(s > 1) above one half needs s₀ > 1, and reaching 0.1 with a fixed s₀ > 1 needs σ ≥ 2.56. So each of the three
variances gets its own material amount, chosen and committed before any run so that the closed form is near 0.9,
0.5 and 0.1. The gate compares the sampled frequency with the closed form evaluated at the s₀ the run computed, using
the 99.9 % Clopper–Pearson interval of the protocol.
