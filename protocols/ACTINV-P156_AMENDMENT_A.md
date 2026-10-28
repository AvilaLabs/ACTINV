# P156 Amendment A — arc-dpa agreement across NRT energies is numerical, not bitwise

Registered before any development or evidence run of the checker and before the shipped table is built. Only G3's
W-184 arc comparison changes; every other gate, tolerance and the build command are unchanged.

## Finding that requires it

The candidate's unit tests (workspace test run on the P156 branch, 2026-10-27) show that item 2 holds pointwise: at
every recoil energy the arc-dpa response of W-184 is bit-identical with NRT `E_d` 90 and 70 eV. Integrated over a
recoil spectrum, the two differ by 9e-6 relative. The damage energy, NRT count and arc count share one set of
Gauss–Legendre panels per recoil distribution, and the NRT breakpoints, which depend on the NRT `E_d`, are panel
edges. The incident-energy grid is refined on all three quantities at the `REFINE_TOLERANCE` of 1e-3, so it also
depends on the NRT `E_d`. A group arc value can therefore differ between the two builds by quadrature and grid
refinement alone. G3's "identical" cannot hold without integrating arc on a separate grid, which item 2 does not
require.

## A1. G3

For W-184 built at `W=90` and at `W=70`, `displacement_arc_barn` agrees within 1e-3 relative (the build's
`REFINE_TOLERANCE`) in every group where it exceeds 1e-6 of its maximum. `displacement_nrt_barn` differs by more than
1e-2 relative in every group where it exceeds 1e-6 of its maximum at `W=70`. The largest arc difference and the
smallest NRT difference are reported. The Fe-56 comparison at `Fe=40` (arc equal to the reference binary's) stays
byte for byte, because its `E_d` equals the arc reference and the panels are unchanged.
