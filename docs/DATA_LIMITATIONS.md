# Known data limitations — shipped nuclear-data artifacts

This document is the disclosure for the activation data ACTINV 1.1.0 ships
with. It is generated from phase-P25/P25b evidence; every claim below is
backed by a hash-pinned record in `results/` and an independently run
checker. It is a statement about the *data*, not about the ACTINV engine.

## What ships

`actinv data fetch` installs the `data-v1.0.0` release:
`actinv-data/v1.0.0/activation/tendl-2025-{neutron-709g,proton-162g,
deuteron-162g,alpha-162g}.npz` plus per-artifact index files carrying
source SHA-256 digests. All four artifacts derive from **TENDL-2025**
evaluations, hash-pinned at build time.

## The confirmed upstream defect

The P25 defect census found internal contradictions in TENDL-2025
emitted-state records: MF=10 partial cross sections exceeding the file's
own MF=3 totals, and nonzero partials where the declared total is zero.

Upstream (A. Koning) confirmed the root cause: an array in the TALYS
`channelsout.f90` subroutine was not flushed, causing the thermal (n,p)
cross section to be written into the ground-state (n,2n) record. The fix
is committed upstream and lands in the **next TENDL release** — every
evaluation ACTINV can currently ship predates it. Upstream's suggested
manual remediation (zeroing the contaminated leading energy points)
produces a modified evaluation that would require its own qualification
phase; it has not been applied to the shipped artifact.

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

## Remediation path

1. **Corrected TENDL release** — the upstream fix is committed; when it
   ships, the P25 qualification machinery re-runs unchanged on the new
   evaluation. This is the preferred path.
2. **EAF route** — would require builder feature work (state-catalog
   emission for the EAF format) plus bounded adjudication of its
   negative-ELFS convention, and even then isomeric coverage is partial
   and its IRDFF accuracy regresses. Recorded, not recommended as the
   primary path.

## Scope of this disclosure

This covers the activation cross-section libraries. Decay data, group
structures, shielding and damage tables are separate hash-pinned
artifacts with their own provenance. Nothing here amends prior phase
verdicts; `P25-FAIL` and `P25b-FAIL` stand as the public record.
