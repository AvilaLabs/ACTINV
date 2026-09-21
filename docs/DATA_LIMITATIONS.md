# Known data limitations — shipped nuclear-data artifacts

This document is the disclosure for the activation data ACTINV 1.2.0
ships with. It is generated from phase-P25/P25b/P25c evidence; every
claim below is backed by a hash-pinned record in `results/` and an
independently run checker. It is a statement about the *data*, not
about the ACTINV engine.

## What ships

`actinv data fetch` installs the `data-v1.1.0` release. The default
bundle is `tendl-2025-patched-neutron`:
`actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz`
plus its index, with proton-162g, deuteron-162g and alpha-162g
alternatives carried unchanged from `data-v1.0.0`. Every artifact
index carries per-target source SHA-256 digests. The patched neutron
artifact derives from the **`tendl-2025-patched` corpus** described
below — a TENDL-2025 derivative, not an official TENDL release. The
unpatched v1.0.0 artifacts remain fetchable for byte-compatible
reproduction of earlier work.

## The confirmed upstream defect

The P25 defect census found internal contradictions in TENDL-2025
emitted-state records: MF=10 partial cross sections exceeding the file's
own MF=3 totals, and nonzero partials where the declared total is zero.

Upstream (A. Koning) confirmed the root cause: an array in the TALYS
`channelsout.f90` subroutine was not flushed, causing the thermal (n,p)
cross section to be written into the ground-state (n,2n) record. The fix
is committed upstream and lands in the **next TENDL release** — every
evaluation ACTINV can currently ship predates it. Upstream's suggested
manual remediation (zeroing the contaminated leading energy points) has
been applied to a **separately labeled derived corpus** under phase P25c
— never to the shipped artifact. See "Derived corpus `tendl-2025-patched`"
below for its exact scope and remaining limitations.

Full evidence: `docs/P25_TENDL2025_DEFECT_REPORT.md` (with the standalone
reproducer submitted upstream) and `results/verdict_p25.json`.

## Which shipped channels are affected

The shipped artifacts were built before the strict state validation in
1.1.0 existed, so evaluations carrying this defect were absorbed rather
than rejected. For the neutron library, **86 evaluations** carry
non-tiny defect classes (81 conservation-excess, 5 state-catalog
conflict; `results/g1_p25_census.json`).

Of the 49 IRDFF-II isotopic benchmark targets, **17** derive from
defect-bearing sources, including **5 of the 6 dosimetry-critical
nuclides**:

| affected IRDFF-II targets | dosimetry-critical |
|---|---|
| Sc-45, Ti-47, **Ni-58**, Cu-63, **Nb-93**, Mo-92, **Ag-109**, **In-113**, I-127, La-139, Pr-141, Tm-169, Ta-181, **Au-197**, Hg-199, Bi-209, Np-237 | Ni-58, Nb-93, Ag-109, In-113, Au-197 |

For these targets the shipped artifact produces predictions from
evaluations whose isomeric/emitted-state channels are internally
inconsistent. Where the contradiction lives at sub-threshold or
near-zero energies the practical impact may be small — but it is not
zero, and ACTINV does not silently paper over it: rebuilding any of
these files under the 1.1.0 builder **fails closed** with a named
defect class rather than emitting data.

## What ACTINV 1.1.0 does about it

- `actinv build-library` now validates emitted-state conservation,
  MF=8 excitation-energy identity and product-state mappings at build
  time; files that fail are rejected with the mechanism named in the
  build ledger (`--strict-states` tightens this further). Libraries
  rebuilt under 1.1.0 also emit a `state_catalog` index section
  recording which product states the artifact can express — the
  shipped v1.0.0 artifacts predate this machinery.
- Predictions involving the 17 listed targets — and isomeric production
  into the affected product states — should be treated as
  **known-uncertain** and checked against the defect report before use
  in dosimetry or safety-relevant work.

## Alternatives evaluated and rejected (phase P25b)

Three on-disk alternate evaluations were hash-pinned and qualified
against the same machinery (`results/verdict_p25b.json`):

| candidate | construction vs frozen floor (1.1.0 builder) | accuracy vs baseline | verdict |
|---|---|---|---|
| TENDL-2023 | 34/47 IRDFF targets (floor 41) | passes IRDFF partition; **fails isomeric** (median \|ln C/E\| 0.250 vs 0.162, 253 paired rows) | not qualified — carries the same TALYS defect class |
| EAF-2010 | 37/47 (floor 47) | fails IRDFF (0.156 vs 0.055, 24 rows) | not qualified — also cannot express isomeric identity (no `state_catalog` for EAF format; negative MF=8 ELFS) |
| FENDL-3.2c | 31/47 (floor 34) | fails IRDFF (0.096 vs 0.040, 16 rows) | not qualified — incomplete corpus, no isomer anchors |

## Derived corpus `tendl-2025-patched` (phase P25c, `P25c-PASS`)

Phase P25c produced a derived neutron corpus under
`~/nuclear-data/tendl-2025-patched/` — an Avila Labs remediation of
TENDL-2025, **not an official TENDL release**. Its exact scope:

