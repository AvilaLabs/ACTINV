# ACTINV P17 — open validation and attribution

Opened 2026-08-28 after P16 closed at commit
`f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d`. The final P16 closure workflow is GitHub Actions run
`33224125433`, which passed every step. P17 changes no production physics, public interface, default, packaged data or
numerical threshold. It expands independent evidence and identifies where material disagreements enter the calculation
before P18 is allowed to repair anything.

The central rule is separation. Solver, chain construction, nuclear-data processing, evaluated data, decay/yield data
and measurement definition are tested by controlled substitutions; they are never collapsed into a single
code-versus-code number. A different-data comparison cannot be described as a solver comparison. A library that was
validated against a measurement cannot be presented as a blind prediction of that measurement. An unexplained result
is recorded as unresolved, not assigned a convenient cause.

## Frozen scope and exclusions

P17 may add controls, compact synthetic fixtures, external-data acquisition metadata, machine-readable evidence,
checkers and documentation. It may run the released ACTINV path and build temporary external control libraries. It may
not edit `crates/`, Python bindings, schemas, production parsers, public examples, release artifacts or default data.
Any product defect discovered here is recorded in the append-only cause ledger and considered only under a separately
hashed P18 repair protocol.

There is no licensed FISPACT-II or SCALE/ORIGEN executable in scope. Their public results remain clearly labeled
different-data context and are not a P17 gate. ACTINV, ALARA 2.9.2, OpenMC 0.15.3, SciPy and a freshly built pinned
NJOY2016.79 are the executable controls. No bulk evaluation, benchmark PDF, generated library, executable or cache is
committed.

P17 does not tune TENDL, alter an experimental value, discard a difficult row, optimize a metric, claim total
uncertainty, implement self-shielding, or publish a new package release. The already-seen CoNDERC FNS family is
diagnostic evidence only and can never become a P17 acceptance oracle.

## Frozen sources and evidence seal

All paths below are relocatable; URL, identity and content hash are normative. The IAEA/author sources were downloaded
and hashed before their numerical tables were parsed. Only document metadata and table captions were inspected when
the held-out partition was selected. One `63Cu(n,alpha)60Co` value surfaced in discovery material, so IRDFF-II Table 19
is deliberately diagnostic rather than held out.

### ACTINV and existing controls

- ACTINV opening source: `f9e6a5c8faf15f1748f1b2c4683889ea8a631c9d`.
- Signed release comparator: `v1.0.1` at `0332779401363d2f39722efe7a0b7218afcfb270`.
- TENDL-2025 neutron library NPZ:
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`; index:
  `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`.
- ENDF/B-VIII.0 decay payload:
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`; JEFF-3.3 fallback:
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.
- CoNDERC FNS archive:
  `ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d`.
- ALARA official source commit `faa5b330460fe865e38fc788f1b792ea33d13d1b`; its sample activation, decay
  and element files are respectively
  `f45ced4d5676c993f6b6dd562d5e312e897eabb959dc6ebba56bbeaecde22312`,
  `810f3b8ca46dd55b965e37b84c9793057a7ee53aa2a194a2fcb1ff0d1b681940` and
  `bdfcfdb255d89b4988be9fab4279c36fb9615709ee6a738e963591db6146c290`.
- OpenMC `0.15.3`, NumPy `2.5.2`, SciPy `1.18.0`; NJOY2016.79 commit
  `ac5adf5f33d893e42f2eed7fb286b0d51c7580da`.

### IAEA IRDFF-II public evidence

The source page is `https://www-nds.iaea.org/IRDFF/`, retrieved 2026-08-28. The primary reference is A. Trkov et al.,
*IRDFF-II: A New Neutron Metrology Library*, Nuclear Data Sheets 163 (2020), 1–108, DOI
`10.1016/j.nds.2019.12.001`; the authors' open arXiv PDF is the frozen document.

