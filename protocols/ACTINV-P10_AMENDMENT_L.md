# ACTINV P10 Amendment L — local log-coordinate stability

**Date:** 2026-08-27. **Trigger:** Amendment K's first isolated Mo-92 rerun improved but did not close the
80-decimal-digit product regression. For the hash-pinned EAF-2010 Mo-92 source
(`c765eaef9c91985786fa22b8030450b981f5a9e64f68786cd02ee1c39eca60be`), MF=3/MT=102 times the MF=9
ZAP=42093/LFS=0 product over CCFE-709 group 476 (`33113.11`–`34673.69 eV`) remained
`0.037051160156079135 b`, a `7.9209e-14 b` deviation from Amendment K's frozen 80-digit reference and 1.0689
times the unchanged acceptance tolerance. Tightening the fallback tolerance by ten had changed the value by only
`3.83e-15 b`, proving that recursive truncation error was not the dominant residual.

## Corrected diagnosis

The adaptive fallback formed every subinterval as `a = ln(low)`, `b = ln(high)` and then integrated over `b-a`.
For the 364 narrow source-breakpoint intervals in this group, subtracting global logarithms near 10.4 discards
significant low bits before quadrature begins. Those width errors do not telescope when the positive integrand varies
between intervals, and a tighter adaptive tolerance cannot recover them. This coordinate cancellation, not an ENDF
interpolation change or discontinuity, accounts for Amendment K's tolerance-independent remainder. Amendment K's
smaller fallback error budget remains a conservative group-level guard, but it is not by itself the complete repair.

## Frozen repair

1. Adaptive lethargy integration uses the local coordinate `u = ln(E/low)` over
   `[0, ln1p((high-low)/low)]`, evaluating interior energies as `low*exp(u)`. The existing direct `next_up` and
   `next_down` endpoint sampling is retained in energy space so zero-measure TAB1 double points keep their intended
   interior-side treatment.
2. Amendment K's `2e-13` product-fallback tolerance, the exact-power path and its 4,096 conditioning cutoff,
   interpolation laws, finite support, breakpoints, compensated group accumulation and all scientific acceptance
   criteria remain unchanged.
3. A synthetic 364-interval regression compares weighted constant integrals with independently evaluated local
   `ln1p` widths. The pinned Mo-92 case joins the permanent 80-decimal-digit product control, bringing it to fourteen
   MF=9 rows across seven files; every row must satisfy the original `2e-12` relative or `1e-14 b` absolute criterion
   and every isolated build must remain below the former five-second cutoff. Amendment J's complete 482-row census
   remains mandatory.
4. This source change invalidates every earlier builder fingerprint by design. All five one-target profiles and all
   fresh/cached complete P10 libraries must be regenerated under one post-Amendment-L fingerprint before G7 passes.

No nuclear-data input, ownership model, concurrency design, allocation strategy, unsafe code, interpolation law or
scientific tolerance changes. Because a frozen gate execution required this appended correction, a successful P10
close remains **P10-CONDITIONAL**.
