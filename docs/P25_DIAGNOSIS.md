# P25 G3 — Diagnosis report and repair proposal

Status: G3 deliverable under `protocols/ACTINV-P25_PROTOCOL.md`. Draws only on
`results/g1_p25_census.json` (committed), `results/g2_p25_traces.json` (committed),
the sealed P18b ledger `results/g5_p18b_heldout.json`, and decimal-precision source
traces through `controls/p18b_decimal_corpus_oracle.py`. No repair code has been
written.

## 1. What the census established

Every one of the 1,945 sealed ledger rows carries exactly one named outcome.
For the candidate build the eligible-population outcome table is:

| stratum  | eligible | scored | construction-failed | zero-pred | undefined-ratio |
|----------|---------:|-------:|--------------------:|----------:|----------------:|
| neutron  |      469 |    103 |     366 (266 quarantined + 100 catalog-cascade) | 0 | 0 |
| proton   |      695 |    122 |     473 (320 quarantined + 153 catalog-cascade) | 0 | 100 |
| deuteron |      143 |      0 |     143 (142 quarantined + 1 catalog-cascade)   | 0 | 0 |
| alpha    |      552 |    131 |     257 (77 quarantined + 180 catalog-cascade)  | 29 | 135 |

The whole coverage failure is one causal tree rooted in **397 quarantined
evaluations**: 805 rows fail because their own target file failed, and 434
further rows fail because a *catalog-supplying metastable evaluation* (the file
whose MF=1 header would declare the needed `(zap, liso)` state) was itself
quarantined — the `catalog_cascade` record traces every staged-built family to
its supplier file. There is no staging omission: every family file was staged;
the failures are construction-level.

## 2. Final mechanism classification (397 quarantined files)

Assigned by exact-decimal evaluation on the union of each channel's declared
grids (`g2_p25_traces.py`): an excess present at a declared product gridpoint is
a source inconsistency; an excess that vanishes at every declared gridpoint is a
grid-density artifact; an excess at or below 1e-15 barn is physically
weightless.

| class | neutron | proton | deuteron | alpha |
|-------|--------:|-------:|---------:|------:|
| floor-artifact only | 22 | 111 | 56 | 38 |
| missing MF=3 comparator (all MT=18) | 9 | 17 | 17 | 7 |
| grid-density interpolation artifact | 25 | 0 | 0 | 0 |
| genuine source: zero-total partials | 40 | 1 | 2 | 2 |
| genuine source: declared-gridpoint excess | 45 | 4 | 1 | 0 |

(Mechanism classes overlap inside files — first-hit vs all-excesses views are
both in the record. Separately, 14 files hit `state_catalog_conflict` first —
5n/5p/1d/3a — the ELFS↔QM−QI identity-chain failure; their final classes are
9 floor, 1 missing-total, 2 interpolation, 2 genuine-source.)

### 2.1 Floor artifacts — the dominant charged-particle cause (227 files)

TENDL prints 1.0e-20 barn as an "effectively zero" floor. Where a channel
carries N co-equal floor states and the MF=3 total carries one floor, the
emitted sum exceeds the runtime total by N× — e.g. `d-Ag104m` MT=103 group 20:
emitted 8.66e-22 vs total 4.33e-22. Absolute excesses are ~1e-20 barn —
fourteen orders of magnitude below any measurable cross section. Every
charged-particle stratum's quarantine set is ~74–83% this class alone.

### 2.2 Missing MF=3 comparator — exclusively MT=18 fission (50 files)

Files carry MF=10 partial-state fission yields for MT=18 with neither an
MF=3/MT=18 total nor the permitted IZAP=−1 sentinel. The builder's audit fails
closed ("conservation is unproven") although the MF=10 sum is the only data the
channel offers — the emitted total would be the sum of its own partials.

### 2.3 Grid-density interpolation artifacts (25 files, neutron only)

MF=10 partials agree with the MF=3 total at every declared ordinate (~5e-7
rounding) but lin-lin across coarse intervals overshoots the total's dense
1/v-shaped curve between gridpoints — the ~0.9% group-0 cluster, e.g.
`n-Ba132` MT=107 thermal region (18× pointwise between gridpoints; 0.9% after
group collapse). The source is self-consistent at its declared data; the excess
is a representation artifact of unequal grids under linear interpolation.

### 2.4 Genuine source inconsistencies (95 files, neutron-heavy)

Excesses at declared gridpoints or positive partials where the total is
exactly zero — including `n-Fe053m` MT=16's stray 1.0758e5-barn ordinate at
7.79 MeV (MF=3 total: 0.0; the rest of the two tables agree point-for-point)
and `n-Eu151` MT=107's 18.7% excess at a declared gridpoint. These are TENDL
defects; no ratio-preserving reconciliation is justified.

### 2.5 ELFS↔QM−QI catalog conflicts (15 files, first-hit)

MF=8 ELFS disagrees with the Q-value-implied excitation by ~2 eV (e.g.
`n-Ag109m` LFS=1: ELFS 93 118 eV vs QM−QI 93 120 eV). A source-level identity
ambiguity in the state chain.

## 3. Adjudications required by the protocol

**(a) The `inelastic()` charged-particle question — adjudicated: an ACTINV
processing bug.** Corpus-wide MF=8 evidence: every one of the 1,081 neutron
files declaring MT=4 names the target residual (correct inelastic semantics);
every one of the 2,156 charged-particle files declaring an inelastic-classified
MT names a *different* nuclide (proton 687, deuteron 757, alpha 712; zero
counterexamples anywhere). Representative: `a-Ta181` MF=8/MT=4 declares
ZAP=75184 = Re-184, the (α,n) channel residual. The builder's excitation path
drops LFS=0 production — the dominant Re-184g channel is discarded — and the
scorer's `INELASTIC_MTS` branch then credits `sg = total − feed − leak` to the
wrong nuclide. The 29 zero-prediction rows and the 38 `181Ta(α,n)184Re`
zero-denominator rows share this mechanism.

