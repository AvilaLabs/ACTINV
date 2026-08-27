# Validation — FNS decay-heat benchmark (IAEA CoNDERC)

Generated from `results/fns/*.json` (P3 run: EAF-2010 709-group library, ENDF/B-VIII.0 + JEFF-3.3 decay, rate-significance pruning at 1e-8 atoms/g, trace-activation formulation). Reference: FISPACT-II with TENDL-2017 as distributed with the benchmark set. Accuracy is reported, not claimed; the instrument gate is the checker's re-derivation of every C/E.

- experiments: 132 (73 materials); with both codes and matched measurements: 132
- median geometric-mean C/E: ACTINV **1.024**, FISPACT-II **1.009**
- median max|ln C/E|: ACTINV 0.284, FISPACT-II 0.223
- within 30 % of measurement at every point: ACTINV 47%, FISPACT-II 52%
- ACTINV within 20 % of FISPACT-II at every point: 103/132; geometric-mean C/E within 10 % of FISPACT-II's: 88/132
- dispositions: {'AGREE-MEAS': 41, 'AGREE-REF': 32, 'DISAGREE': 59}
- solver: median 71 states after pruning, median 2.7 ms per experiment; all 132 in 0.4 s of solver time

## Largest disagreements with measurement (both codes shown)

| experiment | ACTINV max\|lnCE\| | FISPACT max\|lnCE\| | note |
|---|---|---|---|
| Al_1996exp_7hour | 10.27 | 7.80 | calorimeter floor ~5e-5 μW/g at 13–50 d; codes agree with each other |
| V_1996exp_7hour | 4.03 | 4.05 | late-time floor |
| Tb_2000exp_5min | 3.82 | 3.83 | identical pattern in both codes |
| Bi_2000exp_5min | 3.64 | 4.35 | Tl-206m branch differs between libraries; ACTINV closer at early times |
| Dy_2000exp_5min | 3.46 | 3.46 | identical pattern in both codes |
| Bi_1996exp_7hour | 3.45 | 2.26 | Bi-210 / Po-210 library difference |
| Pb_1996exp_7hour | 3.36 | 3.09 |  |
| La_2000exp_5min | 2.89 | 2.77 |  |

Figures: `results/fns_figures/summary.png`, `results/fns_figures/ce_all.png`. Full table: `results/FNS_REPORT.md`.

## P7 decay-photon and dose controls

P7 uses ENDF/B-VIII.0 MF=8/MT=457 spectra and a response file built from NIST dry-air/elemental coefficient tables.
The gate set is intentionally small for dose physics (Co-60, Cs-137, Ba-137m and continuous-spectrum Mn-68) while the
reader audit still traverses the complete primary decay sublibrary.

| gate | result |
|---|---|
| G1 spectrum reader | Independent Python/Rust readers agree over 3,785 selected record lines; maximum numeric difference `3.04e-16` relative. Both parse 3,821 sections and count 7,113 spectra identically by `STYP/LCON`. |
| G2 source conservation | Maximum independent/Rust difference `1.14e-15`; photon-count closure `3.57e-16`; normalized energy-to-`E_EM` closure `2.90e-15`. Missing-spectrum and custom-boundary omissions are planted and recovered exactly. |
| G3 inventory integration | 21-step Fe problem, 518 per-nuclide photon rows: CLI = Python = harness at 0.0. Worst group/source/heat identity `4.15e-16`; all five input hashes match an independent SHA-256. |
| G4 dose references | Co-60 `0.305647` vs `0.309` (1.09%); equilibrium Cs-137/Ba-137m `0.0769510` vs `0.078` (1.34%). Independent Fe-slab equation and nuclide contribution sum agree at or below `2.47e-16`. |
| G5 transport export | OpenMC/MCNP readers recover identical energies after eV/MeV conversion and identical probabilities at 0.0; strength difference 0.0; OpenMC syntax and MCNP 78-column continuation rules pass. |
| G6 provenance/regression | Wrong response/library hashes fail through CLI and Python. The recorded pre-P7 Fe inventory/activity/heat result has zero scalar differences; P5 and the 10-target P6 CI path remain green. |

The checker verdict is P7-CONDITIONAL because the first G5 execution required one repair round: its independent MCNP
reader skipped the first `SP1` probability, and the export's explanatory comment used 80 rather than the enforced 78
columns. [Amendment A](../protocols/ACTINV-P7_AMENDMENT_A.md) records the repair; no physics tolerance changed.

These are source-term and screening-dose controls, not shutdown-dose-rate transport validation. The contact result is a
semi-infinite-slab air-dose proxy, and ordinary-result transport exports retain a point-at-origin spatial placeholder.

