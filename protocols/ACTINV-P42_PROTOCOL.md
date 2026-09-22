# ACTINV-P42 — mechanism closure of P40's open divergence classes

**Status:** active | **Opened:** 2026-09-22 | **Parent:** P40 open classes |
**Depends on:** P40-CONDITIONAL (`results/verdict_p40.json`), P38/P39
FENDL-3.2c artifact lineage

## Intent

P40's 184-case identical-data census left exactly two open divergence
classes, both pre-registered rather than folded into floors:

1. `short_lived_products_absent_in_actinv` — Ti55, V55, Ti53, Cr57, Mn59;
   `other` exceeds 1% of arm activity on 91/912 cases (10.0%). Diagnosed as
   data-availability limits: channels on non-artifact parents, plus Cr-57
   absent from *both* pinned decay archives (ENDF-B-VIII.0 and JEFF-3.3).
2. `common_nuclide_magnitude` — Cr51 (209 cases), Fe59 (146), Cr55 (97),
   Mn57 (86), Fe53 (82), Cr56 (26), Mn58 (19), V52 (8). Directional biases:
   Cr-55 systematically ACTINV-low; Mn-58/Fe-53/Mn-57 systematically
   ACTINV-high; V-52 +23% ACTINV overshoot on the sampled case.
   Per-nuclide mechanisms were left **unclassified** — this phase's core
   obligation.

P42's job is to convert "unclassified" into an enumerated per-nuclide
mechanism ledger, repair only demonstrated defects, and re-measure the
census. It changes no production physics unless a mechanism is proven a
true processing defect — coverage asymmetries become named floors or a
bounded artifact extension, never silent matching to ALARA's output.

## Frozen mechanism vocabulary

Every member nuclide of both classes receives exactly one primary class
from this frozen set, with the recorded trace:

- `coverage_parent` — divergence explained by a reaction on a nuclide
  absent from the 31-target artifact (second-order chain reachability);
  the missing parent's channels are enumerated by KZA.
- `decay_file_gap` — the product nuclide has no entry in either pinned
  decay archive; fail-closed drop is correct behavior, the gap is data.
- `decay_feed_missing` — a decay-feed path ALARA's idx carries that the
  pinned decay files do not declare (e.g. V-55 → Cr-55).
- `burn_out` — ALARA destroys the product via a reaction on the product
  itself; ACTINV cannot because the product is a non-target nuclide.
- `conversion_content` — a channel exists on a *shared* target in both
  arms but its content (branching, lumped-state allocation, threshold,
  group values) differs between FENDL ENDF-6 and ALARA's REAC conversion.
- `alara_heritage` — the channel exists in ALARA's REAC with no
  corresponding FENDL-3.2c ENDF-6 evaluation (heritage data asymmetry;
  unclosable under identical data by definition).
- `true_defect` — ACTINV's processing/solver produces a demonstrably
  wrong row on identical input (independent ENDF-field trace shows the
  defect).
- `unresolved` — a legitimate mechanism exists but cannot be isolated
  from the committed evidence; acceptable, ledgered, never hidden.

`unresolved` is a valid close; an omitted nuclide is not. Every member
must land in exactly one class with its trace artifact hash-pinned.

## Frozen scope

P42 may:

- add controls, per-nuclide mechanism traces, machine-readable evidence,
  checkers and documentation under `controls/`, `results/`, `docs/`;
- build a bounded extended FENDL-3.2c artifact over **exactly** the
  parent nuclides the census materials' decay-reachable chains require
  (enumerated at G1 from the case inputs alone — not from ALARA's
  library), scored as a separate `extended_artifact` arm;
- repair `conversion_content`/`true_defect` mechanisms in
  `controls/` (scoring layer) only; a `crates/` production repair requires
  a separately-frozen amendment and stays out of the release path.

P42 may not:

- tune any value, mapping, or threshold toward ALARA's output;
- re-read or re-partition any P17/P24/P26b/P40 evidence;
- implement self-shielding, cover transport, or new physics;
- relax the 5e-4 identical-data tolerance or redefine `other`;
- touch the pinned decay archives or invent decay data — a nuclide
  absent from both ENDF-B-VIII.0 and JEFF-3.3 stays absent;
