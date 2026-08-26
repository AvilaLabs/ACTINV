# ACTINV P7 Amendment A — G5 repair-round record

**Date:** 2026-08-26. **Trigger:** the first execution of P7-G5 did not pass. This amendment records the one repair
round required by the standing rules; it does not alter the P7 physics, scope, reference values or tolerances.

The failure had two causes:

1. The independent MCNP control reader treated `SP1` as though it had the extra `L` token used by `SI1 L`, skipped
   the first probability, and therefore reported a probability sum of 0.9985356498272275. Inspection of the emitted
   card showed that the probability was present and that the control was wrong. The reader now handles the two card
   grammars separately and compares OpenMC eV with MCNP MeV after the explicit `1e-6` unit conversion.
2. The MCNP explanatory comment occupied 80 columns while the gate control deliberately applies the source-card
   wrapping limit of 78 columns to every emitted line. The comment was shortened; no executable source value changed.

The repaired G5 must still meet the original protocol verbatim: cross-export energy/probability identity at 0.0 after
unit conversion, source strength within 1e-12, Python syntax, and valid MCNP continuation/line lengths. Because a gate
was executed and repaired, a successful P7 close is **P7-CONDITIONAL**, not P7-PASS.
