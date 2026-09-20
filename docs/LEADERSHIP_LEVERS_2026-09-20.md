# Leadership levers — analysis and scoping (2026-09-20)

Where ACTINV stands after the CB3 identical-data campaign, and what it
would take to go from competitive to best-in-class. Written after the
decay-state-resolution work (commit `8ac697f` and the fallback/loose
extension in progress); numbers quoted are the post-fix CB3 values.

## Why the identical-data margin is capped

CB3 compares ACTINV and FISPACT-II on the same TENDL-2017 data. With the
same cross sections in, the C/E is dominated by XS evaluation quality and
measurement uncertainty, not the solver. Measured ceiling: 130/132
experiments already agree within 30% between the two codes; median pooled
|log C/E| is 0.1040 vs 0.1053. The remaining differences are state-handling
defects, and four classes were found and fixed (isomer-product mapping,
coverage scope, product-only isomers, cross-library state numbering). A
*substantial* accuracy lead on identical inputs would require the incumbent
to have large systematic defects — the well is nearly dry.

A durable "best bar none" position therefore has to come from axes where the
architectures genuinely differ: qualified uncertainty campaigns, current
evaluated data, validation breadth, complete spatial workflows, and
provenance/diagnostics.

## Lever 1 — Qualified uncertainty at campaign scale (P30-CONDITIONAL)

**Current machinery** (`crates/actinv-core/src/uncertainty.rs`): hashed
covariance input, first-order propagated variance, per-channel decay and
fission-yield sensitivities, response bands with per-channel coverage
accounting, plus a sampling verification control. `build-covariance`
produces collapsed covariances.

**Conditions recorded at closure** (`results/verdict_p30.json`), with
implementation status as of this session:

- ~~Collapsed covariance required a nonzero diagonal ridge for correlated
  sampling, and a substantial fraction of draws clamped nonpositive.~~
  **Implemented:** the ridge ladder and Cholesky/diagonal fallback are
  replaced by a cyclic-Jacobi eigendecomposition of the *relative*
  covariance, eigen-clipped to the nearest PSD; draws are mean-preserving
  lognormal factors `exp(F·z − ½·diag(FFᵀ))` — never nonpositive, never
  uncorrelated. The clipped eigenvalue mass is ledgered as
  `negative_eigenvalue_mass_clipped_relative`. Flux and composition
  channels use the same lognormal convention.
- ~~Local-vs-nonlinear comparison rests on a root-sum-square
  approximation~~ **Implemented:** `activity.total` is now a first-class
  uncertainty response propagated as sᵀΣs over the full covariance (spec,
  snapshot, tangent, and first_order_std all updated); the per-nuclide RSS
  combination is gone.
- MF=33 covariance coverage is partial — uncovered active rows carry no
  declared uncertainty. *Data-bound:* already ledgered per-band
  (`uncovered_library_rows`, `uncovered_decay_constants`,
  `uncovered_yield_products`); a coverage summary is emitted per step.
- Flux and composition channels exist in the robustness sampling layer;
  `self_shielding` + `uncertainty` remains rejected in the spec.
- No blind experimental validation — the FNS suite is the closest
  available blind-ish set; a per-experiment uncertainty-coverage score
  (fraction of measurements inside the propagated band) is locally
  computable once channels exist.

**Campaign layer** (P31-CONDITIONAL exists): prepared-run amortization,
streaming, resume-by-digest are measured. The remaining step toward
"qualified campaign" is wiring a sampling driver over the robustness spec
knobs with complete population accounting and the per-channel coverage
table above.

## Lever 2 — Current evaluated data (in flight)

TENDL-2017 rebuild with `--decay --decay-fallback` is running; TENDL-2025
follows (2850 files, ~10 h). The fallback/loose-resolution work recovers
six previously homeless isomer states (Sb-120m, Cu-68m, Cs-135m, Dy-147m,
Hf-178n, Au-189m) — 363 synthetic emissions collapse to ~9 genuinely
homeless states. If TENDL-2025 evaluations are better, ACTINV-on-current-
data legitimately beats everything frozen on TENDL-2017 — a *product*
claim about shipped data quality, not a solver claim.

## Lever 3 — Validation breadth

On-disk non-FNS material is thin: `conderc-fission` holds only two U-235
FISPACT inputs; `p17-irdff` holds IRDFF-II reference fields (spectra, not
activation outcomes); the FNS corpus itself is decay-heat only in the
harness. Real breadth needs public families fetched and frozen:

- FNS **activity** measurements (same corpus files may carry them — the
  `.out`/`.exp` pair structure suggests heat only in the current harness;
  check for activity columns before looking elsewhere).
- JAERI/FNS decay-heat publications (the FNS set's origin).
- SINBAD shielding benchmarks (geometry + dose — doubles as the external
  geometry P32 lacks).
- FISPACT-II published validation suites (ITER-relevant).

Each family needs frozen denominators and eligible populations before
scoring — same discipline as CB3.

## Lever 4 — Spatial R2S handoff (P32-CONDITIONAL)

The chain is *executed* (`controls/g1_p32_chain.py`, `results/g1_p32.json`:
neutron tally → flux import → 64-cell mesh activation → distributed
`openmc.stats.Box` sources → OpenMC photon transport → `openmc.deplete`
comparator at 2.14e-7 median deviation). Recorded conditions and their
local discharge paths:

- *Self-produced geometry only* — needs an external benchmark geometry;
  SINBAD is the natural source.
- *Photon leg is a flux proxy, not dose* — fold the photon flux tally with
  published fluence-to-dose coefficients (ICRP/ANSI H*(10)); purely local
  post-processing of the existing statepoint.
- *Tally statistical error not propagated* — carry the neutron tally
  std-dev into the activation comparison band; engineering only.
- *MCNP export remains point-at-origin* — MCNP SDEF distributed-source
  emission is implementable but unverifiable without an MCNP install;
  keep documented as placeholder.

## Lever 5 — Residual same-data gaps (in flight)

The Sb-2000 uniform gap resolved as a *sixth defect class*: cross-library
level-scheme disagreement (ENDF calls Sb-120m 151 keV/LIS 4; JEFF and
TENDL call it 200 keV/LIS 6). Fixed via fallback-decay resolution plus a
sole-candidate loose-ELIS tier. Remaining tail is dominated by shared
hard cases (In, Tb, Rh, Bi, Os, Na) where both codes miss — those are
nuclear-data or measurement limits, not solver defects.

## Ranking

1. **Lever 2** is running — cheapest real gain, watch it land.
2. **Lever 1** has the highest ceiling: it converts the measured speed
   advantage into a *capability* competitors don't offer at campaign
   scale, and most of its conditions are locally dischargeable.
3. **Lever 4** conditions are mostly local engineering on an executed
   chain.
4. **Lever 3** is real but needs external data fetches.
5. **Lever 5** converges — each fixed class shrinks the tail further.

Honesty boundary: none of this produces a blanket "better than FISPACT"
claim. What it can produce, all locally: same-or-better identical-data
accuracy, a qualified-uncertainty workflow at campaign scale, an executed
distributed R2S path with dose and error propagation, and a data pipeline
that ledgers every defect class found.
