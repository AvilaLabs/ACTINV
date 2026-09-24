# ACTINV-P46 protocol — evaluation intelligence

**Status:** FROZEN 2026-09-24 for G0 seal | **Parent:** roadmap draft
innovation extension P46 (`docs/ROADMAP.md`) | **Depends on:** P44
lineage (frozen alignment/scoring conventions, FNS corpus partition).

This document freezes the corpus set, provenance, partition, scoring
rules, accounting rules, report surface and verdict dispositions. Any
change after the freeze is an amendment: the protocol text is updated,
a new sha-256 is taken, the seal re-binds, and the verdict records
that an amendment was used.

## Intent

Productize what the defect censuses already proved: ACTINV can say
which activation cross-section evaluation is best for a target, on
evidence. Score every lawful corpus through the identical
spec→solve→C/E pipeline on the frozen measurement partition, and ship
a per-target/per-family comparison surface whose every recommendation
carries its evidence row. The TENDL-2025 defect record converts from a
liability disclosure into a selection capability; the same scoring
infrastructure produces the decision evidence for whether an EAF
state-catalog phase or the corrected upstream TENDL release earns a
later phase.

## Corpora (five, sha-pinned at G0)

| corpus | artifact | targets | rows | builder fingerprint | provenance label |
|--------|----------|---------|------|--------------------|------------------|
| `tendl-2025` | `~/nuclear-data/tendl-2025/builds/full/neutron.n.p10.npz` | 2850 | 167735 | `7a50ba34…` (index-1) | `legacy_builder` |
| `tendl-2025-patched` | `actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz` | 1679 | 87075 | `38a5eb5c…` (index-2) | `current_builder` |
| `tendl-2017` | `~/nuclear-data/tendl-2017/build/neutron.n.p10.npz` | 1072 | 64044 | `38a5eb5c…` (index-2) | `current_builder` |
| `eaf-2010` | `~/nuclear-data/eaf-2010/actinv_eaf2010_709g.npz` | 816 | 115831 | none (pre-fingerprint) | `legacy_builder` |
| `fendl-3.2c` | `~/nuclear-data/p26b-work/g1-run/actinv_fendl32c_709.npz` | 24 | 481 | converted (P26b) | `converted_subset` |

Draft-scope amendment recorded at freeze: the draft required all
corpora "built by the same builder version". The admitted artifacts
are existing lawful NPZ builds with *declared per-corpus builder
fingerprints*; the identical-pipeline requirement is enforced where
it has scientific force — spec generation, solve, alignment and
scoring are literally the same code for every corpus. No corpus
receives bespoke handling. Rebuilding all five corpora under one
builder revision is a named carry, not performed silently.

## Frozen measurement partition

**FNS decay-heat arm (scored):** the 132-experiment CoNDERC FNS suite
(`~/nuclear-data/conderc-fns/fns`, manifest sha bound at G0), same
consumption rules as P44 — the measurements are consumed C/E evidence
(read before this protocol existed); partition, scorer and metrics
freeze before these scores are computed, and the report says so.

Each experiment: ACTINV nominal `decay_heat_w_per_g` per cooling step
vs the `.exp` measured heat (µW/g → W/g). Alignment uses the frozen
P44 `align()` — unit inference over {s, min, h, d, y}, nearest cooling
step within max(2%, 1 s), nonpositive/zero measured rows named
`nonpositive_time`/`nonpositive_measured`. Decay data is held fixed
across corpora (ENDF/B-VIII.0 primary + JEFF-3.3 fallback): the
comparison isolates the activation cross-section corpus.

**IRDFF-II SACS arm (declared `unmeasured`):** the P17 folded-XS
scoring unit (reaction-rate C/E per dosimetry reaction per benchmark
field) requires per-corpus group-structure folding machinery that is
not executable within this phase — the raw archives and P17 lineage
exist but the adaptation is a dedicated build. Recorded as
`unmeasured` at freeze, parked, not silently dropped.

## Per-point outcomes (frozen)

For each `(corpus, experiment, measured_point)`:

