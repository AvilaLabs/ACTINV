# ACTINV P26 — user workloads and feasibility

Opened 2026-09-17 after P24 closed `P24-CONDITIONAL` at commit
`e8cd2c7c485fa05598c63f43e56210066aad0c8f` and P25 closed `P25-FAIL` at commit
`acd476a9bb342d171583a2d2f881f1f13e4b6e46`. The 1.1.0 release hold stands unchanged; the P26–P35
extension draft in `docs/ROADMAP.md` ("Draft next-evolution extension — P26–P35, 2026-09-13")
explicitly conditions its execution on that recorded disposition, which is now satisfied.

P26 is the extension's entry phase: it establishes *whether* the drafted ambition is feasible and
valuable before any dependent phase opens. It produces a user-derived flagship workload definition,
a current competitive baseline, sealed evidence partitions and bounded method prototypes — and a
verdict. If the ambition is not feasible or not valuable, P26 closes with that finding and an
explicit replan; a missed leadership target cannot silently become a pass by relabeling it a
stretch goal.

Prior verdicts are preserved byte-for-byte and never rewritten. In particular `P17-FAIL`,
`P18-FAIL`, `P18b-FAIL`, `P25-FAIL` and `P24-CONDITIONAL` stand exactly as recorded.

## Why this phase exists

The extension draft proposes an analyst-facing outcome — *an analyst can complete an activation
investigation, understand what controls its conclusions, test the important alternatives and give a
colleague a reproducible result with substantially less effort and waiting* — with draft targets
(10x/3x wall-time headroom on a primary and secondary workload, 50% lower median hands-on time,
complete contract coverage of supported assisted workflows). The draft itself records that no new
benchmark, user study, solver job or AI evaluation was executed to write it. P26 supplies that
missing evidence layer: every proposed target is tested against measured bottlenecks, real user
evidence and an executable comparison contract before P27 may open.

The phase also repairs a provenance obligation: the draft's baseline identities (ACTINV `5049129`,
Core `3e6dd8f0`) are stale. They are refreshed and pinned at G0 below.

## Frozen scope

P26 may:

- add controls, research notes, measurement tooling, machine-readable evidence and documentation
  under `controls/`, `results/`, `protocols/` and `docs/`;
- add clearly-marked bounded prototype code under `prototypes/` (new directory) that no production
  path imports, links or packages;
- profile, time and measure the released and current ACTINV paths on representative inputs, and run
  or inspect comparator tools where a lawful copy is available to the maintainer;
- freeze the flagship workload definition, the comparison contract and the evidence partitions that
  later phases consume.

P26 may not:

- edit `crates/`, Python bindings, schemas, production parsers, public examples, release artifacts
  or default data — this phase changes no production physics and ships no prototype;
- tag, release or publish any package; the 1.1.0 hold is unaffected by this phase;
- change a frozen contract, workload definition, partition or feasibility criterion after its gate
  without an append-only amendment and a conditional verdict;
- claim a user study, population estimate or error-rate guarantee from evidence that does not
  contain it; user-problem claims must cite the recorded source they derive from;
- execute user studies on behalf of the maintainer — P26 may design the instrument and score
  recorded responses; it may not fabricate participants, responses or demand;
- treat AI-generated text as user evidence or as a scientific derivation;
- introduce `unsafe`, new runtime dependencies, `Arc`, `Mutex`, interior mutability, or
  cloning/allocation as borrow-checker workarounds in any committed code (prototype code under
  `prototypes/` may use only existing workspace dependencies);
- spend the fresh-measurement budget of a later phase: any comparison that would consume blind
  benchmark evidence belongs to that phase's own sealed partition.

## Frozen authorities and provenance

- This protocol's SHA-256 is registered in `protocols/protocol_hash.txt` at opening.
- Drafting baseline refresh: ACTINV HEAD at opening `e8cd2c7c485fa05598c63f43e56210066aad0c8f`;
  Avila Core source identity is re-pinned at G0 from the maintainer's checkout, or recorded
  unavailable with the consequence stated.
