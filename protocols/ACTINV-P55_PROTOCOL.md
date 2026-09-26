# ACTINV P55 Protocol — Qualified Inverse: Irradiation-History Estimation
# with Nuclear-Data Covariance

Sealed under standing rules 1–7. Hash of this file is recorded in
`results/g0_p55_seals.json`; any change after G0 is an append-only amendment.

## Goal

P23's `actinv reverse` estimates irradiation-history multipliers from
measured activities — scalar WLS or per-segment Lawson–Hanson NNLS on the
exact linear trace-regime sensitivity matrix, with standard errors from the
measurement-noise term alone. That is the same posture every unfolding code
occupies: the response matrix is treated as truth, and the only uncertainty
entering the inversion is counting error on the measurements.

That posture is wrong, and nobody has the machinery to do it right. The
sensitivity matrix A is itself uncertain under the propagated nuclear-data
covariance — and its columns (responses at different nuclides/steps) are
correlated through the shared XS covariance. Dosimetry unfolding (SAND-II,
STAYSL, LSL-M2, FISPACT's adjustment path) substitutes ad-hoc response
uncertainties or ignores them outright; the estimated flux history inherits
no honest band, and the *correlation structure* between segment estimates —
which is where the information actually lives — is emitted nowhere.

P55 delivers the qualified inverse: the estimated irradiation history under
generalized least squares with `C = C_meas + C_model`, where C_model is the
emitted cross-response covariance `J_iᵀΣJ_j` over the requested measurements
(the same sparse-collapse machinery as P53, now over response pairs in a
single cell/run — no new solver path). The output carries the full posterior
covariance on the segment multipliers, the posterior correlation matrix, an
identifiability certificate (which segments are resolvable, which are
degenerate, the effective dimension), a chi-squared consistency ledger, and
the dominant sensitivity contributions per segment — the VoI content that
tells an experimenter which measurement to buy next.

## Inputs

- **Problem document**: an `actinv-spec-1` run spec or `actinv-mesh-spec-1`
  limited to a single cell — the forward model, as in `actinv reverse`.
- **Measurements**: `actinv-reverse-input-1` as in P23 —
  `{step, nuclide, activity_Bq_per_g, sigma_Bq_per_g?}`. Under the qualified
  mode `sigma_Bq_per_g` is **required** on every measurement (unit weighting
  is rejected, not silently applied), and every measurement's nuclide must be
  declared in `uncertainty.responses` as `activity:<nuclide>` — the inverse
  claims are only as honest as the inputs. A nuclide absent from one
  segment's step inventory is a *causal zero* (that irradiation happens later
  or makes none); it is an error only when no segment produces the measured
  nuclide at all.
- **Covariance sidecar + activation library**: resolved from the spec with
  sha checks, same binding discipline as `export-r2s-joint`.

## Command

```
actinv reverse-qualified PROBLEM.json MEASUREMENTS.json OUT.ndjson
```

A new command name rather than an extension of `reverse`, because the output
document, the covariance requirement, and the classification semantics are
all new; `reverse` stays the point-estimate lane. The qualified mode is
always segmented — a one-segment schedule degenerates naturally to a scalar
normalization with the same posterior machinery.

## Emitted document — `actinv-reverse-qualified-1`

NDJSON: header (input/measurements/spec/covariance shas, coverage scope,
declared statistical model, resolvability thresholds), one `sensitivity`
record per (measurement, segment) carrying the covered-position response
sensitivity map, one `estimate` record per irradiation segment, one
`posterior` record (covariance + correlation matrices + eigenstructure),
one `identifiability` record, one `consistency` record (chi-square, per-
measurement pulls).

Per segment: multiplier, posterior σ, posterior marginal interval at the
declared confidence, resolvable/degenerate flag, and the top three
sensitivity contributions driving its estimate.

## Mathematics

Forward model in the trace regime: predicted activity of measurement i is
`(A f)_i` where column k of A is the unit-fluence response at step k — the
P23 columns, now carrying covariance.

1. **Response covariance**: for every ordered pair (i,j) of measurements,
   `C_model[i,j] = J_iᵀ Σ J_j` — the sparse quadratic form over the shared
   nuclear-data covariance, exactly the P20/P53 collapse restricted to one
   spectrum. Diagonals must equal the emitted per-response σ² — an internal
   exactness anchor.
2. **Total covariance**: `C = C_meas + C_model`, C_meas diagonal with the
   declared measurement σ (a measurement without σ is rejected).
