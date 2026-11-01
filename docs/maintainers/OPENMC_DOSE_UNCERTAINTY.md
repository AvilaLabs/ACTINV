# Dose uncertainty in the OpenMC manager: design note

Status: design only. Nothing here has been run or qualified. Roadmap item: "dose uncertainty in the manager",
`WORKFLOW_ACCURACY_PLAN.md` section 1.

## Goal

`ActinvR2SManager` (`python/pysrc/actinv/openmc.py:295`) should return a band on the shutdown dose rate. OpenMC's
`R2SManager` returns none. Its step 3 (`step3_photon_transport`, `openmc/deplete/r2s.py:400`) builds one
`MeshSource` list per time (`get_decay_photon_source_mesh`, line 539), runs one photon calculation, and stores the
tallies. The only error reported is the tally's statistical error from that one run. The activated source is treated
as exact.

## What ACTINV already has

- Per-cell activity bands from MF=33 covariance: `uncertainty.rs:152` (`ResponseUncertainty`), spec fields at
  `spec.rs:80-135` (`covariance`, `channels`, `confidence_level`). Channels are `cross_section_mf33`,
  `decay_constants`, `fission_yields` and `flux`.
- The `flux` channel (`uncertainty.rs:83`, `FluxParameter`): diagonal across groups, one relative error per group.
  Input is `relative_error` in each cell of the flux NDJSON (`flux.rs:90`, validated at `flux.rs:302`). The adapter
  writes flux records but the tally relative error path is not exercised by the adapter today (not checked here).
- `actinv export-r2s-cov MESH_RESULT MESH_SPEC STEP OUT` (`help.rs:287`, `r2s.rs:1395`). It writes the photon-source
  covariance over (region, photon group) in factored form: shared columns (MF=33, half-life, yield) and
  per-region independent columns (flux noise). So `Var(D) = R^T C_S R + sum_c S_c^2 sigma_Rc^2`.
- Qualification so far: P175 PASS after Amendment A (`results/p175_verdict.json`). `R^T C_S R` matched the variance
  of 200 sampled mesh reruns, ratios 0.87 to 1.29, on four synthetic cases. `sigma_R` was not tested.
- `actinv mesh` does not emit sampled photon sources. Sampling is done by reference harnesses, not by the command.

## What the adapter passes and reads

It sends `cell_result_fields = ["mode", "ledger", "steps.photon_source.groups"]` (`openmc.py:405`). The spec sets no
`uncertainty` block, so no band is computed. `parse_photon_groups` (`openmc.py:198`) keeps only `centroid_eV` and
`photons_s`. Nothing about uncertainty is read back.

## Options

### (a) Sampled sources, N photon transports

Draw N samples of the source from `C_S`, run N OpenMC photon calculations, take the spread of dose.

- Claims: the full distribution of dose, including nonlinearity. Exact in the sampling limit.
- New code: sampler in Rust or Python (the P175 reference sampler exists but is test code); loop in the manager.
- Cost: N photon runs, N of 50 to 200. That is 50 to 200 times step 3. Prohibitive for most users.
- Qualification: it is the reference for (b), not a product.

### (b) First-order propagation with a response vector R

Run the photon step once with the dose tally split by source birth region (`CellBornFilter` or `MeshBornFilter`).
That gives `R_c`, dose per unit source in region `c`. Combine with `C_S` from `export-r2s-cov`.

- Claims: `Var(D) = R^T C_S R + sum_c S_c^2 sigma_Rc^2`. Shared data errors are fully correlated across regions.
  Neutron-tally noise is independent. OpenMC's tally noise per birth bin is added in quadrature.
- New code: Python, about 150 lines. Spec gets an `uncertainty` block. The manager calls `export-r2s-cov`, adds the
  birth-region filter to the user's photon tally, reads per-bin mean and std_dev, and returns a dose band.
  Rust changes only if per-group `R_{c,g}` is wanted.
- Cost: one photon run per time if `R_c` is summed over groups (the pooled source spectrum is fixed by ACTINV, so
  group-wise `R` is a refinement). With groups: 5 to 10 runs, since OpenMC has no birth-energy filter. Plus
  `export-r2s-cov`, small next to the activation solve. The birth filter adds tally bins and some memory.
- Qualification: A1 (below) for the source term; the `sigma_R` term needs its own check; then SINBAD FNG for
  coverage of measured dose. The SINBAD data are export-controlled (RSICC 216443) and cannot go into CI.

Caveat: the pooled-over-groups form assumes the source spectrum shape is not itself uncertain. Spectrum-shape
errors from yields would be missed. Group-wise `R` removes this at 5 to 10 times the photon cost.

### (c) Source band only, beside OpenMC's statistical error

Report `sqrt(R^T C_S R)` ignoring correlations, or report per-region source bands next to the tally error, without
combining them into a dose band.

- Claims: only that the source is uncertain by X. No dose band.
- New code: a few lines in Python. No new Rust.
- Cost: none beyond one `export-r2s-cov`.
- Qualification: the source band is covered by P175; there is no dose claim to test.

## Recommendation

Do (b), pooled over groups first, group-wise as an opt-in. It reuses an exported covariance that already passed P175,
costs one photon run per time, and gives a real dose band. Use (a) only as the qualification reference. Ship (c)
as the fallback when the user's photon model has no tally to split.

Qualification plan:

1. Unit test the combiner against a hand-built `C_S` and `R`.
2. Run A1 from the plan: on the steel cube and one tungsten case, compare the band with brute-force sampling (a),
   with a pre-registered ratio limit.
3. Seed-repeat OpenMC to check `sigma_R`.
4. SINBAD FNG coverage, local only.

## Where the band is a lower bound

- Photon yields per decay and decay-gamma energies carry no published covariance. The `r2s.rs:103` note says so.
  The yield channel covers only independent fission yields, not gamma emission.
- Half-lives are diagonal (`DecayParameter`, uncorrelated). Branching ratios have no channel.
- MF=33 covers only the reactions present in the covariance file. `covered_parameters` / `total_parameters` in the
  band report the gap.
- Photon transport cross sections, geometry and material errors are not in the band.
- The flux channel treats group errors as independent. Tally correlations between groups are not reported by OpenMC.
- `unmodeled_relative` (`spec.rs`, P63) can add a declared term, but it is declared, not measured.

The manager must state these in its output next to the band. Until a recorded protocol covers the dose band against
a measurement, it should be labelled a lower bound on data uncertainty, not a total uncertainty.
