# ACTINV-P75 — Linear-response falsification of the activation operator

Date: 2026-09-29. Status: **frozen before any comparison quantity is computed.** Pre-freeze
profiling ran eight units for wall time, peak RSS and result size only (recorded below); no
comparison quantity was extracted or inspected.

## Question

In the trace regime every activation response is, to first order, linear in the neutron flux
spectrum and in the material composition. If that holds to engineering accuracy, a per-material,
per-schedule response operator (outputs × 709 groups) can replace per-cell solves in mesh
shutdown-dose work: each cell becomes a matrix–vector product, mixed cells become weighted sums,
and cooling-time, schedule, composition and impurity what-ifs become linear algebra.

P75 asks three things, cheaply, before anything is built:

1. Is the linear operator well defined in ACTINV's solver — does exact superposition hold
   numerically in spectrum and composition? (G1)
2. Over what fluence envelope does the linear operator reproduce the full coupled solve? (G2)
3. Does the flux-scaling certificate already shipped in the desktop sweep (P70,
   `crates/actinv-gui/src/sweep.rs::scale_flux_result`, bound `tau_p·|v−1|`, cap `1e-3`) bound
   the error of the result it substitutes for a solve? (G4)

G3 is descriptive: which cheap certificate would predict G2's error.

## Population

Built deterministically by `controls/p75_linear_response.py build`; manifest
`target/p75/cases.json` sha256 `0d9b3f5a54e992222153c8d54f863efddab71e2ed0abba28486d73ad9da241f9`,
1,032 runs.

- Data: `actinv-data/v1.1.0` TENDL-2025 neutron 709-group library (sha256 `ec4c72bf…8cc44`),
  ENDF/B-VIII.0 decay primary, JEFF-3.3 fallback. Temperature 293.6 K.
- Materials (wt%, representative test compositions, not certified specifications): pure Fe, W,
  Cu, Co; SS316L(N)-type steel with Co 0.05, Nb 0.01, Ta 0.01, B 0.001; EUROFER97-type steel with
  ppm-level Nb/Mo/Ni/Cu/Co; ordinary concrete with Co 10 ppm and Eu 1 ppm (K-40 supplies natural
  background activity). Exact values are in the runner.
- Spectra (709-group, unit total): `fns` (FNS D-T shape from `examples/fns_fe_5min.json`),
  `flat` (constant per unit lethargy, 1 eV–1 MeV), `maxwell` (293 K Maxwellian, kT = 0.0253 eV),
  `mix` = 0.2 fns + 0.5 flat + 0.3 maxwell.
- Schedules: `s1_1y` (1 y continuous), `s2_5y` (5 y continuous), `s3_pulsed` (5 × [400 s on,
  1400 s off]); each followed by cooling to cumulative 1 h, 1 d, 1e6 s, 30 d, 1 y, 10 y, 100 y.
  Compared steps: the last flux-on step and every cooling step.
- Amplitudes (total flux, n cm⁻² s⁻¹): 1e10, 1e12, 1e13, 1e14, 1e15.

Arms:

| arm | runs | mode / prune | role |
|---|---:|---|---|
| B | 21 | trace / reach, all multipliers 0 | background `B(m,s)` |
| U | 84 | trace / reach, total 1 | unit linear operator |
| T | 420 | coupled / reach | truth |
| S | 420 | auto / rate (shipped defaults) | the solve the P70 slider replaces |
| Y | 21 | photons on | per-nuclide photons/decay and W/Bq per group |
| CB, CU | 22, 44 | trace / reach, pure elements, `s1_1y`, spectra fns and mix | composition superposition |

## Definitions

- Linear prediction: `L(m,p,s,a) = B(m,s) + a·(U(m,p,s) − B(m,s))`, applied per nuclide activity,
  total activity `A`, and total heat `H`.
- Photon group power `P_g` and rate `N_g` (FISPACT 24-group) are composed as `Σ_n A_n y_n,g` from
  the Y-arm yield table for every arm alike. Photon output is ~20 MB per step, so the population
  runs without it; per-decay yields are decay-data constants.
- Errors per compared step: `e_A`, `e_H` relative to the reference value;
  `e_P = Σ_g |ΔP_g| / Σ_g P_g,ref` (energy-weighted L1); `e_N` likewise on rates;
  `e_Gmax` = max relative error over groups carrying ≥ 1e-3 of reference photon power.
  `e_agg = max(e_A, e_H, e_P)`. A zero reference denominator skips that metric and is counted.
- Fluence of a case: the truth run's `fluence_n_cm2` at the last flux-on step.

## Gates

