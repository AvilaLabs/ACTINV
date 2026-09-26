# ACTINV P53 Protocol — Correlated Spatial Dose Bands (joint uncertainty across mesh cells)

Sealed 2026-09-25 under standing rules 1–7. Hash of this file is recorded in
`results/g0_p53_seals.json`; any change after G0 is an append-only amendment.

## Goal

P52 emits per-cell photon-source bands combined under two bounding rules —
independent (`√Σσᵢ²`) and conservative (`Σσᵢ`). Both are wrong as a *system*
statement: cells share the same underlying nuclear-data covariances, so their
bands are **correlated**. The exact joint variance over the covered MF=33
parameter set is computable — the machinery (`collapse_weighted_multi`, the
exact cross-spectrum collapse) already exists in `actinv-data`.

P53 emits that joint statement: a system-level photon-source band with the
exact propagated variance through the shared covariance structure, plus the
per-cell-pair correlation matrix that explains it. No incumbent shipping
tool does this.

## Inputs

- Mesh result NDJSON (`actinv-mesh-1`) — must carry per-cell
  `uncertainty.responses["activity:<nuclide>"].sensitivities` records
  (parameter identity = `library_row` + `spectrum` fields) and
  `photon_source.by_nuclide` at the emit step. The header's
  `certificate.canonical_flux` binds the flux file by sha256.
- Flux NDJSON (`actinv-flux-1`) — per-cell 709-group spectra (collapse
  weights). Verified byte-identical against `canonical_flux.sha256_declared`.
- Activation library NPZ + covariance sidecar NPZ — `cov.validate()` and the
  existing index `activation_library_sha256` check bind cov ↔ lib; lib sha is
  ledgered in the output header.

## Command

```
actinv export-r2s-joint MESH.ndjson FLUX.ndjson LIB.npz COV.npz STEP OUT.ndjson
```

## Emitted document — `actinv-r2s-joint-1` (one JSON object per line)

- **Header**: schema, binding shas (mesh file, declared-vs-computed flux sha,
  lib sha, cov sha), emit step, and a `scope` declaration: the correlated band
  covers the MF=33 cross-section channel only — the same channel and same
  coverage class the per-cell bands propagate. Decay/yield channels remain
  `not_evaluated` exactly as in the per-cell records.
- **Per-cell records**: identical to `actinv-r2s-source-1` cell records
  (nominal, σ_independent, σ_conservative, coverage) — reused verbatim.
- **Correlation record**: `rho` — the S×S per-cell photon-strength correlation
  matrix, `ρ_cc' = Cov(T_c, T_c') / (σ_c σ_c')`, with `Cov(T_c,T_c')` the exact
  cross-cell propagated covariance over covered parameters; cells with σ_c = 0
  emit `null` off-diagonal entries (diagonal 1.0).
- **Footer**: `cells`, `total_photons_s`, `sigma_independent` (√Σᵢσᵢ² over
  every banded nuclide-cell contribution), `sigma_conservative` (Σᵢσᵢ),
  `sigma_correlated` (√(Jᵀ Σ_joint J) — the exact banded-part variance),
  `coverage` totals (unbanded photon share, partially-unbanded cell count —
  same banded-only semantics as P52), `excluded_blocks` count, and
  `uncovered_row_count`. `totals_cover` declares the correlated σ covers
  banded MF33 contributions only.

## Math

For each cell c, the pooled joint sensitivity per (spectrum=c, covered
MF33 param p):

```
J_{c,p} = Σ_n (P_{c,n} / A_{c,n}) · s_{c,n,p}
```

over nuclides n that are banded (non-null σ) and have A_{c,n} > 0 and
P_{c,n} > 0; `s_{c,n,p}` is the emitted sensitivity of response
`activity:n` to collapsed param p under cell c's spectrum. Nuclides with
A=0 or P=0 contribute zero weight — skipped, not approximated.

Then `σ²_correlated = Jᵀ Σ_joint J` where Σ_joint is the joint multi-spectrum
collapse with the P20 exclusion rules applied identically (per-block
asymmetry and symmetric-eigen diagnosis on the assembled block; excluded
blocks contribute nothing; self-block exclusion cascades into joint blocks
that contain it).

Per-cell-pair: `Cov(T_c, T_c') = J_cᵀ Σ_{cc'} J_{c'}` over the cross block —
the same machinery restricted to two spectra.

