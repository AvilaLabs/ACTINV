# ACTINV P24 — corrected benchmark re-validation

Opened 2026-09-14 after P25 closed `P25-FAIL` at commit
`acd476a9bb342d171583a2d2f881f1f13e4b6e46` (controls and desktop-builds workflows green on that commit).
P24 executes the roadmap's scheduled "corrected benchmark re-validation": it re-derives the three P17
measurement definitions that the committed post-unseal Amendment 1 got wrong, seals a fresh
genuinely-unread held-out partition from the same hash-pinned validation corpus, and re-scores through
unchanged scoring code after an audit that the scorer implements the corrected definitions. The consumed
P17 partition stays public diagnostic evidence. P24 changes no production physics, public interface,
default, packaged data or numerical threshold.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`, `P18-FAIL`, `P18b-FAIL`,
`P25-FAIL`. The 1.1.0 release hold stands unchanged; this phase produces evidence for the release
decision, not a release.

## Why this phase exists

P17 closed `P17-FAIL` under its unchanged procedural rule: its single post-unseal amendment froze
assumptions the held-out source then falsified. The preserved record `docs/P17_HELDOUT_VALIDATION.md`
documents three concrete falsifications:

1. **End-of-irradiation definitions.** Amendment 1 assumed a uniform 960-second power normalization for
   all EOI spectral indices. The held-out source does not support that assumption; the independently
   evaluated pulse limit with the publication's state-specific alias reduces the maximum discrepancy
   with the printed EOI spectral indices from ~100% to 3.58%, with most rows near 1%. A single
   guessed history cannot stand in for each case's recorded irradiation.
2. **Evaluated-state aliases.** Tables 25 and 36 abbreviate the evaluated `Ag109gm` response as
   `Ag109g`; the raw IRDFF evaluation carries that product at final-level identifier LFS=2 while
   ACTINV's processed decay-isomer selector uses ordinal 1. Amendment 1 did not freeze the
   source-specific alias, so the abbreviation was read as a ground-state response.
3. **Shielded-versus-bare treatment.** `bare` was read as infinitely dilute, but the source says
   shielding effects were treated rigorously for finite resonance foils without printing the
   correction factors. An unshielded current-archive fold differs from the published Ag-109 bare
   index by about 240%, proving those capture rows cannot be scored as dilute.

P17's failure is procedural and scientific: the evidence stands as observed, and the definitions may
not be repaired inside P17. P24 is the separately frozen successor that does the repair correctly —
definitions first, sealed partition second, scoring last.

## Frozen scope

P24 may:

- add controls, parsers for the fresh held-out tables, machine-readable evidence, checkers and
  documentation under `controls/`, `results/`, `protocols/` and `docs/`;
- add a corrected measurement-definition layer to the P17 scoring controls (`controls/p17_heldout.py`,
  `controls/g5_p17_heldout.py` lineage or a `controls/p24_*.py` successor), implementing exactly the
  frozen definitions below — and freeze that layer before the held-out read;
- run the released and candidate ACTINV paths and fold hash-pinned evaluation and spectrum archives.

P24 may not:

- edit `crates/`, Python bindings, schemas, production parsers, public examples, release artifacts or
  default data — this phase changes no production physics;
- re-score, re-map, re-partition or reinterpret the P18b/Rodrigo held-out population; its evidence and
  the P25 verdict remain untouched;
- re-seal or re-read the consumed P17 partition as blind evidence — it is diagnostic only;
- read any fresh-partition numerical row before the G3 unseal authorization;
- change a corrected definition, row mapping, metric, inclusion predicate or exclusion after the
  scoring freeze without an append-only amendment and a conditional verdict;
- tune TENDL, alter an experimental value, discard a difficult row, optimize a metric, claim total
  uncertainty, implement self-shielding or cover transport, or publish a package release;
- introduce `unsafe`, new runtime dependencies, `Arc`, `Mutex`, interior mutability, or
  cloning/allocation as borrow-checker workarounds in any committed code.

## Frozen authorities and provenance

The source corpus is identical to P17's and is rehashed at G0 rather than re-downloaded:

- Authors' primary-reference PDF (`https://arxiv.org/pdf/1909.03336`, 110 pages), SHA-256
  `ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f` — the frozen document for all
  measurement rows and the narrative statements the corrected definitions cite.
- Groupwise benchmark-spectrum archive `IRDFF-II_sp_g.zip`, SHA-256
  `544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52` — characterized spectra by MAT.
