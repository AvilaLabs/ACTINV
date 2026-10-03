# E2 data audit — preparatory plan

**State: draft only; not opened, hashed, or authorized for evidence.** This note prepares the demand-led E2
candidate in `docs/ROADMAP.md`. Do not produce a measured audit report, add production code, or treat this plan as a
phase protocol until P105 has completed its successor gates and its GitHub Actions run is green. P103 and P104 are
historical FAIL closures and cannot become green retroactively. E2 must receive its own reviewed,
versioned protocol and G0 seal before any result is generated. All prior scientific verdicts remain unchanged.

## Proposed scope

Deliver a deterministic `actinv data audit` report that reuses the library builder's fail-closed source checks,
without writing or publishing an activation library. Report a record for every selected input file: source path
relative to a declared corpus root, source SHA-256, projectile, parse/build-check outcome, stable defect-class IDs,
and references to existing per-file builder ledger entries where present. Preserve failures and exclusions in the
denominator. This is a diagnostic inventory; it does not repair files, alter a held defect, change runtime input
selection, or authorize contacting an evaluator.

The minimum frozen populations should be:

- Eight TENDL-2017 gamma files named in `docs/defects/tendl2017-gamma-threshold-and-photofission-encoding.md`.
  This validates the known MF=3-above-threshold and MF=10 `IZAP=0` classes against the current `--profile tendl`
  path and the documented fail-closed default behavior.
- All 2,850 TENDL-2025 neutron, 2,850 proton, and 2,850 gamma source files, tied to the exact source manifests and
  builder options of the existing ledgers. Include expected build failures as first-class outcomes; do not silently
  omit them or reinterpret them as audit pass.

An optional later extension to deuteron, alpha, EAF, or non-activation builders is out of scope for E2 unless a
protocol amendment authorizes it. Do not bundle P18b, P25 qualification, P43 uncertainty, or waste-rule questions
into the audit.

## Existing builder checks and defect controls

The source construction path to reuse or expose as a no-library audit entry point is:

- `crates/actinv-data/src/normalize.rs::normalize_tape` applies only the selected normalization profile and emits
  ordered normalization ledger entries.
- `crates/actinv-data/src/activation.rs::parse_evaluations` parses and validates ENDF sections and identities.
- `crates/actinv-data/src/builder.rs::build_source` pins the source hash, normalizes, parses, identifies format and
  projectile, and runs the per-evaluation checks.
- `crates/actinv-data/src/builder.rs::build_evaluation` contains the builder's semantic validation and transforms,
  including the emitted-state and unsupported-shape checks. It returns rows and a `TargetIndex` ledger. A future
  audit entry point may call this path and discard rows, but must not produce or write a library archive.
- `crates/actinv-data/src/builder.rs::build_library` is the full archive path and should not be invoked by the E2
  audit command. The audit must retain the same fail-closed errors and `continue_on_error` accounting while emitting
  only a report.

Existing controls and known classes:

- `controls/check_p94.py` and `controls/p94_g5_actinv.py` check the eight-file TENDL-2017 gamma corpus, profile
  application, per-target ledger lines, and the known 2025 gamma build identities. `controls/p94_g4_endf.py`
  supplies an independent ENDF reader and threshold-extension implementation; `controls/p100_g4_selftest.py`
  covers synthetic threshold cases.
- `crates/actinv-data/src/builder.rs` tests around `p100_threshold_extension_*` and
  `gamma_zero_izap_photofission_*` cover threshold extension and the narrow photofission sentinel shape.
- `docs/defects/tendl2017-gamma-threshold-and-photofission-encoding.md` records eight TENDL-2017 source hashes,
  the two named defect classes, current dispositions, and the fact that the defects remain held. The audit must
  report the observations; it must not send or submit them.
- Existing P94/P98/P101 result records provide measured gamma build and independent-collapse references. Existing
  P10/P25/P25b/P25c records provide full neutron/proton corpus and build-failure references. E2 should compare only
  like-for-like corpus, builder fingerprint, normalization profile, group set, temperature, and catalog inputs.

The checker should independently recompute source hashes and outcome/defect counts from the pinned report rows; it
should not call the production classifier to verify its own output. Plant mutations for omitted file, duplicated
file, altered source hash, rewritten error, reassigned defect class, and omitted build-failure record. Repeated runs
over the same frozen inputs must yield byte-identical logical reports; volatile timing and absolute paths belong in
separate metadata or are excluded from the deterministic digest.

## Provenance candidates to bind at G0

Source files live outside Git and must remain there. Candidate roots and manifests observed in this checkout are:

- TENDL-2017 gamma subset: `/home/connoravila/nuclear-data/tendl-2017/files/g/` and
  `/home/connoravila/nuclear-data/tendl-2017/staging/TENDL-g-2017.subset-manifest.json` (8 files). The originating
  archive identity is also recorded in `docs/defects/tendl2017-gamma-threshold-and-photofission-encoding.md` and
  `protocols/ACTINV-P94_PROTOCOL.md`; G0 must verify the exact current file hashes, not rely on abbreviated hashes.
