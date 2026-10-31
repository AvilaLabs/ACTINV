# ACTINV-P175 — shutdown-dose source covariance: first order against brute-force sampling (workflow item A1)

## Purpose

`actinv export-r2s-cov` (branch `a1-cov`, commit 21630f8) writes the covariance of the decay-photon source over (region,
photon group) in factored form, `actinv-r2s-cov-1`:
- the mean source `S[c][g]`, in photons/s of the whole region;
- `shared` columns `L_k[c][g]`: the MF=33 block as a pivoted-Cholesky factor, and one column per half-life or
  fission-yield parameter, common to every region;
- per-region `independent` columns `M_{c,j}[g]`: the flux-tally noise of the source neutron groups `j`.

Together `C_S = sum_k L_k L_k^T + blockdiag_c(sum_j M_{c,j} M_{c,j}^T)`. A user with a dose response `R[c][g]` (dose per
source photon born in region `c`, group `g`) forms `Var(D) = R^T C_S R` (plus the photon-tally term, which is out of scope
here). `docs/maintainers/WORKFLOW_ACCURACY_PLAN.md` item 1 says why: most R2S chains ignore the neutron tally's error and
none carries nuclear-data uncertainty to the shutdown dose rate.

The question:

> For a fixed dose response, does the first-order variance `R^T C_S R` from the exported factors equal the variance of
> `D = sum R S` over brute-force sampled inputs, to within what N = 200 samples can resolve?

`D` is linear in the source `S` at fixed `R`, so no transport rerun is needed. Sampling is ACTINV-only and `R` is fixed:
this tests the propagation, not the transport.

The outcome is pass or fail. A fail stands; thresholds are not lowered afterwards, and a repair is registered as an
amendment first. This protocol changes no ACTINV source.

## Method

1. **Mesh.** Four 1 cm cubes in a row, 709-group flux, one material, `mode: auto`, `prune: rate`,
   `bmin_atoms_per_g: 1e-8`, 293.6 K, one thread. Schedule: 300 s of irradiation, then 1 d, 30 d and 1 y of cooling. The
   tested steps are step 2 (1 d) and step 3 (30 d).
2. **First order.** `actinv mesh` with an `uncertainty` block, then `actinv export-r2s-cov MESH SPEC STEP OUT` at steps 2
   and 3. The checker computes `V1 = sum_k (R . L_k)^2 + sum_c sum_j (R_c . M_{c,j})^2` and `D0 = R . S` from the
   document.
3. **Sampling.** N = 200 perturbed mesh solves per case, no `uncertainty` block, photon-source groups kept
   (`cell_result_fields`). `D_i = R . S_i` for each sample and each registered `R`.
4. **Statistic.** The sample variance `V_s` of `D_i` (divisor N − 1), its excess kurtosis `kappa`, and
   `SE_rel = sqrt(max(2/(N−1) + kappa/N, 2/(N−1)))`, the relative standard error of a sample variance (never below the
   normal-theory value).

### Data and fixtures

