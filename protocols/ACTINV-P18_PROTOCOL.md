# ACTINV P18 — evaluated product identity and state-branch accuracy

Opened 2026-08-29 after P17 closed at commit
`7a2d1f47b62155c0f7a22a4e0b9ec5d6e6730bc8`. The final P17 closure workflow is GitHub Actions run
`33232228355`, which passed all 39 steps while preserving the checker-derived `P17-FAIL` verdict. That failure was
procedural rather than a production regression: P17 changed no production code, public package or nuclear-data
artifact. P18 is a separately frozen repair phase and may address only the product-state identity cause class exposed
by P17 and supported by the new evidence sealed below.

P18 replaces heuristic excited-level ranking with traceable ENDF-6 product identity, prevents a missing decay isomer
from silently becoming ground-state inventory, and checks state-resolved production against reaction totals. It does
not tune evaluated cross sections, alter measurements, add self-shielding, redesign the solver, broaden projectile
scope or claim general superiority over another product. A changed default ships only if independent physical
controls pass and genuinely held-out state-branch evidence does not regress.

## Frozen defect statement and allowed production scope

The opening implementation reads `LFS` from MF=8/9/10 product records but discards the accompanying physical identity
metadata: MF=8 `ELFS`, MF=9/10 `QM` and `QI`, and target-evaluation `ELIS`/`LIS`/`LISO`. The builder then groups each
`(MT,ZAP)` independently and rank-compresses positive raw `LFS` values into consecutive decay-isomer ordinals. That
can give the same residual state a different identity across reaction channels and can map a higher evaluated level
onto the first decay isomer merely because omitted lower levels are absent from one section. At runtime, an activation
row whose positive state number is absent from the decay library is currently redirected to the product ground state
and ledgered as `isomer_state_absent_from_decay_library_used_ground`. This is visible but physically unsafe because it
creates plausible ground-state atoms of an identity the activation evaluation did not specify.

P18 may change only the following production surfaces:

1. retain target and product state metadata already present in ENDF-6;
2. construct a deterministic evaluation-wide residual-state catalog and map an emitted product to a decay isomer by
   physical excitation identity rather than section-local rank;
3. preserve the raw state metadata and mapping decision in the library index and result provenance;
4. enforce state-partial versus reaction-total bounds during library construction; and
5. replace the runtime ground-state substitution with an explicit fail-closed or accounted-unresolved-state policy.

The implementation may add versioned fields to activation-library/index/prepared schemas and compatible result-ledger
fields. It may not introduce `unsafe`, a new runtime dependency, implicit state guessing, a default escape hatch,
`Arc`, `Mutex`, interior mutability or clone/allocation workarounds. It may not change CRAM, schedule semantics,
pruning, reaction-rate arithmetic, group boundaries, evaluated cross-section values or decay constants. An ownership
or concurrency redesign requires an amendment explaining the concrete constraint before source changes.

## Frozen sources and evidence seal

All external paths are relocatable; source identity, URL and SHA-256 are normative. No raw evaluation, paper,
supplement, generated bulk library or cache enters Git.

### Opening software and nuclear data

- P18 opening source and v1.0.1 production comparator:
  `7a2d1f47b62155c0f7a22a4e0b9ec5d6e6730bc8` and signed release `v1.0.1` at
  `0332779401363d2f39722efe7a0b7218afcfb270`.
- TENDL-2025 raw archives: neutron
  `e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`, proton
  `49340a03b0d9ac86598c6b710c0bc2ec0babd3fa0717a9ff1d75f042fccc5b0b`, deuteron
  `34f459aea0b5ac9c40820c88d898618f926ec3b52858a5393e42d57707ec5f1c`, and alpha
  `25520f6eb42ce024c065f85255277ed169b2f826e9fc24f5d093c99d5c60e018`.
- Exact external staging manifests: neutron
  `b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67`, proton
  `98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef`, deuteron
  `afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9`, and alpha
  `e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf`.
- Released activation-library payloads: neutron
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`, proton
  `0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790`, deuteron
  `8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae`, and alpha
  `ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915`.
- Released decay inputs remain ENDF/B-VIII.0
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` with JEFF-3.3 fallback
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.

### Normative ENDF-6 semantics

The pinned 2024 ENDF-6 Formats Manual is
`https://nds.iaea.org/exfor/x4guide/manuals/endf-manual.pdf`, SHA-256
`77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022`. Its File 8 definition of `LFS` and `ELFS`,
File 9/10 definition `QI = QM -` residual excitation energy, target-state `LIS`/`LISO`/`ELIS` definitions, and File
10 requirement that a state partial not exceed its File 3 reaction total are normative. A raw excited-level number is
not presumed equal to a decay-isomer ordinal.

### New public state-ratio evidence

