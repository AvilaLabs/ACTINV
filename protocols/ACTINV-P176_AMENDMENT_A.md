# ACTINV-P176 Amendment A: the table pin made reachable

Registered after the first scored run was stopped, and before any scored output was read. This is the protocol's one
repair round.

## What happened

The registration (`dfc4575`, ledger 309) pinned the Bondarenko table at `b8ccd5a4…`, the SHA-256 of the rehearsal's
table. The scored run started from `dfc4575` at 11:03 on 2026-10-31, inside the bounded scope with the pinned binary.
Its `table` mode exited 0 in 8 s with 67 checkpoint hits and 54 targets, but wrote a table with SHA-256 `8e0d2a34…`.
Every A1 run then stopped, correctly, with `the table is not the pinned one`.

**Cause.** `actinv build-shielding` writes its input directory into the table's `source.url`. The harness stages the
evaluations under `target/p176-rehearsal-work/eval` in the rehearsal and under `target/p176-work/eval` in the scored
run. The two tables (678,170,917 and 678,170,907 bytes) differ only in that string. Replacing the rehearsal path with the
scored path in the rehearsal table gives a file byte-identical to the scored table (`cmp`). The pin could never match.
The registration should have pinned the table at the scored path, and did not.

**What ran, and what was read.** The coordinator saw the table digest and stopped the run at 11:03. The run had
completed:
- arms A0, A2 and A3 for models S and W, and A0 and A2 for model C;
- A1 for S, W and C, each refused by the pin.

No assembly or checker ran. Only these were read:
- the step exit codes;
- the table record (digest, return code, target count, the builder's summary line);
- the A1 refusal message;
- the two table files, compared byte for byte.

None of the arm outputs was opened. They are deleted unread before the rerun. The rerun is deterministic, so the
deleted outputs carry no information that the rerun does not.

## Change

In `controls/p176_selfshield.py`, `SHIELD_TABLE_SHA256` becomes
`8e0d2a3472b8b1ddcd2c4c9b993349803aaaafe1fef080540f93a2f34d159ba6`. That is the table built from the pinned evaluations,
with the pinned binary, at `<worktree>/target/p176-work/eval`, where the worktree is
`/home/connoravila/Documents/actinv/target/p173-wt`. Its comment now says this.

Unchanged:
- the binary, the evaluations, the arms and the rules;
- the constants (`constants_digest` does not cover the table pin);
- the gates, the thresholds and the checker.

The pin binds the scored run to that worktree path, so the rerun runs there.

## Rerun

1. Delete `results/p176/` (untracked; nothing from the first attempt was committed).
2. Run every mode from this amendment's registration commit, as the protocol's commands list them: `table`, the 16 arm
   modes and `assemble`.
3. Run the checker.

The table step reuses the checkpoints again.

This amendment uses the one repair round. An ACTINV error on an arm is still recorded and scored `arm_failed`, as the
protocol says, and no further amendment is possible.
