# ACTINV P8 Amendment A — G3 repair-round record

**Date:** 2026-08-26. **Trigger:** the first execution of P8-G3 did not pass. This amendment records the one repair
round required by the standing rules; it does not alter the P8 physics, supported formats, fixtures or tolerances.

The independent Python meshtal reader converted the printed `1.0E-4` MeV upper boundary to eV and then located it by
exact Python float equality. The conversion evaluated to `99.99999999999999`, while the separately parsed boundary
evaluated to `100.0`, so the control raised `ValueError` before comparing any production value. The repaired control
selects the nearest boundary and still requires agreement within the protocol's `1e-12` relative tolerance.

The same repair run then reached the total comparison and exposed a second control-only ordering error: energy rows
had been restored to canonical `i`-fastest order but independently read `Total` rows remained in MCNP source order.
The control now maps both through the printed spatial centers before comparison. This is part of the same single G3
repair round; production output again required no change.

The Rust importer had completed successfully and emitted the expected boundary and spectrum; no production code or
gate tolerance changed. Because a named gate was executed and its control repaired, a successful P8 close is
**P8-CONDITIONAL**, not P8-PASS.

During the same pre-rerun repair pass, G4's nonfinite plant correctly failed in the canonical JSON decoder as
`canonical line 2: expected value`. The control had unnecessarily required the phrase `canonical cell`, so it marked
the observed hard error false. Its acceptance check now requires the nonzero exit and actual decoder context. The
plant, parser behavior and final-file absence are unchanged. All affected gates are rerun only after these control
repairs, keeping this one documented repair round.
