# ACTINV P10 Amendment F — G7 EAF product-collapse termination repair

**Date:** 2026-08-27. **Trigger:** the first complete EAF-2010 G7 build created 742 valid target checkpoints, then
spent one hour with one worker at 100% CPU on one remaining source and no further checkpoint. It was stopped before
publishing a library. `/usr/bin/time -v` recorded 4,153.78 user seconds, 684,228 KiB peak RSS and no swap, proving a
bounded CPU/termination defect rather than memory exhaustion.

## Diagnosis

A one-worker classifier ran each of the 74 uncached sources in isolated temporary storage under the same 4 GiB
address-space limit and a five-second per-source cutoff. Sixty-eight completed in at most 4.236 s, none failed, and
exactly six timed out: EAF-2010 Zn-68
(`aafaa5b1424883bc1100545d9d577f9aceab987da5f441df8bafc2d0f43666f5`), Zn-70
(`68d296300a38cc73bd6b233a7ead07c6c13767ba62c2e4526ff7e574381759b5`), Ga-71
(`e08c9a949158c56c14f5baf5378c3746a1440306a38adc4803d29bf704682f66`), Ge-70
(`02041dacd224a97aab4aae26c32b8ceab501946490581f0ee09465a3ce8d40f5`), Se-72
(`a0b17466726c96e537b4f53cd3f71e0cce87a01765d76a51ba09f4e52cd6b2e8`) and Y-88
(`e0e1c8851b6a3c94de4e309c8ef83c4f74d1c7c17ea272b702ea43c0e663c951`).

Each has MT=102 MF=9 ground/isomer yields using lin-lin interpolation multiplied by an MF=3 capture table whose
long low-energy region uses log-log interpolation. `GroupStructure::collapse_product` split at every declared table
point but then sent each smooth subinterval through recursive adaptive Simpson quadrature. On such an interval, with
`t=E/E0`, the log-log factor is `a t^p` and the lin-lin factor is `b+c t`; their product under the lethargy measure is
`a b t^(p-1) dt + a c t^p dt`. The existing generic quadrature therefore expends unbounded practical work on a
function with a direct antiderivative.

## Frozen repair

1. On a product-collapse subinterval, INT=1, INT=2 and INT=5 factors are represented in the normalized variable
   `t=E/E0` as finite sums of powers. Their product is integrated directly using the stable identity
   `integral(t^(q-1), 1..r) = log(r) * expm1_over_x(q log(r))`, with compensated summation. If the term sum is
   materially ill-conditioned, the implementation retains the existing adaptive calculation. Any factor using
   INT=3 or INT=4 also retains the existing path.
2. Finite support, right-continuous duplicate-energy handling, group weighting, source rows and all tolerances remain
   unchanged. This is a termination/performance repair, not an interpolation approximation.
3. Synthetic exact-antiderivative regressions and an independent high-precision control over all twelve affected
   MF=9 product rows must agree with Rust within the existing `2e-12` relative or `1e-14 b` absolute criterion. All
   six isolated sources must complete below the former five-second cutoff, the full fresh/cached EAF build must be
   byte-identical, and the complete P10 controls and Rust quality gate must be rerun.

Because this repair was required during the already documented G7 repair round, a successful P10 close remains
**P10-CONDITIONAL**. No scientific acceptance threshold is relaxed.