3. **Estimate**: GLS normal equations `(Aᵀ C⁻¹ A) f = Aᵀ C⁻¹ y` with
   nonnegativity enforced by Lawson–Hanson-style active-set projection on the
   *posterior* objective (the P23 NNLS routine reused with W = C⁻¹). Posterior
   covariance `C_post = (Aᵀ C⁻¹ A + Λ_prior)⁻¹` on the free coordinates, where
   Λ_prior is an explicit weak prior (declared: 1e-6 × scale, enough to keep
   degenerate directions finite and labelled rather than exploding).
4. **Identifiability**: eigendecomposition of the normalized posterior;
   a segment is `resolvable` when its marginal posterior σ / estimate is
   below a declared threshold AND its posterior correlation with every other
   segment satisfies |ρ| < ρ_max (declared); otherwise `degenerate` with the
   offending correlations named.
5. **Consistency**: chi-square on GLS residuals, plus per-measurement pulls
   `(y_i - (Af)_i)/sqrt(C_ii)`; flagged when |pull| > 3.

## Gates

- **G0** — seal.
- **G1 mechanics** — synthetic fixture (P53 fixture machinery): emission on a
  run spec with planted measurements; structural fields; rejection legs
  (unbanded spec, measurement missing sigma, measurement nuclide not in the
  uncertainty responses, singular segment set).
- **G2 exactness** — independent re-derivation of every emitted quantity on
  the synthetic fixture: the design matrix, first-pass NNLS point (by active-
  set enumeration, not the Lawson–Hanson code path), C_model via the ported
  sparse collapse, the whitened GLS solve, posterior covariance/correlation,
  chi-square and pulls, Kalman gains, and identifiability flags — all at
  machine precision. A degenerate two-identical-segment leg must emit a
  finite ridge-regularized posterior and mark the degeneracy.
- **G3 demonstration** — the corpus activation spec (P51's FNS iron run,
  reused): measured activities synthesized from the banded forward result at
  declared σ, history recovered, per-segment posterior emitted. The demo
  claim: the qualified inverse returns a flux history *with its honest
  correlated band* — the artifact no unfolding code produces.
- **G4 determinism** — byte-identical modulo wall_time_s.
- **G5 independent checker** — re-derives C_model entries, the GLS solve, the
  posterior covariance, and the classification from the raw inputs; planted
  mutations in every emitted field class must be detected. Plus a table
  control for the identifiability thresholds.

## Explicitly out of scope

- Shape estimation (energy spectrum unfolding). P55 estimates *schedule
  multipliers* on a declared spectrum — spectrum-shape unfolding is a separate
  (much harder, much heavier) problem; parked.
- Burnup-regime inversion (nonlinear). Trace regime only, as in P23; a
  nonlinear inverse needs an adjoint and is a different phase.
- MCMC/full posterior sampling. The qualified inverse is linearized-Gaussian;
  the protocol states that class explicitly. MCMC remains a possible later
  refinement, not a silent substitute.
- Whole-geometry multi-cell inversion (needs the mesh-inverse problem; the
  single-cell/run scope is the honest first surface).

## Cost statement

Linearized inverse — the approved medium-compute class. Forward solves: k+1
(same as P23 --segments). Covariance collapses: m(m+1)/2 sparse quadratic
forms over the covered parameter set — at corpus scale (≈4k covered rows) a
pair is seconds; m≈10 measurements → ~45 pairs, minutes worst-case. Dense
linear algebra is m×m and k×k — trivial. No new solver path, no MCMC.

## Amendments

- **A1 (post-G0, mechanical)**: two repairs discovered while assembling the
  unit tests — `Measurement` gained a `declared_sigma` field so the
  sigma-required check fires on *absence* rather than on the coincidental
  `σ = 1.0` value (previously a genuine σ=1.0 measurement would have been
  rejected as undeclared), and two unit tests were added for the Cholesky
  factor and the posterior-precision identity. The sealed artifact sha for
  `core_reverse_module` drifts accordingly; the checks themselves are the
  same class the seal already bound (mechanical repair + test coverage).
  Also: the checker gained a `SHARED_COV` cache — the J-independent sparse
  collapse is built once and reused across the mutation legs (behavioural
  change in wall time only; the corpus collapse dropped from ~45 min to
  ~7 min because the earlier process rebuilt it once per mutation leg).
