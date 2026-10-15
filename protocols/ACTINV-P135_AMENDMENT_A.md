# P135 Amendment A — G2 as registered

Registered 2026-10-15, after the first P135 run and before any rerun.

## First run (preserved)

Candidate: release build of `332f8e5` (SHA-256 `0b62b47c…1170`). Reference: release build of `2f3daa4`. Record and
log: `results/failures/p135_initial/`. G3, G4 and G5 passed (1,503, 1,412 and 100 comparisons, no failure). G2 was
recorded FAIL without a comparison result: besides the two registered examples, the checker compared a spec with a
`summary` section between reference and candidate, and the reference predates `summary` (P122), so it refused the
spec and the exception discarded the example comparisons already made.

## Amendment

G2 is evaluated as registered: `examples/fns_fe_5min.json` and `examples/shielding_demo.json` byte-identical to the
reference apart from `ms`. The checker's additional summary-without-depletion run is kept as a candidate-only check
(no `depletion` key in any step summary, ledger or certificate when depletion is not requested) and no longer
compared with the reference. G3–G5 and all tolerances are unchanged; G2–G5 are rerun on the same candidate.
