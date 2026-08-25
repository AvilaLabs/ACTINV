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