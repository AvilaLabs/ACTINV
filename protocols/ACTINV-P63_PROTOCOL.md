# ACTINV-P63 — Calibrated coverage: declared unmodeled error + honest coverage certificate

## Question

P44's sealed measurement showed modeled bands under-cover: 36% combined
coverage where ~68% is the honest target, and the residual decomposition
(2026-09-26) shows the gap is dominated by systematic data defects
(uncovered residuals p50 at ~10× nominal), not variance around a good
nominal. Two capabilities are missing:

1. The band carries only the covered-covariance variance — there is no
   declared way to fold in an unmodeled-error term.
2. There is no standing instrument that reports empirical coverage
   honestly against a measured corpus.

## Deliverables

- **`uncertainty.unmodeled_relative`** — optional declared scalar u ≥ 0
  in the spec's `uncertainty` block. When present, every banded
  response's total variance gains `(u · nominal)²` in quadrature before
  `standard_uncertainty`, `normal_interval`, and `conservative_interval`
  are formed. The emitted response block records
  `unmodeled_relative`, `unmodeled_standard_uncertainty` (u·|nominal|),
  and `modeled_standard_uncertainty` (the pre-addition σ) so the
  decomposition is re-derivable. Absent ⇒ byte-identical output.
  Declared limitation, stated in emitted docs: a relative term cannot
  cover a zero or missing-channel prediction — those are completeness
  defects (P61 `ledger.completeness`), not uncertainty.
- **`controls/p63_calibrate.py`** → `actinv-calibration-1` — post-hoc
  calibration over a P44-format coverage document: for a grid of
  unmodeled-relative scales u, recomputed coverage of each finite
  measured point under |m − c| ≤ √(w² + (z·u·n)² + σ_m²) — the sealed
  P44 `combined_sigma` rule with the declared term folded in quadrature
  exactly as the emitted band does (c = band center, w = half-width,
  z = normal multiplier at the document's declared confidence); the u
  at which pooled
  coverage first reaches 0.68; per-material breakdown; and the count
  of points uncoverable by any relative term (zero predictions,
  unaligned steps). The certificate names the input document's sha and
  states honestly that u is fitted, not derived.
- No data-fix claims: this phase ships the meter and the declared
  widening term. Whether coverage improves rides on the data-quality
  workstream, measured by this instrument when it lands.

## Honesty rules

- The unmodeled term is user-declared (spec) or empirically fitted
  (certificate) — never silently widened, never hidden inside σ.
- The certificate reports the fitted u AND the pre-widening coverage;
  it never claims "calibrated" without naming the corpus and its sha.
- Zero-prediction and unaligned points are reported as a separate
  uncoverable class — they do not enter the relative-term fit and are
  never silently dropped.

## Gates

- **G1 mechanics** — fixture run with `unmodeled_relative` set: emitted
  `standard_uncertainty` equals sqrt(modeled² + (u·nominal)²) exactly;
  fields present; absent-option output byte-identical to the pre-option
  emit.
- **G2 exactness** — checker rebuilds the fold and the calibration
  curve from raw emitted values and sealed coverage points; every
  emitted coverage recomputed exactly.
- **G3 demo** — `actinv-calibration-1` certificate generated over the
  sealed P44 corpus (`results/p44_sealed_coverage.json`): pooled
  coverage at u=0 matches the sealed number, the σ_u@68% value is
  reported whatever it is, per-material spread and the uncoverable
  class are enumerated.
- **G4 determinism** — certificate generation is byte-identical on
  repeat runs.
- **G5 checker** — reparse the certificate; catch planted mutations to
  fitted u, coverage values, uncoverable counts, and corpus sha.
