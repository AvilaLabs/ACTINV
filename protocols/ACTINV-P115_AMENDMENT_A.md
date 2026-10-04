# ACTINV P115 Amendment A — receipt-test setup and preserved source checkpoint

Registered 2026-10-04 after actual `p115_verdict_regressions` exit 1, eight
tests/one error. Receipt and log share the same temporary parent; creating
that parent twice without `exist_ok=True` raises `FileExistsError` before
receipt assertions. The durable failed receipt records the actual integer
exit and exact argv `python3 controls/test_p115_verdict.py` inside the enforced
scope. Twenty-one other fresh gates have actual zero exits. No P115 G0 seal,
G1/G2 science or implementation/CI qualification exists.

## Exact original evidence

Preserve every original source byte in unpublished Git checkpoint
`887b28d94f32342ea2e3b3410dbb0f0e2aaf2a14`, committed with the completed
indexed manifest before repair. The discovery record
`results/failures/p115_initial/discovery.json` has SHA-256
`22f03d9115f7c2ef5b4c98d19d9fe1cce85e62c38747036f1e130e4c711632db`.
Its `source_sha256` map defines exactly 180 original source/protocol/metadata
files, including the complete 100-file Rust population. Require every original
Git blob from the pinned checkpoint to match this map; no old source is edited
or relabeled retrospectively.

The discovery's `files_sha256` map defines exactly 68 retained receipt/log/
scope-log/resource/writer files under `results/failures/p115_initial/`.
Require exactly those physical files plus the separately pinned discovery
record, safe regular nonsymlink paths, exact SHA bytes and checkpoint Git
blob equality. Require all 22 original gate observations: exactly 21 actual
integer zero exits and one actual integer exit 1 for the verdict regression,
matching the failed receipt schema/phase/gate/argv/cwd/status/log identity.
The failed log SHA is
`51a89379d6fde40f857f9f5b499d79f571b220976ac667ffb420bbf7bcc4945c`.
Bind the complete source map and retained map in amended G0; no initial G0 is
fabricated. Preserve every prior phase disposition and archive unchanged.

## Sole repair

Correct only the redundant temporary-parent creation in the verdict receipt
regression (`exist_ok=True` or create once). Keep all receipt assertions,
source-gate timeout caps, 30 gate names and scientific criteria unchanged.
Update current-round G0/verdict/seal-regression/G3-writer metadata to strict
integer round 1, deriving it from this registered amendment and the exact
pinned discovery/source checkpoint/evidence rather than guessed fields.
Require complete independent rederivation and safe missing/changed/extra
evidence rejection. P114's historical round/evidence remain separate.

Verify original archival bytes before clearing only this phase's 22 active
receipt/log/scope-log destinations. Rerun all 30 gates afresh; adopt no initial
success. Seal and fully replay amended G0 before first CLI science. Keep all
35 requests/138 expectations, immutable independent oracles, tolerances,
refusal/mutation/compatibility criteria, product contract and 100 Rust files
unchanged. The recorder source remains unchanged. All new science uses the
fresh release binary and existing enforced serial workload limits.

This consumes the single permitted repair round. Another failed gate is
terminal P115-FAIL and requires a separately registered successor. Never
convert the original failed observation into a pass or claim an unexecuted
check passed.
