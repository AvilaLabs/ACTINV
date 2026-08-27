# ACTINV P10 Amendment D — G7 upstream neutron-evaluation repair

**Date:** 2026-08-27. **Trigger:** the first complete TENDL-2025 neutron structural preflight, performed only after
G1–G6 passed, found two narrowly bounded classes of defects in the official `TENDL-n.tgz` corpus. This amendment
freezes their treatment before a complete library is built. It changes no gate tolerance and does not permit a
general nonfinite-value or inconsistent-width fallback.

## Frozen source observations

The official neutron archive has SHA-256
`e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`; its 2,850-file compact manifest has
SHA-256 `f38df7c49da6cef8ac3d23c45c81dfb394829eefd38ee4af0db6dde92f0beaa4`.

An independent, bounded scan of all 267,559 LRF=1/2 Breit–Wigner records found exactly 265 reported total widths
below the neutron/capture/fission component sum. Every case has `LRX=0`, a positive `GF=1.000000e-5 eV`, and a
reported `GT` equal to `GN+GG` within the existing `5e-6` field-rounding tolerance: 100 records in
`n-Bi220.tendl` (`37bf6633d430ee5ab0b95b2d7318ddb37fe56329acfbb2cb73fde292478fe6ed`), 99 in
`n-Fr231.tendl` (`5a2a6fea3ca54d822c86255aea2075455cf4ba5b2cd8a0717ccd3bef6c4dccc6`) and 66 in
`n-Ra226.tendl` (`577e5f1bd628e8a1d6f935b903c34f4b6ecbc1a69a747c67a6bda1afa43e7c1c`). There are no other
below-sum records and no above-sum records outside rounding tolerance in an `LRX=0` sequence.

The pinned NJOY2016.79 RECONR source
`054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db` reconstructs SLBW/MLBW total width from
the energy-dependent neutron width plus capture, fission and any declared competitive component; it does not use
the redundant reported `GT` as the resonance denominator. A fresh NJOY run accepts the affected Bi-220 evaluation.

The only literal nonfinite numeric fields in the 2,850 files are two `NaN` values in
`n-Pb208.tendl` (`32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137`), both at 1 MeV: one in
MF=3/MT=1 and one in MF=3/MT=3. They are left-hand members of duplicate-energy discontinuities, immediately after
the same finite `4.925328e-7 b` value at 800 keV. MT=1 and MT=3 are aggregate diagnostic reactions that ACTINV
intentionally omits; neither can contribute an activation row, a resonance background, a product yield or a
fission yield.

## Frozen repair

1. For an LRF=1/2, `LRX=0` record, the Rust parser continues to require nonnegative finite widths and consistency
   within `5e-6` relative field-rounding tolerance. It additionally accepts only the identified omitted-fission
   pattern: `GF>0`, `GT` agrees with `GN+GG` within that tolerance, and `GT` is below `GN+GG+GF`. Reconstruction,
   grid placement, analytic classification and certificates use `GN+GG+GF`, matching NJOY. Any other below-sum
   record, or any above-sum `LRX=0` record outside rounding tolerance, still fails closed. Each affected target's
   index ledger records the count and rule; the official reported fields remain available to the parser.
2. The strict parser continues to reject every nonfinite numeric field, including the official Pb-208 file. A
   deterministic external preparation step creates the G7 neutron working corpus without modifying the official
   extraction. It verifies the complete official manifest and exact Pb-208 hash, copies all 2,850 regular files,
   and replaces only those two eleven-column `NaN` fields with the immediately preceding finite value
   `4.925328-7`. Both official and derived per-file hashes, the two exact records and the transformation-program hash
   are recorded. No raw or derived nuclear data enter Git.
3. A control must prove that the official corpus has exactly the observations above, arbitrary width mismatches and
   nonfinites still fail, the preparation is deterministic, and changing either repaired Pb-208 value to a different
   finite value cannot change any emitted ACTINV row or group because MT=1/3 are excluded. The full build uses the
   derived working manifest but retains the official archive and file-manifest provenance.

G7's “external TENDL-2025 neutron” phrase therefore includes this one hash-pinned working-copy repair. It does not
authorize repairs to another release, file, field, section or anomaly. Because a frozen gate deliverable required a
documented repair, a successful P10 close remains **P10-CONDITIONAL**.
