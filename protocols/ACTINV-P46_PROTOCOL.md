# ACTINV-P46 protocol — evaluation intelligence

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P46 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** machinery-independent; sequenced
after P44 for shared scoring code. Scoring lineage: CB3 FNS scorer
(`controls/cb3_fns.py`), P24/P25 IRDFF scorer.

Frozen corpora, partitions, scorer identity and the report format are
placeholders to be fixed at the freeze; this document authorizes no
execution.

## Intent

Productize what the defect censuses already proved: ACTINV can say
which evaluation is best for a target, on evidence. Build every lawful
corpus through the identical pipeline, score each on the frozen
measurement partitions, and ship a per-target/per-family comparison
surface whose every recommendation carries its evidence row. This
converts the TENDL-2025 defect record from a liability disclosure into
a selection capability FISPACT's take-it-or-leave-it condensed
libraries do not offer, and produces the decision evidence for whether
an EAF state-catalog phase or the corrected upstream TENDL release
earns a later phase.

## Scope

- Corpora (each hash-pinned at G0, built by the *same* builder version):
  `tendl-2025-patched`, `tendl-2025` legacy, `tendl-2017`, `eaf-2010`,
  `fendl-3.2c` — neutron arms only; a corpus is admitted only where its
  source terms permit building and scoring (all listed are public).
- Frozen measurement partitions: the FNS 132-experiment suite and the
  IRDFF-II SACS arm, consumed diagnostic evidence — the same honesty
  boundary as P44 applies: partition, scorer and metrics freeze before
  *these* scores are computed; the underlying measurements are not
  unread and the report says so.
- Per-corpus accounting: eligible targets, built targets, excluded
  source files, construction failures and unscorable arms all counted
  per corpus — never filtered. A target absent from a corpus is
  `uncovered`, not a zero score.
- Expressibility labels: a corpus that cannot express a channel
  (EAF-2010 emits no `state_catalog`; FENDL-3.2c lacks the isomer
  anchors per P25b) is labeled per affected family, not silently
  scored low on a channel it cannot represent.
- Report surface: machine-readable per-target and per-family score
  tables plus a documented recommendation view (CLI output and a
  `docs/` page) in which every recommendation row carries the evidence
  record it stands on — corpus identity, scorer identity, partition,
  family, value. A recommendation without an evidence row is refused
  at construction.

## Out of scope

- Charged-projectile corpora (no comparable measurement partition
  exists), new measurement families, band coverage (P44's metric, not
  C/E), and corpus redistribution (scores are derived artifacts; the
  data rules are unchanged — corpora are never committed).
- Re-scoring consumed P17/P18b/P25 partitions for new conclusions;
  those stay historical evidence.

## Gates (draft)

- **G0** — seal: protocol hash, opening commit, corpus source
  identities, builder identity, scorer code hash and partition
  assignment bound by an independent control before any score exists.
- **G1** — construction: every corpus built through the identical
  pipeline; per-corpus eligible/built/excluded/failed accounting
  complete; expressibility labels assigned with named reasons.
- **G2** — controls: scorer arithmetic re-derived independently on a
  frozen subset; a planted corpus-content difference is detected;
  denominator accounting control (planted excluded target shifts the
  reported eligible count, not the score).
- **G3** — scoring and surface: all admitted corpora scored once
  through the sealed scorer; tables and recommendation view generated;
  a recommendation constructed without its evidence row fails.
- **G4** — independent closure: checker re-derives every table cell
  from raw records, verifies per-corpus accounting and expressibility
  labels, rejects planted mutations, emits the verdict.

## Closure rule (draft)

PASS only if every admitted corpus is scored with complete accounting
and the checker re-derives the published tables. CONDITIONAL if an
amendment was used or an admitted corpus drops out (recorded as
`unmeasured`, not silently removed). FAIL otherwise. Per-corpus wins
and losses publish as measured; no composite ranking hides a coverage
difference.
