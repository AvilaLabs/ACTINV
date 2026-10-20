# P149 Amendment A — study drivers, data choices and definitions

Registered before any evidence run of P149. No study is weakened; where the registered text could not be followed
as written, the reason is given and the replacement is stated. Development runs of the drivers on the candidate
binary are disclosed in A9; they are not evidence.

The revision release is the release that carries P148 (source changed), so S0 to S6 run on that release.

## A1. S0 regeneration

`controls/p149_s0_regenerate.py`. FNS part: `controls/cb3_fns.py` with `ACTINV_LIBRARY` set to the EAF-2010 ACTINV
library (`~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz`), scored with the manuscript's rules, which the driver
states in code: within a factor 1.3 at every point; within 20 % of the FISPACT-II reference at every point;
experiment geometric mean within 10 % of the reference's; the `run_fns.py` dispositions (agreement with the
measurement at every point, `|C − E| ≤ max(2σ, 0.1 E)`, else agreement with the reference at every point,
`|ln(ACTINV/FISPACT-II)| ≤ 0.1`, else neither). Table 3 part: the five controls are rerun, each record copied to
`results/p149/s0/` and the committed record restored byte-for-byte. Each value is printed next to the v1.0.0 value.
The manuscript prints the second disposition as "agreeing more closely with the reference"; the rule it counted is
the 0.1-in-ln rule above, and the revised text says so.

## A2. S1 contributors

`controls/p149_s1_fns.py` records, at every measured point, the three leading heat contributors of each code
(ACTINV from a `summary.dominant_top = 3` step summary, FISPACT-II from the largest printed per-nuclide heat at
that row). The checker selects the points the protocol names (either code beyond `|ln C/E| = ln 2`, or ACTINV
beyond 10 % of FISPACT-II); recording every point is a superset. Gas production at the end of irradiation (H and He
appm, H-3) is recorded per experiment.

## A3. S2 not-run variant

The full-precision decay-library variant is not run. ALARA's `convert_lib` writes half-lives, energies and cross
sections as 32-bit floats (`src/DataLib/ALARALib.C`); no converter option writes more digits, and both codes already
read the same FENDL-2 decay text. The record states this. The three other variants run as registered, by changing
the committed P17 driver's schedule, initial vector and ALARA input in `controls/p149_s2_alara_nickel.py`.
The cobalt-free variant removes the cobalt line from ALARA's mixture and scales the other three element densities
by 3/4: ALARA renormalizes a mixture's volume fractions, so deleting one of four equal lines alone would raise the
others by 4/3 and change every initial inventory. A trace cobalt density (1e-30) was tried first and rejected because
it leaves a cobalt row whose identity in ALARA's output is ambiguous.

## A4. S3 histories and the H3 data

`controls/p149_s3_histories.py`, built on `g2_p17_identical_data.py`.
- Step boundaries. H1 cools to 1 h, 1 d, 30 d, 1 y and 10 y; H2 to 1 h, 1 d, 30 d and 1 y. A year is
  365.25 d. ALARA prints number densities only at shutdown and at the cooling times, so ALARA is compared there; the
  dense reference and OpenMC are compared at every step boundary. H2's zero-flux steps become ALARA delays after the
  preceding pulse; each nonzero multiplier is an ALARA flux with that normalization.
- H3 data. Cu-63(n,γ)Cu-64 and Cu-65(n,2n)Cu-64 are ALARA's FENDL-2 sample records, and Cu-64's β− and EC/β+
  branches are its FENDL-2 sample decay record. The sample library stops at Z = 30, so Eu-151 has no records there.
  The Eu-151(n,γ) ground and isomer records are Co-59's (n,γ) ground and isomer records relabelled: every code reads
  the same arrays, which is what an identical-data comparison needs, but they are not Eu-151 cross sections. The
  decay records of Eu-151, Eu-152, Eu-152m1 and every decay descendant are the ENDF/B-VIII.0 records, appended
  verbatim to the sample decay text before its TEND record. If ALARA cannot convert or run the extended data, H3 is
  reported against the dense reference and OpenMC only, with ALARA's error recorded.
