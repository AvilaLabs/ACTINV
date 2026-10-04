# P117 Amendment A — actual nested handbook evidence

Registered 2026-10-04 before changing the sealed P117 checker. This consumes
the single repair round permitted by the original protocol. A later failed
required gate is terminal P117-FAIL. Preserve the initial failure; no prior
disposition is changed and no scientific acceptance criterion is relaxed.

## Actual failure and immutable boundary

Original source/evidence checkpoint:
`24929f477ab56ed026eabee4260e39572c226428`.
The complete independent verdict returned actual integer 1 under inspected
6 GiB/swap-zero/task-128/200%-CPU limits. G3_local alone failed; protocol,
P116 history, G0, G1, G2, source binding and CI-evidence consistency passed,
with G3_CI pending. All nineteen initial fresh gates and the quality collector
had observed integer-zero exits.

Preservation archive `results/failures/p117_initial/` contains 70 exact files
plus discovery. Discovery SHA-256:
`73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a`.
It binds all original receipts, retained and raw logs, source helpers,
G0-G3, FAIL verdict, inspected actual verdict invocation/exit, protocol,
release metadata and reviewed preservation writer. Its complete sealed
control map and 100-file Rust map match both the original checkout and
checkpoint Git blobs. Verify the exact archive population, bytes, Git
source population, actual exit/resource/log agreement and original failure
before accepting repair round one.

The collector correctly records the qualified handbook map at
`p116_adopted.public_handbook_sha256`. The verdict mistakenly requires an
additional absent top-level copy. Correct only that reference: compare the
existing nested map with G0, retaining its complete equality with the
handbook Git blobs and all other source/adoption/receipt checks. Add a
regression using the actual quality producer's report, rather than a
hand-constructed object with an invented duplicate field. Reject a changed
nested map. Also test original-failure/archive/source tampering.

Only these four existing sources may change: `controls/check_p117.py`,
`controls/check_p117_verdict.py`, `controls/test_p117.py`, and
`controls/test_p117_verdict.py`. Changes are limited to the reference fix,
real-producer regressions, verified original-failure/amendment binding,
round-one metadata and truthful initial-versus-repeated evidence labels.
Bind this amendment, discovery and all 70 retained files into the new seal.
Every other initial sealed source, all 100 Rust files, handbook files,
transport/workflows/recorder/helpers, scientific fixtures and old P116
artifacts remain byte-identical. No nuclear payload enters Git.

## Required repeated and adopted evidence

Repeat these seven affected gates with fresh observed exits and distinct
preserved logs/receipts: `p117_regressions`, `p117_verdict_regressions`,
`g0_seal`, `g0_replay`, `g1`, `g2`, `full_read_only_replay`. Seal and replay
the amended G0 before science. G1/G2/full replay still require complete
canonical equality with the immutable P116 report, all 35 requests/138
targets, 43 mutations, 50 refusals, assay/legacy controls and exact repeats.
Regenerate G3 and independently derive/replay the local verdict.

The remaining twelve initial P117 gates may be adopted only with complete
unchanged source/binary/handbook identity and exact original archive
receipt/log equality: `rust_fmt`, `seed_regressions`,
`p116_history_regressions`, `p116_history_replay`, `recorder_regressions`,
`seed_local_verify`, `seed_offline_install`, `seed_release_download`,
`native_data_fetch`, `fns_science`, `fns_diagnosis`, `fns_regressions`.
Label them as initial P117 observations, not repeated execution. Preserve
the original nineteen successes and failed verdict in their archive.
The source-identical 32 P116 observations remain explicitly adopted under
the original protocol. Never relabel an unexecuted or failed check as green.

Original execution limits, source/science caps, atomic no-overwrite recorder
rules, stage-first indexed-manifest order, owner commit identity, complete
six-workflow exact-implementation-SHA green requirement and closure-push
verification all remain in force. Replace live generated artifacts or
affected receipts/logs only after their exact archived copies and checkpoint
are verified. No feature work begins before green implementation/closure CI.
