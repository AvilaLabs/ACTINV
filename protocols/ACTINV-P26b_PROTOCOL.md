# ACTINV P26b — P26 replan execution and extension re-entry

Opened 2026-09-16 after P26 closed `P26-FAIL` at commit
`b1775e36027d68a610e4b64ed6fa914ecda9cfbf`. The frozen P26 closure rule requires "an explicit
replan recorded in the roadmap's draft section before any extension phase opens"; the recorded
replan (`docs/ROADMAP.md`, "P26 replan entry (2026-09-14)") names four actions and states "No P27+
phase opens on this record." P26b is the successor session under the standing rules: it executes
the replan — makes the comparator leg measurable, re-scopes the unsupported ambition, records the
items that cannot be executed on this workstation — and produces a new verdict and disposition on
which the extension sequence may or may not proceed.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`, `P18-FAIL`,
`P18b-FAIL`, `P24-CONDITIONAL`, `P25-FAIL`, `P25b-FAIL`, `P25c-PASS`, `P26-FAIL`. Releases
`v1.1.0`, `v1.1.1`, `v1.1.2` and `data-v1.1.0` have shipped; this phase changes no release
artifact, creates no release, and asserts no product or competitive claim.

## Why this phase exists

P26 produced the comparison contract, workload selection (flagship `W-MATCMP`, second campaign
`W-CAMPAIGN`, spatial handoff `W-R2S`) and measurement machinery, but closed FAIL because the
drafted ambition was unmeasurable on this workstation:

- ALARA 2.9.2 executed but its only on-disk library covered zero contract elements
  (`contract_gap` on all 1,016 contract cases);
- FISPACT-II, SCALE/ORIGEN and OpenMC were unavailable, leaving no executable equivalent-output
  comparator;
- measured in-process amortization on the 1,000-case W-CAMPAIGN grid was 1.33x, not the drafted
  10x;
- no practitioner study was recorded, so the 50% hands-on target had no baseline.

The replan's remedies are executable here for (a) and (b), and honestly recordable for (c) and
(d):

- **(a)** The on-disk FENDL-3.2c ENDF corpus (`~/nuclear-data/fendl-3.2c`, hash-pinnable) is
  ALARA-convertible through the ALARAJOYWrapper pipeline (`tools/ALARAJOYWrapper` in the ALARA
  2.9.2 tree): NJOY 2016.79 (on-disk build) produces GENDF data, the wrapper emits the DSV, and
  ALARA's `convert_lib` input block produces the binary library. A pinned decay library is
  required and is obtained at G0 as described below.
- **(b)** The headroom ambition is re-scoped to mechanisms with measured support; shared
  prepared-network loading is recorded as the P31 candidate, not a 10x claim.
- **(c)** The practitioner study is the maintainer's; demand and hands-on conclusions remain
  unestablished and P27+ proceeds, if it proceeds, under the roadmap's proxy-labeling rule.
- **(d)** No lawful transport comparator exists on this workstation (OpenMC absent); the R2S leg
  stays unmeasurable and is inherited by P32 unchanged.

## Frozen scope

P26b may:

- add controls, census/conversion tooling, machine-readable evidence, checkers and documentation
  under `controls/`, `results/`, `protocols/` and `docs/`;
- fetch the public UKDD-2020 decay library once, hash-pin it at G0, and record its source URL,
  retrieval date and digest; no other download is permitted;
- build bounded ALARA binary libraries from the pinned FENDL-3.2c corpus and pinned decay data
  under a declared, non-shipping work directory (`target/p26b/` or `~/nuclear-data/alara-2.9.2-build`),
  and build a bounded ACTINV comparison artifact from the same pinned FENDL-3.2c files under
  `target/p26b/` — neither artifact is shipped, added to the catalog, or reachable from any
  production path;
- generate and execute ALARA inputs for contract-subset cases under the resource limits below;
- append dated replan-execution entries to the roadmap's draft extension section only;
- extend `results/p25c_release_publish.json`-style publish records only if a release actually
  changes, which this phase does not cause.

P26b may not:

- edit `crates/`, `python/`, schemas, production parsers, public examples, the embedded catalog,
  shipped data artifacts or release files;
- tag, release or publish anything;
- modify the frozen `results/g2_p26_contract.json` or any P26 artifact — P26b freezes its own leg
  contract as a child document citing it;
- claim comparator superiority, headroom, demand or usability from these measurements — outputs
  are leg-executability and measured-difference evidence only;
- consume any evidence partition sealed by another phase; the `p26b_qualifying` partition below is
  new, sealed at G0 and consumed exactly once, at G3;
- fabricate comparator results where ALARA cannot execute; an unconverted nuclide is a ledgered
  gap, never an estimated row;
- add `unsafe`, new runtime dependencies or production-path code;
- run a second repair round: one append-only repair amendment makes an otherwise passing closure
  conditional; a second failure closes FAIL.

## Frozen execution rules

- This protocol's SHA-256 is registered in `protocols/protocol_hash.txt` at opening; the G0 seal
  binds the hash, the opening commit and every prior verdict verbatim.
- The executable subset is declared and frozen at G0 *before* any conversion or measurement: a
  named subset of `W-CAMPAIGN`/`W-MATCMP` contract cases whose parent-nuclide coverage the FENDL
  corpus can actually supply, recorded with the element list {Fe base; Ag, Co, Cr, Cu, Mn, Mo, Nb,
  Ni, Ta, V impurities} and the per-element nuclide set the conversion will attempt.
- The `p26b_qualifying` partition seals at G0: comparator-leg outputs and wall times measured under
  it are consumed once, by the verdict gate; iteration and debugging use the diagnostic partition.
- All conversion and solver jobs run under the workstation cgroup rule (systemd-run user scope,
  MemoryMax=6G, one heavy job at a time) with artifacts on disk, never RAM-backed `/tmp`.
- Every measured number is published with hardware, input, cache and tool-identity context; no
  bare ratios.
- ALARA/ACTINV output equivalence uses the frozen leg contract's metric definitions; a metric that
  cannot be formed is reported undefined, never silently dropped.

## Gates

### G0 — opening seal and feasibility census

Publish `results/g0_p26b_seals.json` and `results/g0_p26b_check.json`: protocol hash, opening
commit, prior verdicts verbatim, identity pins for `alara` 2.9.2 binary, `njoy` 2016.79 binary,
the FENDL-3.2c corpus (its on-disk MANIFEST plus a re-derived digest sample), the UKDD-2020 decay
file (fetched once, hash-pinned, source recorded — or its unavailability recorded with the
consequence stated), the Avila Core checkout HEAD and binary identity (now present at
`~/Documents/Avila-Labs/project-north-star`, pinned read-only), and the refreshed comparator
census including an explicit OpenMC absence re-check. The frozen executable-subset declaration
and the `p26b_qualifying` partition seal are published in the same record. Independent checker
verifies identity resolution and rejects planted mutations.

### G1 — comparator library construction

Produce the ALARA binary library for the declared nuclide set via ALARAJOYWrapper + NJOY 2016.79 +
ALARA `convert_lib`, and the matching ACTINV artifact from the same pinned FENDL-3.2c files.
Publish `results/g1_p26b_conversion.json`: per-nuclide conversion ledger with named failure
classes, library element coverage vs the declared set, index/digest identities for both
libraries, and one fail-closed smoke run per tool. Independent checker re-verifies coverage,
ledger completeness and that failed nuclides are absent from the claimed coverage.

### G2 — executed comparator leg

Freeze the P26b leg contract (`results/g2_p26b_leg_contract.json`) — subset cases, both legs
(`identical_data`: ACTINV-on-FENDL vs ALARA-on-FENDL; `product_plus_data`: ACTINV shipped
artifact vs ALARA-on-FENDL), metric definitions, tolerances, timeout/failure categories and
decision fields — *before* any run. Then execute and publish `results/g2_p26b_leg.json`: raw
per-case outputs, formed metrics with undefined metrics preserved, wall times with execution
context, and `contract_gap` accounting for any subset case that still cannot execute. Independent
checker rejects post-freeze contract edits and unrecorded case loss.

### G3 — disposition and verdict

Publish the roadmap draft-section replan-execution entry (re-scoped headroom ambition, executed
comparator-leg outcome, and the recorded status of replan items (c) and (d)), the verdict
`results/verdict_p26b.json`, and the independent closure checker `controls/check_g4_p26b.py`
(no production or conversion imports; rehashes inputs, re-forms metrics from raw outputs,
re-checks gate ordering and partition discipline, rejects planted mutations). Manifest
regenerated once at closure.

## Closure interpretation

`P26b-PASS` means the comparator leg executed on the declared subset with measured outputs, the
replan is fully dispositioned in the roadmap, and the extension sequence may proceed to P27 on
the recorded terms. `P26b-CONDITIONAL` marks the single repair round used or partial subset
executability; what opened and what did not is stated explicitly. `P26b-FAIL` preserves all
evidence and leaves the extension closed. No closure rewrites the P26-FAIL record, asserts user
demand, or makes any release decision.
