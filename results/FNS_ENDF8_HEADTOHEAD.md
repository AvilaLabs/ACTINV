# FNS head-to-head: OpenMC/ENDF-B-VIII.1 vs ACTINV/ENDF-B-VIII.1 vs ACTINV/TENDL vs FISPACT-II/TENDL-2017

Controlled identical-data arm: the ACTINV/ENDF-8 column uses
`endfb8_fns_709g_pristine.npz` — all 269 ENDF/B-VIII.1 tapes built from
pristine files (the same IAEA evaluations behind OpenMC's HDF5
library), normalized in the builder under `--profile endfb8`, 0 K
pointwise XS matching OpenMC's 0 K data, collapsed to FISPACT-709
groups. Ca40/Cl35 (RML Coulomb penetrability) and Sr88 (KBK
background/KPS phase shifts) now build natively; the score is
statistically identical to the sealed 266-file staged arm
(0.788 vs 0.787 gm).

Scoring: pooled geometric-mean C/E over 424 measured points, 21
experiments. OpenMC values were recomputed after fixing a
pair-to-state mapping bug in `openmc_fns_headtohead.py` (cooling-offset
times were matched against absolute boundary times, freezing early-time
heat at its end-of-irradiation value).

## Summary (pooled over all points)

| arm | gm C/E | median \|ln C/E\| | p90 \|ln C/E\| | expts all-pts within 30% |
|---|---|---|---|---|
| ACTINV / ENDF-8 (pristine 269-file build) | 0.788 | 0.131 | 0.970 | 10/21 |
| OpenMC / ENDF-8 | 0.685 | 0.181 | 1.640 | 10/21 |
| ACTINV / TENDL-2025 | 1.089 | 0.141 | 0.587 | 12/21 |
| ACTINV / TENDL-2017 | 1.261 | 0.086 | 0.504 | 15/21 |
| FISPACT / TENDL-2017 | 1.256 | 0.085 | 0.490 | 15/21 |

ACTINV wall time: 132 experiments, 88 s total (~0.7 s/case).
OpenMC deplete timing per case is in `openmc_fns_headtohead.json`
(`deplete_s` per case).

## Per-experiment geometric-mean C/E

| case | OpenMC E8 | ACTINV E8 | ACTINV T17 | ACTINV T25 | FISPACT T17 |
|---|---|---|---|---|---|
| Fe 2000 5min | 1.05 | 1.05 | 1.07 | 1.06 | 1.07 |
| Ni 2000 5min | 0.55 | 0.57 | 1.07 | 1.21 | 1.08 |
| Cu 2000 5min | 1.06 | 1.05 | 1.05 | 1.06 | 1.05 |
| Al 2000 5min | 1.07 | 1.07 | 1.07 | 1.07 | 1.07 |
| Ti 2000 5min | 0.99 | 1.01 | 1.03 | 1.01 | 1.03 |
| V 2000 5min | 1.14 | 1.14 | 1.16 | 1.15 | 1.15 |
| Mn 2000 5min | 0.60 | 0.60 | 1.12 | 1.34 | 1.12 |
| Co 2000 5min | 1.00 | 1.00 | 1.10 | 1.06 | 1.10 |
| S 2000 5min | 0.60 | 0.62 | 1.12 | 0.89 | 1.12 |
| Nb 2000 5min | 0.03 | 0.03 | 0.93 | 0.96 | 0.94 |
| Mo 2000 5min | 1.03 | 0.99 | 0.97 | 0.86 | 0.98 |
| Ta 2000 5min | 1.46 | 0.89 | 0.85 | 0.83 | 0.91 |
| W 2000 5min | 0.28 | 1.76 | 1.84 | 2.20 | 1.83 |
| Pb 2000 5min | 0.35 | 0.68 | 1.04 | 0.67 | 1.04 |
| In 2000 5min | 1.28 | 1.18 | 29.19 | 1.82 | 29.06 |
| Ag 2000 5min | 1.89 | 1.88 | 1.03 | 0.97 | 1.03 |
| Sn 2000 5min | 0.49 | 0.47 | 1.33 | 1.32 | 1.12 |
| Zn 2000 5min | 0.95 | 0.94 | 0.90 | 0.99 | 0.89 |
| Y 2000 5min | 0.28 | 0.78 | 1.00 | 1.01 | 1.00 |
| SS316 2000 5min | 1.46 | 1.03 | 1.05 | 1.14 | 1.06 |
| Fe 1996 7hour | 0.90 | 0.87 | 0.91 | 0.90 | 0.91 |

## What the identical-data arm shows

**Solver equivalence, demonstrated.** 16 of 21 experiments agree to
within a few percent per-experiment between OpenMC-ENDF8 and
ACTINV-ENDF8 (Fe, Ni, Cu, Al, Ti, V, Mn, Co, S, Nb, Mo, Sn, Zn, SS316,
Ag, Fe-7h). On Nb the agreement is to ~0.1% at every measured point.

**Shared data gaps.** Nb (0.03 both): ENDF-8 lacks Nb-94m production;
both codes miss ~40x while TENDL arms (which carry the isomer channel)
land ~0.94. Mn/S/Sn/Ni undershoots are likewise identical-data misses,
not solver differences.

**The real divergences are isomer channels.** OpenMC's depletion chain
is ground-state-only for the residual divergent cases:

- **Ta-181(n,2n)**: OpenMC chain routes all production to radioactive
  Ta-180g; ENDF-8's MF10 splits it ~59% ground / ~41% Ta-180m (the
  essentially-stable isomer). ACTINV honors the split (Ta180g = 1.2e10
  vs OpenMC 2.1e10 atoms at EOI) → OpenMC overshoots (1.46), ACTINV
  closer (0.89).
