# P137 — `actinv report`: FISPACT-style tables and plot data (`TAB1`–`TAB4`, `GRAPH`, row 26)

Registered 2026-10-15, after implementation (merged in `e9a37c2`) and before any evidence.

## Change under test

`actinv report RESULT.json [--tables …] [--format csv|tsv|markdown|text] [--out DIR] [--step N|all]
[--nuclides …]` renders an existing result into tab1 (atoms, grams), tab2 (activity, heat), tab3 (dose responses),
tab4 (photon groups), totals, dominant, elements and a timeseries. It is a pure function of the result JSON. CSV/TSV
values use round-trip `{:e}` formatting with units in headers.

## Gates

Checker: `controls/check_p137.py`.

- **G0.** Protocol hash registered. **G1.** fmt, clippy, workspace tests (including `report`).
- **G2, exact values.** For an FNS Fe result with `summary` and photon output, every CSV cell of every table parses
  back to exactly the corresponding JSON value (bitwise), and every JSON value the table claims to cover appears.
- **G3, formats agree.** TSV equals CSV with the separator changed; markdown and text values equal the CSV values
  rounded to six significant digits.
- **G4, missing sections.** A result without `summary`, photon output or radiological responses produces the
  base-column tables and names every omitted column or table, writes nothing invented, and exits as documented.
- **G5.** CI green on the pushed commit.
