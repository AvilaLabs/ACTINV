# ACTINV-P30 protocol — uncertainty and scientific explanation

P30 qualifies ACT-ROBUST-01: a nonlinear sampling path beside the
P11-qualified first-order propagation, plus the accounting and
interpretation rules that keep it honest. The study document gains an
optional `robustness` block; a run gains an optional
`options.rate_scale` perturbation map. This protocol freezes the scope,
the population and the closure rule.

## Scope

- New spec field `options.rate_scale`: `{"<activation library row>":
  factor}` multiplying that row's collapsed reaction rate. It exists so
  sampled covariance draws can perturb the same rates the first-order
  path propagates; it is an input-declared perturbation, not a hidden
  numeric.
- New study block `robustness` (family `ACT-ROBUST-01`):
  `samples` (>= 2), `seed`, `channels` —
  `cross_section_mf33` (correlated draws on the spectrum-collapsed
  covariance over active library rows), `flux_rel_std` (normal draw on
  the total flux normalization, applied to every schedule step),
  `composition_rel_std` (per-element normal draws, renormalized so the
  composition still sums to the declared basis total; negative draws are
  clamped at zero and counted) — plus `responses` (qualified response
  names) and an optional `resource_limit` on total runs.
- Per case the study runs the nominal solve plus `samples` perturbed
  solves, reports per-response per-time sample mean / std / normal CI /
  sampling error (`std/sqrt(N)`), covariance coverage (active rows
  covered, rows absent — named, not silent), composition-clamp count,
  and failed samples counted individually.
- `rate_scale` outside a `robustness` study is a normal spec option and
  is reported in the run ledger.
- Correlations: where the collapsed covariance is block-diagonal or a
  block was excluded by the P20 defect rules, the draws fall back to the
  surviving submatrix; uncovered rows get factor 1.0 and are counted in
  `uncovered_rows`. Independence assumptions are stated in the output.
- Ranking/spread semantics: a spread across samples is a sensitivity
  statement about the supplied distributions; it is not a proved domain
  bound and not an evaluation comparison. Sampling error, covered
  uncertainty, missing covariance and failures are reported separately.

## Out of scope

- Fission-yield and decay-constant nonlinear channels (P11 propagates
  them first-order; sampling them requires perturbed decay/yield inputs
  — recorded as a follow-on, not silently absent).
- Any model-form, processing or predictive discrepancy. The sampling
  covers declared input uncertainty only.

## Frozen population (G1)

Two cases from the P27 smoke population, both in the qualified envelope:

- `fe__fns_709__pulse_5min`
- `fe_co100wppm__fns_709__pulse_5min`

`samples`: 16, `seed`: 0x5EED30. Channels: `cross_section_mf33` +
`flux_rel_std` 0.05 + `composition_rel_std` on the Co impurity
(fe_co100wppm case only). Responses `total_activity_bq_per_g` and
`decay_heat_w_per_g` at each emitted cooling time.

## Frozen controls (G2)

- `zero_variance`: `flux_rel_std` 0 and composition stds 0 with the XS
  channel off — every sample spec must equal the nominal spec exactly.
- `flux_linear`: flux channel only (XS off) on a pure-production
  impulse case — sample mean of atoms must equal nominal within
  sampling error, and the response must scale linearly with the drawn
  flux factor.
- `seed_reproducibility`: two runs of the same study with the same seed
  must produce byte-identical sample specs; different seeds must not.
- `coverage_accounting`: the number of sampled `rate_scale` factors
  equals the number of covered active rows; `uncovered_rows` names the
  rest.
- `composition_sum`: every perturbed composition still sums to the
  declared total after renormalization; clamped draws are counted.

## Frozen conformance probes (G3)

- `zero_samples`: `samples` 0 or 1 is refused at validation.
- `negative_std`: a negative `flux_rel_std` or composition std is
  refused.
- `unknown_channel`: an unrecognized channel name is refused
  (`deny_unknown_fields`).
- `missing_covariance`: `cross_section_mf33` with no usable covariance
  sidecar is a named gap, not a silent nominal run.
- `rate_scale_ledger`: a spec with `options.rate_scale` records the
  applied perturbation in the run ledger.

## Closure rule (G4)

PASS only if every frozen control holds and the full population records
sampling results with complete coverage/failure accounting. CONDITIONAL
if an amendment was used or a named gap/limitation changes downstream
scope. FAIL otherwise.