- `scored` — computed heat > 0 → `C/E = computed / measured`.
- `uncovered` — the corpus carries no target for the experiment's
  material composition (nominal solve produced no populated products
  for the dominant production channel, or the case failed for a named
  target-coverage reason). Counted against corpus coverage, never
  scored zero.
- `failed` — the solve failed (executor error). Counted per corpus.
- `excluded` — the measured point was excluded by alignment rules
  (`nonpositive_time`, `nonpositive_measured`, `unmappable`).

Per-corpus accounting: eligible experiments (all 132), executed,
uncovered, failed, excluded — never filtered.

## Score tables (frozen definitions)

Per `(corpus, material)`: `n_measured`, `n_scored`, `n_uncovered`,
`n_failed`, median C/E over scored points, mean |ln C/E|, fraction
within ±20% (0.8 ≤ C/E ≤ 1.25) and within 2×.

Per `(corpus, family)`: pooled aggregates over the family partition:

- `pure_element` — every material that is a single element (67
  materials, composition = one element ≥ 99 wt%).
- `alloy_composition` — multi-element mixtures (SS304, SS316, NiCr,
  Inc600 — 4 materials).

Family membership is a function of the experiment's parsed
composition (one dominant element → pure_element), not a hand-list.

## Expressibility labels (frozen)

- `fendl-3.2c` → `converted_subset` everywhere: 24-target converted
  NPZ (P26b); most materials are `uncovered` by construction, so its
  score carries the label and uncovered counts, never a low score.
- `eaf-2010` → `no_state_catalog`, `legacy_builder`, 816-target
  partial coverage.
- `tendl-2017` → `legacy_evaluation` (older evaluation baseline).
- `tendl-2025-patched` → `covariance_subset` (the shipped library's
  working set; P44-measured isotope gaps are a named known condition).
- `tendl-2025` → `full_evaluation` (complete unpatched set).

## Recommendation surface (frozen)

Machine-readable `results/p46_eval_tables.json` (per-target and
per-family score tables + accounting) and a `docs/` recommendation
page. The recommendation rule is frozen: for each material with ≥ 3
scored points on ≥ 2 corpora, the recommended corpus minimizes
median |ln C/E|; ties (within 1e-6) list all tied corpora. Every
recommendation row carries its evidence row — corpus identity, scorer
sha, partition identity, n_scored, median C/E, and the per-point
ledger sha it aggregates. A recommendation constructed without a
complete evidence row is refused at construction (the report builder
raises).

## Gates

- **G0** — seal: protocol sha, opening commit, corpus shas + builder
  fingerprints, FNS manifest sha, decay data shas, scorer/driver code
  shas, frozen constants. Before any score exists.
- **G1** — mechanics: the P44 frozen development subset (10
  experiments, sealed at `g0_p44_seals.json`) scored through the
  sealed pipeline on all 5 corpora; per-corpus accounting complete;
  expressibility labels assigned.
- **G2** — controls: synthetic-corpus scoring-control fixtures
  (planted C/E set re-derives every table cell; a planted
  corpus-content change shifts eligible counts, not scores; a
  recommendation without evidence is refused; alignment controls).
- **G3** — conformance + sealed scoring: seal/refusal probes; all 5
  corpora × 132 experiments scored once through sealed code inside
  the envelope.
- **G4** — verdict + independent checker: checker re-derives every
  table cell from raw point records, verifies accounting and labels,
  rejects planted mutations, emits the verdict.

## Envelope

Sealed scoring ≤ 90 min wall for all corpora combined under the
bounded cgroup (`MemoryMax=6G`, `CPUQuota=200%`). Per-solve ~1–3 s.

## Verdict dispositions

- **P46-PASS** — every admitted corpus scored with complete
  accounting; checker re-derives the published tables; all
  recommendations carry evidence rows.
- **P46-CONDITIONAL** — an amendment was used, or a declared arm
  (IRDFF) is unmeasured, or a corpus drops out recorded as such.
- **P46-FAIL** — otherwise.
