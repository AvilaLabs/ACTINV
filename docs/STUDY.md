# ACTINV study document (`actinv-study-1`)

One versioned study document declares a case population: a shared library/decay context and a
materials × spectra × schedules grid. `actinv study build` expands it deterministically into
`actinv-spec-1` files plus a manifest; `actinv study run` executes the population through the same
solver path as `actinv run` and writes `study_record.json`. Users author the study document; they
never author case specs or Core JSON by hand.

Unknown fields are errors. The fields `robustness` and `spatial_handoff` are
recognised and refused with `family_not_qualified` (delivered by P30 and P32
respectively); they are never silently ignored. `refinement` is the ACT-REFINE-01
family, qualified by P29 within the demonstrated envelope below.

```json
{
  "study": "actinv-study-1",
  "study_id": "fns-fe-screen",
  "template": {"id": "activation-screen", "version": 1},
  "library": {"path": "actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz"},
  "decay": {
    "primary": "actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat",
    "fallback": "actinv-data/v1.1.0/decay/jeff-3-3_decay.dat"
  },
  "cases": {
    "materials": [
      {"name": "fe", "composition": {"Fe": 100.0}},
      {"name": "fe_co100wppm", "composition": {"Fe": 99.99, "Co": 0.01}}
    ],
    "spectra": [
      {"name": "fns_709", "spec_ref": "examples/fns_fe_5min.json"},
      {"name": "irdff_709", "flux_file": "assets/irdff_709.flux", "descending": false, "total": 1.116e10}
    ],
    "schedules": [
      {"name": "pulse_5min", "steps": [{"dt": "300 s", "flux": 1.0}]},
      {"name": "cont_1d", "steps": [{"dt": "1 d", "flux": 1.0}]}
    ]
  },
  "cooling_times_s": [0.0, 86400.0],
  "responses": ["total_activity_bq_per_g", "decay_heat_w_per_g",
                "photon_source_per_group", "inventory_per_nuclide",
                "total_atoms_per_g"],
  "comparison": {
    "axes": ["material"],
    "decision_rules": [
      {"id": "impurity-screen", "kind": "ratio_band",
       "response": "total_activity_bq_per_g", "band": [0.5, 20.0]}
    ]
  }
}
```

## Semantics

- **Expansion** — case ids are `{material}__{spectrum}__{schedule}`, materials outermost and
  schedules innermost in declaration order. `spec_ref` takes the spectrum block of an existing
  `actinv-spec-1` file; `flux_file` reads whitespace-separated group fluxes; `flux_per_group` is
  inline. Exactly one form per spectrum. Spectrum paths resolve against the study file's
  directory; library/decay paths behave exactly as in `actinv-spec-1` (literal paths, resolved the
  same way `actinv run` resolves them).
- **Cooling** — `cooling_times_s` are absolute post-shutdown times; each becomes a flux-0 step
  after the irradiation steps. `0.0` adds the shutdown-time response without a step.
- **Determinism** — `study build` output (manifest + spec bytes) is identical for identical study
  bytes and resolver inputs; the manifest records each spec's SHA-256.
- **Population accounting** — `study run` records every declared case as `executed`, `failed` or
  `contract_gap` with its spec digest, output digest, wall time and per-time responses. Cases are
  never dropped; zero predictions and undefined responses are named in `undefined_responses`.
- **Comparison** — `axes` names the axes that vary within a slice; cases are grouped by the other
  axes and compared at every shared time point on the scalar response. `within_rel`/`max_rel` bound
  the maximum relative spread, `min_rel` bounds the minimum, `ratio_band` bounds the max/min ratio,
  `rank_equal` requires the response ordering to match `expected_order` (case ids or axis values).
  A group containing a non-executed or metric-undefined case leaves its rule `undefined`.
- **Evidence kinds** — every record carries `evidence_kind` (`fresh` when executed now) and
  per-case artifact digests (`verified` bytes). The record's `qualification` is `unqualified`: the
  study layer certifies completion and accounting, not scientific qualification.
- **Templates and revocation** — `template.id` names the construction template. A revocations file
  (`--revocations FILE`, `{"revoked_templates": [...]}`) refuses new builds on revoked ids
  (`template_revoked`); historical records are never edited.
- **Limits** — the population cap is 1024 cases (`study_too_large`). The response vocabulary is the
  five qualified responses above; anything else is `family_not_qualified`.

## `refinement` — ACT-REFINE-01 (qualified by P29)