- 725-group IRDFF-II archive `IRDFF-II_g725.zip`, SHA-256
  `6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85` — independent official-data fold.
- Pointwise IRDFF-II ENDF archive `IRDFF-II_ENDF.zip`, SHA-256
  `225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db` — evaluated-state identities
  and pointwise folds.
- IRDFF-II decay-data archive `IRDFF-II_dd_ENDF.zip`, SHA-256
  `397f599ef6389ac84931faa31a8e1f7a1bf3ba684b4a22e92d628d4271699bd7` — half-lives for EOI
  reconstruction.
- Benchmark-field list PDF `NeutronBenchmarkFields-IRDFF-II.pdf`, SHA-256
  `93926f4a9937ef1314ebbaa29a11a638ad9d1b3abad08596b0db57ee6bc9c304`.
- P17 protocol SHA-256 `c1e2d2ef80ee91b63f7806ca2b93c1b49d8396f4dfacf8623fd1c2a623e17e2f`,
  Amendment 1 SHA-256 `8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0`, and the
  P17 seal/verdict records listed at G0.
- Signed release comparator v1.0.1 at `0332779401363d2f39722efe7a0b7218afcfb270`; its shipped
  TENDL-2025 709-group neutron NPZ `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`
  and index `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`.
- The P25-repaired candidate neutron activation artifact: a bounded build by the current production
  builder (the post-P25-repair code path, committed at the opening commit) over exactly the target set
  the IRDFF-II reaction catalog (paper Table 1, a diagnostic table) requires for the fresh partition —
  no full-corpus rebuild is implied, and the target list is derivable from diagnostic metadata alone.
  The built NPZ and index SHA-256 are recorded at G0; a target that fails construction is an explicit
  `construction_failed` ledger entry at scoring, never a silent skip. The v1.0.1 artifact is scored
  alongside on identical rows as the pinned baseline comparator.

## Frozen partition

Partitioning is by primary-paper table number, chosen from the table of contents and captions without
inspecting held-out numerical rows — the same rule P17 used. PDF page numbers are recorded for parser
addressing only.

**Open diagnostic and support (readable now):** Tables 18–20 (Cf-252 and U-235 diagnostic SACS);
the consumed P17 held-out partition Tables 21–25 and 36 (SPR-III, ACRR central cavity, high-temperature
Maxwellian) with all P17 ledgers and evidence; pure-support tables (16 filter dimensions, 21/24
operation records, 22 foil compositions, 5 field list); the consistency tables for consumed or
diagnostic fields (37–40); and the summary aggregation Table 47. None of these may again be called
blind evidence.

**Sealed held-out families** — unread until the G3 authorization:

| family | table(s) | field | spectrum MAT(s) | observable |
|---|---|---|---|---|
| F-MOLBR1 | 26 | Mol-BR1 Mark-III | 9020 | spectral index vs monitor |
| F-TRIGA | 27 | TRIGA-JSI filtered channels | 9041–9044 | filtered/unfiltered reaction-rate ratios |
| F-HMF001 | 28 | Godiva | 9101 | spectral index vs monitor |
| F-HMF028 | 29 | Flattop-25 | 9102 | spectral index vs monitor |
| F-IMF007 | 30 | Big-Ten | 9103 | spectral index vs monitor |
| F-PMF | 31 | Jezebel, Flattop-Pu, Thor | 9104, 9106, 9107 | spectral index vs monitor |
| F-FMR001 | 32 | IPPE-BR1 rodded assembly | 9110 | spectral index vs monitor |
| F-LEGACY | 33 | ISNF, CFRMF, Sigma-Sigma | 9004, 9005, 9007 | SACS or SI ratio |
| F-THERMAL-XS | 34 | thermal Maxwellian | — | σ0 comparison |
| F-THERMAL-I0 | 35 | resonance integrals | — | I0 comparison |
| F-ACRR-LB44 | 41 | ACRR LB44 bucket | 9013 | EOI activity → SI |
| F-ACRR-PLG | 42 | ACRR PLG bucket | 9012 | EOI activity → SI |
| F-ACRR-CDPOLY | 43 | ACRR Cd-poly bucket | 9011 | EOI activity → SI |
| F-ACRR-FREC2 | 44 | FREC-II external cavity | 9015 | EOI activity → SI |
| F-BEDN | 45, 46 | 9Be(d,n) at Ed = 16, 40 MeV | 9408, 9409 | production rate per target atom |

