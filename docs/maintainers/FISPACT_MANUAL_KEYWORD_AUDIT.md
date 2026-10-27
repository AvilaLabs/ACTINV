# FISPACT-II manual keyword audit (P151, 2026-10-27)

Read-only comparison of the FISPACT-II user manual's keyword reference with the `actinv import-fispact` keyword
table, made before the second parity wave (P152–P155) so that the remaining gaps are taken from the manual and not
from the importer's own list. No FISPACT-II code, data or executable was used. Keyword behaviour below is described
in our own words with section references; manual text is not reproduced.

## Source

- *The FISPACT-II User Manual*, UKAEA-CCFE-RE(21)02, May 2023, 261 pages, from the landing page at
  <https://fispact.ukaea.uk/manual/user_manual.pdf> (the PDF itself is served from
  `https://scientific-publications.ukaea.uk/wp-content/uploads/user_manual-4.pdf`).
- PDF SHA-256 `1efe16a62bd56c0e4933ea85fa3eeea29bc1c2ea59859d4fc7ae5bf26ff4a899`. Not tracked.
- Importer table: `KEYWORDS` in `crates/actinv-core/src/fispact_import.rs` at `f433fea`.

## Coverage

- Chapter 4 has 95 keyword sections (4.1–4.95) naming 93 distinct keywords (`SAVELINES` and `SSFFUEL` each have two).
- Every one of the 93 is in the importer table. The scorecard in `COMPETITIVE_BENCHMARK.md` therefore covers the
  manual's whole keyword set; there is no unscored manual keyword.
- The importer table also holds 18 names with no keyword section in this edition (legacy EAF-era or prose-only names:
  `AINPUT`, `ARRAY`, `COLLAPSE`, `CONV`, `DOMINANT`, `EAFVERSION`, `ENFA`, `IRON`, `LEVEL`, `LINA`, `LOOPS`, `MCSEED`,
  `NEWFILE`, `NOHEAD`, `NOHEADER`, `SEQNUMBER`, `SEQUENTIAL`, `TAPA`). They stay recognised so old decks report them.

## Reclassifications

| Keyword | Manual | Was | Now | Reason |
|---|---|---|---|---|
| `IRON` | no section; one passing mention of an output block | absent | not applicable | The manual does not define it; the earlier "iron background removal" wording had no source. |
| `XSTHRESHOLD` | 4.94 | absent | not applicable | Smallest cross section admitted to FISPACT-II's pathway-based uncertainty estimate (default 1e-12 b), a cost control of that method. ACTINV propagates the full covariance without pathway truncation. |
| `NOFISS` | 4.46 | partial | parity | Skips reading yield data when condensing, for speed. Omitting `fission_yields` has the same effect. |
| `USEFISSION` | 4.90 | partial | parity | Puts fission reactions with yield data into the rate matrix. Supplying `fission_yields.files` does the same. |

## Keywords still short, as the manual defines them

- **`SPEK`** (4.73; Appendix C.7.3): for nuclides with a mean γ energy but no spectral lines, builds a truncated
  exponential photon-number spectrum (fixed slope parameter) whose maximum energy is set from the decay mode, and
  normalises it to the mean γ energy. No user parameter and no error estimate. Nuclides so treated are flagged, with a
  warning when they give more than a fifth of the dose. → P152.
- **`NOXRAY`** (4.53): removes X-ray lines from the reported γ spectrum. → P152.
- **`FISYIELD`** (4.22): which actinides produce fission products (by default only three named actinides; a count of
  zero gives none; a positive count lists the included actinides; a negative count lists the excluded ones). Fissile
  nuclides without yield data do not fission. → P153.
- **`CUMFYLD`** (4.12): cumulative (MT=459) instead of independent (MT=454) yields in the rate matrix. → P153.
- **`FISCHOOSE`** (4.20): restricts the fission parents that pathway analysis may use; inventories are unaffected. →
  P153 (pathway parent filter).
- **Yield energy dependence** (C.5.1): yields are mixed over the tabulated incident energies, each weighted by the
  share of the fission rate in its energy range. ACTINV currently interpolates once at a fission-rate mean energy. →
  P153.
- **`ALLDISPEN`, `ATDISPEN`** (4.1–4.2; 5.1.4): one displacement energy for all elements, or per-element values, with
  a default table; the dpa rate folds library damage-energy cross sections (MT=444, with elastic, inelastic and
  disappearance partials) with the NRT efficiency. The damage-energy data come from NJOY-processed libraries. ACTINV has
  the per-element energies but needs a damage table that TENDL and EAF as distributed do not carry. → P154 (native
  damage energy), P155 (mapping).
- **`PROBTABLE`** (4.61): CALENDF-style probability-table self-shielding for nuclides chosen with `SSFCHOOSE`. ACTINV
  self-shields with Bondarenko tables built from evaluations and with the geometry model; the importer gap is a
  mapping. → P155.
