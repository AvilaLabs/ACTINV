# ACTINV P18b — evaluated state consistency and conservative runtime use

Opened 2026-08-29 after P18 closed at commit
`d3456890cf0c4b9221ebf17f6630ef8b4fe768cc`. The complete P18 closure workflow is GitHub Actions run
`33258605964` (job `99116546827`), which passed every repository control while preserving the independently derived
`P18-FAIL` verdict. P18 failed because its frozen File 9/10 conservation tolerance was not compatible with the
precision and checking conventions of the source format; the result was valid under that protocol and is not
rewritten here.

P18b separates three questions that P18 combined: whether a source file strictly follows ENDF-6, whether its printed
numbers pass the standard IAEA checking convention, and whether the groupwise reaction rates ACTINV will actually
use conserve production against loss. It retains P18's physical product-state identity work, removes no recorded
source inconsistency, and permits only a bounded, ratio-preserving reconciliation when the runtime discrepancy is no
larger than the official checker convention. Larger discrepancies fail closed.

No Rodrigo diagnostic or held-out dependent value has been read. Before this protocol was frozen, only the committed
P18 aggregate, its already published worst cases, the ENDF-6 manual and the source of the official IAEA checking
codes were inspected. The ignored per-file P18 checkpoints were not queried for new counts, thresholds or
distributions. Those values may be classified only after this protocol is committed and its opening control is green.

## Frozen defect statement and allowed scope

P18 used `max(1e-12 barn, 5e-10 * max(total, peak_total))` pointwise and a still smaller absolute tolerance after
collapse. This is a useful binary-arithmetic stress test, but it is not an ENDF source-precision rule. ENDF stores
real values in eleven-character decimal fields and describes their accuracy as between IEEE single and double
precision. The official IAEA FIZCON checker tests File 9 multiplicity sums and File 10 cross-section sums with a
configurable fractional epsilon whose standard default is `0.001` (0.1%). At an exactly zero total, its historical
algorithm compares the absolute excess with the same numeric epsilon in the tabulated units.

P18b may change only these production surfaces:

1. separate raw source-format diagnostics from the runtime library-construction decision;
2. retain or derive the minimum fixed-field information needed for a decimal/binary comparison envelope;
3. compare state production with the *same processed and collapsed reaction total used for runtime loss*;
4. proportionally reconcile a runtime state sum only inside the frozen standard envelope below, preserving all
   state ratios and nonnegativity while making the group sum no greater than the runtime total;
5. finish the P18 candidate schema's explicit unresolved-state/leakage behavior so an unsupported positive state can
   never feed ground state by default; and
6. record source-conformance class, reconciliation and unresolved-state decisions in the index, data manifest,
   result ledger and certificate.

It may add compatible schema fields and bounded audit tooling. It may not alter evaluated reaction totals, CRAM,
decay constants, group boundaries, flux normalization, schedule semantics, covariance values or the public v1.0.1
artifacts. It may not silently drop a source file, target, reaction, product or state. It may not introduce `unsafe`,
new runtime dependencies, `Arc`, `Mutex`, interior mutability, or cloning/allocation as borrow-checker workarounds.
Any ownership or concurrency redesign needs an append-only amendment explaining the concrete constraint first.

## Frozen authorities and provenance

The following sources are normative and are never copied into Git:

- ENDF-102 (2024), `https://nds.iaea.org/exfor/x4guide/manuals/endf-manual.pdf`, SHA-256
  `77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022`.
- IAEA-NDS ENDF utility codes, `https://github.com/IAEA-NDS/ENDF-utility-codes.git`, commit
  `c2a6718bd831b5c8a6e975beb1946954b1d73c40`. At that commit:
  - `fizcon/fizcon.f` SHA-256 `15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e`;
  - `checkr/checkr.f` SHA-256 `739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e`;
  - `README.md` SHA-256 `b31bb9034edc43ad3ef623eebc154361fad7131a56e4cc087ab52760843423fe`;
  - `LICENSE.MIT` SHA-256 `f9d773e3ae7e2b9136e8e14b6cdfeac38044595b7a5f1dcdb5cdb6614565cb87`.

The manual's File 9 and 10 procedures are binding: File 9 values are fractions of the matching File 3 reaction;
File 10 values are absolute state-production cross sections; their grids belong in the corresponding File 3 grid;
negative-Q File 10 subsections begin at threshold with zero cross section; File 9 sums must not exceed unity; and
File 10 state cross sections must not exceed the corresponding File 3 reaction. The official utility code is an
independent compatibility reference, not permission to overwrite or call a nonconforming source conforming.