If a fresh table turns out at parse time to contain a second observable type or an extra field, the
rows keep their printed identity; the family attribution follows the printed field label, and any
unmapped structure is ledgered, never guessed. Families are never pooled.

## Frozen corrected measurement definitions

Each definition below must be hash-pinned to the source statements it derives from (paper page/section
or hash-pinned table) at G1, before any fresh numerical value is read. A definition the source does
not support produces a ledgered exclusion, never an inference.

**D1 — end-of-irradiation observables.** Where a held-out table prints the final measured observable
(spectral index, SACS, reaction-rate ratio, σ0, I0), that printed value is the measurement; no ACTINV
EOI reconstruction applies. Where a table prints raw measured activities (the ACRR bucket tables 41–44),
the experimental spectral index is reconstructed from the *case's own recorded irradiation* in the
source's operation records — pulse-limit saturation for burst operations, the stated per-operation
history for finite irradiations, the publication's stated baseline normalization where printed — with
half-lives from the pinned IRDFF-II decay archive. No uniform guessed irradiation time is applied. A
row whose required irradiation record is absent from the source is ledgered `undefined_eoi_history`.
The corrected reconstruction is demonstrated on the consumed P17 partition at G1: it must reproduce
the printed P17 EOI spectral indices within the rounding implied by the printed tables — the same test
that falsified Amendment 1, run as a definition gate on public diagnostic data.

**D2 — evaluated-state aliases.** Each publication reaction label maps to an evaluated product state
by the IRDFF-II evaluation's own MF=8/10 declared level structure, not by ground-state default: where
the evaluation declares a single product state, the label binds to that state regardless of its LFS
index (the `Ag109g`→LFS=2 case); where it declares multiple states, the printed suffix selects among
them by the paper's stated notation convention, and an unresolved abbreviation is ledgered
`undefined_state_alias` rather than silently bound. The complete alias table for every label appearing
in the fresh partition's captions and monitor lists is frozen at G1 as data with its source
justification per entry.

**D3 — cover and dilute validity.** A cover token other than the source's bare field label is
`unsupported_self_shielding`, unchanged. A bare-field row is dilute-scorable only where the source
supports the dilute reading: threshold reactions on thin foils in a characterized field are scored
dilute; capture (resonance-structured) rows in fields where the source states shielding corrections
were applied but not printed are ledgered `unsupported_self_shielding`. The reaction-class list and
the per-field statement each row's validity rests on are frozen at G1 with source citations. No
self-shielding, cover-transport or geometry model is implemented or approximated.

**D4 — monitor and mixture definitions.** Each family's monitor response follows the printed "label
and monitor" column or section statement; composite fission-foil mixtures use the source's printed
atom-fraction compositions (the P17 `rmleu`/`rmlpu`/`rmldu` rule carries forward unchanged for any
fresh family that uses those mixtures). A monitor that is itself unsupported makes dependent SI rows
unscored, ledgered with the monitor's exclusion reason.

## Frozen inclusion, metrics and attribution rules

Carried verbatim from P17:

- Every source row is preserved in a row ledger; a row is scored only when its experimental value is
  finite and positive, the incident particle is a neutron, the target/reaction/product maps without
  inference beyond the publication's own labels and the frozen D2 alias table, and the published
  spectrum/history supplies enough information to calculate the stated observable. Every unscored row
  carries the exact predicate and source identifier — nothing is dropped.
- Per family and calculation variant, with `r_i = calculated/experimental`: scored and unscored counts
  with reasons; geometric-mean C/E; median, population p90 and maximum `abs(ln r)`; within-10/20/30%
  fractions; published experimental uncertainty beside each row (never called total predictive
  uncertainty); per-row signed `ln(r)` and exact source identifiers. Population p90 uses NumPy's
  linear percentile convention.
- A material mismatch is a scored row outside the 30% band, or a control row outside its tolerance;
  each receives exactly one primary cause from `solver`, `chain-construction`, `processor`,
  `evaluation`, `decay-yield`, `measurement-definition`, `unsupported-model`, `unresolved`, with
  optional secondary causes and recorded evidence. `unresolved` is acceptable; an omitted row is not.
- Accuracy has no hidden pass threshold: a poor but valid held-out result is a successful measurement.
  What P24 gates is procedure — definitions frozen first, partition sealed, scorer unchanged,
  accounting complete.