| content | official URL | SHA-256 |
|---|---|---|
| authors' 110-page primary-reference PDF | `https://arxiv.org/pdf/1909.03336` | `ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f` |
| benchmark-field list PDF | `https://www-nds.iaea.org/IRDFF/NeutronBenchmarkFields-IRDFF-II.pdf` | `93926f4a9937ef1314ebbaa29a11a638ad9d1b3abad08596b0db57ee6bc9c304` |
| pointwise IRDFF-II ENDF archive | `https://www-nds.iaea.org/IRDFF/IRDFF-II_ENDF.zip` | `225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db` |
| 725-group IRDFF-II archive | `https://www-nds.iaea.org/IRDFF/IRDFF-II_g725.zip` | `6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85` |
| groupwise benchmark-spectrum archive | `https://www-nds.iaea.org/IRDFF/IRDFF-II_sp_g.zip` | `544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52` |
| IRDFF-II decay-data ENDF archive | `https://www-nds.iaea.org/IRDFF/IRDFF-II_dd_ENDF.zip` | `397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7` |

### Processing-control evaluations

The fresh NJOY comparison uses these already public, hash-pinned FENDL-3.2c evaluations and no substituted mirror:

| target | SHA-256 |
|---|---|
| Fe-56 | `24a45021fb38262dd8fb598c520a807f342bd07e137a36e88d7ae97a0f38715e` |
| Ag-107 | `0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185` |
| W-186 | `bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744` |
| Au-197 | `fb7897fdde04b68b79cfc2a44e90a7c3aba77397815a5be342648af013f39f6d` |
| Co-59 | `a4c6480e200b9474ed04900e4d17d018577d6235d57f31609b75322ae9a3b75d` |
| Ni-58 | `312f5a069dbda4e0abd662a258710ea332dd749191a9bad2a0c70567644af4f4` |

### Diagnostic and held-out partition

Partitioning is by primary-paper table number, chosen from captions without inspecting the held-out numerical rows.

- **Open diagnostic:** Tables 18–20 (Cf-252 spontaneous-fission and U-235 thermal-fission spectrum-averaged cross
  sections), the already-seen CoNDERC FNS family, and all synthetic/identical-data controls.
- **Sealed held-out family H1:** Tables 21–23, SPR-III operations, foil composition and measured end-of-irradiation
  activities.
- **Sealed held-out family H2:** Tables 24–25, ACRR operations and measured end-of-irradiation activities.
- **Sealed held-out family H3:** Table 36, measured high-temperature Maxwellian spectrum-averaged cross sections.

The H1–H3 numeric rows may be parsed only after G1–G4 controls, the diagnostic report, the row-mapping grammar, metric
implementation and cause taxonomy are committed and pushed as the **unseal checkpoint**. That checkpoint records all
relevant file hashes and must pass the normal GitHub workflow. Afterwards the held-out rows are read exactly once by
the scoring control; corrections require an append-only protocol amendment and make an otherwise passing close
conditional. Public availability does not weaken this procedural seal.

## Frozen inclusion, metrics and attribution rules

Every source row is preserved in a row ledger. A row is scored when its experimental value is finite and positive, its
incident particle is a neutron, the target/reaction/product can be mapped without inference beyond the publication's
own labels, and the published spectrum/history provides enough information to calculate the stated observable. A row
that fails one of those predeclared predicates is not dropped: it is emitted with the exact predicate and source text
identifier. Self-shielding or cover corrections are applied only when explicitly supplied by the source; otherwise
the affected row is ledgered `unsupported_self_shielding`, not silently treated as dilute.

For each family and calculation variant, with `r_i = calculated_i / experimental_i`, report:

- scored and unscored row counts, with every unscored reason;
- geometric-mean C/E, `exp(mean(ln(r_i)))`;
- median, population p90 and maximum `abs(ln(r_i))`;
- fractions with `0.9 <= r_i <= 1.1`, `0.8 <= r_i <= 1.2`, and `1/1.3 <= r_i <= 1.3`;
- the published experimental uncertainty beside each row, without calling it total predictive uncertainty; and
- per-observable signed `ln(r_i)` and exact source identifiers.

