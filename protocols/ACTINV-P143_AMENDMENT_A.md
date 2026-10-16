# P143 Amendment A — the twin's waste membership refusal depends on hash order

Registered 2026-10-16, after the first W1 run failed and before any repair. The W1 criterion does not change.

## What happened

The first W1 run (candidate `4e544b2`, release binary `917e52e6…a394`) compared 773 waste and twin invocations of the
six campaigns: all six CI steps exited 0 and 772 comparisons matched. One did not: the P116 G2 refusal case
`refusals/overlap` (a twin spec with two defective components), where the CLI's stderr was
`waste component component-b cell_masses_g keys must exactly match its twin group` and the Python call raised
`twin component cell groups overlap`. The identical G1 case matched. Rerunning the CLI 24 times on a copy of the case
gave the first message 9 times and the second 15 times. The record is `results/failures/p143/w1_attempt1/`.

The cause is in the CLI, not in the binding: `twin_waste::validate_component_membership` iterates the twin's
component groups from a `HashMap`, so when more than one component is defective, which refusal is reported depends on
the per-process hash seed. Successful outputs are unaffected (they are built in sorted order, and P116's byte repeats
pass); only the choice between two valid refusal messages varies.

## Repair

`validate_component_membership` visits the component groups in sorted id order, so a spec with several defects always
reports the first defect in that order. A Rust regression test builds the two-defect case and checks that the reported
message is the same over repeated validations with freshly built maps. No other change.

## Evidence after the repair

W1 is rerun in full on the repaired candidate with a fresh release binary and wheel, under the unchanged criterion.
The P116 step inside W1 must still exit 0, so the sealed P116 records are reproduced with the repaired code. W2–W4 as
registered. This amendment consumes P143's one repair round.
