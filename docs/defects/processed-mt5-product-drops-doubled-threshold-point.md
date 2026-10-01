# FISPACT-II TENDL-2017 gamma gxs-162: MT5 residual production drops the doubled 30 MeV point

Found 2026-10-01 while diagnosing the P98 G5 result. Status: documented and held. Nothing has been
sent upstream; the principal decides whether and when to report.

**Source.** `TENDL2017data/tal2017-g/gxs-162/*.asc` from the FISPACT-II nuclear data archive
`TENDL2017data.tar.bz2`: 2,595,437,294 bytes, SHA-256 `7f305df2…bec8`.

Records examined:

| Record | SHA-256 |
|---|---|
| Al027g | `9b5086b3…` |
| Ni058g | `a23604b4…` |
| Ta181g | `fed6e78e…` |
| W186g | `c6a2cce0…` |

Raw counterparts: the TENDL-2017 gamma files `g-*.tendl` (Al-27 `88308268…`, Ni-58
`ba1d95d9…`, Ta-181 `b0ca1c21…`, W-186 `401df005…`).

## Observation

In the raw TENDL-2017 gamma evaluations, MT5 starts at 30 MeV with a doubled point:
- MF=3 MT5 is (30 MeV, 0), then (30 MeV, σ₊);
- every MF=6 MT5 product yield is (30 MeV, 0), then (30 MeV, y₊).

In ENDF-6, a doubled point is a discontinuity: the reaction is absent below 30 MeV and present
above it. The partial reactions below (for example MT28) end at 30 MeV, so the total production of
each residual is continuous across 30 MeV.

The processed record writes MT5 residual production as MF=10 MT5, one section per (ZAP, LFS),
histogram on CCFE-162. It has no MF=6 or MF=8.
- **MF=3 MT5 itself matches.** The 30–35 MeV value is 7.365309e-3 b for Al-27. The lethargy-
  weighted collapse of the raw MF=3 gives 7.3653086e-3 b.
- **The residual production does not.** It equals the lethargy average of the pointwise product
  σ(E)·y(E), formed on the union energy grid, interpolated lin-lin, and taking the left value (0)
  at the doubled 30 MeV point. This model reproduces the processed values to 1e-6 in every group
  checked (30–35, 35–40, 40–45, 60–70 and 100–120 MeV) for Al-27 → Mg-25, Ni-58 → Fe-56,
  Ta-181 → Ta-178 and W-186 → W-183.

Two consequences, measured against the exact integral of σ(E)·y(E), with each table interpolated
by its own law:

| Residual | 30–35 MeV: processed / exact | 35–40 MeV | 60–70 MeV |
|---|---|---|---|
| Al-27 → Mg-25 | 0.275 | 1.024 | 1.0004 |
| Al-27 → Al-26 | 0.262 | 1.023 | 1.0006 |
| Ni-58 → Fe-56 | 0.328 | 1.028 | 1.0009 |
| Ta-181 → Ta-178 | 0.274 | 1.012 | 1.0002 |
| W-186 → W-183 | 0.232 | 1.008 | 1.0002 |

1. **The 30–35 MeV group loses most of its residual production.** The product is treated as
   rising from 0 at 30 MeV. This leaves a dip in each residual's total production between the
   last partial-reaction group (28–30 MeV) and MT5.
2. **Above 35 MeV, linearising the product overstates it.** Interpolating σ·y lin-lin, instead of
   multiplying the two lin-lin tables, overstates convex products by 1–3 % in 35–40 MeV, falling
   below 0.1 % by 60 MeV.

## Interpretation

Item 1 is a processing defect: the value below a doubled point is not the cross section above it.
Item 2 is an approximation in the processing convention, not a defect. ACTINV integrates each
product exactly, as the ENDF-6 interpolation laws define it.

## Local handling

None. ACTINV's build is unchanged. P98 G5, which compares one-group values against this record
within 2e-3, failed on these differences together with the threshold class in
`tendl2017-gamma-threshold-and-photofission-encoding.md` (ledger Entry 75).
