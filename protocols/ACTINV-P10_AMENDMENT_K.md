# ACTINV P10 Amendment K — product fallback error budget

**Date:** 2026-08-27. **Trigger:** Amendment J's first 80-decimal-digit smoke rerun found one genuine Rust value just
outside the frozen EAF product criterion. In EAF-2010 Mo-92
(`c765eaef9c91985786fa22b8030450b981f5a9e64f68786cd02ee1c39eca60be`), MF=3/MT=102 times the MF=9
ZAP=42093/LFS=0 product over CCFE-709 group 476 (`33113.11`–`34673.69 eV`) is
`0.037051160156158343811332867765231563763374251193176762747501906503671337004310001 b` at 80 decimal digits.
Rust produced `0.037051160156075305 b`: an `8.30377433480578e-14 b` deviation, 1.1206 times the unchanged tolerance
and `2.2412e-12` relative.

## Diagnosis

The group contains 364 source-breakpoint intervals. The exact binary64 power expansion correctly detects a worst
signed-term conditioning ratio of about 31,184, above its conservative 4,096 cutoff, and therefore selects adaptive
log-energy quadrature. That fallback assigned the full `2e-12` gate tolerance independently to each interval. Each
local result satisfied its request, but their accumulated group error can slightly exceed the group-level criterion.
The guarded fast path and its conditioning decision are working as designed; the fallback's local error budget is
too loose for a many-interval group.

## Frozen repair

1. Product-collapse intervals rejected by the guarded exact-power path use `2e-13` relative adaptive-quadrature
   tolerance, one tenth of the unchanged group-level `2e-12` acceptance criterion. Breakpoints, interpolation,
   finite support, conditioning cutoff, exact path, compensated accumulation and all scientific criteria remain
   unchanged.
2. A synthetic many-interval lin-lin product regression compares the fallback result with a separately arranged
   stable analytic segment sum. The hash-pinned external Mo-92 row must agree with the 80-digit value at the original
   `2e-12` relative or `1e-14 b` absolute criterion, and all 482 EAF MF=9 rows must pass Amendment J's complete
   80-digit control. The twelve Amendment F/G hotspot rows must still finish below the former five-second cutoff.
3. This source change invalidates every older builder fingerprint by design. All five one-target profiles and all
   fresh/cached complete P10 libraries must be regenerated under the new fingerprint before G7 can pass. The running
   pre-Amendment-K neutron attempt is stopped; its content-addressed checkpoints remain external evidence but are not
   reused or claimed as final.

No nuclear-data input, architecture, ownership model, interpolation law or scientific tolerance changes. Because a
frozen gate execution required this documented numerical repair, a successful P10 close remains
**P10-CONDITIONAL**.