- **Patched:** the 28 confirmed-signature files only; exactly the 44
  enumerated leaked leading ordinates zeroed, nothing else moved
  (independent replay verified every edit and all 2,822 other files
  byte-identical). Provenance: `results/g1_p25c_signature.json`,
  `results/g2_p25c_patch.json`, `results/g2_p25c_patched_manifest.sha256`.
- **What it recovers:** three union benchmark targets (Sc-45, Y-88,
  Br-80m) now build under the 1.1.0 builder; union coverage 43→45/77,
  IRDFF 29→30/47, anchors 41→42/62; zero regressions
  (`results/g3_p25c_census.json`).
- **What it does not recover:** the five dosimetry-critical targets
  (Ni-58, Nb-93, Ag-109, In-113, Au-197) still fail closed — their
  blockers are *non-signature* defect classes (e.g. Ag-109's MT=107
  conservation-excess residuals, Ni-58's MT=103 self-channel excess),
  which the patch deliberately does not touch.
- **Still ledgered, unpatched:** 46 non-signature conservation-excess
  files and 7 self-channel-only files.
- **Qualification:** the union+anchor artifact (87 targets,
  `state_catalog` liso {0,1}) passes all frozen coverage and
  comparable-case nonregression gates on both partitions
  (`results/verdict_p25c.json`). All scoring is retrospective; no blind
  evidence exists.
- **Release status:** P25c-PASS qualified the corpus as a candidate for
  a separately labeled data release, and the maintainer directed
  publication. It ships as `data-v1.1.0` under the
  `tendl-2025-patched-*` artifact IDs, labeled as an Avila Labs
  remediation derivative — never as an official TENDL release. The
  unpatched `data-v1.0.0` artifacts remain unchanged and fetchable under
  their original IDs for byte-compatible reproduction.
- **Carried repairs:** the sealed corpus equals the official archive
  outside the 44 ordinates, so it retains the official Pb-208 `NaN`
  fields the strict builder fails closed on. The released artifact
  instead builds the P10-pinned working-corpus `n-Pb208.tendl` — the
  exact bytes the v1.0.0 artifact used — so the shipped source set
  equals the working corpus plus the P25c patch. This is recorded in
  `results/p25c_release_build.json` (`carried_repairs`).
- **Full-corpus exclusions:** the release build applies the frozen
  1.1.0 strict construction to every corpus file and evicts each
  failure with its named defect class in the ledger — **1,172 ledgered
  exclusions** (one, the raw Pb-208 `NaN` file, superseded by the
  carried P10 repair described above), leaving **1,679 targets /
  87,075 rows** in the shipped artifact. Files excluded this way — a
  superset of the flagged subsets above, since the earlier censuses
  covered flagged files only — are absent from the shipped artifact.
  Of the 17 defect-bearing IRDFF-II targets listed above, only the
  patch-recovered Sc-45 remains; the other 16 fail closed and are
  absent. Counts: `results/p25c_release_build.json`,
  `results/check_p25c_release.json`.

## Known upstream defect: TENDL-2017 In-115 capture bump

The TENDL-2017 evaluation of In-115 contains a spurious capture
cross-section rise to ~5.4 b at 8.7–22 MeV on its MF=3/MT=102 rows
(physically impossible — capture above 10 MeV is mb-scale and below the
nonelastic sum). TENDL-2025 evaluates the same channel at mb-scale, so
the defect is confirmed upstream-fixed. It is exercised by the FNS
activation benchmark (In foil overprediction geoCE 29.19 on the
identical-library arm, shared by the frozen FISPACT reference).

An Avila Labs remediation of the **benchmark arm** exists:
`controls/patch_tendl2017_in115.py` freezes the four defective rows
(aggregate + three emitted states) above the bump boundary, producing
`~/nuclear-data/tendl-2017/build/neutron.n.p10.infix.npz` (+ index),
bitwise-reproducible. Scored 132-experiment rerun: In geoCE 29.19→1.95,
pooled geoCE 1.0605→1.0353 (`results/cb3_fns_tendl2017_infix.json`).

**Release decision:** the patched TENDL-2017 is a benchmark-internal
evidence artifact and is *not* shipped as a data release — TENDL-2025
already carries the correct evaluation, so the shipped default bundle is
unaffected, and publishing a remediated copy of a superseded library
would only invite confusion. The upstream report sits with the TENDL
team alongside the P41 emitted-state report.

## Remediation path

1. **Corrected TENDL release** — the upstream fix is committed; when it
   ships, the P25 qualification machinery re-runs unchanged on the new
   evaluation. This remains the preferred path.
2. **Derived corpus release** — `tendl-2025-patched` is a qualified
   candidate (P25c-PASS) for a clearly labeled derived data artifact,
   carrying the defect scope above. Bounded, documented, and fully in
   our control.
3. **EAF route** — would require builder feature work (state-catalog
   emission for the EAF format) plus bounded adjudication of its
   negative-ELFS convention, and even then isomeric coverage is partial
   and its IRDFF accuracy regresses. Recorded, not recommended as the
   primary path.

## Scope of this disclosure

This covers the activation cross-section libraries. Decay data, group
structures, shielding and damage tables are separate hash-pinned
artifacts with their own provenance. Nothing here amends prior phase
verdicts; `P25-FAIL` and `P25b-FAIL` stand as the public record.