**G0 — execution integrity.** All 1,032 runs return 0; binary sha256 recorded. Yield table:
every nuclide's per-decay group yields agree across all Y runs and steps within 1e-9 relative.
Composition check: for each Y run and step, `Σ_n A_n y_n,g` reproduces ACTINV's own reported
group power within 1e-9 relative on groups carrying ≥ 1e-6 of step photon power. Coverage: in
every compared step of every U/T/S/B/C run, activity from nuclides never observed in any Y run is
≤ 1e-6 of step activity; otherwise photon metrics for that step are reported unavailable and the
count is stated. G0 failing on runs or yields stops the phase.

**G1 — the operator is well defined.** Background-subtracted superposition at unit amplitude:
(a) spectral: `U(m,mix,s) − B` vs `Σ_p w_p (U(m,p,s) − B)` for all materials and schedules;
(b) composition: for the three mixtures, `U(mix,p,s1) − B_mix` vs `Σ_e (w_e/100)(U_e − B_e)` for
p ∈ {fns, mix}, with pure-element runs from the CU/CB arms (Fe, W, Cu, Co from U/B).
**PASS** iff `e_agg ≤ 1e-9` at every compared step. Per-nuclide deviation (nuclides ≥ 1e-6 of
activity) is reported descriptively.

**G2 — accuracy envelope.** `L` vs `T` at every (m, p, s, a) and compared step. Regime
`R_vessel`: cases with fluence ≤ 3.2e20 n cm⁻² (1e13 for one full-power year).
**GO** iff `e_agg ≤ 1e-2` at every compared step in `R_vessel`; **STRONG** iff also ≤ 1e-3.
Reported in full: the e_agg map; per material and spectrum the largest fluence meeting 1e-3
and 1e-2 at every step; and the nuclide and step responsible for each case's worst error.

**G3 — certificate predictiveness (descriptive, not gating).** For every T case: `tau_p`
(`max_product_optical_depth`), `tau_bulk` (`max_burnup_optical_depth`) and fluence, each against
`e_agg`; count cases where `e_agg > tau_p`; report the rank correlation of each candidate with
`e_agg`. Any certificate proposed from G3 must be validated in a later protocol.

**G4 — shipped P70 flux-scaling certificate.** For every (m, p, s) and base amplitude
a0 ∈ {1e10, 1e13}, and every target a ≠ a0 in the S arm, with v = a/a0: the slider certifies iff
`tau_p(base)·|v−1| ≤ 1e-3`. For each certified pair, the scaled prediction reproduces
`walk_and_scale` (activity and heat ×v; `total_atoms_per_g` ×v; the photon source unscaled), and
is compared against the S solve at a. **PASS** iff `e_A ≤ bound` and `e_H ≤ bound` for every
certified pair. The errors on `total_atoms_per_g` and on the unscaled photon source are reported
descriptively, and all metrics are also reported against T.

## Pre-registered expectations (the author's, frozen here, not criteria)

- G1 passes near 1e-12.
- G2: the likely binding limit at the edge of `R_vessel` is **bulk** burn-up, not product
  burn-up: pure Co and pure W under the Maxwellian at 1e13 × 1 y have `tau_bulk` of order 1e-2
  (Co-59 ≈ 37 b, W-186 ≈ 38 b thermal). A G2 failure there would mean the envelope must be stated
  in optical depth rather than fluence.
- G4 is expected to fail somewhere. The first-order relative correction is `tau_p` times an
  amplification (a short-lived second-hop product per first-hop atom, or a large stable-product
  cross section relative to the direct radioactive channel), not `tau_p²`. `tau_p` is also a
  maximum over all products, relevant or not (the iron example's is Mo-86). And scaling ×v is
  wrong for any zero-flux background activity.

## Execution

Sequential, one job, inside the AGENTS.md systemd cgroup (MemoryMax 6G, CPUQuota 200%,
TasksMax 128), release binary `target/release/actinv` 1.3.1. The working tree at freeze carries
another session's uncommitted uncertainty-path changes (`restrict_tangents_to_covered`, internal,
`serde(skip)`); P75 specs declare no `uncertainty` block. The binary sha256 is recorded in every
checkpoint header and in the verdict. Raw results are deleted after extraction; extractions are
checkpointed to `target/p75/runs.jsonl` (resumable). The verdict is derived only by
`controls/check_p75.py` into `results/p75_verdict.json`, which sha-binds the manifest, the
checkpoint and the binary.

Pre-freeze profiling, timing only: heaviest units took 2.4–7.4 s at 131–220 MB peak RSS without
photon output. With photon output, a pulsed-schedule result reached 1.7 GB, which is why photon
sources are composed from the Y table. Estimated total ≈ 45 min.