## P8 flux interchange and independent mesh controls

P8 uses deterministic four-group/four-cell transport fixtures, h5py 3.16.0 as the independent HDF5 implementation,
the existing 10-target CI activation library, and an eight-cell exact-library-grid case. No transport executable, full
library build, full FNS rerun or million-cell solve is part of these gates.

| gate | result |
|---|---|
| G1 canonical/FISPACT/rebin | Canonical fields and FISPACT values agree exactly; repeated bytes and exact-grid rebin are identical; the split underflow/destination/overflow case differs from the independent logarithmic-overlap calculation by 0.0 with zero closure error. Truncation, duplicate ID, negative value and missing zero-energy floor fail closed. |
| G2 OpenMC statepoint | Mesh-first/regular and energy-first/rectilinear fixtures agree with independent h5py on every flux, error, index and aggregate at 0.0. Adding 48 MiB of unused HDF5 data did not increase measured importer peak RSS. Wrong score, nuclide, filter set, mesh type, version and omitted source rate are named errors. |
| G3 MCNP readers | Independent MESHTAL and MCTAL readers agree with Rust and with each other at 0.0 for all four cells/groups/errors/totals after MeV/eV and source-rate conversion. Inconsistent total, response/multiplier, wrong tally type, extra dimension and truncation plants fail with the premise named. |
| G4 provenance/interchange | Each of four importers repeats byte-identically; every source, auxiliary and canonical hash recomputes; all formats' first physical spectrum and total are exactly equal. Wrong declared hash, nonfinite JSON, changed footer and a source mutated during import fail without a final output. |
| G5 mesh identity | Eight of eight independently pruned mesh cells equal separate ordinary runs at 0.0 after excluding entry labels/timing. All eight choose different pruning sizes (1–44 states). One/four-thread cell bytes and normalized footer totals are identical; a planted bad fifth cell names `cell-4` and leaves no final file. |
| G6 scaling/regression | Four-worker, four-cell-chunk measurements at 8/16/32/64 cells held peak RSS within 270,336 bytes while output grew linearly. Workspace tests and strict Clippy pass; the P5/P6/P7 verdicts and pre-P8 CLI/Python deterministic result remain unchanged. |

The fitted scaling row for `10^6` cells is explicitly **not executed**: under the measured 10-target/two-step/four-worker
configuration it estimates 573.8 s, about 12.80 GB of output and the bounded-memory model's 127.1 MB peak RSS. These
figures are sizing guidance, not performance guarantees for larger chains, schedules or requested output detail. The
full measured and extrapolated table is in `results/g6_p8_scaling_regression.json`.

The checker verdict is P8-CONDITIONAL. Amendment A records one control repair pass: the independent MESHTAL control
used exact float lookup after MeV/eV conversion, retained source order for total rows, and G4 expected a narrower error
phrase than the strict JSON decoder emitted. Production output and all gate tolerances were unchanged.

## P9 fission, coupled-mode and pulse controls

P9 adds hash-pinned ENDF/B-VIII.0 independent fission yields, explicit isotope/isomer materials, exposure-based
automatic trace/coupled selection and arbitrary piecewise-constant pulse histories. Its final gates use one analytic
synthetic fission fixture, OpenMC 0.15.3, official ALARA 2.9.2, and the CoNDERC U-235 thermal Dickens pulse and Yarnell
20,000 s measurement sets.

| gate | result |
|---|---|
| G1 composition/NFPY | Rust and OpenMC agree on every U-235 yield table at `3.24e-16` worst relative. The three raw independent sums differ from two fragments by at most `4.89e-7`. All explicit ground/isomer material bases agree with independent mass calculations at `1.49e-16`; malformed/ambiguous inputs and bad data fail closed. |
| G2 fission matrix | Exact/interpolated/clamped synthetic matrices agree with independent dense assembly at `1.85e-16`. Parent loss occurs once, mapped plus leakage yield closes exactly to the raw source, missing-product/parent paths remain distinct, and changing MT=459 has no effect. |
| G3 coupled/auto | Threshold optical depths/fractions agree at `2.12e-16`; cases immediately below/above `1e-6` select trace/coupled and non-unit multipliers change the decision. Coupled parent depletion agrees with `N0 exp(-tau)` at `4.00e-16`. |
| G4 pulses/OpenMC | Every boundary agrees with a dense exponential at `1.33e-15` and OpenMC CRAM48 at `3.91e-15` on resolvable populations. Time/exposure/fluence are exact; split/merged equal-flux histories agree at `7.09e-16`, and the decay-gap effect matches its analytic prediction. |
| G5 ALARA | Official source commit `faa5b330…` builds and executes its reference pulse case. With identical FENDL-2 Fe-56(n,p)Mn-56 data, the collapsed rate is exact, 10 pulses/9 gaps are exact, and shutdown Fe-56/Mn-56 differ by at most `4.12e-8`, far below the `5e-4` text-precision tolerance. |
| G6 CoNDERC/provenance | All 175 finite channel points are reported; both per-fission normalizations close independently, all external/certificate hashes rematch, the pre-P9 deterministic result has zero differences, and tests/strict Clippy/rustfmt pass. Accuracy remains reported rather than gated. |