Population p90 uses NumPy's linear percentile convention. Families are never pooled, weighted or reduced to a winner
score. Accuracy has no hidden pass threshold in P17: a poor but valid held-out result is a successful measurement and
an input to P18, while invalid provenance, parsing, arithmetic or exclusions fail P17.

A **material mismatch** is a scored row outside the 30% band, a same-operator row outside G1 tolerance, an
identical-data row outside G2 tolerance, or a processing row outside G3 tolerance. Every material mismatch receives
one primary cause from `solver`, `chain-construction`, `processor`, `evaluation`, `decay-yield`,
`measurement-definition`, `unsupported-model`, or `unresolved`; optional secondary causes are allowed. Each assignment
records the controlled substitution, signed log change and supporting evidence. `Unresolved` is an acceptable honest
category but an omitted row is not.

The production score is the unchanged v1.0.1 public path with its shipped TENDL-2025 709-group library and decay
sources. Attribution variants keep every other layer fixed: common transition operators isolate the solver; common
processed rates isolate chain construction; ACTINV and NJOY processing of one raw evaluation isolate the processor;
ACTINV processing of TENDL/FENDL/IRDFF onto one spectrum isolates evaluation choice; common rates with alternate
hash-pinned decay/yield data isolate decay/yield; only publication-defined unit, EOI and ratio transformations may
test measurement definition. IRDFF predictions against IRDFF validation measurements are labeled diagnostic context,
not blind performance.

## Minimum gate input and cost control

No P17 gate requires a full TENDL rebuild. The minimum input is the six FENDL evaluations above, the compact ALARA
sample library, deterministic 2/8/32-state operators, one 12-or-more-reportable-nuclide identical-data network, the
three diagnostic tables, and the three held-out table families. One representative NJOY target and one network run
are profiled before expansion. Work is checkpointed per target/family; scalar thread counts are one; no heavy controls
run concurrently; every job expected to exceed ten minutes uses the repository's `ulimit -v 12000000` guard. External
artifacts remain resumable and hash-verified.

## Gates

### G0 — provenance, access and seal integrity

An independent control verifies the opening source, release tag, every external hash and executable version before
accepting scientific output. Archive traversal, duplicate-member and content-mismatch plants fail closed. It verifies
that diagnostic and held-out table identifiers are disjoint and that no committed pre-unseal result contains an H1,
H2 or H3 numerical row. Missing lawful access is reported, never replaced with copied or licensed material.

### G1 — expanded same-operator numerical controls

ACTINV CRAM-48, OpenMC CRAM-48 and SciPy dense exponentials receive byte-identical branching, capture-chain,
metastable, decay-chain and fission-yield operators at 2, 8 and 32 states, with constant, pulsed and noncommuting
schedules. Every boundary is compared. Populations above `max(1e-24 * initial_1_norm, 1e-30)` agree with dense within
`5e-12` relative or `5e-14 * initial_1_norm` absolute. Values are finite and nonnegative within the absolute bound;
atom/leakage closure and split-schedule relations pass. A disagreement here is solver evidence and cannot be assigned
to evaluated data.

### G2 — broader identical-processed-data networks

Official ALARA 2.9.2 and ACTINV receive the same extracted FENDL-2 group records, fluxes, decay constants, initial
populations and schedules. The minimum exercise reports at least twelve nuclides across at least four target families
and includes a capture chain, competing reaction products, an isomer branch and radioactive decay. Independently
collapsed rates agree within `1e-12` relative. Reportable shutdown populations above `1e-12` of the initial total agree
within `5e-4` relative, matching ALARA's text precision; an independent dense solution accompanies both.

An OpenMC `IndependentOperator`/depletion-chain exercise receives the same processed rates, decay data, initial vector
and schedule for the deterministic network. ACTINV and OpenMC agree within the G1 numerical tolerance on resolvable
populations. The control uses pinned OpenMC source/API behavior and records generated chain and microscopic-rate
hashes. If an interface layer cannot represent a frozen feature, it is an explicit representation result, not a
silently simplified network.