- **Library and covariance:** `catalog:tendl-2025-neutron-709g` (SHA-256
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`) and
  `catalog:tendl-2025-neutron-709g-covariance` (`c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde`),
  the pair `examples/export_r2s/mesh_banded.json` uses. Decay: `catalog:endfb-viii-0-decay`, fallback
  `catalog:jeff-3-3-decay`.
- **Flux:** cell-a of `examples/export_r2s/mesh_flux_boxes.ndjson` (SHA-256 `b9b1e8d1a713292507611b17be5afaaa671d734383cf7a0b1d1a68b31311b7e7`; the FNS iron spectrum
  of `examples/fns_fe_5min.json`, `ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39`) at amplitudes 1, 0.35, 0.10, 0.02 in cells a to d. The harness writes
  the four-cell file at run time. Cells differ in amplitude and not in shape; see Disclosures.
- **Tally errors (flux channel):** a relative error on every group with flux, 0.03, 0.06, 0.12, 0.25 in cells a to d.
  They are synthetic, chosen to span a well-converged to a poor tally; they are not taken from a transport run.
- **Existing controls this builds on:** P93 (flux channel, and its lognormal sampling convention), P53/P55
  (`export-r2s-joint`), P20 (MF=33 sampling check), P43 (decay-constant sampling), `study.robustness`.

## Cases

| Case | Material (wt %) | Channels in the spec | Flux perturbed |
|---|---|---|---|
| A | steel: Fe 89.07, Cr 9, W 1.1, Mn 0.4, V 0.2, Ta 0.12, C 0.11 (the P157 reduced-activation steel) | `cross_section_mf33`, `decay_constants` | no |
| B | the same steel | `flux` only | yes |
| C | the same steel | `cross_section_mf33`, `decay_constants`, `flux` | yes |
| D | tungsten, W 100 | `cross_section_mf33`, `decay_constants`, `flux` | yes |

- **Steel (A, B, C).** A has the shared block alone, B the independent block alone, C both. B is registered separately
  so a flux-channel error cannot hide under the larger nuclear-data variance.
- **Tungsten (D).** The P20 MF=33 census (`results/g0_p20_mf33_census.json.gz`, 2850 files, every file with MF=33) lists
  blocks for W-180, -182, -183, -184 and -186 including MT 102, 103, 107 and 16, so D is registered with MF=33. The
  harness checks that the exported MF=33 factor has rank at least 1. **If the rank is zero,** D is rerun as
  `decay_constants` + `flux` and reported as "the tungsten case did not propagate MF=33"; nothing else changes. The
  checker accepts the substitution only if it follows from the recorded rank.
- **Fission yields** are not a channel of any case: neither material fissions, so the yield path is not exercised and the
  allowed sentence says so.
- **Banded nuclides.** From a nominal mesh of the material, per (cell, step) the photon-emitting nuclides in descending
  strength until the unbanded remainder is below 1e-5 of that region's source; the union, at most 60. They are the
  spec's `activity:NAME` responses. The checker requires the exported unbanded share to be at most 1e-3 in every cell and
  step, because the first-order variance has no term for an unbanded nuclide while the samples do.

### Dose responses (fixed now, before any run)

With `E_g` the midpoint of photon group `g` from the document's `group_bounds_eV`:

- **R_uniform** `[c][g] = 1` for every region and group. `D` is the total photon emission rate. It carries every
  cross-region term.
- **R_hard** `[c][g] = (E_g / 1 MeV)^2` for the region of cell-b (index 1) and 0 elsewhere. It is weighted to high photon
  energies in one cell, where the nuclide mix, hence the sensitivities, differ most from the group average.

Each case is judged on both responses at both steps: 4 cases × 2 steps × 2 responses = 16 rows.

## The sampler

The harness reuses ACTINV's own sampler for the nuclear-data channels and adds a flux sampler written to the same
convention. Nothing is invented for the test.

- **Cross sections and half-lives: `actinv study run`.** `study.robustness` (ACT-ROBUST-01, qualified by P30 and P43) is
  run once per material on a single-spectrum problem with the cell-a spectrum (`spec_ref: examples/fns_fe_5min.json`),
  the same material and schedule, `samples: 200`, seeds 175001 (steel) and 175002 (tungsten), channels
  `cross_section_mf33` and `decay_constants` as the case needs, `first_order_comparison: false`. Its draws are read from the
  perturbed specs it writes, `rob_<i>.json`: `options.rate_scale` (a factor per library row), `decay_scale` (a factor per
  nuclide). Each is applied to the mesh spec's `options` of sample `i`, so all four cells of sample `i` see one draw of
  the data. A and C share the steel study. The sampler draws the correlated MF=33 factor by the nearest
  positive-semidefinite factor of the collapsed covariance and records the clipped eigenvalue mass; decay constants are
  mean-preserving lognormal with relative sigma `d_half_life / half_life`.
- **Flux: independent per (cell, group).** `phi'[c][g] = phi[c][g] · exp(s z − s²/2)`, `z ~ N(0, 1)` from Python's
  `random.Random(seed · 1000003 + i)`, `s² = ln(1 + e²)`, seeds 175101 (steel) and 175102 (tungsten); `e` the cell's
  registered error. This is the P93 convention. Its variance is exactly `e²`, which is the variance the first-order
  channel carries per group, so the two agree by construction on a response linear in `phi`.
- **Why a global `rate_scale` is a faithful draw here.** A factor on a library row scales that row's group rates in every
  cell alike. The exporter's MF=33 columns use the joint covariance of the one-group cross sections, whose cell-to-cell
  correlation depends on the cells' spectra. With the cells' spectra proportional (the registered flux), the relative
  one-group covariance is the same in every cell, so the two descriptions are the same model. With non-proportional
  spectra they would not be, and this sampler could not test the cell-to-cell part; see Disclosures.
- **Checked for the draws and not assumed:** the harness records the number of rows and nuclides the study varied and its
  clipped eigenvalue mass; the checker requires the clipped mass to be recorded.

## Registered constants

| Constant | Value |
|---|---|
| samples N | 200 per case |
| k | 3 |
| delta_lin | `0.02 + rho_D² + (clipped eigenvalue mass of the MF=33 sampler)` |
| regime limit on rho_D | 0.5 |
| pair allowance | `3 · max(1 − rho², 0.01)/sqrt(N − 1) + 0.05` |
| unbanded share limit | 1e-3 of a region's photon source |
| G3 tolerance | 1e-10 relative, plus the document's own truncation |

**Justification of delta_lin.** The first-order variance is exact for a response linear in the perturbation. The samples
are not linear in it, because the factors are lognormal and `D` is a sum of products of them.
- A lognormal factor with `s² = ln(1 + e²)` has variance exactly `e²`, so a response proportional to one factor has no
  linearisation error in the variance at all (the flux channel, and a single dominant rate).
- A response that is a product of several factors is lognormal with log-variance `tau² ≈ rho_D²`, whose relative variance
  is `e^{tau²} − 1 = tau²(1 + tau²/2 + …)`, so the first-order variance falls short by a relative `rho_D²/2`. The
  registered `rho_D²` is twice that.
- `rho_D = sqrt(V1)/D0` is known from the export before the samples are looked at. MF=33 one-group relative sigmas of
  5–30 % and half-life sigmas below 1 % give `rho_D` of order 0.1–0.3 (an allowance of 0.03–0.11).
- The floor 0.02 covers pruning (`prune: rate` can drop a state in one sample and keep it in another) and CRAM round-off.
  The harness records how many samples changed any cell's `pruned_states`; it is reported, not gated.
- The MF=33 sampler clips negative eigenvalues of the collapsed covariance; the exporter's pivoted Cholesky does not. The
  clipped relative mass is the largest difference in total variance this can make, so it is added.
- A row with `rho_D` above 0.5 is outside the first-order regime and is not scored. It is listed with its reason, and
  each case must keep at least one scored row per response.

**What a pass resolves.** `SE_rel` is 0.10 for N = 200 and normal `D`, larger for heavy tails. With k = 3, a row passes if
the ratio of first-order to sampled variance is within about ±30 % plus delta_lin, which is about ±17 % in the standard
deviation. A larger N would sharpen that and costs only mesh solves; N is fixed at 200 here.

## ACTINV under test

- **Binary.** The release build of the `a1-cov` commit that carries `emit_r2s_cov` and passes the `cov_tests` unit tests.
  Its SHA-256 and the source commit are written into `controls/check_p175.py` (`PINS`) when the build is made, in an
  amendment-free registration commit before the evidence run. The evidence commit's `crates/` tree must equal the source
  commit's, except `crates/actinv-gui`, which this binary does not contain.
- **Registered pins:** binary `9e52e19a76034fa038ac837e926077100d1df81dd99e3c441b19535c9bab8ee9`, built from commit
  `327af82` (crates tree `eb18e0c8…`; the same tree as the draft commit `21630f8` it was first built from).
- Runs inside the bounded scope from AGENTS.md, one at a time. The harness needs the data tree (`ACTINV_DATA`, default
  `actinv-data` in the repository root) with the v1.1.0 libraries, decay files and the TENDL-2025 covariance sidecar.
- Cost: 800 mesh solves of four cells (no uncertainty block), two `study run` of 200 solves, four banded mesh runs, eight
  covariance exports, and four joint exports (cases A and D, two steps each).

## Procedure

1. Register this protocol (hash in `protocols/protocol_hash.txt`) and push it alone.
2. Build the release binary; pin its SHA-256 and source commit in `PINS`.
3. `ACTINV_BIN=target/release/actinv python3 controls/p175_r2s_cov.py` inside the bounded scope. Artifacts:
   `results/p175/` (the four case sample sets and the eight covariance documents, gzip) and
   `results/p175_r2s_cov.json`.
4. `python3 controls/check_p175.py` writes `results/p175_verdict.json`. `--no-write` recomputes it and compares.
   `--self-test` runs the checker on synthetic records with injected faults.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Pins and environment:
  - the recorded constants are the registered ones, and the input files are the pinned ones;
  - the binary is the pinned one;
  - the crates tree is the source commit's, apart from the GUI crate;
  - the tracked tree was clean;
  - the run was in the bounded cgroup.
- **G2.** Fidelity, for every case and both steps:
  - the case was run once, with no failed process and no dropped sample (all 200 present);
  - the covariance and sample artifacts reproduce their hashes, and their photon group bounds agree;
  - the exported mean `S` equals the nominal re-solve to 1e-9;
  - the unbanded share is at most 1e-3 in every region;
  - the channels in the document are the case's (the independent block exists iff `flux` is a channel);
  - **for every scored row** `|V1/V_s − 1| ≤ k · SE_rel + delta_lin`, with `V1`, `V_s`, `SE_rel` and `delta_lin`
    recomputed from the artifacts;
  - **per cell pair** (all pairs, both steps, every case): the correlation of the total photon strength
    `T_c = sum_g S[c][g]` from the factor columns, `C_T = sum_k a_k a_k^T + diag(sum_j (sum_g M_{c,j}[g])²)` with
    `a_k[c] = sum_g L_k[c][g]`, agrees with the sampled Pearson correlation within
    `3 · max(1 − rho_1², 1 − rho_s², 0.01)/sqrt(N − 1) + 0.05`. Flux noise is independent between cells, so case B must
    show sampled correlations near zero.
- **G3.** The MF=33 factor columns reproduce the dense covariance as `actinv export-r2s-joint` computes it, by a separate
  code path, for the total photon strength: for cases A and D (if D has MF=33), both steps, the per-cell variances,
  the cell-pair covariances and the total variance from the `mf33` columns equal the joint export's to 1e-10 relative,
  plus the factor's own truncation (`dense_order · truncated_trace_fraction · trace`, which the document reports).
  The full (cell, group) block is not formed outside the exporter.
- **G4.** CI green on the pushed evidence commit.

## Verdict

P175-PASS if G0–G4 hold. Any scored row outside its limit, any failed process, any pin mismatch or any G3 difference is
P175-FAIL. One repair round, registered as an amendment first.

## Allowed sentence

Written by `controls/check_p175.py` only on a pass, from the computed numbers:

1. "ACTINV's exported shutdown-dose source covariance reproduces the sampled dose variance within {X}% for the tested
   cases." `X` is the largest realised `|V1/V_s − 1|` over the scored rows, rounded up to a whole percent. It includes
   the sampling noise of a 200-sample variance, which the next sentence states.
2. The number of samples, the range of `SE_rel`, and the number of rows scored.
3. Whether tungsten propagated MF=33.
4. The channels and scope (below).

The verdict also reports, for every row, the share of the first-order variance by channel, the ratios and the limits.

## Disclosures and not claimed

- **First order.** `V1` is the linearised variance. The registered delta_lin allows for the second-order growth the
  samples show; it is not a claim about large perturbations.
- **The photon-tally term is excluded.** `sum_c S_c² sigma_{R,c}²` needs an OpenMC photon run (item A2) and is not tested.
- **The cases are synthetic.** The flux amplitudes and tally errors are chosen, not transported; the spectrum is the FNS
  iron spectrum for tungsten as well as steel. Nothing here is a measured or transported dose.
- **Equal-shape cells.** The sampler can represent the MF=33 cell-to-cell correlation only when the cells' spectra are
  proportional, which is how the flux is built. The `export-r2s-joint` comparison in G3 therefore also runs at equal
  shapes. The covariance of cells with different spectra, which is where the joint treatment differs from a pooled
  sigma, is not independently sampled by this protocol.
- **Fission yields and photon emission data** are not tested: no case fissions, and photon yields per decay carry no
  covariance (the document's `channels_missing` says so).
- **Sampling resolution.** 200 samples resolve a variance to about ±10 % (one standard error). A pass is agreement at that
  resolution plus the registered allowance, not exact agreement.
- **MF=33 coverage.** Only reactions with an MF=33 block in the TENDL-2025 sidecar are sampled and propagated; the same
  uncovered rows are named in both.
- **Self-shielding** is off (the export refuses self-shielded runs).

## Development disclosure

- The code under test was written before this protocol. This protocol, the harness and the checker were written after
  reading that code and the sampler (`study.rs`), before either was run.
- The registered constants (k = 3, the delta_lin formula, the pair allowance, the unbanded limit, N = 200, the
  responses, the cells and the tally errors) were fixed before any ACTINV output for these cases existed.
- Facts the first run can still change, none of which touches a threshold: the file layout `actinv study run` writes
  (`cases/<material>__fns__irr/rob_<i>.json`), whether the study's active MF=33 rows cover every row the mesh sensitivities
  use, and the photon group count. A harness defect found on the first run is corrected before the registered run and
  disclosed; a changed threshold needs an amendment.

## Rehearsal

Before registration the coordinator ran the whole pipeline once with `P175_REHEARSAL=1`: N = 8 samples, everything
written under `target/p175-rehearsal/`, never `results/`. The rehearsal switch (in `controls/p175_stats.py`, read by
the harness and the checker) changes only N and the output paths.

- **First attempt:** the harness stopped before any sample because the registered SHA-256 of `examples/fns_fe_5min.json`
  was wrong (a drafting error; the file is unchanged since b4a4b1c). The pin was corrected to `ed55ed00…` here, in the
  harness and in the checker.
- **Second attempt:** every process exited 0 (1,351 s). G3 passed. G0 and G1 failed only on registration items (the
  protocol hash and the binary and commit pins). G2 failed on two of 16 rows: step 3, `uniform`, cases A and C
  (V1/Vs = 8.4 and 6.8). A and C share the same eight study draws, so this is one observation.
- **Seen, and what was done.** The coordinator read the rehearsal's ratios to decide whether the gap was a defect. A
  diagnosis from the stored artifacts found none:
  - the exporter's variance is reproduced by six library rows from the covariance file (201.4 against 202.7);
  - every row with a sensitivity is sampled, with the covariance's sigmas and the Fe-54 MT103/MT107 correlation;
  - the first-order prediction from the exported sensitivities and each sample's actual factors reproduces each sample's
    D to 0.2 against a spread of 4.9.

  The low sampled variance came from the eight draws (a chance −0.49 correlation between two independent rows); an
  8-sample ratio this far out has probability about 0.003. No code, harness logic, constant or threshold was changed.
  Eight-sample ratios between about 0.3 and 3 are expected from sampling alone.
- The rehearsal outputs were deleted.
