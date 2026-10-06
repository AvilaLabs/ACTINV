# P119 — CI verification of preserved recovery failures

Registered 2026-10-06, before changing the failed history checker, in response
to the owner's instruction to repair CI. Base commit:
`0353d5e3288a90cfefaabe7d2263b22fbaeeecad`.

## Scope

Repair the P117 history reader's assumption about the pinned initial discovery
schema. Replace live P118 qualification in CI with verification of its preserved
terminal failure. This is a CI/history repair; it does not finish the unchanged
waste twin qualification attempted by P116/P117/P118. Their FAIL dispositions,
all archived bytes, scientific expectations, Rust sources and nuclear data stay
unchanged. No previously unrun P118 gate is described as executed.

Permitted existing source edits are `controls/check_p117_history.py`,
`controls/test_p117_history.py`, and `.github/workflows/ci.yml`. New P118 history
verification and regression sources, this registration, an append-only registry
entry, manifest updates and concise recovery/verification records are in scope.
The workflow transition is the exact replacement of the P118 qualification step
with its history regression and verification commands; all other steps remain.

## Required evidence

Verify the genuine initial P117 schema, its complete seventy-entry file map,
pinned discovery bytes and both complete failure archives. Preserve the terminal
wrapper's explicit count; do not manufacture that field in the initial record.
Retain the existing source, Git, receipt, resource, log and verdict checks.

Bind P118's terminal discovery SHA-256
`4d7f99aabec30439317c7de1bed2a70a26c63cb722c88d78a821038687d5de7a`
to checkpoint `6f3f964cf06ad4e969392b90effbe4f835d183a3`.
Verify its complete 42-file terminal archive, 16-file initial archive and
100-file predecessor archive, complete 319-file checkpoint source map,
100 Rust files and 27 handbook inputs. Require current source identity except
the three named edits, whose behavior is covered by fresh regressions and exact
workflow comparison. Independently check both P118 failure episodes, original
and amended recorder identities, actual child/outer exits, durable/raw log
agreement, terminal verdict identity and absence of all unrun artifacts.
Successful history verification means the recorded FAIL is intact, not that
its calculation or qualification passed. Do not force historical verdict
predicates to hide a newly discovered discrepancy.

Run fresh P117/P118 history regressions and both read-only history verifiers.
Cover real archived records plus mutations of file maps, discovery/source
hashes, exits, logs, resources, absent artifacts and workflow wiring. Run the
unchanged P118 recorder and verdict regression suites separately. Run the
unchanged P116 scientific replay, preflight regression, stage-first manifest
refresh, formatting and CLI test compilation before publishing.

Only the coordinator runs executable jobs, serially, under the required
systemd cgroup (6 GiB memory, zero swap, 128 tasks, 200% CPU), bounded waits and
disk-backed `target/preflight-tmp`. Record actual results and retain failed
attempt logs. Review process spawning before execution. Ordinary implementation
iterations do not alter any predecessor phase or its consumed repair round.

Replay both history verifiers from a clean detached worktree at the exact
candidate commit without transient target logs before push. Require all six
implementation workflows green on that SHA (`controls`, `desktop builds`,
`Build browser workbench`, `Build handbook`, `fusion-isotope`, `fns-iron`).
Record exact-SHA observations in a subsequent closure record; verify the closure
SHA workflows as well. Merge only after the repair's checks are green, then
verify the resulting master SHA. CI recovery is complete only after these
observations; broader waste qualification remains open.
