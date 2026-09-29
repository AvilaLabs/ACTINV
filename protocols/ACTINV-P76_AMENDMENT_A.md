# ACTINV-P76 Amendment A — ex-vessel scenario, nitrogen as a budget variable

Date: 2026-09-29. Status: **frozen before any amendment-A run.** Append-only: the P76 protocol and
`results/p76_verdict.json` stand unchanged.

## Why

P76 executed with G0 and G1 PASS (as-specified points reproduced to 1.1e-15). But in both frozen
scenarios (vessel 1e12, first wall 1e14, 5 y) the matrix alone exceeds CI = 1 at 50 and 100 y
(EUROFER97-type ≈ 4.1e3 at 100 y in the vessel case, C-14 dominated; SS316LN-type ≈ 4.4e4, Ni-63
dominated). So no budget edge exists there, and the spec × k and single-limit verification points
were correctly not emitted: the budget-edge path was not exercised. P76 also found the bundled
limits table has no Ag-108m entry.

## Change

`controls/p76a_impurity_budget.py` (reuses the P76 tool; work dir `target/p76a`; element manifest
sha256 `fb87d68e8e379ce0a6e80d615f03cabaa6e0b366be9b0bd870ed2b5966c539d2`, 20 runs):

- one scenario, `exvessel`: P75 `mix` spectrum at 1e9 n cm⁻² s⁻¹, 5 y, the same cooling steps;
- nitrogen moves from the fixed matrix into the impurity specification (EUROFER97 N 0.03,
  SS316LN N 0.07 wt%), so the budget prices the C-14 driver;
- everything else as P76: materials, limits table, mapping, targets 50/100 y, and the
  verification-point rules.

## Gates (as P76)

**G0** all 20 runs succeed and the amendment hash is registered. **G1** every emitted verification
point reproduces its predicted CI within 1e-6 relative. **Added condition:** if no spec × k or
single-limit point is emitted (the matrix is again unclearable), G1 is reported as
**NOT EXERCISED**, never PASS. Verdict in `results/p76a_verdict.json`.

## Expectation (the author's)

At 1e9 both matrices are below CI = 1 at 50 y, and budget-edge points are emitted and verify near
1e-10. That is uncertain: SS316LN's 12 % Ni gives Ni-63 at roughly 44,000 × 1e-3 ≈ 44 even
before accounting for flux nonlinearity — it may still be unclearable.
