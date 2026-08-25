# ACTINV P2 — Amendment B (append-only), 2026-08-25 — G4 method repair: trace-activation formulation

**Diagnosis (first G4 run, results discarded but logged):** every experiment's reachable network is 1,444 states once
all 816 library targets carry reaction columns. CRAM-16's absolute error is ~1e-16 of the state-vector norm, and the
norm is the stable bulk (≈1e22 atoms/g), so ≈1e6 atoms/g of round-off (of either sign) lands on unpopulated products,
some with λ up to 1e22 s⁻¹. λ × noise × Ē produced spurious, partly negative heat of the order of the μW/g signal:
55 experiments returned NaN geometric-mean C/E and the C/E-reproduction control failed (×180) because stored
inventories excluded negative components while the run's heat included them.

**Repair (one round for G4):**
1. Composition isotopes ("bulk") are not states. A single constant unit state (n0 = 1) carries their production
   terms: every edge (row ← bulk b, rate r) becomes (row ← unit, r·N_b); decay of naturally radioactive bulk isotopes
   is a source in the same way, and their own constant decay heat Σ λ_b N_b Ē_b is added analytically and reported
   separately. Production of a bulk isotope by a reaction is dropped (bulk is constant) and ledgered with its rate.
   Validity is recorded per experiment as the maximum burn-up fraction max_b Σ_r rate_{r,b} × t_irr (must be ≪ 1;
   flagged if > 1e-6). Products keep their full decay and reaction columns.
2. After every step, components < 0 are set to 0; the zeroed atoms are ledgered per step.
3. Stored inventories are the exact vectors used for the heat (no positivity filter), so the reproduction control is
   a true identity.
Nothing else changes. Dispositions and the diagnostic trigger apply to the repaired run.
