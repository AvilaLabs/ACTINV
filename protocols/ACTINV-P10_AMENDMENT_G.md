# ACTINV P10 Amendment G — correctly rounded ENDF numeric parsing

**Date:** 2026-08-27. **Trigger:** Amendment F's independent 80-digit control proved that eleven of twelve formerly
non-terminating EAF-2010 MF=9 product rows met the frozen `2e-12` relative or `1e-14 b` absolute criterion, but the
Zn-68 ground-state row missed it in several groups. The worst result was `5.663341e-12` relative in group 482. No
acceptance threshold is changed.

## Diagnosis

An interval-by-interval reproduction using the values serialized by the Rust reader agrees with the same 80-digit
antiderivative to `7.34e-17` relative in the worst raw-control group. The remaining discrepancy therefore precedes
the Amendment F integration path. The independent parser converts an ENDF exponent-without-`e` field such as
`4.65000+5` into `4.65000e+5` and rounds that decimal once to binary64. The Rust reader instead parsed the mantissa
and exponent separately and evaluated `mantissa * 10^exponent`, introducing a second binary64 rounding. Across the
pinned G1 sources this produces many one- or two-ULP representation differences; long, fine-grid log-log tables can
amplify those input ULPs beyond the frozen collapsed-value criterion even though the decimal source precision is
unchanged.

## Frozen repair

1. For a valid exponent-without-`e` ENDF field, insert an ASCII `e` in a bounded stack buffer and invoke Rust's
   decimal-to-binary64 parser once. Ordinary exponent-bearing decimal fields, blank-field zero semantics, strict
   invalid-field rejection and nonfinite rejection remain unchanged. The fixed-width parser must not add heap
   allocation or `unsafe`.
2. Unit regressions pin exact binary64 equality with the equivalent explicit-`e` spelling, including representative
   values for which the former multiply-after-parse path differs. The independent G1 source comparator must report
   zero binary-representation differences for every pinned activation input.
3. Amendment F's independent 80-digit control over all twelve affected EAF MF=9 rows must pass unchanged, and the
   complete P10 controls and Rust quality gate must be rerun. Source files, source rows, interpolation laws and every
   scientific tolerance remain unchanged.

Because a frozen gate execution required this documented data-handling correction, a successful P10 close remains
**P10-CONDITIONAL**.
