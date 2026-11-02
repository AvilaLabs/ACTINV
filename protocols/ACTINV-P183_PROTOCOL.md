# ACTINV-P183 — the faster rate prune, rerun with a corrected pruning-bound test

## Purpose

P182 asked whether the rate-prune change on master (`793ad03`, the exact sweep speedup, and `ead00ec`, the
significance floor on the stopping rule) leaves every result unchanged apart from looser bounds on nuclides that are
dropped anyway, and how much faster it is. P182 and its one repair round both failed on the pruning-bound totals,
while the results were identical on all 268 problems both times (ledgers 334 and 336). P183 is a new protocol and a
fresh run, with the bound test corrected.

## Disclosure of what has been seen

- Everything P182 discloses, P182's whole record (results, the four failing totals, the recorded times), Amendment A's
  record and the 82 water regions of the 4,096-element ENDF/B-VIII.1 mesh whose overall totals are null
  (`unbounded_components` H1 to He4).
- The test below was written knowing why both failed. The physics results are deterministic and are expected to be
  identical again; what this run adds is fresh timings and the corrected test on fresh outputs.
- Before registration the checker was run on outputs already seen (the 32-region TENDL-2025 mesh and CI's iron
  problem, from P182's field identification) and on a fixture built from a real null-total region's values.

## Builds, problems, runs

As P182 (`protocols/ACTINV-P182_PROTOCOL.md`): baseline `d054c16` and candidate `ead00ec` with the same pinned
binaries, P172's 132 FNS problems on both libraries and P181's mesh at 512 and 4,096 elements on both libraries
(268 problems), each run once by each build in alternating order, inside the AGENTS.md scope on a quiet machine.
The FNS problems are prepared again under `target/p183-work`; outputs go there and are deleted after the verdict.
`controls/p183_harness.py` wraps P182's harness with those paths and this protocol's record.

## Comparison

P182's **Unchanged** and **Pruning record**, except its totals bullet, which becomes:

- the overall totals (`activity_Bq_per_g_bound`, `heat_W_per_g_bound`) are at least the baseline's (within 1e-12)
  and at most `1 + 1e-6` times it, as in P182;
- each part (`dropped_activity_Bq_per_g_bound`, `dropped_heat_W_per_g_bound`, `kept_activity_Bq_per_g_bound`,
  `kept_heat_W_per_g_bound`) is at least the baseline's (within 1e-12), and passes if it meets P182's own test
  (at most `1 + 1e-6` times the baseline's) **or** exceeds the baseline's by at most `1e-6` times the baseline's
  overall total for the same quantity. A part that is null in one result and not the other fails.

So every pair P182's test accepted is accepted, and additionally a part may loosen by an amount below a millionth of
the bound the record reports for the whole.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1–G4:** P182's, on this run's record (pins, clean tree, bounded scope; all 268 problems under both builds, exit
  0; every problem meets the comparison above; quiet machine).
- **G5:** CI green on the evidence commit.

P183-PASS if G0–G5 hold. No repair round.

## Allowed sentences

P182's two sentence templates, filled by the checker. When quoted, they go with this disclosure: the protocol was
written after two failed checks of the same change on the pruning-bound totals, with results identical every time.

## Commands (coordinator)

`controls/p183_harness.py prepare | run | assemble` and `controls/check_p183.py`, from a worktree at the registration
commit, inside the AGENTS.md scope, with no other job running.
