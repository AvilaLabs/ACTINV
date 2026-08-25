# ACTINV P3 — Amendment B (append-only), 2026-08-26 — control refinements (G2 a/b/c, G3)

1. **G2 (a) procedure.** First run: reconstruction at 0 K evaluated only at the ACE grid points, then broadened using
   that grid as the 0 K representation. The ACE grid was thinned by NJOY to represent the *broadened* function
   (0.1 %), so it under-resolves unbroadened narrow resonances; maxima of 2–5 % appeared at such peaks while the
   median agreement was 8e-5 (elastic) and the 1 eV values agreed to 1e-5. Corrected procedure: reconstruct at 0 K on
   a resonance-adaptive grid (log backbone ∪ E_r ± 40 Γ_r in 161 points per resonance ∪ the ACE points), broaden on
   that grid, sample at the ACE points. Tolerance unchanged (≤ 3e-3, median reported).
2. **G2 (b).** A constant cross section is invariant under exact SIGMA1 broadening only for y = √(E/kT_A) ≫ 1; the
   invariant test is applied for y ≥ 10. The 1/v test stands for all E; the test grid is 4,000 log points. Tolerance 1e-6.
3. **G2 (c).** Split: (c1) exact-kernel brute-force quadrature (200,001-point trapezoid over ±10 half-widths) vs the
   analytic SIGMA1 on 1/v, constant, linear, and a single resonance line — ≤ 1e-6; (c2) the ψ-function reference,
   which is the Gaussian-kernel approximation accurate to O(Γ_D/E_r), with the 1/E factor included and E_r = 1e5 eV
   (Γ_D/E_r = 1.4e-4) — ≤ 2e-3 at the peak and in the wings (±20 Γ).
4. **G3 criterion.** First run: max relative heat difference 2.8e-9 between rate-pruned (median 64 states) and
   reachable-set (median 1,440 states) solves, with dropped bounds ≤ 7.4e-6 atoms/g — CRAM round-off on equilibrium
   components (P2 Amendment A phenomenon), not dropped physics. Criterion becomes two-part: (i) heat difference ≤ 1e-8
   relative at every matched point; (ii) for every experiment, Σ_dropped bound × max_i(λ_i Ē_i) ≤ 1e-12 of the
   computed heat at every point (the fail-closed bound on what pruning could have removed). Nothing else changes.
