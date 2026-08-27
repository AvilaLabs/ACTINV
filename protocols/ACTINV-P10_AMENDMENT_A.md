# ACTINV P10 Amendment A — G4 legacy numerical-baseline repair

**Date:** 2026-08-26. **Trigger:** the first seeded P4 regression comparison in G4 showed changes far larger than
`2e-12` even though the new Rust values passed independent exact-kernel checks. This amendment records the one G4
repair round allowed by the standing rules. It does not change P10's temperature, resonance-area, density,
performance, data, or physical-accuracy tolerances.

## Diagnosis

The frozen sentence “Every previously converged seeded P4 row changes by at most `2e-12`” incorrectly treated the
P4 production output as numerical truth in domains that P10 explicitly changes. The baseline was produced by
`controls/tendl_build.py` (`1a2218bfe3fe1433d5a1c69fa59b2c37a5d4d4bd62595bb95635761495bfcbf9`), whose
collapse routine first removes duplicate energies with `numpy.union1d`, linearly samples every ENDF interpolation
law, and extends the final sampled value through a group crossing the source ceiling. P10 instead requires exact
ENDF-law integration, explicit finite source support, complete resonance processing, and omission of negative-ZAP
MF=10 fission sentinels in favour of the canonical fission-products row. Requiring bitwise-scale agreement with the
old calculation in those domains contradicts P10's frozen normative rules 4, 5, 7–10.

The contradiction is directly reproducible in the seeded Y-90m evaluation
`ad267f534b89a05c37d01e1af8a30ed2711ad1481c8362b79bf29e46366db081`. Its MF=10/MT=37 metastable-product table
contains lin-lin points `(29 MeV, 3.053579e-4 b)`, `(30 MeV, 3.220268e-3 b)`, followed by the right-hand
discontinuity `(30 MeV, 0 b)`. In the 29–30 MeV group:

- the P4 baseline is `1.5354160902790478e-4 b` after the double point is collapsed;
- independent analytic integration of the declared left-hand lin-lin segment is
  `1.7545781097903966e-3 b`;
- Rust gives `1.7545781097903972e-3 b`, a relative difference below `4e-16` from the independent value.

Likewise, the old builder extrapolates a nonzero 200 MeV endpoint through the 200–240 MeV group, while the declared
table has no such support, and it publishes `ZAP=-1` MF=10 fission descriptors as inventory products. These are
baseline defects or intentionally corrected semantics, not regressions. The pinned full P4 library used for this
diagnosis is `a9f90234e42c538676de904c734510c4a62126017459e638ed338d052072e92c` with index
`9bd0910e65d57b1d80252b199b81784f0d1f2e5add88c137b453498f8f0be605`.

## Repaired G4 regression criterion

All other G4 clauses remain verbatim. The invalid legacy-numerical sentence is replaced by all of the following:

1. **Structural regression.** The seeded targets are identical. Row identities are identical except for changes
   mechanically required by P10's frozen rules: a resonance-only capture/fission reaction present in MF=2 may add
   its loss/product rows when optional MF=3 is absent, and a negative-ZAP MF=10 fission sentinel is omitted and
   replaced by the canonical `(MT=18, ZAP=0, LFS=0, LMF=0)` product. Every exception is enumerated; any other added
   or removed identity fails.
2. **Unchanged numerical domain.** For every common non-resonant, non-inelastic loss row backed directly by an MF=3
   table whose interpolation regions are all lin-lin, every CCFE-709 group wholly inside the declared table support
   and not containing a value-changing duplicate-energy discontinuity agrees with the P4 value to `2e-12` relative
   or `1e-14 b` absolute. This retains the old baseline as an exhaustive oracle exactly where the old and new
   algorithms have the same mathematical contract.
3. **Changed numerical domains use independent truth.** Resonance-affected capture/fission values are gated by
   G4's independent exact SIGMA1 quadrature, line-area, and density-1/2/4 controls, not by the superseded P4
   approximation. The Y-90m discontinuity case above is independently integrated and must agree with Rust to
   `2e-12` relative or `1e-14 b` absolute. The old/new difference remains reported as diagnostic evidence and is
   never converted into a relaxed accuracy tolerance.

The structural and unchanged-domain audit uses the original seeded 40-target set: the 38 ordinary targets are
checked against the pinned P4 library, while Fr-226 and Rb-94 are checked by the explicit G4 density control. Source
files, sample membership, libraries, binaries, and control sources are hash-recorded; no nuclear data enter Git.

Because a frozen gate criterion required this documented repair, a successful P10 close is
**P10-CONDITIONAL**, not P10-PASS. No production result was changed to make this amendment pass.