- Attribution variants keep every other layer fixed: the pinned IRDFF-II groupwise fold is the
  independent official-data context beside the ACTINV production fold and the v1.0.1 baseline fold,
  all on identical rows and spectra. Different-data columns are labeled as such; no different-data
  number may be presented as a solver comparison.

## Minimum gate input and cost control

No P24 gate requires a TENDL rebuild or new external download. The minimum input is the pinned
IRDFF-II corpus, the v1.0.1 and candidate neutron artifacts, the spectrum archive MAT sections above,
and the compact controls. Jobs follow the workstation rules: one build/test/solver job at a time
inside the enforced systemd cgroup, `target/preflight-tmp` for temporaries, resumable for anything
expected to exceed ten minutes. No bulk data, PDF, generated library or cache enters Git.

## Gates

### G0 — opening, authority, partition seal

An independent control binds this protocol's SHA-256, the opening commit, every authority hash above,
the candidate and baseline artifact identities, and all prior verdicts asserted verbatim. It records
the partition table/page/MAT map and verifies that no committed file contains a numerical row from
the fresh partition (parser configurations committed to date must name only diagnostic table
numbers). Missing, duplicate, mismatched, or premature-value plants fail. G0 is committed, pushed and
green before G1 reads any source statement for definition derivation beyond the captions already
inspected.

### G1 — corrected definitions, frozen and validated on public evidence

Produce `results/g1_p24_definitions.json`: the frozen D1–D4 records, each with its source citations
(page/section/table and document hash), the complete alias table, the reaction-class and per-field
dilute-validity lists, and the monitor map. The corrected EOI reconstruction is executed against the
consumed P17 partition and must reproduce the printed EOI spectral indices within printed rounding —
recording per-row residuals, not just a maximum. An independent checker re-derives the diagnostic
reconstruction from source files without importing the definition module. After this gate the
definitions are frozen; a later change is an amendment and makes an otherwise passing close
conditional.

### G2 — scorer audit and freeze

Audit that the scoring controls implement exactly the frozen definitions: every fresh-table parser
produces only the frozen observable forms; metric code is the unchanged P17 semantics; inclusion
predicates name only frozen reasons; no diagnostic or held-out value reaches a definition, mapping,
metric or exclusion. Generated fixtures exercise each observable type, each cover/alias branch, the
pulse-limit and finite-history EOI paths, and the monitor-dependency rule. Mutation plants on
definitions, aliases, inclusion reasons and metrics fail. After G2, scoring code is frozen:
"unchanged scoring code" for the held-out read means the audited implementation, and a post-audit
change requires an amendment.

### G3 — diagnostic re-score and unseal authorization

Re-score the consumed P17 partition under the frozen corrected definitions as public diagnostic
evidence: every P17 row is accounted as scored or carries its frozen reason, with the falsification
rows (Ag-109 alias, bare-capture rows, EOI rows) landing on their corrected named outcomes. Publish
the diagnostic report, row ledger and checker; a green workflow on that commit is the sole
authorization to unseal the fresh partition.

### G4 — one-time held-out read and report

The fresh partition's tables are parsed exactly once through the frozen code: every source row
appears exactly once as scored or ledgered with a frozen predicate. The report shows all per-row C/E
values, family metrics, exclusions and cause-ledger entries, plus the IRDFF-II official-fold and
v1.0.1 baseline columns on identical rows. No post-read definition, mapping, metric or exclusion
change; one append-only repair amendment makes an otherwise passing closure conditional and a second
repair need fails the phase.

### G5 — independent closure

A checker importing no production, parsing or scoring module rehashes all inputs, repeats the
definition derivations, EOI reconstruction, C/E arithmetic, metrics and exclusion accounting,
verifies the seal and authorization ordering, re-verifies all prior verdicts verbatim, and rejects
planted mutations. Verdict to `results/verdict_p24.json`; the manifest is regenerated once at
closure.

## Closure interpretation

`P24-PASS` means the corrected measurement definitions were derived from hash-pinned sources before
any fresh value was read, a genuinely unread partition was scored once through audited unchanged
code, and the resulting blind C/E evidence is published completely — the P17 benchmark methodology
stands re-validated. It does not assert any accuracy figure, certify TENDL-2025, or lift the 1.1.0
release hold; the release decision remains the maintainer's and consumes this report beside the
`P25-FAIL` record. `P24-FAIL` preserves every artifact and ledger as public evidence exactly as P17,
P18, P18b and P25 did.