The primary reference is A. Rodrigo et al., *Compilation of isomeric ratios of light particle induced nuclear
reactions*, Atomic Data and Nuclear Data Tables 153 (2023) 101583, DOI `10.1016/j.adt.2023.101583`.

| content | public URL | SHA-256 | bytes |
|---|---|---:|---:|
| authors' paper | `https://arxiv.org/pdf/2303.09595` | `6cf1833e268b77177c647cc5504f08731e34ead00c8756b26bf0230a5b32b431` | 185820 |
| publisher supplement | `https://ars.els-cdn.com/content/image/1-s2.0-S0092640X23000116-mmc1.txt` | `945e66f8904bb972662f5178e94e22a08ecb8006eefe1c2d9fbda66fe599763d` | 2065687 |

The paper reports 12,313 curated points in 962 reaction families. A discovery-only structural scan of the supplement
found 12,313 data records but 963 apparent reaction headers. G0 must reconcile this one-header discrepancy from
structure and identifiers alone; it may not discard a record or inspect a dependent ratio to force agreement. Both
the source's stated count and the literal parsed count remain in evidence.

Before the G0 checkpoint, the supplement parser may expose only reaction-family identity, projectile, target,
product, ratio form, source/reference flags, row count and incident-energy metadata. Measured ratios and their
dependent uncertainties remain unread. The two case studies named in the paper text, `93Nb(n,alpha)90Y` and
`197Au(d,2n)197Hg`, are diagnostic regardless of the partition. Every P17-exposed reaction/product family is also
diagnostic. No value from either set may be described as held out.

G0 creates the partition at complete reaction-family granularity. Eligible families are stratified by projectile and
ordered by SHA-256 of the UTF-8 string
`ACTINV-P18-HOLDOUT-v1\n<projectile>\n<canonical-family-id>`. The first 25% in each stratum, rounded down but at least
one when a stratum has four or more eligible families, are held out; the remainder are diagnostic. Forced-diagnostic
families are removed before ranking. The committed seal contains family IDs, row IDs, eligibility reasons, source
hashes and independent variables, never dependent values. Diagnostic values may be read only after that seal is
committed and its workflow passes. Held-out values may be read exactly once only after G4's unseal checkpoint is
committed, pushed and green.

## Frozen evidence eligibility and score

Every source row appears exactly once in the row ledger, whether scored or not. A row is eligible when all of the
following are established without using its measured value:

1. projectile is neutron, proton, deuteron or alpha;
2. target is a single isotope represented by the frozen TENDL corpus rather than a natural-element mixture;
3. the target, reaction and residual nuclide parse without an inferred mass or charge;
4. the ratio identifies a ground state and one explicit metastable state whose physical excitation can be matched to
   the candidate catalog;
5. numerator and denominator can be calculated from state-resolved production cross sections without cumulative
   decay feeding, an unreported state, a natural-abundance mixture or a model-dependent correction;
6. the incident energy lies inside both required evaluated state-partial domains; and
7. no source flag says the point is superseded, rejected, estimated, digitized without tabulated uncertainty, or
   otherwise unsuitable under the compilation's own definitions.

Ineligible rows are retained with exactly one primary predicate and optional secondary predicates. Eligibility cannot
change after its G0 checkpoint except through one append-only amendment, which makes an otherwise passing closure
conditional. A changed measured value, uncertainty, reaction family or ratio form after unseal fails the phase.

The production observable is calculated in the ratio form printed by the source. For two-state products, `M/T`,
`G/T`, `M/G` and `G/M` are computed from the same ground and named-metastable state partials; no ratio is reinterpreted
as another form. Rows involving more states are scored only when the publication's numerator and denominator name a
complete calculable set. A zero or nonfinite denominator is an explicit calculation failure, not an exclusion.

For each projectile and for all eligible rows, separately for opening v1.0.1 behavior and the candidate, report:

- row and reaction-family counts plus every ineligibility reason;
- signed `ln(calculated/measured)` per row;
- median, population p90 and maximum `abs(ln(calculated/measured))`;
- geometric-mean calculated/measured;
- fractions within 10%, 20% and 30%; and
- the source uncertainty as published, without labeling it total predictive uncertainty.

Population p90 uses NumPy's linear percentile convention. Families, not individual rows, are the resampling unit for
a deterministic 10,000-replicate paired bootstrap seeded from the protocol hash; it reports the candidate-minus-
baseline median and p90 changes without using significance as a substitute for the frozen acceptance rule.

## Frozen mapping and numerical rules

1. The residual-state catalog key is `(Z,A)`. Each catalog entry records the source evaluation's target `LIS`, `LISO`
   and `ELIS`, file identity and tolerance decision. Ground is `LISO=0`; positive canonical state identity comes only
   from an evaluated `LISO` with matching physical excitation. File order, reaction MT and raw `LFS` rank cannot
   affect the result.