- The comparison threshold is 1e-12 of the initial atoms (2.5e19 per target, so 1.75e20 in H3).
- Dense reference precision. The per-step exponentials are evaluated at 40 significant digits with mpmath
  (`controls/p149_mp_dense.py`) and again at 60; the largest relative difference between the two is recorded as the
  reference's own precision. A development run found that `scipy.linalg.expm` in double precision differs from
  ACTINV and from OpenMC by 1e-8 for Na-24 over the one-year step, while ACTINV and OpenMC agree with each other:
  with Na-24m1 (20 ms) in the subspace, the rate times the step is near 1e9, and scaling and squaring loses that
  accuracy. The double-precision result is kept in the record beside the 40-digit one.
- ALARA's schedule is the irradiation alone; its cooling times come from its cooling block. ALARA prints each
  cooling time with `sprintf("%7g %c   ")` into a 16-byte buffer (`src/CoolingTime.C`), so `31557600 s` aborts it
  ("buffer overflow detected"); each cooling time is written in the largest of s, m, h, d that gives the exact value
  in at most ten characters (1 y as `365.25 d`). ALARA's own `y` unit is not used: it is 364 days (`365/7`
  truncates in `src/input_file_utils.C`).
- ALARA rows. ALARA prints some rows with the mass number only (`-64`). Its table is ordered by Z, then A, then
  state, so such a row is the lowest-Z unused nuclide of that mass and state at or above the previous row's Z whose
  initial loading matches; half-life breaks remaining ties, and a half-life that disagrees with the record is listed
  in the record, not used to reject the row. A row ALARA prints as `-nan` or `inf` is listed and never compared.

## A5. S4 processing

`controls/p149_s4_doppler.py`. GROUPR on the 709-group structure with `iwt = 3` and infinite dilution
(`sigma0 = 1e10`), appended to the committed P17 deck at each temperature. ACTINV's pointwise values come from
`dump processed-xs FILE MT T E...` at NJOY's BROADR energies inside the first resolved range of MF=2, in batches of
4000 energies. Points and groups below 1e-8 of the largest NJOY value in the range are excluded from the relative
statistics, and the counts are recorded.
Group statistics are also given separately for groups wholly inside the first resolved range, groups straddling
its upper energy, and groups above it. A development run found Ag-107's largest group difference (1.2–1.5 %) in
the one group that straddles its resolved-range top (6.31–6.61 keV across 6.5 keV), where pointwise resolved data
meet the unresolved-range treatment, while the median group difference was 1.4e-5; the split keeps that group from
being read as a resolved-range result. Nothing is excluded: the overall maximum is still reported.

## A6. S5 library and coverage definition

Only TENDL-2025 has an ACTINV covariance sidecar, so S5 uses `actinv-data/v1.1.0` (`tendl-2025-neutron-709g.npz`
and its bound `uncertainty/tendl-2025-neutron-709g.cov.npz`), not the TENDL-2017 benchmark library. The FNS iron case
is Fe `1996exp_5min` (its spectrum is byte-identical to `examples/fns_fe_5min.json`, the P30 spectrum); the Co case is
Fe 99.99 wt% with Co 0.01 wt%; SS316 is `1996exp_7hour`. The schedule is cut to the irradiation and the three named
cooling times (decay-only gaps are exact, so the values at those times are unchanged).
- Decomposition. Sensitivities come from the run's `uncertainty` output; the collapsed covariance of the covered rows
  from `p20_collapse_probe` on the same flux. The variance is split into the diagonal `Σ s_i² C_ii`, cross terms
  within one target nuclide (cross-MT blocks) and cross terms between targets. The recomputed `sᵀCs` is checked
  against the run's `mf33_standard_uncertainty²`.
- Response-weighted coverage is `Σ_covered |s_i x_i| / Σ_all |s_i x_i|`, with `x_i` the collapsed one-group cross
  section: `s_i x_i` is the response change per unit relative change of row `i`, which is defined for rows without
  covariance. The five largest uncovered rows by that weight are named.
- Sampling. `actinv study run` with `robustness` (MF=33 channel only, 1000 samples, seed 149005) and
  `statistics` (200 bootstrap resamples). The driver recomputes mean, standard deviation, skewness and the 2.5 % and
  97.5 % nearest-rank quantiles from the per-sample outputs, checks them against the tool's, and gives
  percentile-bootstrap 95 % intervals (2000 resamples, seed 149505).

## A7. S6 non-zeros, cases and yields

