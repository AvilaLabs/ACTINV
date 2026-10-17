# P147 Amendment B — the P103 control in E6 is judged by identity with its committed record

Registered 2026-10-17, after evidence attempt 1 and before the repair. This amendment consumes P147's one repair round.

## What failed

Evidence attempt 1 ran on candidate `4752d0e` and is preserved in `results/failures/p147/evidence_attempt1/`. E1 to
E5 passed. Every E6 step exited 0 except `e6_p103_check`, which ran `python controls/check_p103.py --no-write`.
Because `e6-collect` requires every step to exit 0, E6 failed.

`check_p103.py` exits 1 by design. The P103 G1 record it compares against, `results/g1_p103_classes.json`, is
committed with `pass: false`. It holds two runtime-control failures: the declared-tritium relocation and the
unequal-cell-timestamps control. P103 failed on them, and P105 superseded P103 for those controls. In attempt 1:

- the recomputation matched the committed record exactly (`persisted_result_matches: true`);
- G0 was sealed (`P103-G0-SEALED`, every check passing);
- the G2 budget control never ran, because `check_p103.py` stops after a failing G1, and P103 has no committed G2
  record (`results/g2_p103_budget.json` does not exist).

The protocol's requirement is that the P103 and P105 waste controls "stay as committed", with byte-identical
outputs. A record that is itself committed as failing therefore cannot be judged by the script's exit code. Attempt 1
did exactly that, which was an error in the evidence step, not a change in the command under test.

## The amended E6 step

`e6_p103_check` becomes `python3 controls/check_p147.py e6-p103`. It runs `controls/check_p103.py --no-write` once,
within a timeout, and writes nothing. It parses the two JSON documents the script prints. The step passes if and
only if all of the following hold:

- the G0 document reports `sealed: true` and verdict `P103-G0-SEALED`;
- every G0 check passes;
- the G1 document's `persisted_result_matches` is `true`. This means the recomputed G1 record equals the committed
  `results/g1_p103_classes.json`, including its recorded failures.

The P103 G2 budget control has no committed record, so it is not part of "as committed". `actinv waste budget` stays
covered by the P105 steps and the committed-controls comparison of E6. The step prints its own record and exits 0
only on a pass.

## Repair and rerun

The repair adds only the `e6-p103` subcommand and its selftest cases to `controls/check_p147.py`: a passing pair of
documents, a G1 mismatch, a missing G1 document and an unsealed G0. Neither the command under test nor any evidence
input changes. E1 to E6 are then rerun in full on the repaired, committed candidate. A further failing gate is
final.
