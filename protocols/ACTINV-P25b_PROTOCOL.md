# ACTINV P25b — alternate neutron-evaluation qualification

Opened 2026-09-17 after P24 closed `P24-CONDITIONAL` at commit
`e8cd2c7c485fa05598c63f43e56210066aad0c8f` and P25 closed `P25-FAIL` at commit
`acd476a9bb342d171583a2d2f881f1f13e4b6e46`. P25 demonstrated that TENDL-2025 cannot currently serve
as the qualified candidate evaluation: 17 of the 49 IRDFF-II benchmark targets fail construction,
including the Ni-58 monitor channel, and the corpus census found 62 files with barn-scale internal
contradictions. The 1.1.0 release decision is hostage to a single evaluation's defect surface.

P25b qualifies an *alternate* neutron evaluation as the candidate artifact, under the same
fail-closed construction discipline, decimal-oracle defect census and frozen acceptance machinery
that P25 established for TENDL-2025. Its verdict informs — never makes — the release decision.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`, `P18-FAIL`,
`P18b-FAIL`, `P24-CONDITIONAL`, `P25-FAIL`. The release hold stands unchanged.

## Why this phase exists

The P25 recommendation, deferred pending P24, was a multi-evaluation fallback so the release is not
bound to one library. P24 has now clarified the benchmark/domain picture: the scoring machinery,
corrected measurement definitions and row ledgers are validated (`P24-CONDITIONAL`), and both IRDFF-II
partitions are fully consumed and public. What the release decision lacks is a candidate artifact
that survives construction qualification. Three alternate evaluations are already on-disk and
hash-pinnable without new downloads:

- **TENDL-2023** (`~/nuclear-data/tendl-2023`): a prior TENDL release with previously built
  ACTINV 709-group artifacts — identical format conventions, potentially different defect surface;
- **FENDL-3.2c** (`~/nuclear-data/fendl-3.2c`): the fusion-oriented activation library, 192
  pointwise ENDF neutron files — activation-curated but far narrower isotope coverage;
- **EAF-2010** (`~/nuclear-data/eaf-2010`): the European Activation File — activation-oriented,
  format compatibility to be determined by census.

A maintainer-supplied additional evaluation (e.g., an ENDF/B-VIII.1 neutron sublibrary download)
may be added at G0 and only at G0, hash-pinned like the rest.

## Frozen scope

P25b may:

- add controls, census/trace tooling, machine-readable evidence, checkers and documentation under
  `controls/`, `results/`, `protocols/` and `docs/`;
- build bounded candidate artifacts from the pinned alternate evaluations over the IRDFF-II
  target set and, where the evaluation supplies them, the P25 isomeric population;
- run the same decimal-oracle defect census on candidate files that P25 ran on TENDL-2025;
- score the now-public P24/P17 partitions retrospectively, labeled as such.

P25b may not:

- edit `crates/`, Python bindings, schemas, production parsers, public examples, release artifacts
  or default data — this phase changes no production physics;
- re-score, re-map or reinterpret the P18b/P25 population as blind evidence — it stays
  retrospective, labeled;
- present any retrospective partition result as blind validation;
- silently drop a source file, target, reaction, product, state or scored row;
- repair by measurement outcome — any builder change applies uniformly by demonstrated defect
  class and requires its own amendment;
- introduce `unsafe`, new runtime dependencies, `Arc`, `Mutex`, interior mutability, or
  cloning/allocation as borrow-checker workarounds in committed code;
- tag, release or publish any package.

## Frozen honesty rules for a consumed corpus

Every IRDFF-II paper table is now public diagnostic evidence — the P17 and P24 partitions are both
consumed. Therefore:

- All scoring in P25b is **retrospective** and must be labeled so wherever it appears; no blind
  C/E claim may be made for an alternate evaluation from this corpus.
- If the maintainer supplies a genuinely new, previously unread measurement corpus, it is sealed at
  G0 like the P24 partition and may be read exactly once under the corrected P24 definitions —
  the only path to a blind claim in this phase.
- A candidate that passes construction gates but has only retrospective scoring is qualified as
  *engineering-qualified, retrospectively validated* — the same label P25's verdict carried.

## Frozen defect taxonomy and repair rules

Carried verbatim from P25:

- defect classes: `processing_bug`, `state_catalog_mapping`, `tiny_absolute_discrepancy`,
  `genuine_source_inconsistency`, `zero_prediction_scored`, `eligibility`;
- repair classes: R1 correctness, R2 source reconciliation inside the frozen 0.001 envelope, R3
  accounting — all by demonstrated mechanism only, verified by the decimal oracle and an
  independent checker;
- format-incompatibility of a candidate file is a coverage fact, ledgered
  `construction_failed:format` — never worked around by a bespoke parser written mid-phase.

## Frozen acceptance gates

The fixed eligible population is the union of (a) the 49 isotopic IRDFF-II benchmark targets from
P24's G0 catalog and (b) the P25 neutron isomeric rows for which each candidate evaluation ships a
corresponding file — the denominator is per-evaluation and published, never silently shrunk.

1. **Complete outcome accounting.** 100% of each candidate's eligible population receives exactly
   one named outcome: `scored`, `zero_prediction`, `construction_failed:<class>`,
   `format_unsupported`, `undefined_ratio:<class>` or `eligibility:<class>`.
2. **Construction coverage.** Per-candidate built/failed counts over the IRDFF-II target set are
   published beside TENDL-2025's 32/49; the dosimetry-critical set (Ni-58, Au-197, Ag-109, Nb-93,
   In-113, In-115) is reported as a named sub-census.
3. **Comparable-case nonregression.** On rows scored by both v1.0.1 and the P25b candidate, P18b's
   frozen additive/multiplicative median/p90 limits and coverage points apply, scored through the
   unchanged fold.
4. **Honest qualification label.** The verdict states explicitly whether any blind evidence
   existed; absent a newly sealed corpus it does not.

## Gates

### G0 — opening, authority, source pins, format census

Commit this protocol; record its SHA-256 and opening commit; hash-pin the candidate archives
(directory manifests); rehash the P24/P25 evidence and all prior verdicts; census which candidate
files the current builder can parse at all — `format_unsupported` counts are a gate input, not a
failure. Confirm the release hold stands and no `v1.1*` tag exists.

### G1 — construction census

Bounded per-target builds for every candidate over the IRDFF-II target set; each failure's
underlying message re-derived and recorded; produce `results/g1_p25b_census.json`. No repair code
before G1 commits.

### G2 — defect census and adjudication

Run the exact-decimal oracle over every quarantined candidate file; classify per the frozen
taxonomy; adjudicate format-coverage questions (does the evaluation declare MF=10 emitted states,
does it use the same isomer conventions). Deliverable: `results/g2_p25b_traces.json` + cause
ledger.

### G3 — repair proposal and acceptance freeze

Publish the projected post-repair coverage per candidate and freeze per-candidate coverage floors
by amendment before any repaired build is scored. Checker green required before repair code.

### G4 — bounded repair, rebuild, retrospective scoring

Apply demonstrated repairs; rebuild; score the public partitions retrospectively under the frozen
gates. One append-only repair amendment makes an otherwise passing closure conditional; a second
repair need fails the phase.

### G5 — independent closure

A checker importing no production, audit or scoring module rehashes all evidence, recomputes census
classification, repair arithmetic and outcome accounting, verifies gate ordering, re-verifies all
prior verdicts verbatim and rejects planted mutations. Verdict to `results/verdict_p25b.json`;
manifest regenerated once at closure.

## Closure interpretation

`P25b-PASS` means at least one alternate evaluation survives construction qualification with
honest outcome accounting and nonregressing comparable-case accuracy — the release decision gains a
candidate that is not TENDL-2025, labeled with the evidence it actually has. It does not authorize
release by itself; the 1.1.0 decision remains the maintainer's and consumes this beside the
`P24-CONDITIONAL` and `P25-FAIL` records. `P25b-FAIL` preserves every artifact, census and ledger
as public evidence and strengthens the case for waiting on a corrected evaluation.
