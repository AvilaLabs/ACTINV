# P142 — Twin waste classification qualified on the current code

Registered 2026-10-16, before any evidence or source change. Base commit: `f0db085` (v1.5.0 published). The owner
directed on 2026-10-16 that the remaining waste program be finished; this is its first step.

## Why

The optional twin waste block (`actinv twin` with `waste`, `crates/actinv-cli/src/twin_waste.rs`) passed its complete
local scientific campaign under P116 on 2026-10-04, but P116, P117 and P118 closed FAIL on CI plumbing: the IAEA
decay archive returned HTTP 403 (P116), an evidence-recorder overwrite guard (P117) and a history checker expecting an
absent `file_count` field (P118). None failed on science. P119 and P120 (P120-PASS, 2026-10-07) repaired the CI
verifiers, and since then every `controls` run executes the step "P116 unchanged twin verification successor
controls", which reruns `controls/check_p116.py --no-write` with the current release binary: G0 from the Git objects
of `2117f3b`, the full 35-request / 138-component-target G1 campaign whose report must equal the sealed
`results/g1_p116_twin_waste.json` byte for byte, and the G2 replay. No successor was registered, so the twin waste
block has no qualifying verdict. P142 supplies one. The preserved P116, P117 and P118 FAIL dispositions do not change.

## Change under test

No production change. The twin waste contract, fixture `controls/fixtures/p113/cases.json`
(`e6bcebc8…d93d5f956`), tolerances, mutations, refusals and sealed G0/G1/G2 records are P116's, unchanged. New:
this protocol, `controls/check_p142.py` (runs the gates and derives the verdict from actual exits), one Rust
regression test, the records under `results/p142/` and their ledger entries.

The regression test settles a parked item from P140 (ledger entry 169): waste composition verification turns every
run-ledger defect class into an `audit_<class>` reason, so `unmodelled_decay_branches_to_leakage` must yield
`audit_unmodelled_decay_branches_to_leakage` and a non-complete coverage. The test pins that; no source change is
expected. If it fails, the repair needs an amendment registered first.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
The candidate is the release build of the commit carrying this protocol and the new test.

- **Q1, unchanged science.** With the candidate release binary, `controls/check_p116.py --no-write` exits 0: G0
  passes from pinned objects; the full G1 campaign (35 requests, 138 component targets with independent comparisons,
  at least 30 rejected mutations, at least 30 sentinel-preserving refusals, byte-identical repeats) reproduces the
  sealed P116 G1 report byte for byte; G2 replays G0 and G1 in separate paths and matches. The run's output is kept
  in `results/p142/q1_check_p116.log`.
- **Q2, history intact.** The CI steps P103 through P118 each exit 0 as `ci.yml` lists them.
- **Q3, regression.** The new test passes, together with the workspace tests, `cargo fmt --check` and clippy
  `-D warnings` (native and wasm).
- **Q4, CI.** Every workflow triggered by the pushed evidence commit completes success, `controls` among them.

## Verdict

P142-PASS if Q1–Q4 hold: the twin waste block is qualified within P116's scope (nominal U.S. 10 CFR 61.55 classes
from original mesh activities for declared components, with conditional coverage and unassigned-cell reporting). A
failing gate is preserved under `results/failures/p142/`; one repair round, registered as an amendment first.

## Not claimed

No package class, disposal acceptance, intrusion dose, legal compliance or physical completeness. Assay, mixture,
propagated and dose updates still do not change the waste classification. P116, P117 and P118 stay FAIL; P142 is the
qualifying successor, not a re-derivation of them.
