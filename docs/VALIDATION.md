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
