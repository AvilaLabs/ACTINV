# Partial cross sections above the partial-wave unitarity bound (TENDL-2025)

Status: internal ledger entry (2026-09-29). **Held — not reported.** Whether, when and how to
report is the maintainer's decision; smaller defects are batched into one later report.

## Observation

`controls/qa_unitarity.py` screens every row and group of a groupwise activation library. For each
group it compares the partial cross section with the generous partial-wave ceiling
`π·ƛ²·(L+1)²`, where ƛ uses the neutron–target reduced mass, `L = ceil(kR)+1`, and
`R = 1.35·A^(1/3)` fm. The ceiling is evaluated at the group's lower edge, where it is largest.

On `tendl-2025-neutron-709g.npz` (sha256 `ec4c72bf…8cc44`, 167,735 rows), **269 rows in 242
target files exceed the ceiling**: 26 by less than 2×, 60 by 2–10×, and 183 by more than 10×.
By MT: 16 (n,2n) ×165, 115 ×62, 105 ×11, 106 ×10, 103 ×10, 116 ×5, 32 ×4, 107 ×2.
Output: `results/qa_unitarity_tendl-2025-neutron-709g.json`.

**No naturally occurring ground-state target is affected.** Every flagged file is an exotic
(mostly proton-rich or isomeric) evaluation. First-order activation of real materials is
therefore untouched. The defects enter results only through secondary reactions on exotic
products, and through diagnostics that take a maximum over all products.

## Worst examples

| file | sha256 | MT | group lower edge | σ | ceiling |
|---|---|---|---|---:|---:|
| n-Nb085m.tendl | `a857e71c…446f` | 106 (n,³He) | 0.603 MeV | 1.0e8 b | 10 b |
| n-Ag096m.tendl | `c5d3521f…502a` | 16 (n,2n) | 12.6 MeV | 1.5e7 b | 2.6 b |
| n-Mo086.tendl | `96af56b9…4c13` | 103 (n,p), 107 (n,α) | 1e-5 eV | 2.2e12 b | 6.0e11 b |

For Mo-86 the value is verbatim in the evaluation (MF=3 MT=103, source line 1800:
`1.000000-5 2.17495+12`; MT=107 line 1929: `1.000000-5 2.18063+12`), with a 1/v-like fall over
the first ~320 groups. It is not a resonance-reconstruction artifact, since MT=103/107 have no
MF=2 parameters. The other files were not individually cross-checked against raw MF=3.

## Consequence in ACTINV

`ledger.max_product_optical_depth` is a maximum over all product columns. On every TENDL-2025 run
examined (P75) it was set by Mo-86, with implied one-group loss cross sections of 1e6–1e11 b. This
makes the desktop sweep's flux-scaling certificate (P70, `sweep.rs::scale_flux_result`) never
certify (P75 G4: 0 of 672 pairs).

## Relation to known defects

This is distinct from the documented thermal (n,p) leak (28 files, P25/P25c,
`docs/DATA_LIMITATIONS.md`): none of the 242 files is in that list, and magnitudes here reach
1e12 b versus the leak's 385.6 b maximum.

## Release persistence (checked 2026-09-29)

Same screen on `~/nuclear-data/tendl-2023/actinv_tendl2023_709g.npz` (164,315 rows):
`results/qa_unitarity_actinv_tendl2023_709g.json`.

- **The 242-file class is new in TENDL-2025.** None of those files exceeds the ceiling in the
  TENDL-2023 library.
- TENDL-2023 has **one different violation, on a natural target**: `n_017-Cl-35_1725.dat` MT=16
  (n,2n) at 19.5 b in the group starting at 13 MeV, 10× the 1.91 b ceiling. It is absent in
  TENDL-2025, so it was corrected upstream. It is relevant only to work that still uses
  TENDL-2023 libraries with chlorine (concrete, salts).

## Not yet done

- Check TENDL-2019/2021 if those libraries are ever built.
- Decide whether the library builder should ledger or quarantine rows above the ceiling.
- Decide whether to report upstream.
