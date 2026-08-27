# ACTINV P10 Amendment H — local thermal-kink seeding scale

**Date:** 2026-08-27. **Trigger:** the first final-fingerprint full TENDL-2025 neutron build failed closed on Hs-278
(`0563c68e7db9705394b64739546d3ef624442a5b1c3370343127722519c7e1bb`) before publishing a library. At 293.6 K,
MT=18 Doppler linearization ended its frozen sixteenth pass with four segments remaining; the worst relative midpoint
error was `2.457977e-4` at `2.00000136254882818e5 eV`, above the unchanged `2e-4` criterion. The failed run wrote
only three valid target checkpoint pairs, took 25.56 s, peaked at 43,912 KiB RSS and used no swap.

## Diagnosis

The Hs-278 MF=3/MT=18 lin-lin table is approximately zero through 200 keV (`1.502061e-9 b`) and then rises to
`2.747336 b` at 300 keV. Its slope therefore has a sharp threshold kink at 200 keV. The existing feature detector
estimated the kink's thermal significance as the slope change times one Doppler width, but normalized that quantity
by the maximum of the three tabulated ordinates. The distant 300 keV ordinate dominated this scale even though it is
roughly 100,000 eV away while one Doppler width is only about 8.6 eV. The detector consequently omitted its bounded
transition seed. Generic midpoint refinement then spent most of the 16 passes traversing the unrelated 100 keV
source interval and stopped a fraction of one pass short. This is a seed-selection defect, not a failed resonance
model or a reason to relax convergence.

## Frozen repair

1. The background thermal-feature detector retains the same slope-change estimate, but its comparison scale is
   local: the ordinate at the kink and each adjacent linear segment extrapolated by at most one Doppler width (and no
   farther than that segment's endpoint). The existing 129-point `+/-8`-width transition seed is added when the
   unchanged `2e-4` test is met. The SIGMA1 kernel, source interpolation, group weighting, tolerance, pass cap and all
   other grid rules remain unchanged.
2. A regression pins the Hs-278 threshold shape and proves that the first `width/8` transition points are seeded.
   The isolated hash-pinned target must complete, and an independent pure-Python SIGMA1 calculation at interleaved
   points across the full transition must agree with the final Rust linearization under the same `2e-4` relative
   scale (including its `1e-6 b` floor).
3. The final-fingerprint full fresh/cached neutron builds must be byte-identical with zero target errors, followed by
   the complete P10 controls and Rust quality gate. No nuclear-data input or scientific tolerance changes.

Because a frozen gate execution required this documented numerical-grid repair, a successful P10 close remains
**P10-CONDITIONAL**.