- The P26–P35 draft text in `docs/ROADMAP.md` at the opening commit is the frozen proposal under
  test; the roadmap file may receive dated entries only in its draft sections and never rewrites
  this protocol.
- Prior-phase evidence P26 consumes read-only: `results/verdict_p24.json`,
  `results/verdict_p25.json`, `results/g4_p24_fresh.json`, `results/g5_p25_acceptance.json`,
  `results/g0_p24_candidate_build.json`, `docs/P25_TENDL2025_DEFECT_REPORT.md` and the P22 release
  scorecard.
- Comparator claims must name the exact comparator identity and version: candidates for the
  baseline set are FISPACT-II, ORIGEN-S/SCALE, ALARA, OpenMC (depletion/activation capability) and
  the ACTINV v1.0.1 signed release. A comparator that cannot be lawfully obtained or executed is
  recorded as such; no comparator number may be estimated from documentation.

## Frozen workload derivation rules

- The flagship workload is *derived*, not asserted: each candidate workload must cite the recorded
  user evidence (application studies, public user discussions, maintainer project history) that
  demonstrates a consequential problem. A workload with no citation cannot be the flagship.
- The flagship's comparison contract (population, comparators, equivalent-output definition,
  tolerances, decision rules and failure scoring) is frozen at G2 **before** any prototype runs
  against it. Timeout and failure cases are predeclared scoring categories.
- Evidence partitions: measurements intended to qualify a later phase's claims are partitioned from
  diagnostic measurements; a partition may be consumed exactly once by the phase that sealed it.
- Feasibility is judged per draft target: each is recorded feasible, infeasible, or undetermined —
  with the measurement or absence of evidence that produced the judgment. Undetermined is a failure
  mode of the phase, not of the target.

## Gates

### G0 — opening, authority, baseline refresh

An independent control binds this protocol's SHA-256, the opening commit, the refreshed Core
identity (or its recorded unavailability), and all prior verdicts asserted verbatim. It records the
comparator-availability census (which baseline tools are installed/licensed/executable on this
workstation) and verifies no committed file contains prototype code on a production path.

### G1 — user-problem derivation

Publish `results/g1_p26_workloads.json`: the ranked candidate workload list, each with its recorded
user-evidence citations, the flagship selection and the explicit rejection reasons for
non-selected candidates. An independent checker verifies every citation resolves to a recorded
source in the repository or a maintainer-provided record — no citation may be text generated inside
this phase.

### G2 — comparison contract freeze

Publish `results/g2_p26_contract.json`: the flagship's complete comparison contract — eligible
population, comparator set, equivalent-output definition, tolerances, decision rules, timeout and
failure scoring categories, and the evidence partitions each measurement class feeds. Independent
checker rejects missing fields and post-freeze edits. After G2 the contract is frozen; a later
change is an amendment and makes an otherwise passing close conditional.

### G3 — bounded prototypes and headroom measurement

Run the bounded method prototypes and measure headroom against the frozen contract: wall-time on
the primary and a second distinct workload, hands-on task decomposition for the interpretation
tasks, and the contract's executable-coverage census. Publish raw timings with hardware, input and
cache state; no number may be presented without its execution context. Prototypes remain
unshipped.

### G4 — feasibility verdict and independent closure

The independent checker (no production, prototype or scoring imports) rehashes inputs, re-derives
the workload ranking from the cited evidence, recomputes headroom arithmetic from raw timings,
verifies gate ordering and partition discipline, re-verifies all prior verdicts verbatim and
rejects planted mutations. Verdict to `results/verdict_p26.json`; the manifest is regenerated once
at closure.

## Closure interpretation

`P26-PASS` means the flagship workload is derived from recorded user evidence, its comparison
contract is complete and frozen, and measured headroom makes the drafted ambition credible — it
authorizes opening P27, nothing else. It asserts no product, release or competitive claim.
`P26-CONDITIONAL` marks a single append-only repair round used. `P26-FAIL` preserves every artifact
and ledger as public evidence and requires an explicit replan recorded in the roadmap's draft
section before any extension phase opens. In every closure the 1.1.0 release decision remains the
maintainer's and unchanged.
