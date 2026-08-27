# ACTINV P10 Amendment J — full-EAF MF=9 oracle precision

**Date:** 2026-08-27. **Trigger:** the first bounded complete-EAF G7 regression preflight checked all 816 targets,
115,702 current rows and 82,032,718 group values. Structural parity passed, but the independent binary64 analytic
oracle rejected cancellation-sensitive MF=3 x MF=9 products. Its worst result was EAF-2010 Mo-92
MF=3/MT=102 x MF=9 product ZAP=42093/LFS=1 in CCFE-709 group 413: the oracle gave
`0.001789649670961842 b`, Rust gave `0.0017896496705367608 b`, and the apparent deviation used 42.51 times the
unchanged tolerance.

An 80-decimal-digit evaluation made directly from the binary64 source fields gives
`0.0017896496705370347320735716584233754034994367074775212194123160728584575683530582 b`. Rust therefore uses
only 0.02740 of the frozen tolerance, while the screening oracle uses 42.48. The production result is correct; the
purported independent truth was not precise enough. The same preflight also left four MF=9 groups in the legacy
domain because its product-table classifier still used Amendment C's superseded bound rather than Amendment I's
ratio-log correction.

## Diagnosis

The binary64 oracle expands each lin-lin/log-log product interval into signed powers. Although the physical product
is nonnegative, coefficient construction can be severely cancellation-conditioned. `math.fsum` accurately sums the
already rounded terms but cannot recover digits lost while constructing those coefficients. This is independent of
the production implementation's compensated and guarded path. A complete source census finds exactly 230 MF=9
files and 482 product rows: 142 use MF=3 INT=2 with MF=9 INT=2, 283 use MF=3 INT=2/5 with MF=9 INT=2, and 57 use
MF=3 INT=5 with MF=9 INT=2. No other interpolation law is needed for this corpus.

## Frozen repair

1. Every group of all 482 EAF-2010 MF=9 product rows is evaluated at 80 decimal digits from the exact binary64
   source fields. The independent calculation uses the union of group and source breakpoints and the analytic
   lethargy antiderivative for histogram, lin-lin and log-log power factors. Conversion to binary64 occurs only after
   the high-precision group average is complete. The acceptance criterion remains `2e-12` relative or `1e-14 b`
   absolute; the original binary64 oracle remains a non-normative conditioning diagnostic.
2. Product groups eligible for legacy P2 comparison use Amendment I's corrected ratio-log bound, including its
   intercept and group-width logarithm terms. Groups excluded by that bound remain covered by the 80-digit oracle;
   no group is silently dropped.
3. The bounded complete EAF control must again cover all 816 targets, all current rows and every group, enumerate
   every structural exception, and pass both the unchanged legacy domain and independent current-domain checks.
   The twelve isolated Amendment F/G product rows and the complete P10/Rust gates must also pass.

No production code, builder fingerprint, nuclear-data input, interpolation law or scientific tolerance changes.
Because a frozen gate execution required this documented control repair, a successful P10 close remains
**P10-CONDITIONAL**.
