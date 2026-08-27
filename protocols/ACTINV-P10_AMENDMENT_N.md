# ACTINV P10 Amendment N — corpus-bounded linearization depth

**Date:** 2026-08-27. **Trigger:** The first post-Amendment-M complete neutron build completed 2,177 of 2,850
cold checkpoints, then rejected TENDL-2025 Co-58 MT=102 because zero-K resonance linearization exhausted the frozen
sixteen-pass ceiling. Thirty-five segments remained; the worst midpoint relative error was `8.697030e-3` at
`1.03500001493450497e1 eV`, versus the unchanged `2e-4` acceptance tolerance. The completed external checkpoints
remain diagnostic evidence only and are not final artifacts.

## Diagnosis

The repaired working Co-58 evaluation has SHA-256
`bbc3f94bb2bb47148feab4825d882c7619a6c431670c11ec5840b9663002d674`. With a temporary diagnostic ceiling of
32, its MT=102 zero-K reconstruction converged on 21,712 points with reported refinement-pass index 19. No grid,
cross-section formula or tolerance changed.

Before selecting a production bound, a four-worker diagnostic processed every resonance-bearing MT=18 and MT=102
reaction at 0 K, CCFE-709 and grid density 1.0 in all 2,850 files of the hash-pinned neutron working corpus. It
examined 3,303 reactions. Co-58 MT=102 was the only reaction with a reported pass index at or above 16 and was the
corpus maximum at 19; no reaction exhausted the temporary 32-pass ceiling.

The old iteration count was therefore an undersized safety bound, not a memory bound or scientific convergence
criterion. The existing ten-million-point cap independently bounds materialized state. Co-58 used 0.21712% of that
cap.

## Frozen repair

1. Raise `MAX_LINEARIZATION_PASSES` from 16 to 20. The loop's zero-based pass index 19 is the final convergence
   check admitted by this bound. No further iteration is allowed without another documented repair.
2. Keep the `2e-4` relative midpoint tolerance, `1e-6` scale floor, midpoint rule, source grids, discontinuity
   handling and ten-million-point safety cap unchanged.
3. Add an input-independent regression with a continuous piecewise-linear kink at the depth-19 alternating-bit
   dyadic coordinate `349525/524288`. It must retain the exact kink, report pass index 19 and fail if the old bound
   is restored.
4. The hash-pinned Co-58 MT=102 zero-K probe must report 21,712 input/output points, pass indexes 19/0 and a finite,
   nonnegative cross section at 10.35 eV. The complete neutron build remains the finite-temperature production
   control.
5. This source change invalidates every earlier builder fingerprint by design. All five one-target profiles and all
   fresh/cached complete P10 libraries must be regenerated under one post-Amendment-N fingerprint. The complete P10
   controls and Rust quality gate must then pass before G7 closes.

No nuclear-data input, ownership model, concurrency design, unsafe code, scientific tolerance or physics formula
changes. Because a frozen gate execution required this appended bounded repair, a successful P10 close remains
**P10-CONDITIONAL**.
