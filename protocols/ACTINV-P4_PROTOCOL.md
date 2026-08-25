# ACTINV P4 — TENDL-2023 activation library with the own reconstruction; FNS on equal data

**Roadmap row:** P4 (docs/ROADMAP.md). **Opened:** 2026-08-26. **Time box:** three calendar days including background
compute. **Scope (frozen at hash):** build the full TENDL-2023 neutron activation library from the IAEA mirror with
ACTINV's pipeline — own ENDF-6 parsing (MF=2, 3, 8, 9, 10), resolved-resonance reconstruction (SLBW/MLBW/Reich–Moore)
for the loss cross sections MT 1/2/102/18 where a resolved range exists, SIGMA1 broadening to 293.6 K on a
resonance-adaptive grid, 709-group flat-lethargy collapse of every (target, MT, product, LFS); products from MF=8
(LMF 3/9/10), else MT arithmetic, else leakage. Rerun the FNS set with this library. Anything else → docs/PARKING.md.

## Gates
**G1 Build.** All 2,848 target files parsed; every failure or unsupported feature ledgered per target: NRO≠0, LRF=4/7,
LRU=2 with LSSF=0 (capture then uses MF=3 background only and the target is flagged INCOMPLETE-URR), MT=5 products
(not tracked; flagged), MF=8 header mismatches. Zero silent skips. Peak memory per worker ≤ 2 GB; `ulimit -v`.
**G2 Reconstruction controls.**
 (a) FENDL twins: for every TENDL-2023 target whose MF=2 section is byte-identical (numeric fields) to the FENDL-3.2c
     file of the same nuclide, the library's 709-group capture from our reconstruction+broadening is compared with the
     same collapse of IAEA's NJOY ACE (293.6 K): one-group value on the FNS Fe spectrum ≤ 3e-3 relative; per-group
     ≤ 1e-2 for groups with σ ≥ 1e-4 b (report the count and the worst).
 (b) Non-resonant consistency: on a seeded sample of 40 targets (seed 20260826), every MT without resonance
     contribution: library group values = pointwise collapse (P2 method) to 1e-12.
 (c) Grid convergence: on the seeded sample's resonant targets, capture group values from the standard adaptive grid
     vs a grid with twice the arctan density agree ≤ 1e-3 relative on every group with σ ≥ 1e-4 b.
**G3 FNS on equal data.** All 132 experiments with the TENDL-2023 library; C/E reproduced by the checker to 1e-12;
tables: ACTINV/TENDL-2023 vs ACTINV/EAF-2010 vs FISPACT-II/TENDL-2017 vs measurement; per-experiment dispositions;
library-difference table (top contributors' one-group σ, TENDL-2023 vs EAF-2010). Accuracy reported, not gated.
**G4 Certificate.** Inputs (TENDL zips manifest, library npz, decay files, fns.zip, sources, binary) hashed; re-derived.

## Verdict (`controls/check_p4.py`)
P4-PASS: G1 zero silent skips, G2 (a)(b)(c) pass, G3 ran and reproduced, G4 matches. P4-CONDITIONAL after one repair
round. P4-FAIL otherwise. UNSCORED: time box. Standing rules of the roadmap apply.