- **W-186(n,2n)**: chain maps to W-185g only; ENDF-8 declares the
  W-185m1 branch. ACTINV's W-185m1 carries 97% of its first-cool heat
  (0.122 of 0.125 µW/g); OpenMC's chain lacks the nuclide entirely.
  All TENDL-based codes also overshoot W (~1.8) — the case is a known
  hard outlier; OpenMC's "lower" number is partly the missing channel
  compensating other error.
- **Y-89**: no (n,n') channel exists in the chain at all, so the
  ENDF-8 Y-89m1 isomer production (ACTINV: 4.9e8 atoms, 0.65 µW/g of
  first-cool heat) is absent (OpenMC: 0.12 atoms). ACTINV 0.78 vs
  OpenMC 0.28 — both under, ACTINV's extra channel gets closer.
- **Pb**: ACTINV additionally produces Pb-204m1/Pb-203m1
  (Pb-204(n,n')/(n,2n) isomer branches) — 0.68 vs 0.35.
- **In flips libraries, not solvers**: the 29x TENDL-2017 overshoot
  collapses to ~1.2 under ENDF-8 for both codes — ENDF-8's In-115
  isomer split is the better evaluation there.

**Ag is a shared ENDF-8 overshoot** (1.88 vs 1.89) — same data, same
answer, both wrong; TENDL arms sit at ~1.0.

## Isomer-routing diagnosis (2026-09, post-record)

Per-point curves were re-diffed against FISPACT's dominant-nuclide and
per-step heat tables.

**W is not an ACTINV defect.** Every isomer-carrying arm overshoots
early: ACTINV-ENDF8 1.83, ACTINV-TENDL17 1.94, FISPACT-TENDL17 1.97 at
t=50 s — ACTINV is within ~3% of FISPACT on identical XS data. OpenMC's
0.28 gm hides a mis-shaped curve (0.03 at 50 s recovering to ~1.7) —
its chain lacks W-185m. The universal ~1.9x overshoot is
evaluation-vs-measurement tension in W-185m production, unsolvable at
the solver level.

**Ta is a confirmed library-generation defect in the TENDL arms.**
TENDL-2023 Ta-182M labels its (n,g) isomer product LFS=1; ENDF/B-VIII.0
decay numbers that physical state (519.58 keV, t1/2=15.8 min) LISO=2,
while LISO=1 is a 0.283-s isomer at 16.26 keV. The prebuilt
`actinv_tendl2023_fns_709g.npz` was assembled by the legacy Python
`controls/tendl_build.py`, which rank-compresses positive LFS values
per (MT,ZAP) — it cannot distinguish isomer identity. Ta-182m
production therefore lands on the 0.283-s state, which instantly ITs to
ground: solved Ta-182m inventory ~0 (9.4e-11 atoms) despite a correct
nonzero rate (collapsed XS 3.59e-2). Verified by surgical npz patch
(MT102 ZAP=73182 LFS 1->2): the 15.8-min isomer then populates
(3.5e8 atoms, 2.55e5 Bq). Same library class, systematic scope:
267 isomer rows across the FNS set feed ZAs with multiple decay
isomers, each a rank-map guess; 27 rows point at (zap,lfs) with no
decay state at all. Fix path: rebuild the TENDL FNS library through
the Rust builder with `--decay` so LIS/ELIS-resolved mapping replaces
rank compression — currently gated by fail-closed TENDL normalization
defects (e.g. Ta-181 MT107/MF10 state-sum excess 4.7% > envelope).

**Residual Ta caveat:** with correct routing, ENDF-8.0 decay assigns
Ta-182m ~506 keV/decay deposited heat while FISPACT's EAF decay data
implies ~16 keV/decay for the same 15.8-min isomer — a 31x decay-data
disagreement independent of XS. FISPACT also produces ~4.5x more
Ta-182m atoms (its collapse of the capture-isomer branch differs).
"Identical data" in the TENDL arms covers activation XS only; decay
data differs (ENDF-8.0 vs EAF-2017) and dominates some tails.

**Sn** (+25-29% late-tail excess vs FISPACT-TENDL17): matches FISPACT
at 36 s (0.194 vs 0.189 uW/g, N-16 identical), diverges with time.
The excess is distributed over a rest-tail rather than one carrier
(Sn-111 28% high, In-117 ~50% high, while Sn-123m runs 3x *low* —
inconsistent decay-heat conventions between ENDF-8.0 and EAF decay
files). Same isomer-routing class plausibly involved for the
multi-isomer In products (In-116/118/120 rows carry both lfs=1 and
lfs=2 labels) — unproven per nuclide pending the `--decay` rebuild.

## Coverage caveats

- ACTINV/ENDF-8 library: all 269 tapes build under `--profile endfb8`
  from PRISTINE files (269 targets, 5873 rows). Ca40/Cl35 Coulomb
  penetrability (Steed's method + propagation fallback, ENDF-6
  D.80–D.85) and Sr88 KBK background/KPS phase-shift extensions were
  implemented after this head-to-head record was sealed; none of the
  three are FNS case materials so the scored results stand.
- ~30 tapes needed normalization (see `stage_manifest.json`):
  MF9/MF10 state-vs-total reconciliation under the builder's lethargy
  collapse, zero-width unresolved dof fills, BW width rounding, RML
  photon-pair PNT/SHF -1->0, LRF=0 degenerate ranges, Pb-204 MF10
  relabel+strip, O18/Zn68 EAF-comment scrub, Ta-181 sub-eV n-alpha
  partial-tail reconciliation.
- ACTINV-ENDF8 heat is computed by the ACTINV solver from its own
  ENDF/B-VIII.0 decay chain; identical-data claim applies to the
  activation XS only.
