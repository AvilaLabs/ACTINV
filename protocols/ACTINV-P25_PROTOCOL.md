# ACTINV P25 — state identity, construction coverage and predictive qualification

Opened 2026-09-13 after P22 closed with `P22-PASS` and the maintainer directed that the 1.1.0 release
candidate remain untagged and unpublished pending repair of the two standing failures. P25 succeeds
P18b under a new protocol: the P18b amendment clause authorizes a repair only inside an
otherwise-passing closure, and P18b closed `P18b-FAIL`. Its verdict, evidence and failure record are
preserved byte-for-byte and are never rewritten or amended by this phase.

## Why this phase exists

P18b's held-out record `results/g5_p18b_heldout.json` passed its overall metrics while failing the
frozen per-stratum nonregression gate. An independent read-only review of that record then established
three findings that define the present problem:

1. **The headline improvement is a coverage-selection artifact.** Baseline metrics were computed over
   1,286 scored rows; candidate metrics over 356 rows spanning only 13 reaction families. On those
   paired rows the maximum change in ln(C/M) is 5.7e-7 and the paired bootstrap mean change is
   2.6e-8. The candidate has therefore demonstrated no measurable predictive change on comparable
   cases; every claimed gain lives in rows that did not survive construction.
2. **Zero predictions disappear from the metrics.** Twenty-nine `181Ta(α,n)184Re` rows carry status
   `scored` with candidate ratio exactly zero; `ln(C/M) = -inf` is removed by the scorer's
   `math.isfinite` filter (`controls/g5_p18b_heldout.py`). 385 scored rows minus 29 equals the 356
   metric rows.
3. **A concrete processing defect exists in charged-particle channel handling.** The builder applies
   `inelastic(mt) = (mt == 4 || 51 <= mt <= 91)` without regard to projectile
   (`crates/actinv-data/src/builder.rs`), and the scorer repeats the same assumption. In ENDF-6
   charged-particle sublibraries, MT=4 and MT=51–91 are neutron-emission channels whose residual is a
   different nuclide. This is established by direct source inspection: `a-Ta181.tendl` MF=8/MT=4
   declares residual ZAP=75184 (Re-184, not Ta-181), and `d-Ag104m.tendl` MF=10/MT=103 carries two
   state tables (LFS=0 and LFS=1) while its MF=8 declares NSP=1.

A fourth observation bounds the diagnosis: neutron, the only stratum that passed its numerical gate,
retained only 103 of its baseline's 453 scored rows — coverage loss is not confined to charged
particles.

## Frozen scope

P25 may change:

1. the TENDL builder's channel semantics, state-identity handling and conservation reconciliation —
   only where the diagnosis demonstrates the mechanism;
2. the held-out scorer's outcome accounting — only where the diagnosis demonstrates silent drops;
3. audit, classification and checker tooling; and
4. separately versioned, hash-pinned corrected source extracts, where a defect is proven to be a
   source inconsistency rather than a processing defect.

P25 may not:

- alter decay constants, CRAM numerics, group boundaries, flux normalization, schedule semantics,
  covariance values, or any public v1.0.1/v1.1.0 release-candidate artifact;
- weaken, remove or rewrite any P18/P18b frozen threshold, verdict or evidence file;
- read, re-score or reinterpret P24's future sealed partition;
- silently drop a source file, target, reaction, product, state or scored row;
- introduce `unsafe`, new runtime dependencies, `Arc`, `Mutex`, interior mutability, or
  cloning/allocation as borrow-checker workarounds;
- select which repairs apply by measurement outcome. Repairs apply uniformly by defect class across
  the corpus; the checker verifies this.

## Frozen defect taxonomy

Every outcome that removed a P18b-eligible row from candidate scoring is assigned exactly one class
during diagnosis:

- `processing_bug` — ACTINV code misinterprets a conformant source (candidate: unconditional
  `inelastic(mt)` on charged-particle files).
