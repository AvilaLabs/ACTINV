# ACTINV-P187 — The MF=33 activity band against brute-force sampling

## Purpose

ACTINV's activity band is first order: it computes `V1 = s^T C s` from the response's sensitivities `s` to the
collapsed cross sections and their MF=33 covariance `C`. P186 showed that ACTINV computes that quantity as defined,
including the state-resolved (MF=10) rows that now take their reaction's covariance. P187 asks whether that is the
spread of the activity itself:

> Does `V1` equal the variance `Vs` of the activity over N = 200 solves with cross sections drawn from the same
> covariance, to within what 200 samples can resolve?

The question tests the sensitivities (ACTINV's tangent solve) and the first-order assumption. It also tests that
the sampled perturbation of a state-resolved row, which moves with its reaction total, matches the band's
treatment. It does not test the covariance data: both sides use TENDL-2025's v1.2.0 sidecar. Whether the band
covers measured activities is a different question. This is the accuracy program's band-sampling check for point
problems; P175 did it for the shutdown-dose source covariance.

The outcome is pass or fail. A fail stands, thresholds are not lowered afterwards, and a repair is registered as an
amendment first. P187 changes no ACTINV source.

## Data and binary

- **Activation library:** v1.2.0's `tendl-2025-neutron-709g.npz`, SHA-256 `ec4c72bf…`.
- **Covariance:** v1.2.0's `tendl-2025-neutron-709g.cov.npz`, SHA-256 `c19dec86…`.
- **Decay:** v1.2.0's ENDF/B-VIII.0 file, with JEFF-3.3 as fallback.
- **Spectrum:** `examples/fns_fe_5min.json` (SHA-256 `ed55ed00…`), the FNS iron spectrum, 709 groups.
- **Binary:** `actinv` built from a clean checkout of the registration commit. Its Rust sources are those of master
  since P186 (`4ce2822`).

## Problems

P186's two problems, with the same schedule and options: 300 s at the example's flux, then cooling steps of 66 s,
3,600 s and 86,400 s; `mode: auto`, `prune: rate`, `bmin_atoms_per_g: 1e-8`, 293.6 K.

- **A:** iron (100 wt% Fe). Responses: the activities of Fe-53, Mn-54, Mn-56, Cr-51 and Mn-57.
- **B:** 40 wt% Ni, 20 Co, 20 Al, 10 W, 10 Nb. Responses: the activities of Co-58, Co-57, Ni-57, Co-60, Al-26,
  Na-24, W-185, W-187 and Nb-92m (`Nb92m1`).

## Method

1. **First order.** `actinv run` on each problem with an uncertainty section: channel `cross_section_mf33`, the
   responses above, the v1.2.0 sidecar. From each response and step: the nominal activity, `V1` (the square of
   `mf33_standard_uncertainty`) and `rho` (`relative_standard_uncertainty`).
2. **Sampling.** `actinv study run` on each problem with `study.robustness`:
   - N = 200 samples, seed 187001 (A) and 187002 (B);
   - channel `cross_section_mf33` only, with the same sidecar;
   - `first_order_comparison` off.

   This is the sampler qualified by P30 and repaired for P175. Each sample is a full solve with correlated,
   mean-preserving lognormal factors on the covered library rows of the material's nuclides. Each sample's
   activities are read from its own solved output (`rob_<i>.out.json`).
3. **Statistic,** per (problem, response, step), as in P175:
   - the sample variance `Vs` of the 200 activities (divisor N − 1);
   - their excess kurtosis `kappa`;
   - `SE_rel = sqrt(max(2/(N−1) + kappa/N, 2/(N−1)))`.
4. **Rows.**
   - A row is **gated** when the nominal activity is positive, `0 < rho ≤ 0.5` (the first-order regime) and
     `Vs > 0`.
   - A gated row **passes** when `|V1/Vs − 1| ≤ 3 SE_rel + delta`, where `delta = 0.02 + rho² + c` and `c` is the
     sampler's recorded `negative_eigenvalue_mass_clipped_relative` for that problem. `rho²` allows for the response's
     curvature, and `c` for the sampler's repair of indefinite blocks.
   - Rows outside the regime are recorded, not gated.

The sampler perturbs only rows of nuclides in the material. The band also has sensitivities to rows of nuclides made
during the 300 s irradiation, which are negligible here; the floor in `delta` covers them.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1 (provenance):**
  - the record shows the pins above, N = 200 and the seeds;
  - the binary is built at the registration commit from a clean tree, with exit status 0;
  - the first-order runs and both studies are made at that commit, inside a bounded scope (at most 6 GiB, no swap,
    128 tasks, 4 CPUs);
  - every run exits 0 with that binary, and every study has 200 samples.

  The checker reads every sample's draw and output, matches them to the recorded SHA-256, and extracts the
  activities again with its own code. They must equal `results/p187_samples.json.gz` exactly. Each study's nominal
  solve must give the first-order run's nominal activity, bit for bit, for every response and step.
- **G2 (the band):** every gated row passes. There is at least one gated row.
- **G3 (it tests the new rows):** in each problem, the draws perturb at least one state-resolved (MF=10) row. Fe-53
  in A and Co-58 in B are gated at 66 s after shutdown. In each problem, a gated response has a state-resolved
  parameter.
- **G4:** CI green on the evidence commit.

P187-PASS if G0–G4 hold. One repair round (an amendment with a fresh run) is allowed.

## Rehearsal and disclosure

- A development study of iron with 2 samples was run to learn the output layout. Its nominal solve was compared with
  a plain run of problem A: identical bit for bit. One sample's total activity and one nuclide's activity were
  printed while reading the layout; no variance was computed.
- A rehearsal of the full harness ran with `P187_REHEARSAL=1`: 4 samples per problem, outputs under `target/` only.
  It was read for exit statuses, times and peak memory (first-order runs 61 s; sampling 18 s; 1.6 GB). The checker was
  then run on the rehearsal for G0, G1 and G3 only. Its outputs are deleted before the scored run.
- P186's record holds the first-order relative standard deviations of these responses at every step. Those were seen;
  no sampled spread has been.

## Allowed sentences

On P187-PASS, filled by the checker from the record:

1. "On two FNS-spectrum point problems (iron, and a nickel, cobalt, aluminium, tungsten and niobium mixture) with
   TENDL-2025's MF=33 covariances, ACTINV's first-order activity band agreed with the spread of 200 sampled solves
   within the registered limit on all {n} tested (nuclide, time) pairs; the ratio of first-order to sampled variance
   ranged from {lo} to {hi}."
2. "The tested nuclides with state-resolved (MF=10) production, which take their reaction's covariance, are
   {list}."

These concern the band's propagation, not whether TENDL-2025's covariances are right.

## Commands (coordinator)

- Run `controls/p187_harness.py build`, `first`, `sample` and `assemble`, then `controls/check_p187.py`.
- Run them from a worktree at the registration commit, inside the bounded scope.
- `results/p187_samples.json.gz` (activities only) is committed with the record. The raw outputs under
  `target/p187-work` are deleted after the verdict.
