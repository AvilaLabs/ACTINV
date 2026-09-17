# ACTINV-P35 protocol — product qualification aggregation

P35 aggregates the shipped candidate's qualification state: every prior
phase's evidence is re-verified against the exact current artifact set,
a fresh-installation reproduction leg is executed, and the
claim/limitation matrix plus release recommendation are produced under
an independent checker. P35 does not re-open phase science and does not
manufacture the missing legs: P32's source-handoff battery, P33/P34's
AI evaluations and any comparator/user evidence remain unmeasured and
are named as such.

## Scope

- Candidate identity: the release binary built at HEAD of the P31
  closure commit, plus the data artifacts each phase sealed.
- Battery re-verification: every gate checker G0–G4 for P27, P28, P29,
  P30, P31 is re-executed; each must pass with its mutation self-test
  intact. A checker that cannot locate its evidence reports a named
  failure, not a pass.
- Reproduction leg: `examples/study_fe_smoke.json` — the committed
  catalog-reference study — executed twice into fresh output
  directories; the two runs must produce semantically identical
  results (volatile timing fields excluded) and each must verify
  against the embedded catalog's pinned data identities.
- Claim/limitation matrix: every roadmap capability family is assigned
  exactly one of `qualified`, `conditional`, `unmeasured` or
  `blocked`, with the evidence digest or blocker named. No family may
  claim more than its phase verdict recorded.
- Release recommendation: an explicit statement of what the product
  may claim, what it must not claim, and which gates remain missed.
- Unmeasured evidence is never silently favorable: P32 (ACT-SOURCE-01),
  P33/P34 (AI families) and the P26b comparator legs are carried as
  `blocked`/`unmeasured` and excluded from any shipped claim.

## Out of scope

- Tagging, publishing, external communication — those retain their
  existing authorization requirements and are not performed here.
- Any new scientific measurement beyond re-execution of sealed
  controls.

## Frozen battery (G1)

- Gate checkers: `check_g{0,1,2,3,4}_p{27,28,29,30,31}.py` — all must
  report `pass: true` and `planted == rejected`.
- Reproduction study: `examples/study_fe_smoke.json`, two fresh runs,
  semantic digest equality per case.

## Controls (G2)

- `matrix_consistency`: every family's matrix status is re-derived from
  the on-disk verdict files; a mismatch fails.
- `battery_complete`: the sealed checker list ran to completion — no
  skipped or silently-absent checkers.
- `reproduction_identity`: both reproduction runs produce the same
  semantic output digests and verify the catalog-pinned data
  identities.
- `blocked_families_named`: P32/P33/P34 appear in the matrix as
  `blocked` with their blockers named — never omitted.

## Negative controls (G3)

- `upgraded_verdict`: a verdict file claiming a stronger result than
  recorded (e.g. P30-CONDITIONAL → P30-PASS) is rejected by the
  checker.
- `omitted_family`: a matrix missing a blocked family is rejected.
- `forged_reproduction`: a reproduction record whose second run digests
  do not match the first is rejected.
- `unsupported_claim`: a release recommendation asserting a capability
  no phase qualified (e.g. spatial source handoff) is rejected.

## Closure rule

PASS requires all gates green, every battery checker green and the
matrix fully consistent. CONDITIONAL when the aggregation is complete
but the release recommendation must exclude capabilities (the honest
case here: P32–P34 blocked). FAIL on any verdict drift, missing
battery result or forged evidence.