A `refinement` block declares per-(response, time) numerical criteria. Each criterion-bearing
case is re-solved at reference settings (`prune: none`, `bmin_atoms_per_g: 0`, `cram_order: 48`,
`mode: coupled`), the declared-vs-reference difference is compared against the declared bound,
and the error is decomposed per component:

```json
"refinement": {
  "criteria": [
    {"response": "total_activity_bq_per_g", "time_s": 0.0, "rel": 1e-6},
    {"response": "decay_heat_w_per_g", "time_s": 0.0, "rel": 1e-6, "abs": 1e-12}
  ],
  "resource_limit_runs": 4
}
```

Criterion verdicts: `satisfied`, `unmet`, `unestablished`. Near zero (`|reference| < 1e-6`)
the `abs` bound applies and a rel-only criterion is `unestablished` — never a silent pass. When
a criterion is unmet the runner escalates the declared spec one ladder step at a time
(`bmin → 0`, `prune → none`, `cram_order → 48`, `mode → coupled`), re-checking against the same
reference, until satisfied or `resource_limit_runs` is exhausted — then `unmet` with
`resource_limit_reached`. Error components are reported individually: `solver_time_integration`
and `population_pruning_and_mode` are empirically estimated (CRAM order and pruning variants);
`processing_collapse` is bounded at the P25c/P28 measured tolerances. Computational error,
nuclear/input uncertainty and predictive discrepancy remain separate: these criteria cover the
first only.

## `robustness` — ACT-ROBUST-01 (qualified by P30)

A `robustness` block runs `samples` perturbed solves per case alongside the nominal solve, with a
fixed `seed` (repeatability only — not convergence evidence):

```json
"robustness": {
  "samples": 16,
  "seed": 6191152,
  "channels": {
    "cross_section_mf33": true,
    "flux_rel_std": 0.05,
    "composition_rel_std": {"Co": 0.20}
  },
  "covariance": {"path": "...", "sha256": "..."},
  "responses": ["total_activity_bq_per_g"]
}
```

Channels: `cross_section_mf33` draws correlated perturbations of the spectrum-collapsed
cross-section covariance over the active library rows. The collapsed absolute covariance is
converted to the *relative* covariance `Σ[i,j]/(σ_i σ_j)`, symmetrized, and spectral-factored on
its nearest positive-semidefinite projection (cyclic-Jacobi eigendecomposition, negative
eigenvalues clipped — the clipped mass is reported as
`negative_eigenvalue_mass_clipped_relative`, never a diagonal ridge and never an independence
fallback). Each parameter's perturbation is a mean-preserving lognormal factor
`exp(F·z − ½·diag(FFᵀ))`, positive by construction.
`flux_rel_std` perturbs the total flux normalization with the same mean-preserving lognormal
convention; `composition_rel_std` perturbs named
element weight percents the same way, then renormalizes to the declared total. Composition sums are
preserved; correlations are preserved through the clipped spectral factor. Per-sample specs and
outputs are persisted under each case directory
(`rob_<i>.json`, `rob_<i>.out.json`).

The record reports per-response `mean`/`std`/`ci95_half_width`/`sampling_error_std`, the
`covered_rows`/`uncovered_rows` split (MF=33 coverage only — uncovered rows are named by row
index, never silently zero-uncertainty), failed-sample counts, and `truncated_by_resource_limit`.
`local_vs_nonlinear` compares the sampled spread against the P11 first-order propagation per
(response, time); `channel_attribution` reports isolated per-channel variances plus the
unexplained interaction remainder; `pathway_view` names the dominant nuclides and their
production legs per cooling time with the unattributed `pathway_closure` remainder.

## Execution efficiency (P31)

The runner shares prepared nuclear data across cases and samples whose specs carry the same
data-and-option signature (library, decay, photon response, fission yields, projectile,
spectrum shape, temperature, uncertainty/radiological/damage/self-shielding options). The
collapsed activation library is flux-bound, so two spectra are two signatures; material
composition, schedules and `rate_scale` are runtime inputs and share one prepared run. The
record reports `prepared_runs` (contexts constructed) and `prepare_wall_s`.

The record is written after every case (`status: "partial"` until the campaign completes), and
`study run` into an existing output directory resumes: a case is skipped only when its recorded
spec digest, output digest and every robustness sample artifact re-verify byte-for-byte against
the files on disk — skipped cases are listed under `resumed_cases` with `evidence_kind:
"resumed"`. A torn record, deleted artifact, corrupted output or edited study re-executes the
affected cases; nothing is trusted on the strength of a record entry alone.
A sample spread is a sensitivity over the declared input distributions — it is not a domain
bound, a rigorous confidence interval, or an evaluation comparison.
