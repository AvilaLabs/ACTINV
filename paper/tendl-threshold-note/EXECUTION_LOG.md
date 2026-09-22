# TCS-1 execution log

2026-09-14 — Protocol SHA-256 frozen as
`7b0cc7abccd94ec93aeae3fc4ecd320f66a710b1cc6eb386cf69f553be156f06`.
Systemd resource limits independently inspected: 6 GiB memory, no swap,
128 tasks, two CPU equivalents.

2026-09-14 — First primary execution stopped before producing results because
the parser demanded strictly increasing energy across the complete subsection.
ENDF permits equal-energy points. The implementation now accepts nondecreasing
energies, skips zero-width integration intervals, and uses the last exact point
for a point query (right-hand value). This is a parser correction, not a change
to the frozen spectra, one-field intervention, or validation tolerances.

2026-09-14 — Primary calculation completed for all four pinned source files.
Independent parser/quadrature checker passed all four cases, decimal area
checks, variant-identity checks, constant/linear fixtures and corrupted-hash
rejection. Maximum absolute fold/delta disagreement: 7.324869877312068e-15 b.
No comparison tolerance or spectral definition was changed.

2026-09-14 — Generated manuscript HTML, Word and eight-page PDF with a vector
figure. The initial layout split the main results table; the document builder
was adjusted to keep tables intact, repeat headers, and set explicit widths,
borders and padding. The revised main table and figure were visually inspected.
Changes were formatting only; numerical evidence was not rerun or modified.
