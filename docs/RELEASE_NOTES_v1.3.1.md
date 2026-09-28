# ACTINV v1.3.1 — release notes

ACTINV 1.3.1 is a feature release centred on the measurement-driven product line: assays, facility
twins, certified decision loops, and calibrated uncertainty. It also lands the resolved-resonance
self-shielding quadrature — SIGMA1-broadened per temperature — plus the chance-constrained design
optimizer and the OpenMC/MCNP/Serpent photon-source export surface. Schemas are additive;
runs that do not touch the surfaces listed under "Results to re-check" are unchanged.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.3.1
```

## Supersedes v1.3.0

v1.3.1 is a packaging repair on top of v1.3.0: `actinv-core` bundled the IAEA clearance table via a
path outside the crate root, so the crates.io tarball and the PyPI source distribution could not
build it. The table now lives inside the crate (`crates/actinv-core/data/`, byte-identical to the
canonical `data/clearance_iaea_2004.json`, drift-guarded by `controls/g2_p54_exactness.py`). Use
v1.3.1; v1.3.0's PyPI wheels remain valid, but its sdist cannot build from source.

## Results to re-check

- **Shielded runs at temperatures above the first table column.** Resolved-resonance Bondarenko
  factors are now computed per temperature column on a SIGMA1-broadened pointwise grid instead of
  reusing the lowest-temperature curve. Capture factors relax toward 1 as temperature rises
  (resonance valleys fill in), which is the correct physics — the previous columns were over-shielded.
  Factors divide by each column's own broadened dilute mean; the emitted `group_unshielded_b`
  reference is the lowest tabulated temperature.
- **Specs combining `uncertainty` and `self_shielding`.** The combination was previously rejected;
  it now runs — the covariance collapse folds the shield plan's per-row scales so sampled parameters
  mean what the matrix means. Specs that relied on the refusal for validation order will instead fail
  later, on the missing or mismatched covariance artifact.
- **`sigma_p_b` for resolved-heavy nuclides.** Composition-dilution σ₀ estimates now include the
  lethargy-mean elastic contribution from resolved ranges; effective σ₀ can move for targets whose
  tables previously carried the unresolved-range mean only.
- **Every `twin`/`assimilate`/`decide` result is new output** — these commands did not exist in 1.2.1.

## What changes

### Measurement assimilation (the D5 lane)

- `actinv assimilate` folds an `actinv-assay-1` document into a response band: scalar assays,
  multi-nuclide `entries` (one HPGe count updates several nuclide bands, with a worst-of aggregate
  verdict so one conflicting line cannot hide), and `mixture` assays measuring a linear combination
  `Σcᵢ·xᵢ` (gross activity, total heat, dose) via a Kalman H-row update that emits the induced
  correlation matrix. Every assay emits the Kalman gain and a `consistent`/`marginal`/`conflict`
  verdict.
- `--emit-result` writes fused bands back into a valid run result, chainable into `decide`,
  `clearance`, or another assay. Results persist the joint ln-space Gaussian under
  `assimilated.state`: a later assay on one member of a correlated mixture conditions its siblings
  (stamped `kind: correlated`, listed under `jointly_moved`). Malformed, inconsistent, or
  time-mismatched states fail closed; a scalar-only fusion emits no state.
- `actinv twin` evaluates declared clearance limits on per-cell certified band edges over mesh
  output: cell×time×limit margins, facility rollup, binding cells, `components` rollups,
  per-cell `materials` overrides, `dose_points` (declared point-kernel detectors with optional slab
  shields; photon flux per step, `dose_gy_h` when a conversion is declared), per-cell `assays`
  fused before margins, `propagates` declared-ρ propagation to sibling cells, `dose_assays` that
  invert the point-kernel forward map into per-cell dose-share updates, and
  `facility.assay_recommendations` — for every restricted (cell,time,limit), the assay precision
  needed to clear it at the prior nominal, sorted achievable-first.
- The desktop workbench results page folds scalar/entries/mixture assays into a loaded result
  through the same fusion core the CLI uses (`assimilate::fuse_document`) — verified bit-identical.

### Calibrated uncertainty

- Sampled decay/yield channels with per-sample streams and paired survival (P43); measured band
  coverage campaign (P44); executed whole-workload benchmark (P45); evaluation-intelligence layer
  over five corpora (P46); isomer-resolved uncertainty and channel introspection (P58);
  measurement-design reports with Kalman-gain value-of-information (P50, P60).
- `uncertainty.unmodeled_relative` declares an explicit unmodeled band term;
  `uncertainty.unmodeled_table` resolves it from a calibrated `actinv-unmodeled-table-1` artifact
  (per-material key or dominant-element inference, spec fallback then table default). The D2
  calibration record ships `u_pooled = 0.437` fit/holdout SHA-split on the decay-aware TENDL-2023
  corpus, held-out `u@68% = 0.306`.

### Certified decision machinery

- `actinv clearance` — certified probabilistic clearance against the shipped IAEA limits table (P54).
- `actinv decide` — one-command loop: banded solve → certified margins → audit → measurement
  targets, emitting `actinv-decision-1` (P64).
- `options.screen` — certified screening tier: prune-bound widening emits per-step certified
  intervals for every banded response (P65); the screened tier drives the workbench's live sweep
  at sub-second warm latency (P68).
- `options.flux_scale` — certified linear flux-scaling for trace-regime responses with an
  optical-depth correction bound; the live slider answers flux points in ~60 ms with a
  `flux_scale` certificate (P70).
- `actinv optimize` — chance-constrained design search (P49) with banded-constraint certification
  and nominal-edge margins (P56), per-candidate certified early-exit via `optimizer.prescreen`
  (P73), a `actinv-surrogate-1` surrogate tier that evaluates candidates through a certified
  multilinear band (`ŷ ± ε_cert`, holdout-max-residual plus max-secant-slope distance; refuses
  extrapolation, never emits zero-width certificates) with fallthrough to prescreen/full solve
  (P74), and a persistent prepared-input cache (P62) with certified Pareto frontier output (P67).

### Interoperability and throughput

- `actinv export-r2s` — banded R2S photon-source interchange (`actinv-r2s-source-1`); `export-r2s-joint`
  adds the joint-covariance variant for correlated spatial dose bands (P52/P53).
- `actinv export-source {openmc|mcnp|serpent}` — foreign photon-source adapters over
  `actinv-r2s-source-1` under the INTERCHANGE_TRANSPORT contract (P57).
- `actinv worker` — persistent prepared-input cache serving NDJSON requests; 0.37× warm wall on
  identical specs, bit-identical results (P51), and `actinv export-mcnp` writes a full
  shutdown-source SDEF.
- `actinv qualify inverse` — GLS irradiation-history estimation under nuclear-data covariance (P55).
- `outputs audit` — chain-completeness audit surface (P61).
- Python SDK: `actinv.decide`, `actinv.optimize`, and screen options (P66).
- Workbench: live certified probe on a shared-cache worker — the sweep slider submits the newest
  position only and each landed point carries a P65 screen certificate (P69); live Pareto frontier
  view over landed (activity, heat) pairs (P71).

### Nuclear data and physics

- **Resolved-region self-shielding** (D6e): `build-shielding` folds resolved-resonance ranges into
  per-group Bondarenko factors by knot-seeded adaptive Gauss-Legendre quadrature over the pointwise
  MF=2 reconstruction (Breit-Wigner, Reich-Moore, R-matrix limited), SIGMA1-broadened per
  temperature column with per-column dilute-mean normalization. Gate: W-186 capture factor falls to
  0.028 at σ₀=0.1 and relaxes monotonically with T; Fe-56 smooth groups stay at 1.0.
- **D6 accuracy lane**: `evalspread` (cross-evaluation spread as a band term), decay overrides
  (`actinv-decay-override-1` content for measured half-lives/branchings), `product_coverage` in every
  build print (which nuclides sit beyond evaluated data), `xN` ultra-fine group refinement
  (`fispact-709x4` = 2836 subgroups), TENDL isomer routing fix (LIS/ELIS resolution), and the
  decay-aware corpus benchmark — the default build's residual population sits at the
  evaluated-data boundary (787/3,632 unbuilt, all without an evaluated file in any local library).
- Benchmark posture is documented in `COMPETITIVE_BENCHMARK.md` at the repo root: identical-input
  parity with FISPACT-2007 inside decay-eval noise on the decay-aware TENDL-2025 corpus, plus the
  OpenMC ENDF-8 head-to-head section.

## Qualification boundary

The qualification posture is unchanged from v1.2.1: `QUALIFICATION.md` governs. The P18 corpus
remains **P18-FAIL** — the measured gap is a TENDL-2025 evaluation regression (documented by the
CB2/FNS-gap diagnosis and P46 evaluation intelligence), not a solver defect; the P25 successor
investigation is a bounded cause-diagnosis milestone, still open. Conditional verdicts (P43, P44,
P45, P46, P47, P48, P49) carry their published amendment histories; closed verdicts are listed in
the ledger.
