# ACTINV P105 — nominal waste control replay successor

Opened 2026-10-03 under the owner's direction to complete the waste extension.
P103 and P104 remain closed FAIL. P104's repaired G1 write passed all 126 public
vectors and supplemental gates, but the required read-only replay failed before
G2: its checker inserted `persisted_result_matches` before repeating whole-result
equality. The inserted diagnostic key guaranteed inequality. Preserve the failed
replay and all prior seals, amendments and verdicts.

## Frozen contract

The complete scientific, CLI, scope, physics, source, vector, field, numerical,
cost and gate contract is inherited without change from
`protocols/ACTINV-P104_PROTOCOL.md`, SHA-256
`75498fb685bdb4e91370c7f7af0c590c463b29f34e1e0681a25045f51436567b`,
and its Amendment A, SHA-256
`b83a177adcd2614b771da62726a0e3d941f9e986c62cd218f8baea3a7ec8bf54`.
These inherit the P103 protocol and independently checked official CFR rule
pack. Public vectors and production Rust remain unchanged. Prior outcomes are
observed diagnostic evidence, not a new blinded predictive partition.

Correct only replay ordering: compute whole-result equality before inserting
the diagnostic field, and use that saved boolean for the pass decision. New
control paths and schemas identify P105. The source/vector inputs, independent
arithmetic and P104 Amendment A fixture corrections remain unchanged.

## Gates

G0: register this protocol hash and seal successor checker/helper/lifecycle-test
hashes before production CLI evidence. Independently recheck the inherited P103
source and vector seals read-only. Retain the P104 failure.

G1: execute all 126 unchanged public-rule vectors and all supplemental controls
under the full inherited field contract. Then explicitly replay G1 with
`--no-write` and require equality against saved evidence. Reject missing or
altered persisted evidence; no diagnostic key may enter the comparison.

G2: execute the exact frozen tiny Nb capture/decay fixture and complete independent
analytic, interval, full-solve, refusal, mutation, above-limit and contributor
checks specified by P104 and its amendment. Check both times, both Nb intervals,
joint margin and the four named Rust interval regressions. Preserve every failure.

G3: execute the inherited real tiny component run/waste and budget byte-repeat
gates, workspace fmt/check/clippy/test and CLI test compilation, four bounded
Python-child lifecycle tests, handbook build/link/browser checks, and full
read-only control replay. After a fresh release build, GitHub Actions runs the
data-free P105 checker. Every pushed-commit workflow must be green before the
checkpoint is complete.

Checker-derived P105-PASS requires every gate. One repair round is available;
a further failed gate closes FAIL and requires a new successor. Numerical
tolerances, source interpretation and population cannot change using measured
outcomes. Classification is nominal single-component U.S. Part 61 arithmetic;
full BTP, uncertainty/composition ranges, intrusion screening and site acceptance
remain outside the phase.

Coordinator runs one executable job at a time in the enforced 6G/zero-swap,
128-task/200% CPU systemd scope with disk TMPDIR and bounded child waits/reaping.
The existing tiny fixture settles the gates; no bulk fetch is needed. Close with
session, append-only ledger, indexed manifest, owner-authored commit, push and
green Actions.
