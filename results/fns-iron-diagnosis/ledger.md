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

## 2026-09-18: intermediate manifest failure

- The protocol/evidence-only commit `0fe2833` preceded the associated runner
  changes and manifest refresh. Its full-controls run failed the tracked-file
  manifest check before building:
  https://github.com/AvilaLabs/ACTINV/actions/runs/35400723083
- `db856cf` refreshes the manifest and adds the spectrum-swap implementation.
  This was a packaging failure, not a numerical result; final checks must pass
  on the final committed tree.

## 2026-09-18: spectrum and inventory diagnosis

- Amendment frozen and pushed in `0fe2833`, implementation `db856cf`.
- Successful hosted diagnostic run:
  https://github.com/AvilaLabs/ACTINV/actions/runs/35400790079
- Five focused tests, original-case controls and all three numerical cases passed.
- `diagnosis.json` preserves the extended receipt from that run.
- Spectrum-swap heat ratios: 0.9992700604987458–1.0083667909543392.
- Mn57 first-point ACTINV/FISPACT atom ratio: 0.8179413518313414;
  inferred mean-energy ratio: 1.000605969508966 (rounded reference).
- No solver change or data adjustment is justified by these controls. Production
  attribution remains unresolved across the different activation libraries.
