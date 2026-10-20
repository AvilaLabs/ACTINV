# P148 Amendment B — R5 hash class for lists of hashes

Registered 2026-10-19, after the first R5 evidence run failed and before it is repeated. No gate is relaxed.

## B1. The failure

The first R5 run on candidate c2beaa0 (preserved as `results/failures/p148/r5_attempt1.json`) failed on one record,
`results/p15_cache_integrity.json`. Every difference in it was the normalized-result hash moving from `9853ccc8…` to
`34f21e21…`, the change P148 is expected to make. Four of the six were classified `hash`. The other two are the items
of a list, `concurrent_preparation/normalized_result_sha256/0` and `/1`. The classifier took the field name from the
last path component, which for a list item is an index, so it found no hash marker and classed them `other`.

## B2. The repair

In `controls/p148_baselines.py`, a value's field name is the nearest non-index component of its path, so a list item
under a `*_sha256` key is a hash like the key itself. Nothing else changes. The selftest gains an allowed change (a
list of hashes changing with the result) and a planted one (an item of a list with no hash marker changing), which
must still be caught as `other`.

## B3. Other R5 results of the failed run

P110 G1 and G2 differed only in hashes; the CI end-to-end spec in pruning, drop-set, rounding and noise differences;
the P10 legacy neutron spec only in pruning, with both pre- and post-P148 hashes reproduced. R5 is repeated in full on
the same binaries after this amendment.

## B4. Evidence-run driver changes disclosed

Before the R1–R3 evidence batch completed, `controls/p148_runs.py` was changed in three commits, none touching the
solver: independent cases run in two worker processes with per-case resume (989924f); probe runs request only
inventory, activity, heat and the ledger, which are all R1 and R2 read (24839c0; on Dy 2000exp_5min the unpruned
CRAM-48 reference took 540 s and 4.1 GB with default outputs and 6 s and 112 MB without, inventories, activities and
heat bit-identical); and the per-case cache key includes the outputs (af9eb2f). All 134 cases of the evidence batch
were computed by af9eb2f. A first two-worker attempt with default outputs was stopped by the cgroup's 6 GB limit
(the OOM killer acting inside the scope).