2. MF=8 `ELFS` and the independently derived MF=9/10 excitation `QM-QI` must agree within
   `max(1 eV, 5e-6 * max(abs(ELFS),abs(QM-QI)))` when both exist. A finite nonnegative excitation may match a catalog
   state within the same tolerance. Multiple matches, conflicting sources, a missing positive-state excitation,
   `LFS=98`, or no catalog match are explicit non-emitted reasons unless the ENDF manual supplies an unambiguous
   special meaning exercised by an independent fixture.
3. Duplicate catalog evaluations for the same `(Z,A,LISO)` must agree in excitation within the mapping tolerance;
   otherwise construction fails. Duplicate product declarations must agree in identity and numerical content.
4. Every finite state partial is nonnegative within the existing interpolation tolerance. At every union-grid energy,
   each MF=10 state partial and the sum of mutually exclusive state partials must be no greater than the matching MF=3
   total plus `max(1e-12 barn, 5e-10 * max(total, peak_total))`. The same inequality is checked after 709-group
   collapse with `max(1e-14 barn, 5e-10 * max(total, peak_total))`. A missing total is recorded and cannot be called a
   closure pass.
5. An activation row naming a state absent from the decay network must never feed ground by default. The production
   amount and rate are booked to a distinct accounted unresolved-state sink, with target/reaction/product/raw and
   canonical state identity in the ledger and certificate. A strict option may reject the run before solving. The
   compatibility alias `isomer_state_absent_from_decay_library_used_ground` is retired for newly built P18 libraries;
   legacy libraries remain readable but retain their old, visibly ledgered behavior only when explicitly selected as
   legacy schema input.
6. Where canonical identity and emitted rows do not change, row values, collapsed rates, trace/coupled results and
   normalized public output are bit-identical to v1.0.1. Where identity changes, the sum of all product-state
   production and total reaction loss is bit-identical before versus after remapping; only the named state or
   unresolved-state destination may differ.

## Minimum gate input and cost control

No gate begins with a full four-projectile rebuild. G1 uses compact synthetic ENDF fixtures plus the smallest real
TENDL files exercising ground, one isomer, omitted excited levels, multiple isomers and the P17 Ag identity. G2 first
performs a metadata-only scan of all staged evaluation headers and product sections, checkpointed per projectile, then
builds only affected targets and a deterministic unaffected sample. G3 uses a compact generated decay network. A full
neutron/proton/deuteron/alpha rebuild occurs only after G1--G4 pass.

One representative affected target is profiled before expansion. No concurrent heavy build is allowed. Jobs expected
to exceed ten minutes are resumable per target under `ulimit -v 12000000`; no single array may exceed approximately
1 GiB without a ledgered justification. All external work and payloads stay outside Git.

## Gates

### G0 — provenance, metadata-only seal and partition

An independent control verifies the opening commit, P17 closure workflow, every source hash, raw archive, staging
manifest and released artifact above. It parses the supplement through a bounded streaming grammar that proves row
and family structure without materializing or emitting dependent values. It reconciles the 962/963 header discrepancy,
applies the frozen eligibility predicates that do not require calculated values, creates the deterministic family
partition, and proves diagnostic/held-out disjointness. Path, URL, hash, duplicate-family, reordered-family,
dependent-value-leak and partition plants fail. The compact seal is committed, pushed and green before diagnostic
values are read.

### G1 — physical product identity parser and mapper

The production parser retains target `LIS`/`LISO`/`ELIS`, MF=8 `LFS`/`ELFS` and MF=9/10 `QM`/`QI` without changing
their source precision. Generated fixtures cover ground, a first isomer, an omitted lower level, two isomers, the same
residual through multiple MTs, shuffled section/file order, duplicate agreement, duplicate conflict, `LFS=98`,
missing metadata and tolerance boundaries. Independent expected mappings are written directly from ENDF semantics,
not produced by the mapper under test. The P17 Ag fixture must map the physically matched raw level to `m1` rather
than rank-compressing within one MT. Every ambiguous or unsupported mapping fails or is ledgered under the frozen rule.

### G2 — corpus audit and state-partial conservation

A streaming audit covers every MF=8/9/10 product declaration in all four frozen TENDL corpora. Every declaration is
accounted as emitted with a catalog-backed identity or under one predeclared non-emitted reason; there is no silent
rank remap. The control checks raw and collapsed individual/summed state partials against MF=3 totals, nonnegativity,
duplicate consistency, cross-MT identity and file-order invariance. It emits old-versus-candidate mapping counts and
every changed identity without treating a large change count as success. Independent fixtures plant excess partials,
missing totals, excitation conflicts, duplicate conflicts and state-order changes.

### G3 — fail-loud runtime and interface identity

