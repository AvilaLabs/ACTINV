# ACTINV P10 Amendment I — legacy ratio-log roundoff bound

**Date:** 2026-08-27. **Trigger:** the first bounded G7 whole-EAF regression-control execution reached EAF-2010
Eu-152m (`aafe42f259567ed0deb059cb4222c44c82862cd28d90f78a48c97fcae1bff844`) MF=3/MT=102, CCFE-709 group
450 (`10000`–`10471.29 eV`). The Amendment C classifier called the group unchanged, but the P2 library value
`9.35831494259656 b` differs from the exact Rust value `9.358314942377081 b` by `2.1947954564893735e-10 b`,
above the unchanged `1.8716629885193122e-11 b` tolerance. An independent exact lin-lin collapse is bit-identical
to Rust. The frozen forward-error expression reported only `5.806132864723021e-13 b`, so its claimed conservative
bound was false.

## Diagnosis

The group contains a `10000`–`10000.01 eV` source segment. P2 evaluates its analytic primitive as
`a*log(E2/E1) + b*(E2-E1)` after first rounding `E2/E1`; the segment intercept is approximately `-9.71006605e4 b`.
For such a close ratio, division can move the logarithm by an absolute quantity on the scale of binary64 epsilon.
Multiplication by that large intercept dominates the observed error. Amendment C's
`gamma_(32N+64) * sum(|a log(E2/E1)| + |b(E2-E1)|)` term models arithmetic on the final primitive terms, but omits
the absolute error introduced before `log` and the analogous error in the group-width logarithm. This is a control
model defect, not a Rust parser, interpolation or physics defect.

## Frozen repair

1. Replace only Amendment C's legacy forward-error bound. For each contributing lin-lin segment define
   `ell_i = log(E2_i/E1_i)`, `t_i = a_i*ell_i + b_i*(E2_i-E1_i)`,
   `A = sum(|a_i*ell_i| + |b_i*(E2_i-E1_i)|)`, `C = sum(|a_i|)`, group width
   `L = log(G2/G1)`, binary64 epsilon `eps`, and `gamma = gamma_(32N+64)`. Let
   `D = gamma*A + 8*eps*C`. The corrected absolute group-value bound is
   `D/(L-8*eps) + (A+D)*8*eps/(L*(L-8*eps))`; a group with `L <= 8*eps` is excluded from legacy comparison.
   The `8*eps` allowance covers division plus libm logarithm input/output rounding, while the second term covers the
   independently rounded group-width logarithm. All other eligibility rules and the unchanged `2e-12` relative or
   `1e-14 b` absolute acceptance criterion remain unchanged.
2. Every group newly excluded by this conservative correction is still checked against the independent exact
   collapse at the same scientific tolerance. A pinned Eu-152m regression records the old bound, corrected bound,
   observed P2 deviation and independent value, and requires the corrected bound to cover that deviation while the
   independent value agrees with Rust.
3. The bounded complete EAF structural, unchanged-domain and independent-collapse control, all P10 controls and the
   Rust quality gate must pass. No production code, nuclear-data input, interpolation law or scientific tolerance is
   changed by this amendment.

Because a frozen gate execution required this documented control repair, a successful P10 close remains
**P10-CONDITIONAL**.
