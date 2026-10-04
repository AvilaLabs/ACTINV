# P118 — serialized CI data-cache and unchanged twin qualification

Registered 2026-10-04 after the inspected P117 terminal preservation job
returned actual integer zero, before P118 implementation or executable evidence.

## Objective and boundary

P118 is a control-only successor to complete the approved exact-byte CI data
cache and qualify the already implemented, unchanged P116 waste twin. P117 is
terminal FAIL. Preserve both its original G3-local failure and its amended
coordination/recorder failure, along with all earlier P116 and waste verdicts.
The P117 single repair round is consumed. P118 begins with zero P118 repairs;
that does not restore or waive P117's consumed round.

Do not change Rust, nuclear data, catalog entries, activation inputs, nominal
waste rules, twin calculations, or scientific expectations. The only P117
source transition allowed is the CI workflow change from live P117 qualification
to immutable P117 failure-history verification. Keep the P117 source and
artifacts otherwise byte-identical. New work is limited to standalone P118
controls, verdict/history/receipt helpers and their tests, CI workflow wiring,
and concise maintainer records.

The P117 coordination failure to preserve is: the first `g0_replay` durable
receipt records exit 0; a duplicate invocation was started before the original
tool command had returned; that duplicate child also exited 0, but the recorder
returned 1 when its no-overwrite log/receipt guard found the original files.
This is a required gate failure, not a scientific failure. Preserve the exact
original and duplicate receipts/logs, command argv, coordinator invocation
records, source snapshot, and final P117 verdict. The terminal checkpoint
and archive identities are:

| Item | P117 pinned value |
|---|---|
| Terminal checkpoint commit | `b81e8c3365a5a08ed55e9f99c0c88f945709996d` |
| Failure archive path | `results/failures/p117_terminal/` — 28 retained files, 71 exact prior-archive files and discovery |
| Archive discovery SHA-256 | `0c736be95e42413481378d38ebaea66424caa5caab1aa7712699e93f1866a26e` |
| Initial archive discovery SHA-256 | `73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a` |
| Initial P117 failure verdict SHA-256 | `871231b33029b8696e9ed2f0e260a1f8b44b01a49b08803fd9e7f866f16c96a8` |
| Terminal P117 failure verdict SHA-256 | `11ea137a5362b5cc42c771137771cbad08b5cd7084939480138b80e656ee4e9b` |
| Failed duplicate-recorder observation SHA-256 | `ce4ce3483ae7b1afb4a9a8947d83d1c9611c835d01b12e9fb4b042bf49e39b50` |
| Failed duplicate-recorder output SHA-256 | `4f989719df72b3ef41050748bb96939364f4f1b683b7545d4ee5529f3dbe7dee` |

The P117 history verifier must bind the exact 193-file source population at the
terminal checkpoint and its complete Git blob map. At the current candidate
tree, require those same P117 paths to match byte-for-byte except the registered
`.github/workflows/ci.yml` transition; bind new P118 sources independently.
Bind the complete 100-Rust-file population separately. Verify both complete P117 failure archives,
all archived Git blobs and source maps, and every observed exit/receipt/log,
including the first passing `g0_replay` and the duplicate recorder's actual
exit 1. Confirm that no P117 G2 or G3 artifact exists for the terminal attempt.
Keep the original P117 control sources immutable and do not rerun the old live
P117 qualification suite after changing its workflow: those historical test
and verdict sources rely on the old live workflow and are now qualified only
as archived P117 history.

To reproduce the original P117-FAIL after the CI workflow transition, the
history verifier may use a historical-source projection only for the old
G0/source predicates, and only after proving the complete old Git/source
snapshot, 193-file map, 100 Rust files, both failure archives and actual exits.
It must run the unchanged historical P117 verdict logic against that proved
snapshot. Do not mock or force any physics, scientific disposition, or original
verdict-error predicate. The derived terminal failure must match the retained terminal
P117 verdict exactly. Independently verify the initial metadata FAIL through
its complete archive, original Git sources, actual exit and gate map; never
relabel either episode or force a scientific predicate to reproduce it.

## Evidence policy

The local collector must verify raw target logs against the durable copied logs
before it records adopted observations. The portable verdict must not depend on
untracked `target/p117-*` files: it verifies each adopted raw log from the
immutable P117 archive, verifies that archive raw log equals its archived
durable log, and verifies that the live committed durable receipt/log still
matches the archived bytes and descriptor. Fresh P118 gates bind their committed
durable receipts and logs; raw target paths are only required during local
collection. A clean detached-worktree replay must derive the same verdict with
no temporary P117/P118 target logs present.