CoNDERC aggregate C/E results are:

| history/channel | points | geometric mean C/E | C/E range | max \|ln C/E\| | RMS experimental sigma |
|---|---:|---:|---:|---:|---:|
| Dickens pulse beta | 32 | 0.9882 | 0.9305–1.0430 | 0.0720 | 0.925 |
| Dickens pulse gamma | 32 | 1.0183 | 0.8852–1.1884 | 0.1726 | 2.169 |
| Dickens pulse total | 32 | 1.0070 | 0.9555–1.0691 | 0.0668 | 0.993 |
| Yarnell 20,000 s total | 79 | 0.9845 | 0.9218–1.0194 | 0.0814 | 1.144 |

The paired FISPACT-II context is UKAEA-R(18)003. ORIGEN values from Gauld's 2019 summary are context rather than a
same-data code comparison because they use SCALE 6.1.3 with ENDF/B-VII.0 yields and ENDF/B-VII.1 decay. The complete
point tables, source-unit treatment, archive metadata anomalies and hashes are in
`results/g6_p9_conderc.json` and [the P9 session record](../sessions_P9.md).

The checker verdict is **P9-CONDITIONAL**. [Amendment A](../protocols/ACTINV-P9_AMENDMENT_A.md) records the one repair
pass: ALARA transcript markers, the FISPACT flux-file trailer, the Dickens pulse ordinate definition, and two
mechanical Rust 1.98 Clippy findings. No physics implementation, source datum, acceptance tolerance or post-hoc
accuracy threshold changed.

## P10 data-completeness and Rust-builder controls

P10 validates the production Rust library path from strict parsing through complete external builds. All source data
and generated libraries remain outside Git; committed results contain their SHA-256 identities and compact evidence.

| gate | result |
|---|---|
| G1 Rust builder/parity/determinism | 231,816 retained ENDF fields across 12 evaluations agree with an independent reader at 0 ULP. Fresh/cached and one/four-worker outputs are byte-identical; all ten rejection plants fail without publishing. |
| G2 R-matrix-limited | FENDL W-186 structure matches independently; CCFE-709 capture and the range integral meet the frozen FENDL/NJOY tolerances, with unsupported-feature plants failing closed. |
| G3 unresolved averages | Ag-107 infinite-dilution values agree with a fresh NJOY2016.79 UNRESR run and independent high-order quadrature; `LSSF=0` addition and `LSSF=1` non-addition close. |
| G4 temperature/ultra-narrow | 0/293.6/600/900 K kernel controls pass; 52 Fr-226 analytic lines agree with direct integration, and Fr-226/Rb-94 density convergence is `8.79e-5` with no flags. |
| G5 charged particles | TENDL-2025 p/d/alpha residual production matches official pointwise tables, and identical TENDL-2017 group rows/rates match official processed FISPACT rows. No FISPACT executable run is claimed. |
| G6 runtime contract | Proton/deuteron/alpha analytic cases are identical through CLI, Python, prepared and mesh paths; mismatch plants fail before assembly and every legacy omitted-projectile neutron field except the intentional v0.2→v0.5 solver-semver certificate leaf is unchanged. |
| G7 complete builds/regression | Five fresh/cached byte-identical libraries contain 12,216 targets and 1,849,479 rows with zero errors, silent fallbacks or convergence flags. Full EAF regression, P5–P9 verdicts, CI subset and strict Rust quality gates pass. |

The checker verdict is **P10-CONDITIONAL** because the append-only frozen record contains repair amendments. The final
builder fingerprint is `7a50ba3441b30b829ae857ed192b2e52554d6c149460475f7735599f29548a43`; exact complete-library hashes and resource
profiles are in `results/g7_p10_builds.json` and [the P10 session record](../sessions_P10.md). This establishes
infinite-dilution unresolved processing, not finite-dilution self-shielding or probability tables.

## P11 covariance, sensitivities and uncertainty controls

