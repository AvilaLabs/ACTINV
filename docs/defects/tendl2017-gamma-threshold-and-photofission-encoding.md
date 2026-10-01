# TENDL-2017 gamma: MF=3 tables that start above threshold, and IZAP=0 photofission totals

Found 2026-09-30 while building the P94 G5 inputs: 8 raw TENDL-2017 gamma evaluations from
`TENDL-g.tgz`, SHA-256 `dfa0df7f…d23a`.
Status: documented and held. Nothing has been sent upstream; the principal decides whether and
when to report.

## 1. MF=3 total starts above the reaction threshold

In several evaluations the MF=3 table starts at a round energy with a nonzero value, and has no
threshold point. The MF=10 state-resolved section of the same MT starts at threshold with a zero.
Below the first MF=3 point, the MF=3 total is therefore zero, while the MF=10 states are not. This
is a source inconsistency. The MF=10 ramp is the physical one.

| File (SHA-256) | MT | MF=3 first point | MF=10 first points | Threshold (−QM) |
|---|---|---|---|---|
| g-Ta181 (`b0ca1c21…`) | 4 | 8.0 MeV, 1.759e-2 b | 7.616 MeV 0 → 8.0 MeV 1.755e-2 b | 7.577 MeV |
| g-W186 (`401df005…`) | 4 | 8.0 MeV, 3.135e-2 b | 7.216 MeV 0 → 8.0 MeV 3.092e-2 b | 7.192 MeV |
| g-Pb208 (`222d9875…`) | 4 | 8.0 MeV, 4.362e-2 b | 7.938 MeV 0 → 8.0 MeV 4.362e-2 b | 7.368 MeV |
| g-Nb093 (`1322cbc4…`) | 16 | 17.0 MeV, 1.143e-2 b | 16.717 MeV 0 → 17.0 MeV 1.112e-2 b | 16.717 MeV |

A related small case: g-Cu063 (`fad99af7…`) MT106. The MF=3 table has 0 at 20 MeV. The MF=10
ZAP=27060 states are about 6e-15 b just above that, and exceed the collapsed total in group 121
by 5.5e-15 b, a relative excess of 0.36. This is physically weightless, but it lies above the
1e-15 b floor.

## 2. Photofission total written as MF=10 IZAP=0

g-Pb208, g-Ta181 and g-W186 write the MT18 total photofission cross section as follows:

- a single MF=10 section with IZAP=0 and LFS=0 (for Pb-208: tabulated from 1 MeV, nonzero above 30 MeV, up to 200 MeV);
- an MF=8 descriptor with ZAP=0 and LMF=10;
- no MF=3 MT18.

TENDL-2025 writes the same quantity as the MF=10 IZAP=-1 total-fission sentinel. IZAP=0 is not a
residual nuclide.

## Local handling

- **Default profile (`none`):** construction fails closed on both classes. Five of the eight files
  are refused.
- **`--profile tendl`:** class 1 is handled by the MF=3 threshold extension (P100, merged after
  P101 PASS, ledger Entry 76). The MF=3 table is extended below its first point by the MF=10
  state sum, so the threshold-ramp group is kept, and each extension is recorded in the ledger.
  Before P100 the state rows were scaled to the zero MF=3 total and that group was lost.
- **Default profile, small ramps:** a file whose lost state sum stays within the standard
  zero-total envelope (≤ 1e-3 b, as for Al-27 MT4) still builds without the extension, with the
  group scaled to zero and recorded. Build TENDL-2017 gamma files with `--profile tendl`.
- **Class 2:** handled by the P98 rule. Under a normalization profile, exactly this shape is read
  as the IZAP=-1 sentinel and recorded in the ledger. Any other shape still fails closed.

## Cross-code consequence (P98 G5, 2026-10-01)

Class 1 is wider than the five files that fail closed under the default profile. The P98 G5
comparison with FISPACT-II found the same shape in these places:

| File | MT | MF=3 first point | MF=10 first point |
|---|---|---|---|
| g-Al027 (`88308268…`) | 4 | 14.0 MeV | 13.286 MeV |
| g-Ta181 | 17 | 24.0 MeV | 22.055 MeV |
| g-W186 | 17 | 22.0 MeV | 20.358 MeV |
| g-Nb093 | 16 | 17.0 MeV | 16.717 MeV |

In each case, the CCFE-162 group just below the first MF=3 point has a zero MF=3 total and a
nonzero MF=10 state sum.
- **ACTINV:** the state rows in that group are scaled to the zero total. This happens under the
  standard zero-total envelope (≤ 1e-3 b, as for Al-27) or under `--profile tendl`. The group's
  production is lost, and the loss is recorded in the ledger. Since P100 (ledger Entry 76),
  `--profile tendl` keeps it; P101 G5a then found 95 of 95 non-MT5 values within 2e-3 of
  FISPACT-II (maximum 1.3e-6).
- **FISPACT-II (`tal2017-g/gxs-162`):** keeps the MF=10 ramp. For example, Al-27 → Al-26 in
  13–14 MeV is 4.29e-4 b in FISPACT-II and 0 in ACTINV.

Effect on one-group values:

| Residual | Spectrum | ACTINV vs FISPACT-II |
|---|---|---|
| Al-26 | `brems_20_MeV` | −11.7 % |
| Ta-178 | `gdr_flat_8_30_MeV` | −9.3 % |
| W-183 | `gdr_flat_8_30_MeV` | −8.3 % |
| Nb-91 | `brems_20_MeV` | −3.2 % |
