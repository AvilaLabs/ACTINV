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
