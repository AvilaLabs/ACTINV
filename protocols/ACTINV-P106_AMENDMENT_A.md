# P106 Amendment A — control construction repair (2026-10-03)

The first G0 attempt stopped before sealing or invoking the production CLI.
`frac` still defaulted to applicability `all`, although its called `row_limit`
had been corrected to `general`. The forwarded value prevented C-14's general
row from matching and raised StopIteration. The complete failed log is retained
at `results/p106_g0_attempt_1.log`. No P106 numeric CLI outcome was observed.

Use the existing one repair round to align `frac`'s default with the frozen
general-waste vectors; activated-metal vectors still select their explicit row.
Before any further execution, also repair construction of exact-boundary test
activities: multiply a fraction by its full activity denominator, rather than
rounding fraction-times-limit before multiplication by the unit conversion.
The exact Tc-99 test activity is written as 5,550 Bq, so its fraction is 0.05
and the two-contributor A boundary is exactly 0.1 in the nominal evaluator.
This implements the protocol's mathematical input definitions and avoids an
unintended last-bit offset in a test that asserts an exact boundary.

Register this amendment and bind its hash in G0 before resealing. Preserve the
first attempt. Production arithmetic, the rule pack, all 126 inherited vector
bytes, the 20 case identities and mathematical inputs, class labels, acceptance
tolerances, coverage semantics and scope remain unchanged. No second repair
round is available; a further failed gate closes P106-FAIL and requires a new
successor protocol.