- claim the extended artifact is "identical data" — it is labeled
  `extended_artifact` everywhere it appears.

## Frozen authorities and provenance

- P40 verdict `results/verdict_p40.json` and the sealed census
  (`results/g0_p40_seals.json`, census SHA-256
  `912034ba493e7f8d66f85b5013d57664635a4a0eff618859ffcffcb25bd85f5e`).
- Census case directories under
  `~/nuclear-data/p26b-work/p38-run/g2-cases-p39` — the persisted
  `out.json`/`case.stdout`/`alara.dmp` per-case records; their content
  is read-only evidence, rehashed at G0.
- ACTINV FENDL-3.2c artifact `actinv_fendl32c_709_p39.npz`, SHA-256
  `18547cc311560f29ed20a4e553427f238d046046461feaf7bfbec8adf298e097`
  (31 targets).
- The ALARA `fendl32c_709` idx/dump lineage already persisted in the
  case dirs — the comparator stays as executed, not re-derived.
- FENDL-3.2c ENDF-6 source archives as already hash-pinned by the P38
  lineage — the extended build reuses exactly those files.
- Pinned decay archives: ENDF-B-VIII.0 and JEFF-3.3 decay-data files as
  carried in `actinv-data` — their absence gaps are evidence, not bugs.

## Gates

### G0 — opening, authority, case-dir seal

An independent control binds this protocol's SHA-256, the opening commit,
the P40 verdict/artifacts, the census case-dir tree hash (all `out.json`,
`case.stdout`, `alara.dmp` files), and the artifact identity. It asserts
the P40 verdict verbatim and confirms no fresh numerical content from the
case dirs has entered any committed evidence file. G0 is committed and
green before G1 reads any per-case trace.

### G1 — mechanism census, frozen criteria

Produce `results/g1_p42_mechanisms.json`: for every member nuclide of
both open classes, exactly one primary class from the frozen vocabulary
plus its trace (ENDF field reference, decay-file lookup record,
chain-reachability enumeration, or conversion diff), each hash-pinned.
Independent checker re-derives the classification from source files
without importing the classifier. If a bounded extended artifact build
is warranted by `coverage_parent` enumeration (i.e., FENDL-3.2c ENDF
contains evaluations for the enumerated parents), its target list is
frozen here — derived from case inputs, never from ALARA's library.

### G2 — demonstrated-mechanism repairs

Only `true_defect` and `conversion_content` classes may receive repairs,
each with an independent control proving the repaired behavior on
identical input. `decay_file_gap` rows are ledgered with the exact
absence record (file, nuclide, missing state). `coverage_parent` and
`burn_out` rows may optionally be exercised through the `extended_artifact`
arm — labeled as such, never blended into the identical-data claim.
`alara_heritage` rows stay named floors. Mutation plants on class
assignments, trace records and reason strings fail.

### G3 — re-census and report

Re-run the identical 184-case census inputs through the repaired layer
(and the `extended_artifact` arm where built): every case's divergence
re-decomposes into the P40 class map plus the repaired-residual report.
The report shows per-class before/after shares, the scoped-equivalence
median on class-cleaned channels, and a complete per-nuclide ledger.
Rows unchanged by any repair must be byte-identical to P40's record.

### G4 — independent closure

A checker importing no production, parsing or scoring module rehashes all
inputs, repeats the mechanism classification, C/E arithmetic and class
accounting, verifies gate ordering, re-verifies the P40 verdict verbatim,
and rejects planted mutations. Verdict to `results/verdict_p42.json`.

## Closure interpretation

`P42-PASS` means every member nuclide of both open classes carries a
hash-pinned mechanism classification, demonstrated defects are repaired
with independent controls, and the residual divergence decomposition is
measured — it does not assert that identical-data equivalence improved
beyond what the coverage asymmetry permits. `P42-FAIL` preserves the
ledger as public evidence; the classes stay open and named.