P18b reuses exactly the P18 TENDL-2025 neutron, proton, deuteron and alpha archive and staging hashes, the released
v1.0.1 comparator artifacts, the decay inputs, and the Rodrigo family/row seal. Their identities remain those frozen
in `protocols/ACTINV-P18_PROTOCOL.md`, `protocols/ACTINV-P18_AMENDMENT_1.md`, and
`results/p18_family_seal.json`. The five prematurely displayed families remain forced diagnostic forever. The
remaining held-out dependent values stay sealed until P18b G5 is separately authorized.

## Frozen numerical definitions

### Exact-decimal oracle

An independent Python control reads the original eleven-character fields directly and imports no ACTINV production,
audit or scoring module. A field is parsed as an exact base-10 value plus its printed quantum (the place value of its
least significant written digit). Nonblank malformed fields fail. The control evaluates ENDF interpolation at 80
decimal digits and repeats every nontrivial result at 120 digits; a changed classification is an oracle failure.
Histogram, lin-lin, lin-log, log-lin and log-log laws follow ENDF interpolation definitions. Domains are zero outside
their tabulated range, and both one-sided values of a repeated energy are retained.

For source-printing classification only, a nonzero printed ordinate has the closed interval
`[value - quantum/2, value + quantum/2]`, clipped at zero for cross sections and multiplicities. A printed zero has
the analogous nonnegative half-quantum interval. These intervals diagnose whether independent last-digit rounding
could explain a discrepancy; they do not change source values or by themselves authorize runtime acceptance.

Each individual and mutually exclusive `(MT, ZAP)` state sum receives exactly one primary class, in this precedence:

1. `malformed_or_nonfinite`;
2. `missing_total_or_grid_contract`;
3. `threshold_contract`;
4. `definite_source_excess` when even the lowest partial/sum exceeds the highest total permitted by printed-field
   intervals;
5. `printing_envelope_excess` when exact printed values exceed but their intervals overlap;
6. `binary_only_excess` when binary64 fails the P18 stress threshold but exact decimal values do not exceed; or
7. `source_conformant`.

Orthogonal flags record individual versus sum, MF9 versus MF10, pointwise side versus collapsed group, raw MF3 versus
processed runtime total, exact excess, relative excess, source quantums and the P18 classification. Classification
never deletes a comparison and never changes a source ordinate.

### Standard compatibility envelope

`EPS_STANDARD = 0.001` is frozen from the IAEA FIZCON default. At a positive comparator `T`, a sum `S` is standard-
compatible exactly when `(S - T) / T <= EPS_STANDARD`. At `T == 0`, it is standard-compatible exactly when
`S <= 0.001 barn` for File 10 or `S <= 0.001` for File 9. The report also runs unmodified CHECKR and FIZCON with
their standard settings and with SUMUP enabled on deterministic fixtures and a predeclared corpus sample. ACTINV's
independent implementation must agree with the checker's message/no-message decisions for the exercised cases;
differences are retained and explained, never normalized away.

This envelope is a compatibility and bounded-repair ceiling, not a declaration that every source inside it is exact.
Strict source-conformance mode reports any exact ENDF rule violation regardless of size.

### Runtime conservation and bounded reconciliation

The runtime comparator is the exact groupwise total row ACTINV uses for target loss: processed MF2+MF3 at the
requested temperature when resonance reconstruction applies, otherwise collapsed MF3, or the permitted MF10 fission
sentinel where the manual supplies that representation. Comparing MF10 with raw MF3 remains a source diagnostic but
cannot substitute for this application-level check.

For every group and mutually exclusive `(MT, ZAP)` state vector with nonnegative entries `p_i`, let `S=sum(p_i)` and
let `T` be the nonnegative runtime total:

- if `S <= T`, every `p_i` is byte-for-byte unchanged;
- if `S > T` and the pair is standard-compatible under the definition above, replace every state value with
  `p_i * (T / S)` using one common factor, followed by at most a one-ULP downward correction on the largest row if
  needed to make the recomputed binary64 sum `<= T`;
- if `S > T` outside the standard envelope, construction fails with target, MT, ZAP, group, total, sum, relative
  excess and source hashes. No row is emitted, scaled, relabeled or silently skipped.