The correlated σ is an *exact* propagated value, not a bound — it may sit
below `sigma_independent` where cross-cell covariances are negative; no
ordering vs the bounding rules is asserted.

## Memory

The full joint matrix (S·n_covered)² is not materialized. The collapse
assembles per-(target, MT, MT1) block matrices (small), applies exclusion
diagnosis identically to the dense path, and accumulates the quadratic form
block-by-block. Declared ceiling: G3's 8-cell corpus demo must run inside the
6 GB workstation envelope.

## Gates

- **G0 (seal).** Protocol + controls hashed; manifest snapshotted.
- **G1 (mechanics).** Synthetic mesh/flux/cov records on the P11 fixture
  library exercising: identical-spectrum degenerate case (correlated equals
  conservative for a single fully-correlated param), two distinct-spectrum
  cells, nuclides with null σ excluded from the joint but counted in
  coverage, zero-source cells emitting null ρ rows, binding-sha rejection
  (wrong flux file), malformed/missing-field rejection.
  `results/g1_p53_mechanics.json`.
- **G2 (exact value).** A 2-cell synthetic-covariance fixture where Python
  computes JᵀΣ_jointJ closed-form from the raw sidecar — agreement to a
  declared relative tolerance; planted excluded-block fixture → block
  skipped identically. `results/g2_p53_exactness.json`.
- **G3 (demonstration).** The P52 G3 8-cell corpus mesh → joint document
  emitted with σ_correlated, ρ matrix, all three totals, coverage;
  executed wall time ledgered. `results/g3_p53_demo.json` +
  `results/p53_r2s_joint.ndjson`.
- **G4 (determinism).** Two exports byte-identical (pure deterministic
  transform; no wall-clock fields). `results/g4_p53_determinism.json`.
- **G5 (independent checker).** `controls/check_g5_p53.py` re-derives
  σ_correlated, ρ, and all totals from the raw mesh ndjson + covariance
  sidecar (its own blockwise quadratic-form implementation, not the emit
  path's); rejects planted mutations. `results/check_g5_p53.json`.

## Amendments (append-only)

- **A1 — checker repair + reuse (control-layer only, no emit change).**
  During the first G5 corpus run two control defects surfaced: (a) the
  checker's collapsed-vector builder divided by a zero base-row σ without
  the Rust `vector_for_grid` guard (`base==0 → multiplier 0`, since the
  nonzero-row/zero-base case errors in emit and cannot appear in accepted
  output); (b) the reference collapse was computed inside the per-input
  comparison, forcing the ~20-minute sparse joint-covariance assembly to
  rerun for each planted-mutation leg. The checker is split into a
  J-independent `build_joint_covariance` pass computed once and a cheap
  per-input `evaluate`; the `jnz_keys` component prefilter was dropped so
  the built covariance is reusable for any mutation of J. Verified
  identical to the G2 closed-form reference on the 2-cell fixture
  (σ_correlated = 2.0999498475546367e-26 vs emitted 2.099949847554637e-26)
  before the corpus run. Fixture note: `controls/p53_fixture.py` (2-group
  library + a gamma-bearing Mn57 — the sealed P11 decay fixture carries
  only MT=457 energy totals and emits no photon lines) replaces the
  P11-only fixture originally implied by the G1/G2 text; response list
  unchanged.
- **A2 — checker kind-code and exclusion-ordering repair (control-layer
  only, no emit change).** The first full-corpus G5 run flagged
  σ_correlated +0.3% and a spurious `(390,102,103)` exclusion; root cause
  was in the checker, not the emit path: (a) the sidecar stores
  `ComponentKind` discriminants 8/9 (LB=8/LB=9 short-range), but the
  checker branched on literal `kind == 2`, so every LB=8 block was folded
  with the LB=9 variance formula — the wrong diagonal contributions made
  a genuinely borderline (λmin −3.4e-16 vs −1.2e-16 threshold) union
  block fail PSD when the emit's correctly-folded copy passes; (b) the
  checker removed excluded-block params *inside* the diagnosis loop,
  letting earlier verdicts damage later blocks — the emit diagnoses all
  blocks on the fully assembled map first and removes afterwards
  (two-phase). Both fixed; the corrected diagnosis reproduces the emit's
  verdict on the borderline block in isolation (kept, λmin −4.8e-17 ≥
  −1.2e-16) and exact G2 fixture values; the full corpus re-run is
  recorded in `results/check_g5_p53.json`.