- `state_catalog_mapping` — evaluated-state catalog or LISO/ELFS identity mapping failure.
- `tiny_absolute_discrepancy` — excess below a named absolute magnitude with no physical weight
  (candidate: the 1.0e-20 barn floor value carried by two MF=10 state tables sums to exactly 2× the
  total at floor-energy groups).
- `genuine_source_inconsistency` — provenance-traced contradiction inside the evaluation itself
  (candidate: MF=8 NSP=1 while MF=10 tabulates two states for MT=103 in `d-Ag104m.tendl`).
- `zero_prediction_scored` — candidate predicts exactly zero for a positive measurement and the
  metric filter drops it.
- `eligibility` — the row's frozen ineligibility reason; carried forward unchanged.

The census covers all 1,859 eligible rows plus the ineligible categories, across all four projectile
corpora. Representative ENDF-field → emitted-inventory-row traces are required per populated class —
at least three per class per affected projectile, independently recomputed at decimal precision.

## Frozen repair rules

Repairs are admitted only for mechanisms the diagnosis demonstrates. Three classes:

- **R1 — correctness repair.** The code implements ENDF semantics incorrectly (candidate: MT=4/51–91
  same-residual treatment on charged-particle files). Proof obligation: an ENDF-field trace showing
  the correct residual identity, plus a before/after emitted-row diff on every traced case. R1
  repairs change absolute production rates and must be disclosed as such.
- **R2 — source reconciliation.** A proven source inconsistency is reconciled inside the frozen
  0.001 standard envelope, preserving within-product state ratios and nonnegativity. A common
  rescaling factor preserves isomer ratios but changes absolute production rates, so conservation
  alone never justifies reconciliation — the defect mechanism must be proven independently. Sources
  whose defects exceed the envelope are not reconciled: they produce a corrected, separately
  versioned source extract or an explicitly limited support claim, and the affected rows are
  ledgered `genuine_source_inconsistency`.
- **R3 — accounting repair.** Scorer/reporting defects (candidate: the `isfinite` zero-prediction
  drop) are corrected so that zero predictions, failed construction and undefined ratios appear as
  named outcomes rather than disappearing.

All repairs are verified by the existing decimal oracle at 80- and 120-digit precision where
arithmetic is involved, and by an independent checker that re-derives classification and
reconciliation without importing production or audit modules.

## Frozen acceptance gates

These gates are fixed at protocol opening and apply to the post-repair candidate. The P18b held-out
population (1,859 eligible rows across 180 families, four projectile strata) is the fixed eligible
population; its measured values are public and the re-score is **retrospective, not blind**.

1. **Complete outcome accounting.** 100% of the fixed eligible population receives exactly one named
   outcome per candidate build: `scored`, `zero_prediction`, `construction_failed:<class>`,
   `undefined_ratio:<class>` or `eligibility:<class>`. No silent drop of any kind.
2. **Comparable-case nonregression.** On rows scored by both the v1.0.1 baseline and the P25
   candidate, each projectile stratum with at least ten paired rows may not regress beyond P18b's
   frozen additive and multiplicative median/p90 limits, nor by more than one percentage point at
   10%, 20% and 30% coverage — scored through the unchanged P18b fold.
3. **Coverage qualification.** Per-projectile scored share of the eligible population, and per-
   family coverage counts, are reported beside row counts. The scored-coverage floor per stratum is
   fixed by Amendment A after the diagnosis census and before any repaired build is scored; rows
   dropped for proven `genuine_source_inconsistency` are excluded from the floor denominator only
   where the independent trace verifies the class.
4. **No empty stratum.** Every projectile stratum must produce at least ten scored rows unless every
   one of its eligible rows carries an independently verified `genuine_source_inconsistency` trace.
5. **Historical reproducibility.** Re-running the unchanged P18b scorer over the P18b candidate
   artifacts reproduces the sealed P18b metrics exactly; both reports are published and neither is
   silently replaced.
