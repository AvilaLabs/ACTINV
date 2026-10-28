# P159 — the P156 damage table and catalog 1.2.0 qualified on CI after the catalog sweep

Registered 2026-10-28, before any evidence. Base commit: `aff750b` (origin/master). P158 stays reserved for the
nuclear-data uncertainty layer named in P157.

## Why

P156's G8 requires CI green on the pushed commit. On the evidence commit `4812ba1` the `controls` workflow failed
(run 40693413505) in "P13 verified data-distribution contract", and `fns-iron` (run 40693413532) failed in its case
step: both pinned catalog 1.1.0 and its `v1.1.0/` install directory, and P156 changed the embedded catalog to 1.2.0.
Amendment E had used P156's one repair round, so P156 closed FAIL on G8 (ledger 250). G0–G7 stand as recorded in
ledger 249. The repair `1b02817` changed only controls, the README and the install guide.

As P150 did for P148, this protocol qualifies the unchanged P156 candidate on the current code.

## Change under test

No Rust source, no P156 protocol or amendment, no P156 evidence record and no catalog or data artifact changes. The
candidate is master after this registration: the P156 candidate (`354138a`) plus the `1b02817` repair and the P156
verdict. P159 adds only this registration, its hash line, its ledger entries, its records under `results/p159/`,
its verdict and `MANIFEST.sha256`.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **Q1. Sources and binary.** The Rust sources (`crates/`, `Cargo.toml`, `Cargo.lock`) at the candidate are identical
  to `354138a`, and the release binary used is the P156 candidate binary (SHA-256 `e767e0d0…`, `results/p156_verdict.json`).
  Both embedded catalog copies are identical to those at `4812ba1`.
- **Q2. Published data.** Every asset of the published `data-v1.2.0` release, downloaded afresh, matches the SHA-256
  and size that catalog 1.2.0 records, and the damage table's SHA-256 equals P156's G5 `table_sha256`. A clean
  `data fetch` with the candidate binary installs under `v1.2.0/` and `data verify` passes on it.
- **Q3. Local CI sweep.** Under Python 3.12 with `requirements-ci.txt` (CI's interpreter and pins), each of these exits
  0 as the workflow lists it: the `build-and-control` steps before the build (manifest, preflight regression,
  fusion-isotope opening audit); every step of the `current-controls` job, the P13 contract and public examples among
  them; and every step of the `fns-iron` workflow, from a fresh data directory. `cargo fmt --all -- --check` passes and
  the manifest is refreshed after staging. Steps not run locally are named; CI is their gate.
- **Q4.** CI green on the pushed evidence commit for every triggered workflow.

The `1b02817` and `aff750b` CI runs, which were green before this registration, are disclosed in the ledger but are
not P159 evidence.

## Verdict

P159-PASS if Q1–Q4 hold: the P156 shipped damage table and catalog 1.2.0 are qualified, with P156's G0–G7
(ledger 249) as their scientific evidence. P156 itself stays FAIL. A failing gate is preserved under
`results/failures/p159/`; one repair round, registered as an amendment first.

## Not claimed

Nothing new about displacement damage, the shipped table's values or the Reich–Moore channel fix. P159 establishes only
that the unchanged P156 candidate passes every CI control once the catalog-version pins name 1.2.0.