Generated activation/decay networks prove that a missing positive isomer never becomes ground under the candidate
schema. Default execution conserves the amount in the explicit unresolved-state sink; strict execution rejects it.
Ordinary CLI, Python, prepared and mesh paths agree on state identity, amount, ledger and certificate. Existing
ground, mapped-isomer and no-decay-data behaviors remain distinct. Mutation tests reject sink removal, ground feed,
state relabeling, legacy-schema misclassification and certificate omission.

### G4 — diagnostic accuracy, compatibility and unseal authorization

After G0 is green, diagnostic Rodrigo values are parsed once. The signed v1.0.1 builder/runtime and candidate are
scored under the frozen formulas; the two named paper cases, P17-exposed state rows and deterministic diagnostic
families are all retained. An independent arithmetic implementation reproduces ratio transforms, interpolation,
metrics and paired bootstrap without importing production or scoring modules.

The candidate must pass G1--G3; preserve all unaffected normalized release results exactly; preserve affected total
production and loss exactly; and satisfy runtime/RSS ceilings of 1.05 times the locally rebuilt opening median and the
larger of 1.05 times opening peak RSS or opening plus 16 MiB on the frozen P16 public workload. The diagnostic report,
score code, evidence schema, eligibility ledger and independent checker are then committed and pushed. A fully green
workflow on that commit is the sole held-out unseal authorization.

### G5 — one-time held-out state-ratio score

After G4 authorization, the held-out dependent values are read exactly once and passed through unchanged code and
row mappings. Every sealed row appears once as scored or with its already frozen ineligibility reason. The candidate
passes only if, overall and in every projectile stratum with at least ten eligible rows:

1. median absolute log error is no more than baseline plus `0.005` and no more than `1.01` times baseline;
2. population-p90 absolute log error is no more than baseline plus `0.01` and no more than `1.01` times baseline;
3. 10%, 20% and 30% coverage each decline by no more than one percentage point; and
4. every mapping changed from a valid baseline state has a catalog-backed identity and conserved state sum.

In addition, changed default behavior may ship only if it corrects at least one independently demonstrated physical
state identity or improves either overall median or p90 absolute log error by at least 5% without failing any rule
above. A measurement disagreement alone never overrides an unambiguous ENDF identity. No row, stratum, threshold,
ratio form or metric can change after unseal. One documented amendment makes an otherwise passing close conditional;
a second repair or any unamended change fails the phase.

### G6 — full rebuild, artifact and release-candidate reproducibility

Only after G5 passes, rebuild the complete frozen neutron, proton, deuteron and alpha libraries under the candidate
schema. Fresh and cached builds are byte-identical; every source target is accounted; peak memory remains bounded;
no bulk artifact enters Git. The versioned external data-catalog candidate records payload, index, source archive,
staging manifest, builder, schema and provenance hashes. A clean fetch and the public example reproduce the candidate
result from those artifacts. Old v1.0.1 libraries remain readable and hash-identical; no existing signed tag or asset
is moved or replaced.

### G7 — quality, independent closure and public minor release

The exact four Rust commands in the repository `AGENTS.md` pass:

```text
cargo fmt --all -- --check
cargo check --workspace --all-targets --all-features
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
```

Doctests, Python-binding Rust gates, public examples, schema round trips, bounded parsers, prior verdicts,
self-contained clone, CI end-to-end, release-note, dependency and artifact controls also pass. A closure checker that
imports no production or scoring module rehashes all evidence, repeats every mapping/accounting/metric/threshold,
verifies the unseal authorization and rejects value, row, identity, ratio, partition, metric, sink, artifact and
provenance plants. The manifest is regenerated once at closure and clean regeneration is byte-identical.

A `P18-PASS` or scientifically successful `P18-CONDITIONAL` authorizes an additive `v1.1.0` source/Python/crates.io
release and a separately versioned activation-data catalog. Release candidates are installed in clean environments
before publication; PyPI, crates.io and GitHub must resolve to the same signed source tag, and downloaded assets must
match published SHA-256 files. Publication never rewrites v1.0.1. Registry or signing failure pauses publication but
does not alter scientific evidence. After the release closeout workflow is green, P19 is opened under its own hashed
protocol and begins immediately.

## Closure interpretation

`P18-PASS` means the narrow state-identity repair, conservation rules and held-out nonregression requirements passed;
it does not mean every isomeric ratio is accurate, every TENDL branch is experimentally validated or ACTINV is
generally more accurate than FISPACT. One append-only documented repair round makes an otherwise passing close
`P18-CONDITIONAL`. A second repair, post-unseal unamended change, missing row, silent fallback, failed threshold or
unreproducible artifact closes `P18-FAIL`; no changed default is released, and unfinished work moves to a new hashed
phase rather than weakening this protocol.
