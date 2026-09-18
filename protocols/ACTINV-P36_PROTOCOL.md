# ACTINV-P36 protocol — flagship demonstration W-MATCMP (non-AI leg)

P36 executes the roadmap's selected flagship workload W-MATCMP
(`results/g1_p26_workloads.json`, `results/verdict_p26.json`): an
impurity-sensitive material comparison under a specified neutron
spectrum and irradiation/cooling history, reporting activity, decay
heat and photon production at selected cooling times, identifying the
controlling nuclides and pathways, testing whether the conclusion
survives declared composition and nuclear-data uncertainty, and
packaging the result for independent reproduction.

The roadmap's defining demonstration is the complete flagship
investigation *with and without AI*. The with-AI leg is blocked by
P33/P34 (no user AI provider account, no human evaluators). This phase
executes and qualifies only the non-AI leg; the with-AI leg is recorded
as inherited-blocked, not omitted.

## Frozen study (G0 seal)

Study file: `target/p36-work/w-matcmp/study.json`, hashed in G0 before
execution. Fixed content:

- **Materials** (composition wt%):
  - `rafm_base`: EUROFER97-like nominal — Fe 88.34, Cr 9.0, W 1.1,
    Mn 0.4, V 0.2, Ta 0.14, C 0.11, N 0.03, B 0.001, plus declared
    impurities at reference levels: Ni 0.05, Co 0.005, Nb 0.001,
    Mo 0.001, Ag 0.0001, Cu 0.05.
  - `rafm_low`: identical alloy system with impurities reduced to
    low-activation limits: Ni 0.005, Co 0.0005, Nb 0.0001,
    Mo 0.0001, Ag 1e-6, Cu 0.005 (Fe raised to preserve 100 wt%).
  - `rafm_high`: same system with impurities at spec-limit highs:
    Ni 0.5, Co 0.02, Nb 0.01, Mo 0.01, Ag 0.001, Cu 0.1
    (Fe adjusted).
- **Spectrum**: FNS fispact-709 neutron spectrum borrowed verbatim by
  `spec_ref` from `examples/fns_fe_5min.json` (digest-pinned).
- **Schedule**: `fe_2y` — one continuous 2-year irradiation step at
  flux multiplier 1.0 of the FNS spectrum total
  (1.116e10 n cm^-2 s^-1).
- **Cooling times** (s): 0, 86400 (1 d), 31557600 (1 y),
  3155760000 (100 y), 31557600000 (1000 y).
- **Responses**: `total_activity_bq_per_g`, `decay_heat_w_per_g`,
  `photon_source_per_group`, `inventory_per_nuclide`.
- **Comparison** (axis `material`):
  - `act_rank_100y`: rank_equal on `total_activity_bq_per_g` at 100 y,
    expected order `["rafm_low", "rafm_base", "rafm_high"]`
    (highest first).
  - `heat_rank_100y`: rank_equal on `decay_heat_w_per_g` at 100 y,
    same expected order.
  - `impurity_span_100y`: ratio_band on `total_activity_bq_per_g` at
    100 y across the three materials, band [1.0, 1e6] — the study must
    show impurity level measurably changes the 100 y response.
- **Robustness** (ACT-ROBUST-01 qualified machinery): 24 samples,
  seed 20260917, channels `flux_rel_std` 0.05 and
  `composition_rel_std` {Ni 0.3, Co 0.3, Nb 0.3, Mo 0.3, Ag 0.3,
  Cu 0.3} — declared composition uncertainty on the impurity elements
  only; `cross_section_mf33` off (the TENDL-2025 covariance sidecar
  covers reactions; composition uncertainty is the impurity question's
  declared channel — MF33 is exercised in P30's evidence and is out of
  this study's declared perturbation set). Responses as above.
- **Options**: `mode: coupled`, outputs enabled for photon source and
  pathways (`pathways: true` if the study schema supports it — else
  pathway evidence is taken from the per-case `out.json`
  `pathways`/`pathway_closure` fields).

## Gates

- **G1** — execution: `actinv study` completes all 3 cases + robustness
  sampling; record shows every case complete with per-time responses;
  comparison rules evaluated; robustness stats emitted per response.
- **G2** — controls (independent re-derivation, not trusting record
  fields alone):
  1. `case_completion` — all 3 cases executed, no gaps.
  2. `decision_rules` — both rank rules pass and `impurity_span_100y`
     passes, re-evaluated from the per-case response values.
  3. `dominant_nuclides` — at 100 y the top activity nuclides are
     independently re-derived from `inventory_per_nuclide` and the
     impurity-driven nuclides (Nb-94, Ag-108m family) are identified by
     name.
  4. `pathway_attribution` — `pathway_closure` per case within 1e-3 of
     1.0 and top pathways name a declared parent→product chain.
  5. `robustness_survival` — the rank ordering of the three materials at
     100 y is preserved in the robustness sample means, and the
     sampling ledger (covered rows, seed, draws) is present.
  6. `packaging` — record + per-case outputs hashed; the frozen study
     digest in the record equals the G0-sealed digest (reproduction
     identity).
- **G3** — negative controls: tampered study digest, missing case,
  flipped expected order, removed robustness channel — each must be
  rejected by the checker or fail closed.
- **G4** — verdict by checker: P36-CONDITIONAL (non-AI flagship
  executed and qualified; with-AI leg inherited-blocked) is the maximum
  reachable verdict while P33/P34 are blocked. P36-PASS is not
  attainable.

## Honesty constraints

- The conclusion is about *declared compositions under the declared
  spectrum/schedule on pinned data* — not a generic material-selection
  claim, not experimental validation.
- Impurity levels are declared scenario values, not measured material
  certificates.
- The spectrum is the FNS experimental-campaign spectrum; it is not a
  DEMO/ITER first-wall spectrum and that substitution is named in the
  verdict.
- No competitive or speed claim is made.
