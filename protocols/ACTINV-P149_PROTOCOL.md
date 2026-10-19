# P149 — Evidence for the JRNC revision (studies without source changes)

Registered 2026-10-19, before any study run. Owner direction 2026-10-19: answer the J Radioanal Nucl Chem review of
"ACTINV: an open and reproducible activation and nuclide-inventory solver with executable validation" (submission
a431a67f-…, revision due 2026-11-09). The revised paper describes one revision release (v1.5.0 if no source changes
follow, otherwise the release that carries P148), archived as a new version of Zenodo concept record 22181617. Every
number in the revised paper is regenerated on that release.

This protocol changes no source. It fixes, before any run, what each study computes, from which inputs, and how it
is reported. The studies are descriptive: no study can pass by producing a favourable number. A study passes when its
record is complete, its inputs match the declared identities, its checker recomputes every reported number from the
stored per-case outputs, and a repeat run is identical apart from timing fields. A study that cannot be run is
reported as not run, with the reason; it is never replaced by a weaker study without an amendment.

## Common rules

- Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
- Every study runs on the revision release binary, built from a clean tracked tree; its commit and SHA-256 are
  recorded. Nuclear data are identified by the SHA-256 of every input file.
- Study records go to `results/p149/<study>.json` with a checker `controls/check_p149.py` and a selftest. No `.npz`,
  `.zip` or `.endf` files are tracked.
- The paper's tables and figures are generated only from these records and from records already committed; the
  generator is committed under `paper/jrnc-revision/`.

## Studies

- **S0, regeneration.** Rerun on the revision release every result the v1.0.0 manuscript reports: the 132-experiment
  FNS benchmark with EAF-2010 at the paper's settings, and the Table 3 numerical controls (dense and OpenMC CRAM48
  boundaries, the ALARA Fe-56(n,p) case, the sensitivity and S C Sᵀ controls, the FNG/ITER cell-620 history).
  Report each value next to the v1.0.0 value, with the difference.
- **S1, FNS on TENDL-2017 with gas production.** The 132 experiments with the ACTINV TENDL-2017 library already used by
  `results/cb3_fns_tendl2017.json`, with `options.gas` enabled and per-point nuclide contributions recorded. Report
  the experiment-level, pooled, material-group, cooling-band and irradiation-length metrics of
  `paper/jrnc-revision/analysis/fns_error_metrics.py` (median, 90th percentile and RMS of |ln C/E|, signed mean,
  fractions within 10 %, 1.3× and 2×, within 2σ of the measurement), the same for ACTINV/EAF-2010 (S0) and the
  published FISPACT-II/TENDL-2017 reference, and the per-point ACTINV-to-FISPACT-II ratio. For every experiment where
  either code exceeds |ln C/E| = ln 2 at some point, and for every experiment whose ACTINV-to-FISPACT-II ratio exceeds
  10 % at some point, report the three largest contributors from each code at that point. The decay data differ
  (ACTINV: ENDF/B-VIII.0 with JEFF-3.3 fallback); this is stated with every comparison.
- **S2, ALARA Ni-60 and Ni-61.** In `results/g2_p17_identical_data.json` ACTINV agrees with the dense and OpenMC
  solutions to 1e-15 but differs from ALARA by 4.3e-4 (Ni-61) and 3.9e-5 (Ni-60), above ALARA's five-figure
  print precision. Rerun the committed P17 case with ALARA 2.9.2, changing one factor at a time: Co removed from the
  mixture; one continuous 24-h irradiation in place of the pulses; `truncation` at 1e-30; the converted decay library
  replaced by one written with full-precision half-lives and branchings (if the converter allows it). Report each
  variant's Ni-60 and Ni-61 differences and the explanation they support. If none explains the difference, say so.
- **S3, identical-data histories.** Extend the P17 identical-data method (shared rates, decay data and schedule, an
  independent dense-matrix reference, ALARA 2.9.2, and OpenMC CRAM48 where its operator accepts the schedule) to:
  - H1: one year at constant flux, then 10 years of cooling;
  - H2: twelve steps with flux multipliers 1, 0.5, 0, 2, 1, 0, 1, 0.25, 1, 0, 1, 1 over 30 days, then 1 year cooling;
  - H3: a branching network with β−/EC/β+ competition and two isomers (Cu-64 from Cu-63(n,γ) and Cu-65(n,2n); Eu-152
    and Eu-152m1 from Eu-151(n,γ)), under H1 and H2.

  Report the largest relative difference against the dense reference and against ALARA for every nuclide above
  1e-12 of the initial atoms, at every step boundary.
- **S4, Doppler broadening against NJOY2016.** For Fe-56 (FENDL-3.2c) and Ag-107 (the existing g2 resonance cases)
  at 293.6, 600 and 1200 K: the ACTINV pointwise MT 2 and MT 102 cross sections against NJOY2016 (RECONR, BROADR at the
  same tolerance as the committed 293.6 K comparison) on NJOY's energy grid; and the 709-group MT 102 cross sections
  (constant flux per unit lethargy) against GROUPR with the same weight. Report median, 99th percentile and largest
  relative difference over the resolved range, and the largest group difference, with the energy where it occurs.
- **S5, covariance contributions and sampling.** On FNS iron (5 min), iron with 100 wppm Co (the P30 case) and SS316
  (7 h), for total activity and total decay heat at the first, a middle and the last measured cooling time:
  - each MF=33 row's share of the first-order variance `S C Sᵀ` (diagonal blocks and cross terms reported apart),
    the ten largest rows, and the share of each response produced through rows that have no evaluated covariance
    (response-weighted coverage), next to the row-count coverage;
  - a correlated sampling run of 1000 samples (seed fixed in the record), against the first-order result: mean,
    standard deviation, skewness and the 2.5 % and 97.5 % quantiles, with bootstrap intervals.
- **S6, scaling.** On FNS iron, SS316, tungsten, and a fission case (natural uranium in the FNS spectrum, coupled, the
  bundled fission yields), with `prune` set to `rate`, `reach` and `none`: the number of states and non-zeros, the
  solve time over 10 repeats (median and range), and the peak RSS, at CRAM order 16 and 48. The cgroup CPU quota and the
  host are recorded; times are reported as measured on that host only.

## Verdict

P149-COMPLETE when every study has a checked record or a stated reason it could not run. A failing checker is
preserved under `results/failures/p149/`; repairs are registered as amendments first.

## Not claimed

The studies are descriptive and do not establish accuracy outside their cases. S1 is not an identical-data
comparison: the activation evaluation is shared, the processing, decay data and solvers are not. S3 checks solver and
schedule handling on shared data, not nuclear data. S5 covers only MF=33 cross-section covariance. S6 times are
specific to one host and its limits.
