# P134 Amendment A — Gate definitions corrected after the first run

Registered 2026-10-15, after the first P134 checker run (`results/failures/p134_initial/`, candidate `2708493`
release build, SHA-256 `73786dbf…d410`) and before any further run or code change.

## Finding

G2 passed. G3 and G4 failed because of two errors in how the gates were written, not in the change under test:

1. **G3's `pathway_closure ≤ 1e-9` cannot be met by any build.** `pathway_closure` is the largest relative
   difference over every populated nuclide, with no significance floor. It is dominated by nuclides near 1e-68
   atoms/g, where CRAM round-off is relative order one: the reference's own `examples/fns_fe_5min.json` result
   reports 3.05e48. The registered per-nuclide check (nuclides above 1e-12 of the tracked inventory, decomposition
   total recovered from each listed chain) passed in all three cases: worst 4.6e-15 (517 nuclides, flux 0.37),
   4.7e-15 (566, pulsed), 3.8e-15 (517, step spectrum).
2. **G4's linearity in the multiplier is false for chains with more than one reaction.** A chain with k reaction
   links scales as multiplier^k; the 0.63 deviations are second-order chains (for example Fe-56 → Mn-55 → … →
   Ti-51), which scale as 0.37².

## Amendment

1. **G3** passes when the per-nuclide check passes: for every nuclide above 1e-12 of the step's tracked inventory,
   every listed chain's `atoms_per_g / fraction` equals the inventory within 1e-9, with at least one nuclide
   compared. `pathway_closure` is reported, not gated.
2. **G4** is replaced by an equivalence that holds at every chain order: the FNS spec with flux 0.37 on the
   irradiation step against the same spec whose spectrum (`flux_per_group` and `total`) is scaled by 0.37 and run at
   multiplier 1.0. Every step's inventory and every listed pathway (matched by nuclide, source and first product)
   at or above 1e-12 of that step's tracked inventory agree within 1e-9. The pre-repair code fails this (its
   multiplier run feeds sources at the unit rate, its spectrum run at the scaled rate).
3. G2 is unchanged and is re-run. A parking entry records that `pathway_closure` has no significance floor.
