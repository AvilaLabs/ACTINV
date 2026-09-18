# FNS iron diagnosis ledger

Append-only execution history; no experimental-accuracy acceptance gate.

## 2026-09-18: initial execution

- Protocol frozen and pushed in `8446fee` before execution.
- Implementation `5697487219535b49485b11f07d579cbd02467ee8`.
- Hosted run: https://github.com/AvilaLabs/ACTINV/actions/runs/35400313891
- All original-case regression checks and four new diagnostic tests passed.
- `initial.json` preserves the first diagnostic receipt unchanged.
- Independent heat closure: maximum relative discrepancy 4.45e-16.
- 1996 geometric mean C/E 0.9262795135736329, 5/20 inside reported errors.
- 2000 geometric mean C/E 1.0594975056891731, 8/21 inside reported errors.
- The archived spectra are numerically different. The procedure did not assume
  equality or require it to pass. An explicit follow-up spectrum-swap control
  is frozen in Amendment 1 before running that additional calculation.
