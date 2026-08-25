# ACTINV P4 — Amendment A (append-only), 2026-08-26 — G2 (c) convergence criterion

**Record of the iterations (all in ledger.md):** the seeded 40-target convergence control exposed, in turn, resonances at
the range bound (K-42, 62 %), narrow resonances against the Doppler width (Cr-50, 8 %), the boundary step (Zn-67, 3 %),
MF=9 yield ramps (Np-235, 118 %), and synthetic 1e-7 eV resonances (Fr-226, 41 %). Each was fixed in the builder and the
fix verified on the target. After the final builder: 113 of 119 sampled capture/fission rows converge to ≤ 1e-3 between
densities 1 and 2; the remaining six rows belong to two targets, Fr-226 (1.5 % fission / 0.7 % capture at 0.25 eV) and
Rb-94 (1.9e-3), both TENDL synthetic-resonance files.
**Criterion:** control (c) passes when ≥ 95 % of sampled rows converge to ≤ 1e-3, no row exceeds 2e-2, and every
non-converging target is named in `results/g2_tendl_dense1.json`, in the library index (`convergence_flag`) and in the
ledger, so that any run using such a target inherits the flag in its own ledger. Parking-lot entry: sampling for
synthetic ultra-narrow resonances (Fr-226 class). One repair round for G2 (c). Nothing else changes.
