# ACTINV P10 Amendment M — bounded normalized product fallback

**Date:** 2026-08-27. **Trigger:** The first post-Amendment-L complete EAF-2010 build made 773 of 816 cold
checkpoints, then spent several minutes without completing another target. It was stopped after 11:00.92 wall time;
the authoritative resource record showed 2,639.31 user seconds, 399% CPU, 728,536 KiB peak RSS and zero swaps.
The 773 complete content-addressed checkpoint pairs remain external diagnostic evidence and are not final artifacts.

## Diagnosis

Amendment L removed loss of significance from adaptive integration widths, but Amendment K's tighter recursive
fallback was still applied separately to every exact-power expansion rejected by the 4,096 conditioning guard. A
single EAF-2010 Au-197 source
(`16107335e57e5d6ad6f42963a4366b212600d57692a1f3c228f4bb96f7c6fab3`) contains 95,861 such intervals across
its two MF=9 product rows; its worst signed-power conditioning ratio is about `4.0e10`. Nb-93m and Os-188 add
19,299 and 4,373 rejected intervals respectively. Recursive Simpson integration is accurate but has non-terminating
practical cost at this corpus density.

An initial 8/16-point Gauss comparison still reconstructed each node as an absolute energy. At roughly 5 keV, the
rounding error of that reconstruction is material relative to source intervals only 0.001 eV wide: just 30.82% of a
10,000-interval Au-197 sample met the unchanged local error budget. Evaluating the same interpolation directly in a
normalized interval coordinate raised acceptance to 100%; the worst 8/16 disagreement used 0.0022 of the budget.

## Frozen repair

1. The guarded exact-power path and its 4,096 conditioning cutoff remain unchanged. Rejected product intervals are
   first evaluated by paired 8- and 16-point Gauss-Legendre rules over normalized local log-energy fraction
   `s in [0,1]`.
2. Each factor is evaluated from the source segment selected by the shared interval midpoint. Its values at the
   interval endpoints are computed from the nearer source endpoint. INT=1 is constant; INT=2 and INT=4 use the
   local energy fraction `expm1(s*L)/expm1(L)`; INT=3 and INT=5 use `s`, where
   `L=ln1p((high-low)/low)`. Linear-value laws blend endpoint values and logarithmic-value laws blend their logs.
   These are algebraic restatements of the existing ENDF interpolation laws, not new interpolation.
3. The 16-point result is accepted only when its difference from the 8-point result is within Amendment K's
   unchanged `2e-13` relative local budget. Otherwise adaptive Simpson remains the final fallback, now over the same
   normalized coordinate. Its tolerance and depth remain unchanged. Compensated interval and group accumulation is
   retained.
4. The synthetic many-interval analytic regression remains mandatory. Au-197 joins the permanent 80-decimal-digit
   product suite, bringing it to sixteen MF=9 rows across eight hash-pinned files; every row must meet the original
   `2e-12` relative or `1e-14 b` absolute criterion and every cold isolated build must finish below the former
   five-second cutoff. The first repaired Au-197 smoke completed in 0.95 s at 22,752 KiB RSS with zero swaps, and its
   worst 80-digit comparison used 0.0357 of the tolerance. Amendment J's complete 482-row census remains mandatory.
5. This source change invalidates every earlier builder fingerprint by design. All five one-target profiles and all
   fresh/cached complete P10 libraries must be regenerated under one post-Amendment-M fingerprint before G7 passes.

No nuclear-data input, ownership model, concurrency design, unsafe code, scientific tolerance, interpolation law or
exact-path acceptance rule changes. Because a frozen gate execution required this appended performance repair, a
successful P10 close remains **P10-CONDITIONAL**.
