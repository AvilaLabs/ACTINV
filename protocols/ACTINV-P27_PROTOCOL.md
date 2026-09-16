# ACTINV P27 — shared studies and Core contracts

Opened 2026-09-16 after P26b closed `P26b-CONDITIONAL` at commit `8b9c552`. The P26b verdict records
the terms on which P27 may open: the comparator-leg evidence is measured and partitioned, the
`identical_data` leg stays closed, the headroom ambition is re-scoped to measured mechanisms, and
replan items (c) and (d) remain open obligations. P27 opens on exactly those terms; it does not
relax them.

P27 is the extension's first delivering phase: it makes study construction and contract handling a
shared product capability. It delivers the versioned study schema, the deterministic study
generator/executor, the initial qualified contract paths (ACT-STUDY-01, ACT-COMPARE-01) limited to
already qualified operations, the pinned Core interchange and failure contract, and the explicit
qualification policy — each with independent checkers and a verdict. Product capability this phase
does not qualify remains explicitly unavailable; nothing unqualified may read as qualified.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`, `P18-FAIL`,
`P18b-FAIL`, `P24-CONDITIONAL`, `P25-FAIL`, `P26-FAIL`, `P26b-CONDITIONAL` stand exactly as
recorded.

## Why this phase exists

The roadmap draft assigns P27 the shared-capability layer: a study document the user authors,
a deterministic generator that turns it into an auditable case population, executable contract
paths that carry evidence and verdict semantics, and a pinned boundary with Avila Core's
interchange so that ACTINV results can be staged, receipted and claimed by an external orchestrator
without ACTINV losing control of what it certifies. Without this layer every later phase builds on
an unversioned construction path and an unpinned integration surface.

## Frozen scope

P27 may:

- add and edit production code under `crates/` where the deliverable requires it — the study
  schema (`actinv-study-1`), its deterministic expansion, the `actinv study` command family and the
  family-gating errors; every production change carries tests and the workspace quality gates;
- add controls, machine-readable evidence, checkers, protocols and documentation under
  `controls/`, `results/`, `protocols/`, `schemas/` and `docs/`;
- add JSON Schema documents for the study schema and the ACTINV-side adapter package under
  `schemas/`;
- run the released and current `actinv` binaries and the pinned `avila-core` binary under the
  workstation cgroup rule for measurement and conformance demonstration;
- create the ACTINV-side Core adapter package (a Core `package.json`-conformant bundle with
  digest-bound executables, inputs, adapter declarations and expected outputs) under
  `interchange/` for the end-to-end demonstration.

P27 may not:

- change the physics, numerics, data handling or certificate semantics of the existing `actinv run`
  path — the base calculation path stays independent of this integration and behaves exactly as at
  the opening commit on identical inputs;
- edit the pinned Avila Core checkout (`~/Documents/Avila-Labs/project-north-star`) or require
  changes in it; the interchange contract records what ACTINV supplies and what Core consumes — if
  Core's runner cannot execute an out-of-tree package, that limitation is recorded, not worked
  around;
- tag, release or publish any package; the release decision remains the maintainer's;
- claim comparator superiority, headroom, demand, usability or qualification beyond what the
  recorded controls demonstrate;
- execute the maintainer's practitioner study or procure a transport comparator — replan items (c)
  and (d) stay open and are inherited by their owning phases;
- mark an ACT-STUDY-01/ACT-COMPARE-01 path qualified for any operation not already qualified by
  prior phases; unsupported families and combinations must fail with a named `family_not_qualified`
  style error, never a silent fallback;
- introduce `unsafe`, new runtime dependencies, or borrow-checker workarounds; every behavior
  change carries a regression test;
- run a second repair round: one append-only repair amendment makes an otherwise passing closure
  conditional; a second failure closes FAIL;
- modify a frozen gate record after its gate except by the declared amendment path.

## Frozen execution rules

- This protocol's SHA-256 is registered in `protocols/protocol_hash.txt` at opening; the G0 seal
  binds the hash, the opening commit and every prior verdict verbatim.
- The executable scope is frozen at G0: the study-schema draft surface, the qualified-operations
  matrix, the contract-family gating table, the smoke-study population and the adversarial battery
  list — before any product code lands.
- The `p27_qualifying` partition seals at G0: study-run outputs, determinism proofs and
  conformance demonstrations measured under it are consumed once, by the verdict gate; iteration
  and debugging use the diagnostic partition.
- All builds, tests and solver jobs run under the workstation cgroup rule (systemd-run user scope,
  MemoryMax=6G, MemorySwapMax=0, TasksMax=128, CPUQuota=200%, one job at a time) with temporary
  artifacts on disk under `target/preflight-tmp`, never RAM-backed `/tmp`.
- Every measured number is published with hardware, input, cache and tool-identity context; no
  bare ratios.
- A study record must preserve the eligible population exactly: executed, failed, contract_gap and
  undefined-metric cases are counted, never dropped; zero predictions and empty populations are
  reported, not hidden.
- Determinism is byte-level: rebuilding a study emits identical manifest and spec bytes; an
  execution receipt binds executable digests, input digests, the invocation and output digests.
- The qualification policy is explicit: a study record distinguishes `attested` (recorded
  identity), `verified` (bytes re-hashed this run), `reused` (committed-receipt reuse) and `fresh`
  (executed now) evidence; a verdict built on unqualified evidence is labeled `unqualified` — a
  warning display is not a qualification.

## Gates

### G0 — opening seal and scope freeze

Publish `results/g0_p27_seals.json` and `results/g0_p27_check.json`: protocol hash, opening
commit, prior verdicts verbatim (including `P26b-CONDITIONAL` with its recorded terms), identity
pins for the `actinv` binary at the opening commit, the `avila-core` checkout HEAD and binary,
the Core semantic-profile string and draft schema set the interchange will consume, the frozen
qualified-operations matrix, the contract-family gating table, the frozen smoke-study population,
the adversarial battery list and the sealed `p27_qualifying` partition. Independent checker
verifies identity resolution and rejects planted mutations.

### G1 — study schema and deterministic generator

Deliver `actinv-study-1` (`docs/STUDY.md`, `schemas/actinv-study-1.schema.json`,
`crates/actinv-core/src/study.rs`, `actinv study validate|build|run`): a versioned study document
materials×spectra×schedules grid expands deterministically into an ordered case population with
emitted `actinv-spec-1` specs; `study run` executes the population through the existing run path
and writes `study_record.json` with per-case status, artifact digests, response metrics and a
completion verdict. ACT-STUDY-01 is executable for the frozen smoke-study population. Independent
checker re-expands the frozen study, verifies byte-identical rebuild, population accounting and
rejects planted mutations.

### G2 — comparison path and Core interchange contract

Deliver ACT-COMPARE-01: the study `comparison` block — declared variation axes, predeclared
decision rules from a bounded vocabulary, preserved eligible population and zero/failure/undefined
accounting — executed end to end on the frozen population. Publish
`docs/CORE_INTERCHANGE.md` and `results/g2_p27_interchange.json`: the pinned Core semantic
profile (`avila.core/semantic/0.2-draft`) and supported Core range, the external-process
interchange (digest-bound executable and inputs, staged invocation, execution-receipt fields,
claim extraction) and the failure contract (nonzero exit, missing or drifted artifact, unknown
schema, missing qualification each fail closed). Demonstrate the ACTINV-side adapter package
executing one smoke-study case end to end with a receipt whose fields satisfy the contract; if
Core's runner cannot execute an out-of-tree package, that is recorded and the demonstration runs
under the documented contract locally. Remaining families (ACT-ROBUST-01, ACT-REFINE-01,
ACT-SOURCE-01) are declared with their interfaces and fail `family_not_qualified` on use.
Independent checker verifies the contract fields, the receipt conformance and the gating errors.

### G3 — adversarial controls, migration and revocation

Publish `results/g3_p27_controls.json`: the adversarial battery executed — incorrect units,
changed data or executables, missing or duplicate cases, zero or undefined metrics, forged or
stale evidence, missing qualification, altered limits, unexpected schema fields — each rejected
with a named failure; template/schema-version migration behavior (accepted versions migrate
forward, undeclared versions refuse) and template revocation (a revoked template cannot produce
new accepted runs; historical records are not edited) demonstrated; the unqualified-evidence
policy demonstrated end to end. Independent checker replays the battery and rejects planted
mutations.

### G4 — disposition and verdict

Publish the roadmap draft-section entry, the verdict `results/verdict_p27.json` and the
independent closure checker `controls/check_g4_p27.py` (no production or scoring imports;
rehashes inputs, re-derives the case population and determinism digests, re-forms a sample of
study-record metrics from raw artifacts, re-checks gate ordering, partition discipline and the
adversarial-battery ledger, rejects planted mutations). Manifest regenerated once at closure.

## Closure interpretation

`P27-PASS` means the study layer and both initial contract paths execute on the frozen scope, the
Core interchange contract is pinned and demonstrated, the adversarial battery rejects what it must
reject, and the extension may proceed to P28 on the recorded terms. `P27-CONDITIONAL` marks the
single repair round used or a recorded limitation (such as Core's out-of-tree execution
restriction); what qualified and what did not is stated explicitly. `P27-FAIL` preserves all
evidence and leaves the extension closed. No closure asserts user demand, usability, headroom,
release readiness or product superiority; the release decision remains the maintainer's and
unchanged.