Adopt only the twelve unaffected, integer-zero initial P117 gate observations
listed in Amendment A, after exact source, Rust, binary, handbook, receipt, log,
argv and resource checks. Do not label these as rerun. Adopt the 32 P116 local
observations only through the unchanged P117 evidence and P116 failure-history
verifiers. Re-execute the seven P117 Amendment A affected gates under P118's
new recorder. The full scientific population remains the immutable P116
campaign: 35 requests, 138 targets/comparisons, 43 mutations and 50 refusals,
four actual assay variants, 35 complete no-waste legacy comparisons, unchanged
numeric/categorical tolerances and raw byte-identical repeats.
No new data fetch, numerical assertion, or science case is added.

## Gates and ordering

1. Verify and seal P117 terminal FAIL history, including both P117 failure
   episodes, complete source/artifact/archive maps, every actual exit, and
   the duplicate `g0_replay` recorder collision. Verify P116 history and its
   unchanged implementation/CI evidence.
2. Run and record exactly these eleven fresh receipt gates, each in its own
   tool session with a distinct durable receipt and log: `rust_fmt`,
   `p118_regressions`, `p118_verdict_regressions`, `p117_history_regressions`,
   `p117_history_replay`, `recorder_regressions`, `g0_seal`, `g0_replay`,
   `g1`, `g2`, and `full_read_only_replay`. P117 CI workflow wiring runs the
   new immutable history verifier and its tests in place of the old live P117
   qualification calls.
3. Seal P118 G0, then replay it read-only and compare the complete canonical
   report. A failed or missing G0 seal prevents any native campaign launch.
4. Run G1 and G2 in separate output directories with the unchanged P116
   campaign and require exact canonical equality. Run the full read-only replay
   and verify G0/G1/G2 again.
5. Regenerate G3 and derive/replay the local verdict from the newly collected
   evidence. Adopt the twelve P117 observations and 32
   P116 observations with the labels above; record fresh exits for P118's
   eleven fresh gates. Derive LOCAL-PASS only when every required local check
   and replay passes.
6. Before push, use a clean detached worktree at the exact candidate commit to
   replay the source-only verdict with no `target/p117-*` or `target/p118-*`
   files. Record this as a separate pre-push proof outside the candidate tree;
   it must derive the same LOCAL-PASS from committed durable evidence. Do not
   add the proof to the candidate commit, which would create a self-hash cycle.
   The later closure record may bind the completed pre-push proof.
7. Bind all six scheduled implementation workflows to the exact pushed SHA;
   require all six completed green: `controls`, `desktop builds`,
   `Build browser workbench`, `Build handbook`, `fusion-isotope`, `fns-iron`.
   Then verify the closure push's workflows
   are green before terminal PASS.

## Execution discipline and stop rule

The coordinator launches **one gate per tool session**. Do not issue another
gate until the previous tool call itself has returned a terminal integer exit,
its recorder receipt is durable, and its actual scope/process is confirmed gone.
An empty log, a child exit line, or an interrupted orchestration is not proof
that the tool invocation ended. Never duplicate a gate to obtain a status. The
no-overwrite recorder is a final guard, not a substitute for waiting on the
original tool call. Do not terminate or reconfigure another process as a way to
establish completion.

Before any run, enforce the workstation's required 6 GiB memory, zero swap,
128-task and 200%-CPU cgroup limits, one Cargo build job, one Rust test thread,
two Rayon threads, and disk-backed `target/preflight-tmp`. Stop if the limits
cannot be verified. Record each actual argv, integer exit, log SHA-256, receipt,
scope/resource snapshot and timeout. Use distinct, predeclared gate names and
paths; refuse existing outputs.

Limit source and science gates to 600 seconds. Limit artifact/quality,
verdict, receipt, and clean detached-worktree replay gates to 1200 seconds.
Record the selected limit and actual observed duration/exit in each receipt;
do not extend a timeout in place after launch.

P118 permits one repair only after registering a narrowly scoped amendment and
preserving the failed source, exact invocation, receipt and log. Any later
required gate failure is terminal P118-FAIL; preserve it and start a separately
registered successor. Never convert a missing, duplicated, unobserved or
nonzero gate into a pass. No CI qualification is claimed without exact-SHA
green workflows and closure-push verification.
