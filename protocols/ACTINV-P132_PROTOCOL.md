# P132 — FISPACT-II input-deck importer

Registered 2026-10-15, before any P132 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: run existing FISPACT decks unchanged, plus the migration guide.

## Change under test

`actinv import-fispact DECK.i [--fluxes] [--files] [--library] [--decay] [--covariance] [--strict] OUT.json` converts
a FISPACT-II deck into an ACTINV spec. The work is:

- tokenize per the manual's lexical rules
- interpret the three phases
- map MASS, FUEL, FLUX/FLUXES, TIME/ATOMS/ZERO, PULSE, UNCERTAINTY, PROJECTILE, MIND, NOSTABLE and SORTDOMINANT

A sidecar `OUT.import.json` reports every keyword occurrence as mapped, approximated, noted or unsupported.
`--strict` fails on any unsupported keyword.

## Gates

Checker: `controls/check_p132.py`, which drives `controls/p132_import_all.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P132 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the importer unit tests.
- **G2, corpus import.** All 132 FNS decks under `~/nuclear-data/conderc-fns/fns/*/` import without error.
  - Every resulting spec passes `actinv validate --files`.
  - The report has zero `unsupported` entries for keywords ACTINV implements, and lists the others.
  - The keyword status table is recorded.
- **G3, schedule fidelity.**
  - For every deck, the imported schedule's cumulative times equal the FISPACT `.out` interval times ("TIME IS",
    cooling times) within 1e-9 relative.
  - The flux multipliers equal the deck's FLUX values divided by the spectrum total.
- **G4, run equivalence (report-only plus one hard check).**
  - Hard check: the imported Fe 1996 5-minute spec and the hand-written `examples/fns_fe_5min.json` give
    byte-identical results apart from `ms` and echoed title and paths, or every difference is listed and explained.
  - Report-only: for 10 decks across elements, the imported runs' total activity is tabulated against the FISPACT
    outputs.
- **G5.** CI green on the pushed commit.

A gate that fails is recorded as P132-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

FISPACT-II output formatting (TAB files, the printed report) is a separate phase (P132b).
