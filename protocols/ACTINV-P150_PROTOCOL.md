# P150 — the P148 solver qualified on CI after the P110 replay repair

Registered 2026-10-19, before the repair commit. Base commit: `c50fce2` (origin/master).

## Why

P148's R6 requires CI green on the pushed commit for every triggered workflow. On the evidence commit `c50fce2` the
`controls` workflow failed (run 39360320526) in the P110 replay, because the evidence regenerated two P110 records
that a chain of history verifiers keeps frozen (P120 Amendment D). P148 allows one repair round, and Amendment B
used it, so P148 closes FAIL on R6. Its R1–R5 evidence stands as recorded in ledger entry 212; the failure is CI
plumbing, not the bound. Entry 212 stated the P148 verdict as a pass before CI had run; that statement was premature
and is corrected by this protocol's registration entry.

As P142 did for P116, this protocol qualifies the unchanged P148 solver on the current code.

## Change under test

No Rust source, no protocol of P148 and no P148 evidence record changes. The candidate commit adds P120 Amendment D
and its implementation (`controls/check_p110.py`, `controls/test_p120_amendment_d.py`), restores the sealed P110 G1
and G2 records, preserves the failed CI run under `results/failures/p148/r6_ci_attempt1/`, and adds this
registration, its hash line, ledger entries and `MANIFEST.sha256`. The other baselines committed by `c50fce2`
(`results/p15_cache_integrity.json`, `controls/ci_expected.json`, the P10 legacy hash) stay as committed.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **Q1.** The release binary used is the P148 evidence binary (SHA-256 in `results/p148/binaries.sha256`), and the
  Rust sources at the candidate are identical to `c2beaa0`.
- **Q2.** P120 Amendment D's D2 and D3.
- **Q3.** Locally, every `controls` workflow step that the 6 GB limit allows, from "P103 source-seal integrity" to
  "Repository is self-contained", exits 0 as `ci.yml` lists it; the steps not run locally are named. The manifest is
  refreshed after staging and `cargo fmt --all -- --check` passes.
- **Q4.** CI green on the pushed candidate for every triggered workflow.

## Verdict

P150-PASS if Q1–Q4 hold: the P148 solver is qualified, with P148's R1–R5 as its scientific evidence. P148 itself
stays FAIL. A failing gate is preserved under `results/failures/p150/`; one repair round, registered as an amendment
first.

## Not claimed

Nothing new about the pruning bound. P150 establishes only that the P148 candidate passes every CI control once the
P110 replay maps the unpruned P148 ledger block.
