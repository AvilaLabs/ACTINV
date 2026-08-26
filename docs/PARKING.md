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
- 2026-08-26 — TENDL synthetic resonances 1e-7…1e-5 eV wide (Fr-226, Rb-94 class): group values converge to 2e-3–1.5e-2, not 1e-3, between grid densities; flagged in the library index. Refine sampling or treat analytically → P10.
- 2026-08-26 — SIGMA1 cost is dominated by output points in the smooth thermal region, where the exact kernel laws
  (1/v invariant; constant → σ₀(1+1/(2y²))) make the correction analytic. Broadening only where σ departs from
  linear-in-E across the window, or reformulating as per-group kernel weights (≈130 grid points per group), would cut
  the dominant cost by ~10–100× → P10, with the exact-quadrature control as the gate.
- 2026-08-26 — the original P5 draft claimed explicit isotope/isomer material keys (`Fe56`, `Ta180m`), but the shipped
  parser only implements natural elemental compositions. P7 corrected the normative documentation and the three
  elemental bases rather than pretending isotope keys work. Explicit isotope/isomer compositions are needed before
  coupled fuel/fission work and are routed to P9.
- 2026-08-26 — finite-dilution unresolved self-shielding, Bondarenko factors and probability-table use are distinct
  from P10's required infinite-dilution LSSF=0 averages. They remain out of v1.0 unless a later licensing use case
  explicitly requires them; ACTINV must not imply them from the P10 implementation.
- 2026-08-26 — TENDL-2025 also publishes triton, helion and gamma incident-particle sublibraries. The v1.0 roadmap
  names neutron plus proton/deuteron/alpha only, so those three additional projectiles remain post-v1.0 scope.
