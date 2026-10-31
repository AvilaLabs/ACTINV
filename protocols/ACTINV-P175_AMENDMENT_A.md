# ACTINV-P175 Amendment A — the reference sampler repaired, the run repeated

Registered after the P175-FAIL verdict (`ae258dd`, ledger 305) and before any rerun. This is the protocol's one repair
round.

## What happened

The scored run failed G2 on two rows: steel at 30 days, uniform response, `V1/V_s` = 1.741 (case A) and 1.673 (case C).
Every other row passed. After the verdict the per-group ratios were read from the recorded artifacts, and the sampler
was emulated exactly from the library, the covariance sidecar, the cell-a flux and its own random stream. The read found
two defects in the reference sampler (`study.robustness`, `crates/actinv-core/src/study.rs`), not in the exports under
test:

- **Steel: the eigendecomposition stopped before it converged.** `jacobi_eigh` stopped when
  `off_norm <= 1e-12 · max|a_ii|`. Threshold rows collapse to about 1e-20 b, so their relative variance reaches 1e19–1e20,
  and the tolerance was far above every physical entry. The applied factor carried 0.0118 of Cr-52 MT16's 0.0310
  relative variance and 0.151 of Fe-54 MT103's 0.176. Through the run's own draws, the emulation reproduces the sampled
  Cr-51, Mn-56 and Mn-54 spreads to within 4 %.
- **Tungsten: the PSD repair added variance.** The evaluated W-182 block over MT 16, 102, 103 and 107 is jointly
  indefinite (negative eigenvalue mass 0.0015). Clipping the covariance's eigenvalues raised the least uncertain row,
  W-182 MT16, from 3.88 % to 4.75 % standard deviation. W-181, which comes almost entirely from that row, was sampled 1.5
  to 1.6 times wider than the evaluation states.

The exports reproduce the raw collapsed relative covariance (Cr-51 0.175 against 0.174, Mn-56 0.364 against 0.364,
Mn-54 0.350 against 0.350), and G3 passed.

## Change

The sampler is fixed in ACTINV (commit `6e39ea9`), with three changes:

1. **Per-pair Jacobi convergence.** A pair is rotated only while `|a_pq| > 1e-14 · sqrt(|a_pp a_qq|)`, and the sweeps
   stop when one rotates nothing. A unit test reproduces a four-row matrix with a 1e20 diagonal beside rows near 1e-2 to
   1e-10 relative per entry. A Python replica of the previous stopping rule ends after one sweep on that matrix, 3.7e-3
   off; the per-pair rule converges in three sweeps to 8e-16.
2. **Repair in correlation space.** The correlation matrix is eigendecomposed and its negative eigenvalues are clipped.
   Each factor row is then rescaled to unit norm and multiplied by the row's standard deviation, so every evaluated
   variance is kept and only an indefinite correlation structure changes. The recorded `xs_clipped` is now the clipped
   mass relative to the correlation matrix's trace, which is what its field name
   (`negative_eigenvalue_mass_clipped_relative`) always said.
3. **Moment-matched lognormal draws.** The log covariance is `L = ln(1 + Σ_rel)`, so the factors' covariance is `Σ_rel`
   itself rather than `exp(Σ_rel) − 1`. This is the convention the protocol's flux sampler already uses
   (`s² = ln(1 + e²)`, P93).

Change 3 was decided after the failing rows had been seen. It moves the sampled variance toward the first-order
variance by design, by about 1 % to 13 % on the rows that matter here; changes 1 and 2 remove the defects. All three
are disclosed here because the reference is the thing being changed.

`controls/check_p175.py` changes its `PINS` and its CPU limit (below):
- `binary_sha256` becomes `1460accb937f6457ff03207d417ae104a4226d6be795a7f82ac60dd47e52d660`, the release build of
  `6e39ea91fc9e49355e927afa9f38027601ca45ff`;
- `source_commit` becomes `6e39ea91fc9e49355e927afa9f38027601ca45ff`.

The harness, the statistics module, the cases, the responses, the registered constants (k, delta_lin, the pair
allowance, N, the seeds), the gates and the allowed sentence are unchanged. `delta_lin` still adds the recorded clipped mass.

`cgroup_ok` in the checker accepts up to four CPUs (`cpu.max` quota at most 4 × period), not two. The owner raised the
per-job limit to four cores on 2026-10-31, and the rerun runs with `CPUQuota=400%` and `RAYON_NUM_THREADS=4`. Memory
(6 GiB, no swap) and tasks (128) are unchanged. The thread count is not a registered constant. Nothing here shows
whether it changes any result beyond floating-point summation order, and the gates resolve variances to about 10 %.

## Rerun

`target/p175-run/` and the first run's outputs are deleted before the rerun, so no sample carries over:
- the twelve case artifacts in `results/p175/`;
- `results/p175_r2s_cov.json`;
- `results/p175_verdict.json`.

`results/p175/ci_runs.json` is replaced at the verdict. All four cases run again from the amendment's registration commit, with the pinned
binary inside the bounded scope. `controls/check_p175.py` then writes `results/p175_verdict.json`, and G4 needs CI
green on the new evidence commit. The first run's evidence stays in history at `cf4620f`.

A second failure is final. There is no further repair round.

## Expected, not a gate

With the emulated, converged factor, the steel step-3 rows should come close to 1. Tungsten's Ta X-ray groups should
come close to 1 once W-182 MT16 keeps its evaluated variance. Neither expectation is a threshold; the registered gates
decide.
