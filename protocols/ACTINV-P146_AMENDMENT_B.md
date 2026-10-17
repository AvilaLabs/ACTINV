# P146 Amendment B — repair after evidence attempt 1

Registered 2026-10-16, after evidence attempt 1 and before any repair source change.

## What failed

On candidate `b98042d`, every gate command exited 0, but the checker's own selftest step exited 1. The B6 record
requires every step of the evidence to exit 0, so B6 failed and with it the phase. The selftest crashed in
`b3_check()`. The Amendment A handling of capped suprema iterates the result's `verification_skipped` member, and
when the member is absent the checker's field lookup returns a non-iterable sentinel. The selftest's synthetic
result has no such member. The attempt is preserved in `results/failures/p146/evidence_attempt1/`.

## Repair

1. `controls/check_p146.py` treats an absent or non-list `verification_skipped` as empty. A missing 1.01 point for a
   capped rank then still fails the rank-coverage check of B3, so nothing is relaxed. The selftest's synthetic
   result also carries the member, so both the present and the absent cases are exercised.
2. No change to the command, the inputs, the gates or their thresholds.
3. B1–B6 are rerun in full on the repaired candidate, with the checker selftest run before the candidate commit
   and as an evidence step.

This consumes P146's one repair round.
