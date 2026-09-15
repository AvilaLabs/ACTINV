# ACTINV P25c — bounded source remediation and re-qualification of TENDL-2025

Opened 2026-09-14 after P25 closed `P25-FAIL`, P25b closed `P25b-FAIL` (no alternate
evaluation qualified), and ACTINV 1.1.0 shipped with `docs/DATA_LIMITATIONS.md`
disclosing the shipped `data-v1.0.0` defect surface.

Upstream has confirmed the dominant defect mechanism: TALYS writes the thermal
(n,p) cross section into the ground-state record of another channel because an
array in `channelsout.f90` is not properly flushed. The upstream author's stated
interim remediation is to *put the cross section of the first energy points to
zero*. Local inspection confirms the mechanism at field precision: in
`n-Ag104m.tendl`, the first tabulated cross section of the MF=10/MT=16 record is
`3.856002+2` barn — byte-identical to the file's own MF=3/MT=103 first-point
value `3.856002+2` at 1e-5 eV.

P25c applies that remediation *surgically and auditable*, only to records that
match the confirmed signature, producing a derived corpus
`tendl-2025-patched` with its own hash identity, then re-qualifies it under the
same frozen machinery. Its verdict informs — never makes — the data-release
decision.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`,
`P18-FAIL`, `P18b-FAIL`, `P24-CONDITIONAL`, `P25-FAIL`, `P25b-FAIL`.

## Why this phase exists

- The shipped catalog is known-defective and the upstream fix has no committed
  release date; the project's data posture is hostage to TENDL's calendar.
- No alternate evaluation qualified (P25b-FAIL): TENDL-2023 carries the same
  defect class, EAF-2010 cannot express isomeric identity under the current
  builder, FENDL-3.2c is coverage-incomplete, and none survives comparable-case
  nonregression.
- The confirmed defect has a precise, enumerable signature — a leaked
  first-point value equal to the file's own thermal (n,p) cross section — and a
  bounded, upstream-endorsed remediation. A surgical patch converts a
  known-defective corpus into a remediated, re-measured corpus whose remaining
  defect surface is re-censused honestly.

## What the patch is — and is not

The patch **is**: for each enumerated record carrying the confirmed signature,
set the leaked leading cross-section value(s) to zero. Nothing else in any file
changes. The derived corpus is byte-identical to the pinned source except at
the enumerated (file, MF, MT, record, ordinate) cells, which a checker replays
from source bytes.

The patch **is not**:

- a claim that the residual record is physically correct — it removes the
  confirmed contamination; the remaining shape is the evaluation's own;
- a repair of any other defect class — non-signature files remain quarantined
  and keep failing closed;
- a TENDL release — the derived corpus is an Avila Labs remediation derivative
  and is labeled as such wherever it appears;
- a redistribution commitment — license terms for a derived TENDL corpus are
  verified and recorded at G0 before any publication decision.

## Frozen scope

P25c may:

- add controls, census/trace tooling, a deterministic patcher, machine-readable
  evidence, checkers and documentation under `controls/`, `results/`,
  `protocols/` and `docs/`;
- write a derived source corpus under `~/nuclear-data/tendl-2025-patched/` (or
  the G0-pinned path), with a per-file old→new SHA-256 manifest and a
  machine-readable patch log;
- build bounded candidate artifacts from the patched corpus under the shipping
  1.1.0 builder;
- score the public P24/P17 partitions retrospectively, labeled as such.

P25c may not:

- edit `crates/`, Python bindings, schemas, production parsers, public examples
  or release artifacts — the patcher is a control, not production code;
- modify, move or re-hash the pinned `~/nuclear-data/tendl-2025/` source — it is
  read-only input;
- patch any record absent the confirmed signature, or patch by measurement
  outcome — a second defect mechanism would require its own phase/amendment;
- present any retrospective partition result as blind validation;
- silently drop a source file, record, product, state or scored row;
- claim upstream endorsement of the derived corpus;
- tag, release or publish any package or data artifact.

## Frozen signature definition

A record carries the **confirmed leak signature** when all of the following
hold, evaluated under the exact-decimal oracle:

1. the file appears in the P25 G1 census with class
   `conservation_excess_untraced` (the sealed 81-file neutron population);
2. the file's MF=3/MT=103 section exists and has a positive first-point cross
   section (the thermal (n,p) reference value);
3. an MF=9 or MF=10 record in that file has a leading tabulated cross section
   exactly equal (as parsed decimals) to that reference value;
4. the record's channel is not MT=103 itself unless the duplicated first point
   is inconsistent with the section's own thermal origin (enumerated, not
   assumed).

Each signature hit records (file, MF, MT, record index, ordinate count,
reference value). The leading contaminated run — one or more consecutive first
ordinates equal to the reference — is enumerated per record at G1; the patch
zeroes exactly the enumerated ordinates.

## Frozen acceptance gates

The eligible population is the sealed P25 81-file `conservation_excess_untraced`
neutron set, plus the union scoring population used by P25b G4 (47 IRDFF-II
targets + 37 isomeric-row targets + enumerated product-state anchors) for the
re-qualification legs.

1. **Surgical integrity.** The patched corpus differs from the pinned source
   only at enumerated cells; an independent checker replays every patch from
   source bytes and verifies the rest of each file byte-for-byte.
2. **Post-patch census.** Re-running the decimal-oracle conservation scan over
   the patched corpus resolves the enumerated signature violations and
   introduces zero new violations; remaining defect classes are re-ledgered,
   not silently cleared.
3. **Coverage.** Patched-corpus construction coverage over the IRDFF-II and
   union target sets is published beside both the unpatched TENDL-2025 counts
   and the P25b alternates; per-candidate floors are frozen by amendment at G3
   before repaired builds are scored.
4. **Comparable-case nonregression.** On rows scored against both the shipped
   baseline and the patched candidate, the frozen additive/multiplicative
   median/p90 limits and coverage points apply through the unchanged fold.
5. **Honest qualification label.** The verdict states explicitly that all
   scoring is retrospective and no blind evidence exists.

## Gates

### G0 — opening, authority, source pins, license check

Commit this protocol; record its SHA-256 and opening commit; hash-pin the
TENDL-2025 neutron corpus manifest and the 81-file sealed population; rehash
the P24/P25/P25b evidence and all prior verdicts; record the TENDL license
terms applicable to a derived corpus (read from upstream documentation, cited);
confirm no `v1.1*` data tag exists and the shipped-catalog identity is
unchanged.

### G1 — signature census

Enumerate the confirmed-signature records across the sealed population:
(file, MF, MT, record index, leading contaminated ordinates, reference value).
Classify every non-matching file into its residual class. Deliverable:
`results/g1_p25c_signature.json` + independent checker. No patch code before
G1 commits.

### G2 — patch spec and patcher

Freeze the patch specification (derived from the G1 enumeration, not
re-derived); implement the deterministic patcher as a control; it emits the
derived corpus, a per-file old→new SHA-256 manifest and a per-cell patch log.
Independent checker replays every patch from source bytes, verifies surgical
integrity and rejects planted extra edits.

### G3 — post-patch census and acceptance freeze

Re-run the conservation census over the patched corpus; publish projected
post-patch coverage over the IRDFF-II and union sets; freeze coverage floors
and the nonregression reference by amendment before any patched artifact is
scored.

### G4 — artifact build and retrospective scoring

Build the `tendl-2025-patched` union artifact under the 1.1.0 builder (including
enumerated product-state anchors, as P25b G4 established); score both frozen
partitions retrospectively; evaluate the frozen nonregression gates.

### G5 — independent closure

A checker importing no production, audit or scoring module rehashes all
evidence, replays a sampled set of patches, recomputes signature
classification, census deltas, coverage and nonregression arithmetic, verifies
gate ordering, re-verifies all prior verdicts verbatim and rejects planted
mutations. Verdict to `results/verdict_p25c.json`; manifest regenerated once
at closure.

## Amendment 1 — G3 measured census and frozen floors (2026-09-14)

G3 measured post-patch construction coverage under the shipped 1.1.0 builder
(`target/release/actinv`, per-file bound 300 s, FISPACT-709 groups, 293.6 K)
over 139 deduplicated files: 77 union-target files (47 IRDFF-II targets ∪
37 isomeric-row targets) and 62 product-state isomer/ground anchor files.

Measured coverage (patched vs byte-identical source):

| set          | source | patched | recovered |
|--------------|--------|---------|-----------|
| IRDFF-II     | 29/47  | 30/47   | n-Sc045   |
| union        | 43/77  | 45/77   | +n-Br080m, n-Y088 |
| anchors      | 41/62  | 42/62   |           |

All three recoveries are `confirmed_leak_signature` files; zero files
regressed. The post-patch oracle rescan shows the leaked-ordinate signature
cleared in all 28 patched files; residual non-signature defect classes
(`floor`, `interp`, `gridpoint`, `zero_total`, `no_mf3_total`) remain
ledgered, not cleared. Published limitation: the patch recovers none of the
five dosimetry-critical targets still failing on non-signature classes
(Ni-58, Nb-93, Ag-109, In-113, Au-197).

Frozen floors for G4/G5:

- **F1 — no regression.** `newly_broken_files` is empty.
- **F2 — non-inferiority.** Patched-corpus coverage ≥ source coverage on
  every measured set (IRDFF ≥ 29, union ≥ 43, anchors ≥ 41).
- **F3 — material recovery.** ≥ 1 union-set file builds solely because of
  the patch (measured 3).
- **F4 — leak clearance.** Zero `confirmed_leak_signature` hits when the
  frozen signature census is re-run over the patched corpus
  (checker-verified live at G3).
- **F5 — artifact coverage.** The G4 union artifact must contain every
  union-set file that built in G3 (45); any G3-building file absent from
  the artifact is an artifact-build defect counted against the gate.

## Closure interpretation

`P25c-PASS` means the patched corpus is a surgically-remediated, re-censused
candidate that passes the frozen construction, coverage and nonregression
gates — the data-release decision gains a TENDL-2025-derived artifact with a
documented, bounded remediation. It does not authorize a `data-v1.1.0`
publication by itself; that decision remains the maintainer's and consumes
this verdict beside the `P25-FAIL`, `P25b-FAIL` and DATA_LIMITATIONS records.
`P25c-FAIL` preserves every artifact, census and ledger as public evidence;
remaining unpatched defect classes keep failing closed either way.
