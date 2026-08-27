# ACTINV P10 Amendment E — G7 Reich–Moore cancellation repair

**Date:** 2026-08-27. **Trigger:** after Amendment D made the complete official neutron corpus strictly parseable, the
first bounded one-target Pb-208 build reached physics processing and failed zero-K linearization. At the frozen
default grid density it still had 38,328 rejected segments after 16 refinement passes; the worst relative midpoint
error was `3.031975e-3` at `1.07368243453865350e-5 eV` in MT=102. The process used less than the memory cap and
published no library, so this is a numerical implementation defect rather than an interrupted or partial result.

## Diagnosis

The no-fission Reich–Moore branch formed capture as reconstructed total minus reconstructed elastic. In a far
subthreshold resonance tail those two values share large potential-scattering terms while capture is tiny. Their
binary64 subtraction loses the small absorption term; adjacent evaluations then contain roundoff-scale jitter that
does not shrink under midpoint subdivision, so the strict linearizer correctly refuses to converge.

For that scalar eliminated-capture collision matrix, with
`W = 1/(1-iK)` and `U = exp(-2i phi) (2W-1)`, the exact absorption identity is

`sigma_capture = (pi/k^2) g (1-|U|^2) = (pi/k^2) g 4 Im(K) / |1-iK|^2`.

The phase factor has unit modulus. The rightmost form computes the small positive quantity directly and is
algebraically identical without subtracting the total and elastic cross sections.

## Frozen repair

Only the LRF=3 no-explicit-fission branch changes: compute capture from the direct identity above, retain the same
collision matrix for elastic, and continue to reject nonfinite or materially negative results. Explicit-fission
Reich–Moore, LRF=1/2, LRF=7 and unresolved paths do not change. A regression exercises a near-unitary far-tail case;
an independent high-precision evaluation checks the identity, and the complete P10 controls and Rust quality gate
must be rerun before G7 can pass. No physics or linearization tolerance is relaxed.

Because a frozen gate execution required this documented implementation repair, a successful P10 close remains
**P10-CONDITIONAL**.