- TENDL-2025: `/home/connoravila/nuclear-data/tendl-2025/files/{n,p,g}/` (2,850 files each) and
  `/home/connoravila/nuclear-data/tendl-2025/staging/TENDL-{n,p,g}.manifest.json`. Each manifest records a
  `file_manifest_sha256`; G0 must bind the full manifests, exact selected file list/hashes, and any working-file
  substitutions separately.
- Existing TENDL-2025 builds and indexes are outside Git under
  `/home/connoravila/nuclear-data/tendl-2025/builds/full/`. Candidate indexes include
  `neutron.n.p10_index.json` and `proton_index.json`. The current gamma indexes and candidate verdict records are
  kept in the historical P94/P98/P101 work area and/or checked-in `results/p94_verdict.json`,
  `results/p98_verdict.json`, and `results/p101_verdict.json`. At G0, resolve each exact artifact path, hash, and
  builder/options identity from the authoritative records before selecting a comparison baseline.
- Relevant in-repository authorities include `docs/DATA.md`,
  `results/g0_p25c_manifest_tendl_2025_n.txt`, `results/g0_p25c_seals.json`,
  `protocols/ACTINV-P10_PROTOCOL.md`, `protocols/ACTINV-P25_PROTOCOL.md`,
  `protocols/ACTINV-P94_PROTOCOL.md`, `protocols/ACTINV-P98_PROTOCOL.md`,
  `protocols/ACTINV-P100_PROTOCOL.md`, and `protocols/ACTINV-P101_PROTOCOL.md`.

At opening, verify current acquisition/license provenance in `docs/DATA.md` and any applicable archive terms.
Record source URLs, archive and extracted-file manifest hashes, file counts, source dates, builder commit/fingerprint,
exact check options, expected build indexes and any exclusions. Do not copy nuclear-data inputs, generated bulk
libraries, or outside build indexes into Git.

## Gate sketch (provisional; not frozen)

- **G0 — authority and seal:** register the new E2 protocol, checker, corpus manifests and exact baseline IDs before
  producing any report. Reconcile current file trees against the sealed manifest; source drift or an unavailable
  baseline stops the phase rather than changing the population.
- **G1 — minimum mechanics:** use synthetic ENDF tapes for one clean case, each known TENDL-2017 defect class,
  malformed header/section cases, and known fail-closed shapes. Re-derive normalization and outcome rows
  independently. This gate proves report semantics before the complete audit.
- **G2 — known actual gamma controls:** audit only the eight hash-pinned TENDL-2017 gamma files under the sealed
  profiles. Reconcile each file's error/normalization/target-ledger status against P94/P100/P101 records, preserving
  `default` refusal separately from profile-handled observations. Do not interpret a normalized build as a clean
  source evaluation.
- **G3 — complete current-rule release census:** audit all three 2,850-file TENDL-2025 populations without writing
  library archives; compare per-target successes, build failures, convergence/unsupported entries, and ledger class
  counts against the exact current builder indexes. Preserve any mismatch as a failure; never trim the population or
  redefine a source defect after reading the report.
- **G4 — independent closure:** a standalone checker reparses the pinned report, source manifests and baseline
  indexes, reconstructs all counts, verifies determinism and rejects all planted mutations. Closure records the
  report SHA, source identities, gate verdict, limitations, and a separate append-only ledger entry. No source
  submission or historical verdict change is in scope.

The final report must distinguish source defects, builder refusals, profile-normalized observations, and build-ledger
warnings. It must not label a profile-handled defect as absent. Existing P18b/P25/P24 verdicts and their wording stay
unchanged.

## Cost and resource envelope

The full population is 8,558 source files: 8 TENDL-2017 gamma plus 8,550 TENDL-2025 neutron/proton/gamma. The
largest recent complete builder timing in `results/g7_p10_builds.json` is the fresh 2,850-file TENDL-2025 neutron
build at 2:34:58, with recorded peak RSS about 2.33 GiB. The fresh TENDL-2025 proton build is 1:53.68; P94's full
2,850-file gamma build took 102.68 seconds with peak RSS about 2.39 GiB. These are reference measurements of full
library builds, not a claim about the proposed audit. The full audit must exercise per-evaluation validation and
collapse to establish builder outcomes and reconcile them against per-target build indexes; a source-only scan can
inventory file presence, hashes, and manifest consistency, but cannot prove parse/build outcomes, builder defect
classes, or index reconciliation. If E2 executes the same per-evaluation validation/collapse
path while discarding rows, budget up to roughly 2.7 hours for the largest single corpus and about 2.7 hours for
the sequential three-projectile audit until G1 profiling establishes a smaller bound. Avoid repeating complete
corpora: reuse only source/build evidence that matches the exact G0 hashes/options. Profile one representative
neutron, proton and gamma target before G3, with bounded memory and one job at a time. The final envelope must use
the workstation's enforced cgroup and repository temporary-directory policy; no uncapped fallback is allowed.

No computation, audit, builder, test, or solver was run in preparing this draft. Every gate, selected baseline, and
cost cap remains subject to protocol review after P105 has green CI-backed closure. The full-builder audit may cost
near the full three-corpus build envelope above; any source-only limited proposal must be separately scoped and
cannot be represented as satisfying this full E2 audit.
