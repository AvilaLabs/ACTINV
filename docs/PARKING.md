# Parking lot

Discoveries that are out of the open phase's scope. Dated, append-only. Each item is either scheduled into a phase
of `ROADMAP.md` or explicitly left out of v1.0.

- 2026-08-25 — 18 EAF-2010 products (W-193, Re-195, Os-197…201, Ir-200…202, Pt-203, Au-206, U-243…245, Np-245/246,
  Am-250) have no evaluated decay data in ENDF/B-VIII.0 or JEFF-3.3. Ledgered as such; left out of v1.0 unless a
  library appears (none known).
- 2026-08-25 — fission (MT=18) on actinide targets booked to leakage: no yields yet → P9.
- 2026-08-26 — R-matrix-limited (LRF=7) resolved ranges (FENDL W-186) unsupported → P10.
- 2026-08-26 — unresolved ranges with LSSF=0 (FENDL Ag-107) unsupported → P10.
- 2026-08-26 — abundance and atomic-mass tables copied from openmc.data; independent re-verification → P12.
- 2026-08-26 — ψ-function reference disagrees with the exact kernel by ~1 % beyond ±5 Γ (Gaussian-in-energy
  approximation); information only; no action.
- 2026-08-26 — TENDL-2023 vs EAF-2010 Fe-56 on the FNS spectrum: (n,2n) −19 %, (n,γ) +60 %; investigate which is
  closer to measurement once the TENDL library exists → P4 report.
- 2026-08-26 — CRAM round-off on equilibrium components (2e-11 relative per component; 3e-9 on heat) limits
  cross-network agreement criteria; consider CRAM-48 or substepping for tighter reproducibility → P11 (uncertainty
  budget must include it).
- 2026-08-26 — openmc 0.15.3 wheel lacks its compiled resonance module; do not rely on openmc for reconstruction
  references; NJOY-processed ACE of the same evaluation is the reference.
