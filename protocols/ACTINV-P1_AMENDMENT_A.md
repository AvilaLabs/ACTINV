# ACTINV P1 — Amendment A (append-only), 2026-08-25 — control (2) domain

**Trigger:** control (2) as written — dense `scipy.linalg.expm(A·dt)` on the full 3,821-nuclide matrix —
overflows to NaN: the decay sublibrary contains nuclides with λ·(1 y) far beyond double range (max λ
recorded in ledger entry 2), so scaling-and-squaring cannot represent the exponential. This is a
limitation of the control, not evidence about the solver.

**Repair:** control (2) is evaluated on the closed sub-network reachable from the initial nuclide
(breadth-first over the nonzero pattern of the irradiation matrix, leakage row included). The restriction
is exact because no other component is ever populated. Tolerance and threshold unchanged (≤ 1e-6 relative
for components > 1e-15 of the total). The full-matrix CRAM result is compared on those indices. This is
the one permitted repair round for control (2). Nothing else changes.
