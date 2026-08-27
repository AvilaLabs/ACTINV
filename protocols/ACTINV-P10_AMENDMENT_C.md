# ACTINV P10 Amendment C — G1 legacy builder-parity domain repair

**Date:** 2026-08-26. **Trigger:** the first bounded G1 EAF parity comparison found 139/139 identical Fe-56 row
identities but rejected 673 groups because the frozen all-group legacy comparison includes mathematical domains that
P10 explicitly corrects. This amendment records the one G1 repair round allowed by the standing rules. It changes no
production implementation, scientific tolerance, input, or independent-accuracy criterion.

## Diagnosis

The G1 sentence requiring every pre-P10 neutron/EAF group to reproduce the Python builders conflicts with P10
normative choices 3, 4 and 7–10. The legacy builders sample every ENDF interpolation law onto a finite grid and then
integrate all segments as lin-lin. They also seed each group boundary before interpolation. At a table's upper support
boundary this creates a triangular cross-section ramp from the nonzero endpoint to zero across the next group, even
though the evaluation has no support there. Their lin-lin primitive also evaluates `a ln(E2/E1) + b(E2-E1)` directly;
for finely tabulated reactions its two terms can cancel and accumulate roundoff beyond G1's much tighter tolerance.
R-matrix, unresolved, exact SIGMA1, ultra-narrow and duplicate-energy domains are likewise intentionally changed by
P10 and already have independent controls.

The contradiction is directly reproducible with the pinned EAF-2010 Fe-56 evaluation
`af8e32e7ed025949b65959980d9e2cd5fb6f5ce3c6a7adf2cb4afaac5976d5ab`. Its MF=3/MT=32 TAB1 ends at exactly
60 MeV with 0.0185772 b. In the CCFE-709 60–65 MeV group:

- the P2 Python EAF library gives `9.412500886145632e-3 b` from the invented terminal ramp;
- the declared finite-support integral is exactly zero;
- the Rust P10 builder gives exactly zero, as required by normative choice 4.

The initial comparison was performed through the bounded Rust target extractor; only the 139-row Fe-56 slice was
materialized in Python. The first unbounded exploratory load was killed by the host and produced no evidence or
repository change.

## Repaired G1 parity criterion

All other G1 clauses remain verbatim. Replace only its pre-P10 Python-builder parity sentence with all of the
following:

1. **Structural parity.** Every target and row identity is identical except for changes mechanically required by
   P10's frozen rules: resonance-only capture/fission rows may be added when optional MF=3 is absent; negative-ZAP
   MF=10 fission sentinels are omitted in favour of the canonical fission-products row; and a charged-particle row
   may reflect P10's incident-projectile residual arithmetic. Every exception is enumerated.
2. **Unchanged numerical domain.** For every common non-resonant, non-inelastic row backed directly by the same MF=3
   or MF=10 source table, every group wholly inside the declared table support, using only lin-lin interpolation and
   containing no value-changing duplicate-energy discontinuity, retains the original `2e-12` relative or `1e-14 b`
   absolute criterion where a conservative IEEE-754 forward-error bound for the legacy direct formula is no larger
   than that criterion. The bound is `gamma_(32N+64) * sum(|a ln(E2/E1)| + |b(E2-E1)|) / ln(G2/G1)`, where `N` is
   the number of contributing segments, `gamma_n = n epsilon / (1 - n epsilon)`, and epsilon is binary64 machine
   epsilon. An MF=9 product is in this domain only where one factor is constant on every contributing segment, so its
   product is also lin-lin. The same criterion applies to EAF and neutron inputs.
3. **Changed numerical domains use independent truth.** Groups touching finite source edges, non-lin-lin laws,
   duplicate-energy discontinuities, legacy sums whose declared forward-error bound exceeds the comparison tolerance,
   nonlinear MF=9 products, resonance reconstruction/broadening, MF=6 production or corrected product semantics are
   not compared to the superseded Python approximation. They must instead pass the exact independent controls in
   G2–G5 or an equivalently strict independent collapse in G1, including explicit zero outside source support.
   Excluded identities and groups are counted and reason-coded; none may disappear silently.

Because a second frozen gate criterion required a documented repair, a successful P10 close remains
**P10-CONDITIONAL**. This amendment does not create a weaker numerical tolerance and cannot turn an independently
incorrect Rust result into a pass.