The common factor preserves every finite ratio among the state rows exactly up to binary rounding, never increases a
partial, retains zero, and changes no reaction-loss total. The index records every corrected group, minimum scale,
maximum absolute/relative correction, raw and reconciled sums and source-conformance class. A strict option rejects
all `S>T` cases before reconciliation. No large-excess override exists.

For MF9, the same rule applies to the collapsed production rows generated from the processed reaction total and its
state multiplicities. The raw source audit separately checks each multiplicity and its mutually exclusive sum against
unity. For inelastic state production, the comparator is the production/loss quantity actually constructed for the
metastable transition, and the ground-return row is not double-counted.

### Excitation identity at decimal boundaries

P18's physical catalog and inclusive tolerance remain unchanged:
`max(1 eV, 5e-6 * max(abs(left), abs(right)))`. P18b changes only how the boundary is decided. The independent oracle
uses exact decimal `ELFS`, `QM` and `QI`. Production may use a conservatively bounded binary interval derived from
the parsed inputs, but it must agree with the oracle on every generated boundary fixture and every one of P18's 143
recorded conflicts. A true value one eV inside or outside, a one-quantum perturbation and cancellation at large Q must
all classify correctly. A genuine conflict still becomes explicit non-emitted production; no rank fallback returns.

## Frozen evidence and scoring rules

P18b carries forward P18's family-level diagnostic/held-out partition, eligibility predicates, ratio definitions,
metrics, population-p90 convention, family bootstrap and baseline (`v1.0.1`) without alteration. Diagnostic values may
be read once only after G3 source/runtime controls are committed, pushed and green. Held-out values may be read once
only after G4's complete diagnostic, compatibility and performance checkpoint is committed, pushed and green.

The P18 acceptance thresholds are retained verbatim at G5: overall and each projectile stratum with at least ten
eligible rows may not regress beyond the frozen additive and multiplicative median/p90 limits or by more than one
percentage point at 10%, 20% and 30% coverage. A changed default also needs an independently demonstrated identity
correction or at least 5% improvement in overall median or p90. Standard-envelope reconciliation cannot count as an
accuracy improvement because it preserves within-product state ratios.

No diagnostic or held-out measurement may choose `EPS_STANDARD`, a classification boundary, a reconciliation
formula, a row mapping, an eligibility decision or an exclusion. Every sealed row remains present as scored or with
its already frozen reason.

## Cost and checkpoint discipline

All raw evaluations, checker builds, reports larger than a compact aggregate and regenerated activation libraries
remain outside Git. The four P18 checkpoints may be reused only after their file, manifest and source hashes are
verified; new fields require new versioned checkpoints rather than in-place edits. Processing is one evaluation at a
time, projectiles are sequential, jobs expected to exceed ten minutes are resumable, and the 12 GB virtual-memory and
approximately 1 GiB single-array limits remain.

The official checker sample is frozen before its output is read: all four P18 worst-case files, every file containing
one of the 143 excitation conflicts, every generated fixture, and the first 25 corpus files per projectile ordered by
SHA-256 of `ACTINV-P18b-IAEA-SAMPLE-v1\n<projectile>\n<source-sha256>`. Duplicate selections are collapsed. The full
ACTINV decimal/runtime audit still covers all 11,400 files; the official sample is an independent control, not a
substitute.

## Gates

### G0 — opening, authority and quarantine seal

An independent control binds the P18 closure commit and green workflow, this protocol, the P18 protocol/amendment,
all carried source/artifact/seal hashes, the official manual and IAEA utility-code commit/file hashes. It proves that
no Rodrigo dependent value or new per-file checkpoint classification was read before opening and writes the frozen
official-checker sample identities without checker output. Path, hash, commit, sample-order, dependent-value and
held-out-access plants fail. G0 is committed, pushed and green before any new checkpoint aggregation.

### G1 — decimal and checker oracle

Generated fixed-width ENDF fixtures exercise every legal real spelling, quantum boundary, interpolation law,
repeated energy, below/above-domain behavior, MF9 fraction, MF10 individual/sum, exact zero total, 0.1% boundary,
threshold rule and excitation-cancellation case. The independent decimal oracle and a small Rust diagnostic agree;
80- and 120-digit classifications are identical. Unmodified pinned CHECKR/FIZCON runs agree on the predeclared
standard-compatibility fixtures. Boundary, digit, exponent, interpolation, side, grid and tolerance mutations fail.