P11 validates strict MF=33 ingestion, spectrum collapse, exact differentiation of the selected CRAM recurrence and
first-order response propagation before scanning the complete TENDL-2025 neutron corpus. Python dense/parser controls
are independent of production Rust; NJOY2016.79 GROUPR/ERRORR and OpenMC 0.15.3 provide external numerical anchors.

| gate | result |
|---|---|
| G1 parser/sidecar | Fe-56/Ni-58 retain 209 components and 36,535 numeric fields at zero ULP. One/four-worker and fresh/cached NPZ/index bytes match; one source mutation reuses exactly one checkpoint; all 12 fail-closed plants pass. Peak child RSS is 64,438,272 bytes. |
| G2 collapse/reference | Synthetic LB=0--6/8/9 worst relative difference is `2.754e-16`; three real Fe-56 spectra agree with the independent collapse at `1.198e-15` worst. The amended group-constant NJOY comparison is `2.7266e-4`, inside `5e-3`. |
| G3 CRAM/sensitivities | CRAM-16/48 dense controls pass. Across trace/coupled irradiation, pulse and cooling cases, 288 analytic/finite-difference comparisons have `3.013e-11` worst connected relative difference and `7.124e-13` maximum absolute difference; omitted order is byte-identical to explicit CRAM-16 after the frozen normalization. |
| G4 propagation | Direct, Rust and reported `S C S^T` agree to `2.055e-16` relative or exactly. A fixed-seed, chunked antithetic `2^26` sample differs by `1.943e-4`, inside `1e-3`; cross-term and all negative/nonfinite/dimension edge cases pass. |
| G5 reports/entry points | CLI, PyO3, prepared and mesh scientific/provenance fields are exact across 24 response records. Five input hashes rematch, every required report field is present, and all ten mismatch/selector/completeness plants fail before publishing. |
| G6 corpus/regression/docs | An independent raw-source scan reproduces 2,850 files, 84,489 sections and 285,023 components with zero errors or omissions. Current fresh/cached sidecar and index bytes match; peak fresh/cached RSS is 1,121,943,552/933,879,808 bytes with zero swaps. P5--P10 verdicts, CI subset, docs and exact Rust gates pass. |

The complete sidecar covers 105,817 of 127,724 eligible non-MF10 activation rows (82.848%). Another 40,011 MF=10
rows are explicitly outside MF=33; 21,907 non-MF10 rows lack a self-covariance and remain uncovered. These are corpus
coverage facts, not fabricated error estimates. The sidecar contains 84,489 LB=5, 116,045 LB=6 and 84,489 LB=8
components and has SHA-256 `c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde`.

The checker verdict is **P11-CONDITIONAL** because Amendments A--E preserve the aggregation/control repairs. Exact
inputs, exclusions, hashes and resource evidence are in `results/g1_p11_covariance.json` through
`results/g6_p11_complete.json` and [the P11 session record](../sessions_P11.md). MF=33 bands do not include decay,
MF=32, MF=40/yield, flux, composition, response-coefficient or model uncertainty and are not licensing safety margins.

## P12 v1.0 hardening controls

P12 adds explicit radiological-response inputs, independently rechecks the embedded natural abundance/mass table,
and compares one published FNG/ITER cell history before packaging the 1.0 interfaces. External primary data and all
generated nuclear-data libraries remain outside Git; compact results retain their hashes.

| gate | result |
|---|---|
| G1 radiological responses | Clearance, waste, ingestion and inhalation responses agree with an independent dense calculation in all 80 comparisons. CLI, Python, prepared and mesh scientific fields are exact. Thirteen malformed, ambiguous, incomplete or hash-mismatch plants fail without a result. |
| G2 primary tables | Independent extraction from the pinned Meija et al. and AME2020 primary files reproduces all 289 abundance/mass pairs, all 84 element sums and the generated Rust table byte-for-byte. The production certificate names and hashes both sources. |
| G4 FNG/ITER cell 620 | Co-58, Tc-99m, Mn-56 and Cr-51 agree at every one of 170 endpoints. The worst relative population difference at or above one million atoms is `2.88e-14`, inside the frozen `1e-4` bound; 120 independently read reaction-rate comparisons differ by at most `3.24e-16` relative. Repeated temporary libraries and scientific results are identical. |

The cell-620 comparison validates activation-history reproduction for the supplied material, one-group data, decay
chain and schedule. It does not execute or validate neutron/photon transport, shielding geometry, a full shutdown-dose
model, or a regulatory analysis. Radiological-response controls verify formulas, data handling and provenance, not the
applicability of a user's table or scenario.

P12 remains open while its remaining reliability, clean-clone release, and closure controls are completed. A technical
repository verdict does not create a tag, registry publication, GitHub Release, software qualification, or approval.
