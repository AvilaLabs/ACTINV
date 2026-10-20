# P149 Amendment B — S5 sampling within the memory limit

Registered after evidence attempt 1 stopped in S5 and before S5 is rerun. No study is weakened: S5 still samples
1000 correlated MF=33 draws per case and compares the sampled spread with the first-order result. S0–S4 are
unaffected.

## B1. What happened

Evidence attempt 1 ran S0–S6 serially on the v1.6.0 release (`b07a763`, `actinv` SHA-256 `7b76596b…`) inside the
required 6 GB cgroup. S0–S4 completed. In S5, Fe and Fe with 100 wppm Co completed. For SS316 the first-order run
completed and all 1000 sampling draws and all 1000 attribution draws were written. Nineteen minutes after the last
draw, the kernel OOM killer stopped `actinv study run` at 5.9 GB resident (`oom-kill`, constraint MEMCG). The limit
held, and nothing outside the cgroup was affected.

The record of the attempt is preserved under `results/failures/p149/s5_oom_attempt1/`:
- the run script, step summary and driver log;
- the kernel and systemd lines;
- the binary hashes;
- the SS316 case-directory listing;
- the partial S5 record holding the two completed cases.

## B2. Cause

After its draws, `actinv study run` runs a built-in first-order comparison (`robustness.first_order_comparison`,
default on). This is a separate uncertainty solve whose responses include `activity:*`, that is, every nuclide.
On SS316 that solve is the only step that runs at the time of the kill, and it holds tangents for every covered
row against every nuclide. The per-sample results are not the cause: the study keeps only the extracted response
values of each draw. A second limit would have been reached next. The driver loaded all 1000 per-sample outputs at
once, about 10 MB of JSON each for SS316, which would also exceed 6 GB.

The built-in comparison's memory use on large networks is a limitation of the solver. It is recorded for the paper
and for a later release, and it is not changed here.

## B3. Changes

`controls/p149_s5_covariance.py`:
- The sampling study sets `robustness.first_order_comparison: false`.
- Each sampling row now records `first_order_std` and `sampled_over_first_order`.
  - `first_order_std` is the first-order run's `mf33_standard_uncertainty` for the same response and time.
  - `sampled_over_first_order` is the recomputed sampled standard deviation divided by `first_order_std`.
  - Both quantities are the same `S C Sᵀ` that the built-in comparison computes. In attempt 1, for Fe and Fe+Co, the
    driver's first-order standard deviation equals the built-in comparison's `first_order_std` to at most
    2.2e-16 relative at every response and time (the partial record above).
- Each per-sample output is read once, reduced to the two responses at the three times, and released. No value
  changes.

`controls/check_p149.py`:
- For every S5 row, the checker requires that `first_order_std` equals the first-order run's value.
- It also requires that the ratio recomputes to within 1e-12.
- A selftest plant with a wrong ratio must be caught. The selftest passes.

## B4. Rerun

All three S5 cases are rerun with the amended driver from a fresh work directory, then S6 and the checker. They run
on the same release binary, so the S0–S4 records of attempt 1 stand. They were produced by unchanged drivers on that
binary.

## B5. Development run

Amended driver, Fe, 20 draws, on the release binary:
- peak resident set 1.6 GB;
- the checker's S5 checks pass;
- `local_vs_nonlinear` is reported as `not_evaluated`;
- sampled-over-first-order ratios are 1.04–1.06.

This run is not evidence.
