# Defect reports

Observed defects in upstream nuclear data (evaluations, decay files,
processing artifacts) encountered while building or validating ACTINV
libraries. Any library or data product is in scope — TENDL, ENDF/B, JEFF,
EAF, FENDL, decay sublibraries, etc.

Purpose: an internal running ledger, not an upstream report. Entries are
written with enough evidence (file, line, raw record, SHA-256,
interpretation) that a consolidated upstream report can be drafted later
without re-finding anything.

## Conventions

- One file per defect or defect class, named by what it is, not whom it
  affects: `pb208-nan-field.md`, not `tendl-....md`.
- Record: affected file(s), library release(s), physical line numbers, the
  raw 80-column record(s), SHA-256 of the source file, our interpretation,
  the local repair applied (if any), and scope (was it the only instance?).
- Verify whether the defect persists into the newest release before
  considering it reportable — regenerated evaluations often self-correct.
- Historical/sealed reports remain at their original locations under
  `docs/`; new entries go here.

## Index

Report status is the maintainer's call. `held` = documented, not sent; small defects are
collected and reported together.

| entry | library | status |
|---|---|---|
| [pb208-nan-field.md](pb208-nan-field.md) | see entry | see entry |
| [tendl2023-o18-width-inconsistency.md](tendl2023-o18-width-inconsistency.md) | TENDL-2023 | see entry |
| [tendl2023-orphan-product-sections.md](tendl2023-orphan-product-sections.md) | TENDL-2023 | see entry |
| [unitarity-violating-partial-cross-sections.md](unitarity-violating-partial-cross-sections.md) | TENDL-2025 (242 exotic files, new in 2025); TENDL-2023 Cl-35 (n,2n) | held |
| [tendl2017-gamma-threshold-and-photofission-encoding.md](tendl2017-gamma-threshold-and-photofission-encoding.md) | TENDL-2017 gamma (Al-27, Nb-93, Cu-63, Ta-181, W-186, Pb-208) | held |
| [processed-mt5-product-drops-doubled-threshold-point.md](processed-mt5-product-drops-doubled-threshold-point.md) | FISPACT-II TENDL-2017 gamma gxs-162 | held |
