# P140 Amendment C — H3 failures on the unrepaired candidate

Registered 2026-10-15, before H3 is run on any repaired candidate. This amendment is late for one repair: the fix for
the `OVER` defect below was written together with the Amendment A importer repairs, before this text was
registered. That ordering deviates from the protocol rule that amendments precede repairs, and is recorded as such.

## Evidence

H3 on `cand9` (SHA-256 `0b62b47c…1170`, unrepaired) fails (`results/p140/h3_cand9_unrepaired.json`). The 132 FNS
decks, 2 fission decks and 4600 of the synthetic decks pass; 4 cases fail:

1. `synthetic/stress pulse_nested_empty_1e9`: no answer within 60 s (1e9 × 1e9 repetitions of a step-free body are
   executed, not refused). Covered by Amendment A repair 6 (`PULSE` expanded work capped before expansion).
2. `synthetic/stress pulse_depth_100000`: the importer overflows its stack and aborts. Covered by Amendment A
   repair 6 (`PULSE` depth capped).
3. `synthetic/stress over_10000_subkeywords`: no answer within 60 s. Not covered by Amendment A. Cause, found by
   reading `sub_entry`: each `OVER` sub-keyword entry was built with `..parent.clone()`, copying the parent's
   whole argument list (about 20,000 words) once per sub-keyword, which is quadratic in the deck size.
4. `pypact test5`: refused because the proton library `tendl-2025-proton-162g` was not installed. This is the
   environment, not the importer; the library is now installed with `actinv data fetch`, and the deck is rerun.

## Repair

`sub_entry` builds each sub-keyword entry field by field, without cloning the parent's arguments. Regression test
`p140_if_h3_over_many_subkeywords_is_linear`: a deck with 20,000 `OVER` sub-keyword lines must finish within 10 s
on a worker thread and either succeed or fail with a message. No cap on `OVER` sub-keywords is added.

## Gate

H3 is rerun on the candidate carrying Amendments A–C. The `cand9` record is kept.
