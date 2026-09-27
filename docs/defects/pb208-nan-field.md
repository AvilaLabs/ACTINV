# Literal `NaN` data fields in TENDL neutron Pb-208 (TENDL-2023 and TENDL-2025)

Prepared 2026-09-26 by Connor Avila / Avila Labs, from ACTINV activation-library
construction. Status: reproduced in locally archived source files for both
releases; awaiting evaluator review.

## Observation

The neutron Pb-208 evaluation contains the literal token `NaN` in two numeric
data fields — one in MF=3/MT=1 (total cross section) and one in MF=3/MT=3
(nonelastic). ENDF-6 does not admit `NaN` in a numeric field, so a conforming
reader either rejects the tape outright or propagates a nonfinite cross
section into group constants.

Both occurrences sit at exactly the same energy, E = 1.000000+6 eV, which is
also the declared upper boundary of the resolved resonance range
(MF=2/MT=151, [1e-5, 1e6] eV). The NaN ordinate is the last point of the
in-range segment; the immediately following record opens the post-range
segment at the same abscissa (E = 1.000000+6 eV) with a finite ordinate —
i.e., the table intends a duplicate-point step at the range edge, and the
`NaN` occupies the position where the left-segment endpoint's ordinate
belongs.

| Release | Archive member | Section | Physical line | Field content |
|---|---|---|---:|---|
| TENDL-2023 | `n_082-Pb-208_8237.dat` | MF=3/MT=1 | 656 | `1.000000+6        NaN` |
| TENDL-2023 | `n_082-Pb-208_8237.dat` | MF=3/MT=3 | 1366 | `1.000000+6        NaN` |
| TENDL-2025 | `n-Pb208.tendl` | MF=3/MT=1 | 781 | `1.000000+6        NaN` |
| TENDL-2025 | `n-Pb208.tendl` | MF=3/MT=3 | 1613 | `1.000000+6        NaN` |

An exhaustive scan of every neutron-sublibrary file we hold finds this token
in exactly one file per release (`n_082-Pb-208_8237.dat` in TENDL-2023,
`n-Pb208.tendl` in TENDL-2025). Other projectile sublibraries were not
scanned.

## Source identification

TENDL-2023 member `n_082-Pb-208_8237.dat`, extracted archive SHA-256
(`n_082-Pb-208_8237.zip` from the acquisition manifest):

```text
484bebb214af5b63b9ef6162f62e872dd307f2380e44ce1d0f2306d500f427fa  n_082-Pb-208_8237.zip
```

Per-file SHA-256 values, independently recomputed during review:

```text
be41e05275cfa1b0a643f7ebd01a29b90d6f2517e9702fcfccc4806aa0f03abe  n_082-Pb-208_8237.dat  (TENDL-2023)
32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137  n-Pb208.tendl        (TENDL-2025)
```

The TENDL-2025 file comes from the same archive acquisition as our earlier
report (`TENDL-n.tgz`, archive SHA-256
`e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`). The
archives were not redownloaded during this review; the checks establish the
defect in these exact local source versions.

## Exact source records

TENDL-2025 `n-Pb208.tendl`, MF=3/MT=1, lines 779–782 (line 781 carries the
defect; columns 67–80 identify MAT 8237, MF 3, MT 1, record 36):

```text
 1.207968+5 1.908309-7 1.559004+5 2.170003-7 2.000000+5 2.459698-78237 3  1   34
 2.596751+5 2.797595-7 3.000000+5 3.012054-7 4.000000+5 3.478833-78237 3  1   35
 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  1   36
 1.000000+6 3.224674+0 1.005300+6 3.771019+0 1.007100+6 3.675211+08237 3  1   37
```

MF=3/MT=3, lines 1611–1614 (line 1613 carries the identical defect):

```text
 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  3   36
 1.000000+6 7.415722-4 1.005300+6 7.474730-4 1.007100+6 7.494770-48237 3  3   37
```

The MF=2/MT=151 resolved-range declaration (TENDL-2025 line 660) places the
NaN abscissa exactly at the range's upper edge:

```text
 1.000000-5 1.000000+6          1          3          0          18237 2151    3
```

## Reproduction

The defect is visible in the source text and requires no processing:

```sh
grep -n "NaN" n-Pb208.tendl
# 781: 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  1   36
# 1613: 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6       NaN8237 3  3   36
```

## Interpretation

The pattern — `NaN` at a duplicated abscissa where the evaluated table joins
the post-resonance segment — suggests the writer emitted a sentinel where a
segment endpoint's ordinate was uncomputed. If so, the intended record is a
duplicate-point step at E = 1.000000+6 eV, which ENDF-6 encodes as two
adjacent pairs sharing the abscissa.

The ordinate these fields should carry is not uniquely determined by the
surrounding text; two repairs we consider defensible are (a) substituting
the following segment's ordinate at the duplicated abscissa (3.224674+0 b
for MT=1), preserving the step exactly, and (b) substituting the preceding
ordinate (4.925328-7 b), extending the in-range segment. In ACTINV's own
processing we apply (a). Either choice is defensible because MF=2 supplies
the cross section within the resolved range regardless; we flag it so the
evaluator can supply the intended value rather than our inference.

## Questions for the evaluator

1. Is the `NaN` emission reproducible in the current generation pipeline,
   and does it indicate a missing segment-endpoint value at resolved-range
   joins more generally (i.e., should other evaluations be grepped beyond
   the neutron sublibrary)?
2. Is the intended ordinate at E = 1.000000+6 eV the segment-join step we
   inferred, or a value from another source?
3. Could this be recorded on the known-deficiencies page, and is there a
   preferred public tracking route for format-level findings?

## Context: TENDL-2023 observations resolved in TENDL-2025

For completeness: while building an activation library from TENDL-2023 we
encountered two further defect classes that TENDL-2025 appears to have
resolved by regeneration, so they need no action:

- `n_008-O-18_0831.dat` (TENDL-2023): an LRF=2 Breit-Wigner record declares
  total width GT = 6.500000+3 eV below the component sum
  GN+GG+GF = 6500.21 eV (line 88). TENDL-2025's O-18 is an entirely
  regenerated evaluation without this record.
- Approximately thirty TENDL-2023 evaluations (isomer and auxiliary files
  such as `n_051-Sb-128M_5147.dat`, `n_077-Ir-192M_7729.dat`, plus targets
  Sr-86, Zr-91, Dy-156, Dy-160, Er-168, W-186, Os-186, Os-192, Br-79) carry
  product sections (MF=6, MF=8, MF=9) or MF=8 product descriptors whose MT
  has no MF=3 or MF=10 cross section — e.g. MT56/MF=6 with no matching
  reaction. Spot-checks of the same nuclides in TENDL-2025 find no such
  orphans.

## Contact

Connor Avila, Avila Labs. ACTINV is open-source; the failing records, the
normalization repair described above, and the scan used to establish scope
can be shared on request.