6. **Honest qualification label.** If no sufficiently independent unread isomeric measurement set
   exists, the verdict states that qualification rests on engineering evidence and retrospective
   scoring — a fresh blind stratum may be attempted only after construction completeness and family
   coverage prerequisites pass, and only on material sealed before its values are read.

## Frozen evidence and cost rules

The frozen corpus manifest `results/p18b_source_manifest.json.gz` is rehashed at G0; the corpus is
the same 2,850-file-per-projectile TENDL-2025 tree. Large artifacts stay outside Git. Rebuilds are
resumable, one evaluation at a time, projectiles sequential, under the workstation cgroup; jobs
expected to exceed ten minutes are resumable; the 12 GB virtual-memory and ~1 GiB single-array limits
stand. Every failure message of every quarantined file is re-derived and recorded — no reason may be
carried forward as "quarantined in an earlier run" without its underlying error.

## Gates

### G0 — opening, authority and seal

Commit this protocol; record its SHA-256 and the opening commit; rehash the corpus manifest, the P18b
session seals, all prior verdicts (P17-FAIL and P18b-FAIL asserted verbatim), the toolchain and the
P22 release-candidate record. Confirm no `v1.1*` tag exists and the release hold stands.

### G1 — complete cause census

Enumerate every dropped, quarantined, zero-predicted or ineligible outcome across all four corpora
into the frozen taxonomy with exact counts; re-derive every quarantined file's failure message;
produce `results/g1_p25_census.json`. No repair code may be written before G1 is committed.

### G2 — mechanism traces and adjudications

Per populated class, at least three representative ENDF-field → emitted-inventory-row traces per
affected projectile, verified at decimal precision. Adjudicate: (a) the `inelastic` charged-particle
question against ENDF-6 and file evidence; (b) the MF=8-declared-state vs MF=10-tabulated-state
inconsistency class; (c) floor-value versus physical conservation excesses; (d) the neutron-stratum
coverage loss. Deliverable: `results/g2_p25_traces.json` plus a cause ledger.

### G3 — diagnosis report, repair proposal, Amendment A

Publish the census-derived repair proposal: which mechanisms are R1/R2/R3, which files get corrected
source extracts, the projected post-repair coverage per stratum, and an evidence-based effort
estimate. Amendment A freezes the per-stratum coverage floor from the census before any repaired
build is scored. The gate commits with its checker green; only then may repair code be written.

### G4 — bounded repair and rebuild

Implement the demonstrated repairs; rebuild the four corpora resumably; run per-repair proofs and
the decimal oracle; verify repairs apply by defect class only. Deliverable: rebuilt candidate
artifacts plus `results/g4_p25_repairs.json`.

### G5 — retrospective scoring under the frozen gates

Re-run the unchanged P18b scorer for the historical record; run the P25 acceptance gates — complete
outcome accounting, paired nonregression, coverage floors, no empty stratum. Deliverable:
`results/g5_p25_acceptance.json`. No post-hoc tolerance, mapping or metric change is permitted after
this gate runs; one append-only repair amendment makes an otherwise passing closure conditional, and
a second repair need fails the phase.

### G6 — independent closure

A checker importing no production, audit or scoring module rehashes all evidence, recomputes the
census classification and reconciliation arithmetic, verifies gate ordering (census before repairs,
floor before scoring), re-verifies all prior verdicts including P17-FAIL and P18b-FAIL, and rejects
planted mutations. Verdict written to `results/verdict_p25.json`.

## Closure interpretation

`P25-PASS` means the demonstrated mechanisms are repaired, coverage and paired accuracy satisfy the
frozen gates, and the qualification label honestly states whether any blind evidence was available.
It does not by itself authorize release: the 1.1.0 publication decision remains the maintainer's, and
P24 may still be required before tagging. `P25-FAIL` preserves every artifact, census and ledger as
public evidence exactly as P18/P18b did.