**(b) MF=8-declared vs MF=10-tabulated states — adjudicated: zero mismatches.**
All 397 files declare every tabulated state; the frozen protocol's candidate
example was itself corrected by Amendment A (the apparent NSP=1 conflict was a
field-offset misread).

**(c) Floor vs physical excesses — adjudicated by the discriminator above.**

**(d) Neutron-stratum coverage loss — adjudicated: real, and mixed.** 266 rows
die to quarantines (74 of them to genuine source defects) and 100 to the
catalog cascade whose neutron suppliers are all genuine-source files.

## 4. Repair proposal

| # | mechanism | repair class | action |
|---|-----------|--------------|--------|
| R1a | `inelastic()` projectile-blind | **R1 correctness** | `inelastic(mt, projectile)`; for charged projectiles MT=4/51–91 route through the normal product path (keep LFS=0, per-ZAP reconciliation vs MF=3 total). Scorer's `INELASTIC_MTS` branch gets the same projectile-awareness. Disclosed as changing absolute production. |
| R1b | floor artifacts | **R2 policy reconciliation** | Where the runtime total and every emitted state sit at or below the TENDL 1e-20-barn floor in a group, treat the group as conserving-zero; ledger `floor_reconciled`. Justified by proven weightlessness, not by conservation convenience. |
| R1c | MT=18 without MF=3/sentinel | **R2 policy** | Emit the channel total as the sum of its MF=10 partials with a named `missing_total_self_comparator` diagnostic. No invented values, no rescaling. |
| R1d | grid-density artifacts | **R2 reconciliation** | Apply the frozen T/S envelope *extended by Amendment B to a proven-mechanism secondary envelope* — applicable only where the discriminator verifies the excess vanishes at every declared product gridpoint. Bound set from the census: observed max relative excess 2.3e-2 at declared-curve level; propose envelope 0.03 for this class only. |
| R1e | ELFS↔QM−QI ≤ 1 keV | **R2 reconciliation** | Declared precedence: MF=8 ELFS is the evaluated excitation; conflict ledgered as `elfs_qm_qi_conflict_resolved`. Larger deltas stay quarantined. |
| — | genuine source inconsistencies | none | Stay quarantined; ledgered `genuine_source_inconsistency`. Corrected source extracts are NOT proposed — no misprint is provable enough to invent an ordinate. |

The catalog cascade needs no separate repair: once a supplier file's own class
is repaired, the catalog gains the state and the dependent rows score.

## 5. Projected post-repair coverage (per fixed eligible population)

If every repairable-class file builds and its rows score — an upper bound
pending G4 verification; Amendment B freezes floors *below* this bound:

| stratum | scored slots | +floor | +missing | +interp | +cascade | +undefined-ratio upside | ceiling | genuine-only rows |
|---------|-------------:|-------:|---------:|--------:|---------:|---------------:|--------:|------------------:|
| neutron | 103 | +39 | +54 | +99 | +0 (suppliers genuine) | 0 | 295/469 (63%) | 174 |
| proton | 122 | +237 | +25 | 0 | +153 | some of 100 undefined | 537/695 (77%) | 58 |
| deuteron | 0 | +74 | +11 | 0 | +1 | — | 86/143 (60%) | 57 |
| alpha | 160 † | +70 | +7 | 0 | +180 | some of 135 undefined | 410/552 (74%) | 7 |

† The 29 alpha `zero_prediction` rows occupy scored slots with cm=0 — the
inelastic fix converts them into real values (an accuracy gain, not a coverage
gain). The 135 alpha and 100 proton `undefined_ratio` rows are not counted in
any ceiling; the 38 `181Ta(α,n)184Re` M/G rows among them share the inelastic
mechanism and may convert — upside, not committed.

Excluding independently traced `genuine_source_inconsistency` rows from the
denominator, the repairable-population ceilings are 295/295, 537/637, 86/86,
410/545.

## 6. Coverage floor proposal (for Amendment B)

Floors must commit below the ceiling to absorb rows that still land on
non-scored outcomes. Proposed scored-share floors of the full eligible
population:

- neutron ≥ 40% (≥ 188 rows; repairs projected to 63%)
- proton ≥ 60% (≥ 417 rows; projected 77%)
- deuteron ≥ 50% (≥ 72 rows; projected 60%)
- alpha ≥ 55% (≥ 304 rows; projected 74%)

plus the protocol's no-empty-stratum gate and the paired-nonregression gate on
whatever rows score.

## 7. Effort estimate (evidence-based)

- R1a inelastic repair (builder + scorer + oracle proofs): the largest piece —
  the emitted-row model changes for ~2,156 files.
- R1b/R1c: small bounded changes to `reconcile_emitted_states` and the no-MF3
  path, each with decimal-oracle proofs.
- R1d: envelope plumbing plus the mechanism-gate predicate.
- R1e: precedence rule + ledger.
- Rebuilds: four corpora, resumable single-file builds (observed ~17 s/file,
  ~2 h per full quarantine-set pass; built files are cheap).
- G5 retrospective scoring + G6 checker: established pattern.

## 8. Risks / honesty notes

- The P18b holdout is spent for blind purposes; G5 is retrospective and will be
  labeled as such. No fresh blind stratum exists inside the sealed corpus.
- The 58 deuteron genuine-source rows and 174 neutron rows stay failed; the
  public claim must say charged-particle coverage was restored *where sources
  are internally consistent*, not universally.
- `zero_denominator` rows may convert to scored after R1a — counted as upside,
  not assumed in the floors.