`controls/p149_s6_scaling.py`. No result field reports the matrix non-zeros. They are counted from `prune_probe`'s
capture of the pruning inputs (the full decay, reaction, feed and removal triplets over every state): distinct
off-diagonal positions between kept states plus one diagonal entry per kept state. For `none` every state is kept
and the capture is taken with `reach` (the inputs do not depend on the mode). Tungsten is W `1996exp_7hour`. ACTINV
ships no fission yields; the fission case uses the ENDF/B-VIII.0 neutron fission-yield files for U-234, U-235, U-238
and Pu-239, each hash-pinned in the spec, with `energy: spectrum_average`. Each configuration runs once as a warm-up
and then ten times; repeats must produce byte-identical results. Peak RSS is GNU time's maximum resident set size.
The timed runs request `outputs` = inventory, activity, heat and ledger. On Dy 2000exp_5min the unpruned CRAM-48
solve took 6 s and 112 MB with those outputs and 540 s and 4.1 GB with the defaults (photon sources over every
state), with identical inventories, activities and heat; timing the defaults would measure photon output, not the
solve. One default-output run per pruning mode at CRAM order 16 is recorded alongside, so that cost is reported.

## A8. Checker

`controls/check_p149.py` recomputes from each record's stored per-case values: S0 values from the copied
records and their differences from the v1.0.0 values; S1 per-experiment and summary metrics (`cb3_fns.product_metrics`
and `aggregate`) and the contributor selection; S2 and S3 relative differences and maxima from their rows; S4
internal consistency of the reported statistics (S4 stores summaries, not arrays); S5 variance splits, shares and
their sums, the variance check and the agreement of the recomputed sampling moments with the tool's; S6 pattern
consistency and that kept states shrink from `none` to `reach` to `rate`. The paper's FNS tables are produced by
`paper/jrnc-revision/analysis/fns_error_metrics.py` from the S0 and S1 records. It has a selftest that plants one error per study and requires each to be
caught.

## A9. Development runs

Each driver was run on the P148 candidate binary (`13af0225…`) to find driver errors before this registration.
None of these runs is evidence; the evidence runs on the revision release. Runs and the changes they led to:

- S0, Table 3 part: the five controls reran and every committed record was restored byte for byte; values agree
  with v1.0.0 to the printed digits except the ALARA Fe-56(n,p)Mn-56 difference, 4.1146e-8, which the manuscript
  prints as 4.12e-8 (a misprint, corrected in the revision). No change.
- S1, all 132 experiments (about ten minutes): the checker accepts the record. With gas production on, ACTINV and
  FISPACT-II agree on Al 1996exp_7hour (geometric-mean C/E 0.0758 and 0.0751), where the v1.0.0 run without gas
  differed elevenfold. No change.
- S2, all four variants: the cobalt-free variant first used a trace cobalt density, which left an ambiguous ALARA row,
  and then deleted the cobalt line alone, which ALARA's volume-fraction renormalization turned into a different initial
  inventory; it now scales the other densities by 3/4 (A3).
- S3, H2 and H3_H1: `ACTINV_CACHE_DIR` must be absolute (work paths resolved); ALARA received cooling inside its
  schedule (now irradiation only) and aborted on long cooling labels (A4); the double-precision dense reference was
  replaced by the 40-digit one (A4); ALARA prints unlabelled rows (`-64`), so stable isobars (Ni-64, Zn-64) are told
  apart by ALARA's Z order, with half-life and initial loading as cross-checks, and ALARA lists Eu-151 as stable where
  the ENDF/B-VIII.0 record does not, which is recorded; ALARA printed `-nan` and `inf` for He-4, Nd-143, Pm-147 and
  Sm-147, none of them in the compared network; every non-finite ALARA row is recorded and none is compared.
  Smoke results: ACTINV against the 40-digit reference 1.3e-12 (H2) and 3.3e-12 (H3_H1); OpenMC 2e-14; ALARA 4.2e-5
  and 3.7e-5 at its four printed digits.
- S4, all six cases: the group statistics were split by region after Ag-107's largest group difference was found in
  the group straddling the resolved-range top (A5); the checker's group relative difference was aligned with the
  driver's (relative to NJOY).
- S5, Fe with 20 samples: the study's `spec_ref` was written as a relative path, which `actinv study run` reads
  against the study file; work paths are resolved (also in S6).
- S6, Fe with one repeat: no change.
