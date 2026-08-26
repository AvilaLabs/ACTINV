# ACTINV P4b — correction of two mis-specified controls from P4

**Opened:** 2026-08-27 at the principal's direction after P4 closed P4-FAIL. **Scope:** only the two controls whose
premises P4 showed to be wrong. No change to the library, the solver, or any physics. The third P4 failure (G2c, the
Fr-226/Rb-94 grid sensitivity) is **not** in scope: it is a real limitation, flagged in the library index, carried in
the roadmap's v0.1 known-limitations table and routed to P10.

## What was wrong, with the numbers that show it

**1. G2b — non-resonant consistency.** The control compares each MF=3 reaction's library group values against a
pointwise collapse of the same MF=3 data. Since P4's inelastic-isomer fix, the library deliberately stores for MT=4 and
MT=51–91 the *isomer partial* cross section (production of the metastable state), not the total inelastic cross section,
because (n,n′) is a transmutation only into a metastable state. The control therefore compares two different physical
quantities for those MTs. Evidence: of 771 reactions, every failing one is MT=4 — Zn-77m 8.1e-1, Co-62m 3.5e-1,
Se-77m 2.2e-1 — and all 768 others agree to 4.3e-16.

**2. Subset-vs-full equality (P4 Amendment B).** The criterion demanded 1e-12, i.e. bit-identity. That premise is wrong:
the full library also carries reaction channels for the *products*, so a full-library run includes activation of trace
products that the composition-only subset cannot represent. The difference is a real physical effect, measured at
2.5e-6 on decay heat, not numerical noise.

## Corrected controls

**C1 (replaces G2b).** For MTs that are *not* inelastic: library group values = pointwise collapse to 1e-12, unchanged.
For inelastic MTs, the meaningful statement is internal consistency, so the control instead requires that the library's
loss row equals the sum of that MT's isomer-partial product rows to 1e-12 — a real check that the ground-state loss is
not the total inelastic cross section. Both parts must pass.

**C2 (replaces the Amendment B criterion).** FNS decay heat from the subset library and from the full library must agree
to **≤ 1e-4 relative** at every matched point, and the sign and size must be consistent with added product activation.
Justification for the threshold: the FNS measurements carry ~5 % experimental uncertainty, so 1e-4 is ~500× below the
smallest quantity either library is being compared against; anything larger would mean the subset approximation is
physically visible and the subset schedule would be invalid. The measured value is reported either way.

## Gates
P4b-PASS iff C1 and C2 pass and no other P4 gate regresses (G1, G2a, G3, G4 re-derived unchanged). G2c is expected to
remain FAIL and is excluded from this verdict; P4's record keeps its P4-FAIL close. One repair round. Standing rules apply.