### G2 — complete source/runtime classification

All four frozen TENDL corpora are scanned completely. The report reproduces the P18 inventory, declaration, mapping
and strict-violation totals before adding the new mutually exclusive classification, raw-versus-runtime comparison,
standard-compatibility and reconciliation-candidate counts. Every prior violation is accounted exactly once; every
new comparison identifies its source and group/energy context. The independent decimal control recomputes aggregate
counts and every class boundary without importing production. The official checker sample is run and retained.

G2 passes when the audit is complete, reproducible and classification agreement is exact; observed source
nonconformance is a reported result rather than an automatic phase failure. Any missing row, stale checkpoint,
unexplained oracle/checker disagreement, measurement access or post-result class change fails the phase.

### G3 — conservative builder and fail-loud runtime

Production source-format diagnostics no longer reject a target merely for failing P18's obsolete stress threshold.
The builder instead applies the frozen runtime comparator and bounded reconciliation, retaining the raw diagnostic.
Generated and smallest-real fixtures prove unchanged rows are bit-identical, standard-compatible excess is projected
with one common scale, ratios are preserved, sums close, and an excess one representable step beyond the ceiling
fails. Processed-resonance and charged-particle paths are both exercised.

The candidate schema routes every unsupported positive state to the existing explicit leakage state with a distinct
ledger/certificate reason; it never looks up or feeds product ground. CLI, Python, prepared, mesh and strict modes
agree. Legacy v1.0.1 libraries remain readable with their historical behavior only when identified as legacy schema.
No public artifact changes at this gate.

### G4 — compatibility, diagnostic accuracy and unseal authorization

After G3 is green, diagnostic Rodrigo values are read once and scored with the unchanged P18 formulas. Candidate and
v1.0.1 use identical inputs, reaction totals, schedules and output normalization. All unaffected normalized release
results are bit-identical; affected total loss is identical; every reconciliation is within its frozen ceiling and
reported. Runtime and peak RSS are no worse than 1.05 times the locally rebuilt opening median and the larger of
1.05 times opening RSS or opening plus 16 MiB on the frozen P16 public workload.

An independent scorer reproduces all ratios, metrics and bootstrap values. The complete report, row ledger,
performance evidence, schemas and checker are committed and pushed. A fully green workflow on that exact commit is
the only held-out unseal authorization.

### G5 — one-time held-out decision

The still-sealed P18 held-out values are read exactly once through unchanged G4 code. Every sealed row is accounted.
The frozen nonregression and minimum-benefit rules above decide the candidate. No post-unseal tolerance, policy,
mapping, reconciliation, row, metric or output change is allowed. One append-only repair amendment makes an otherwise
passing closure conditional; a second repair need fails the phase.

### G6 — full rebuild and release artifacts

Only after G5 passes are all four activation libraries rebuilt. Fresh and cached builds are byte-identical; every
target is either built or fails under a predeclared explicit reason, and release coverage may not regress from the
v1.0.1 catalog. Every source-conformance and reconciliation summary is in the versioned manifest. No bulk data enters
Git. Clean fetch/install reproduces examples; v1.0.1 artifacts and tags remain byte-identical.

### G7 — independent closure and additive release

The exact four Rust quality commands in `AGENTS.md`, Python-binding Rust gates, doctests, public examples, bounded
parsers, prior verdicts, self-contained clone, CI end-to-end, release-note, dependency and artifact controls all pass.
An independent closure checker imports no production/audit/scoring module, rehashes all evidence, repeats the
classification and reconciliation arithmetic, verifies authorization, and rejects source, class, threshold, scale,
ratio, sink, row, metric, artifact and provenance plants. Manifest regeneration is byte-identical.

A scientifically successful P18b authorizes only an additive minor release and a separately versioned activation-data
catalog after clean source, wheel, crates.io and fetched-artifact installation tests. Registry/signing failure pauses
publication but does not alter evidence. A failed threshold closes `P18b-FAIL`; it never weakens a rule or modifies
v1.0.1.

## Closure interpretation

`P18b-PASS` means ACTINV distinguishes raw evaluation quality from runtime conservation, maps product states by
physical identity, bounds any numerical reconciliation by an independent standard convention, fails loudly beyond
that bound, and passes the frozen held-out decision. It does not certify TENDL, correct a gross evaluator defect,
prove all activation cross sections experimentally accurate, or claim superiority over another product. Source
nonconformance remains visible even when runtime use is safe.
