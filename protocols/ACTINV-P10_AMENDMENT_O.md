# ACTINV P10 Amendment O — G4 effective-width control alignment

**Date:** 2026-08-27. **Trigger:** The post-Amendment-N rerun of G4 stopped before scoring because its independent
Fr-226 ultra-narrow control could not pair the first MT=18 certificate at 1.514178 eV with the corresponding parsed
resonance. The Rust certificate reports the Amendment-D effective width `2.1228859e-6 eV`; the control still matched
against the separately rounded evaluator `GT=2.122886e-6 eV`, exceeding its unchanged `1e-10` relative pairing
tolerance.

## Diagnosis

Amendment D froze NJOY's LRF=1/2 component-width reconstruction for production grid placement, analytic
classification and certificates. For `LRX=0`, the effective width is `GN+GG+GF`; for `LRX!=0`, it is the larger of
reported `GT` and that component sum. At the triggering Fr-226 resonance, `GN=1.238151e-6 eV`,
`GG=1.167643e-7 eV` and `GF=7.679706e-7 eV`, whose sum is `2.1228859e-6 eV`.

G4 predated Amendment D and independently reparses the raw evaluation, but its line matcher and direct area
calculation continued to use `GT`. Scanning both MT=18 and MT=102 certificates showed that all 52 pair exactly under
the frozen effective-width rule. The largest relative difference between raw `GT` and effective width among those
lines is `3.851411021897677e-7`; this is evaluator field rounding, not a production or scientific discrepancy.

## Frozen repair

1. The independent G4 line control derives the effective width from its separately parsed `GN`, `GG`, `GF`, `GT`
   and `LRX` fields using the Amendment-D rule before certificate pairing and area integration.
2. Its isolated direct-quadrature copy replaces only that resonance's redundant `GT` with the derived effective
   width, so the independent reconstruction integrates the same NJOY-defined natural width as production.
3. The `1e-10` pairing tolerance, `1e-6` line-area tolerance, source data, temperature, quadrature, group structure
   and every production implementation remain unchanged. The result records the width rule and maximum raw/effective
   difference so a return to raw-`GT` matching is visible.
4. G1, G4, the complete P10 controls and the Rust quality gate must pass before G7 closes.

This amendment corrects a stale control premise exposed by the required post-N rerun. It does not change the final
builder fingerprint or invalidate the five post-N complete libraries because no Rust builder source changes.