### G3 — raw-evaluation processing differential

A fresh pinned NJOY2016.79 build and ACTINV process the six exact FENDL evaluations. Selected capture and threshold
reactions span resolved LRF=2/3/7, unresolved LSSF=0/1 where present, thermal, 1/E, fission-like and fusion-like
spectra. Both processors receive identical temperature, boundaries, weighting spectra and reconstruction tolerances.
Spectrum-averaged one-group values must agree within `5e-3` relative; non-negligible per-group values must agree within
`2e-2` relative, with the non-negligible cutoff frozen as `max(1e-12 barn, 1e-8 * reference_peak)`.

The report prints every maximum, population p50/p90/p99, energy/group location and below-cutoff count. Existing P3,
P10 and P11 values are context only; P17 executes fresh calculations. A threshold miss is recorded against the
processor layer and does not authorize production edits.

### G4 — open diagnostic attribution

The diagnostic IRDFF tables and all 132 FNS experiments are rederived without changing their established alignment.
Controlled variants quantify, in order, numerical solver, processor, evaluation, decay/yield and explicitly supported
measurement-definition effects. Each variant records identical and changed input hashes. All diagnostic rows and
unscored reasons are emitted under the frozen metric formulas.

Before any held-out value is read, a diagnostic report, schema, scoring implementation, cause-ledger schema and
independent checker are committed and pushed. Mutation plants cover value, unit, row identity, inclusion reason,
family assignment, hash, metric and cause removal. The successful workflow run and checkpoint commit form the unseal
authorization.

### G5 — held-out public measurement score

Only after G4's authorization, H1–H3 are parsed and run through the unchanged production path and predeclared
attribution variants. Every source row appears exactly once as scored or with a frozen exclusion predicate. The report
shows all per-row C/E values and family metrics, including losses and unsupported rows. No result, reaction, foil,
cooling time or family may be removed; no metric or mapping may change after unsealing without an amendment and a
conditional verdict.

H1 and H2 additionally rederive publication-defined EOI activities from recorded reactor operations, foil
composition, spectra and decay data. H3 independently folds each mapped pointwise evaluation and spectrum before
comparison. Independent arithmetic must reproduce the observable before a product/data difference is interpreted.

### G6 — complete cause ledger and independent arithmetic

Every material mismatch from G1–G5 is present in an append-only machine-readable ledger. A checker that imports no
production or scoring-control module rehashes raw compact evidence, repeats inclusion predicates, reconstructs C/E and
all family statistics, verifies controlled-substitution identities, and accounts for every mismatch exactly once.
It rejects missing, duplicate, relabeled and unsupported-causality plants. Documentation distinguishes demonstrated,
bounded and unresolved causes and makes no competitor claim unsupported by an identical layer.

### G7 — quality, reproducibility and close

The exact four Rust commands in `docs/maintainers/AGENTS.md` pass, as do doctests, Python-binding Rust gates,
self-contained-clone, CI end-to-end, bounded parser, public examples, prior verdicts and release-note/dependency
controls. Compact P17 controls run in CI without bulk data; external controls have hash-bound replay instructions and
committed compact evidence. No nuclear-data input or generated bulk library enters Git. The session binds source,
evidence and unseal commits plus successful GitHub workflows; the manifest is regenerated once at closure and a clean
regeneration is byte-identical.

## Closure interpretation

`P17-PASS` means the frozen attribution and held-out procedure is complete and reproducible; it does not mean every
prediction is within 30% or that ACTINV is generally more accurate than another product. One append-only documented
repair round makes an otherwise passing close `P17-CONDITIONAL`. A second repair, a post-unseal unamended change, an
unaccounted mismatch or an unmet required gate closes `P17-FAIL` and moves unfinished work to a separately frozen
successor. P18 may address only cause classes demonstrated here and must freeze its own acceptance evidence before a
production change.
