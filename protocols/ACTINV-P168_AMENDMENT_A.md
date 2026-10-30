# ACTINV-P168 Amendment A — the comparison stops on far-subnormal references, and M3's scope

Registered after the first evidence run stopped and before any record was written. This is the protocol's one repair
round.

## What happened

The harness ran on the registration commit a9aeda0 inside the bounded scope. Case 1a and 1b finished at both CRAM orders
and printed "holds". The comparison then stopped on case 2 (`decimal.Overflow`) before writing a record. That printed
status is the only ACTINV output seen.

Some reference inventories lie far outside the range of a double. For example, 1.2 × 10⁻⁴³⁴⁷²⁹¹⁹⁸⁹⁰² atoms/g of Sc-50
after 1,000 s at λ = 10⁹ s⁻¹. `controls/p168/compare.py` formed the relative error |N − R| / R for every nuclide,
including nuclides that no metric scores. For such an R that division overflows Python's `Decimal` context.

## Changes

1. **Where the relative error is computed.** It is now formed only where M2 or M3 uses it. A division beyond Decimal's
   exponent range counts as an infinite error. M1 and M2 are unchanged.
2. **M3's scope.** M3 (trace case 6b) now covers reference values of at least 10⁻³⁰⁰ atoms/g, instead of every
   R > 0.
   - Case 6b's references go down to 1.05 × 10⁻¹¹²⁰ atoms/g.
   - ACTINV writes doubles, and the smallest normal double is about 2.2 × 10⁻³⁰⁸.
   - As registered, M3 would mark 6b as broken for values no double-precision code can print. That is a
     representation limit, not a solver error.
   - The floor was set from the reference files alone, not from any ACTINV output.

The cases, references, thresholds, M1, M2 and the classification rule are unchanged. `controls/check_p168.py
--selftest` gains two regression cases:
- a reference at 1.2 × 10⁻⁴³⁴⁷²⁹¹⁹⁸⁹⁰² must not stop the comparison (this case fails on the registered code);
- M3 must leave out a reference below the floor.

## Rerun

The whole evidence run is repeated on this amendment's registration commit. Nothing from the stopped run is kept.
