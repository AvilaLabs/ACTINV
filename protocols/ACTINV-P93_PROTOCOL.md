# ACTINV-P93 — Transport-tally statistical error as a first-order uncertainty channel

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Background

Flux importers (OpenMC statepoint, MCNP meshtal and mctal) read each tally group's relative
error. ACTINV only echoes it in mesh cell records (`source_relative_error`). It never reaches an
activation or heat band, and both the uncertainty ledger and the certificate list incident flux
under `excluded_sources`. P32 estimated the effect by brute force: 8 lognormal re-solves of a
64-cell Fe cube gave 0.8–2 % relative spread in total activity. That is too costly to do routinely,
and too few samples to quote as a band.

The first-order machinery already differentiates the CRAM recurrence along independent matrix
directions: the MF=33, `decay_constants` and `fission_yields` channels. Reaction rates are linear in
the group flux. The derivative with respect to one group's flux is therefore the reaction part of
the burn matrix, assembled at that group's flux alone.

A probe on the P32 cube (reference binary, 1 CPU) showed the cost of turning on uncertainty per
mesh cell today. The nominal mesh took 12.8 s for 64 cells. The MF=33-only uncertainty run had not
finished its first 16-cell chunk after 4 minutes, because the MF=33 channel always runs and needs
the covariance file. A tally-error band that is usable on meshes needs a mode without MF=33.

## Change under test

Branch `p93-tally-error`, created from the master commit that registers this protocol.

1. **Spectrum input.** `spectrum` gains an optional `relative_error` array. It has the same length
   and order as `flux_per_group` (honouring `descending`), and every value must be finite and ≥ 0.
   In mesh runs, each cell's `relative_error` from the flux file is used.
2. **Channel.** `uncertainty.channels` accepts `"flux"`.
   - It creates one parameter per input group with φ_g > 0 and e_g > 0, for each spectrum the run
     uses. For mesh runs these are the source groups of the flux file, before rebinning.
   - The parameter is the relative perturbation of that group's absolute flux, after any declared
     `total` scaling. Its standard uncertainty is e_g. Its direction is the reaction part of the burn
     matrix assembled at that group's flux alone, rebinned to the library groups with the same rebin
     the nominal run uses. That direction is scaled by the step's flux multiplier, as MF=33
     directions are.
   - Groups are treated as independent, so the channel variance is Σ (s_g e_g)², where
     s_g = ∂R/∂ln φ_g.
   - Every other spectrum-dependent choice (energy-dependent fission-yield selection, pruning,
     mode selection) is held at nominal. The ledger says so.
   - Requesting the channel is an error if a spectrum it applies to has no `relative_error`, or, in a
     mesh, if a cell has none. The error names the spectrum or cell.
3. **Flux-only mode.** `uncertainty.covariance` becomes optional only when `channels` is exactly
   `["flux"]`. Then MF=33 is not propagated and the band is the flux channel alone. The method and
   band name say so. With a covariance given, MF=33 runs as before, and the flux channel adds in
   quadrature like the other channels.
4. **Output.** Each response band gains a `flux_channel` block, shaped like the decay-channel block:
   - `variance`, `covered_parameters`, `total_parameters`;
   - per-parameter `sensitivities`: spectrum, group index as declared, energy bounds, e_g, s_g;
   - `fully_correlated_bound` = Σ |s_g| e_g, a reported upper bound on the standard uncertainty
     under any correlation between groups.

   When the channel runs, the ledger's and certificate's excluded-source entry for flux is narrowed
   to *systematic* flux uncertainty (transport model, geometry, transport nuclear data). The
   statistical tally error is what the channel covers.
5. **Unchanged behaviour.**
   - A run that does not request `"flux"` behaves as before, byte for byte. A `relative_error` in
     such a spec is accepted and not used.
   - Existing validation of MF=33 specs is unchanged.
6. `docs/SPEC.md`, `docs/QUANTITIES.md` and the uncertainty documentation describe the channel, its
   independence assumption, the flux-only mode and the correlation bound.

## Gates

Reference: master release `actinv`, whose sha256 is recorded by the checker at archive time as
`target/p93/ref_actinv`. Checker: `controls/check_p93.py`, run under the 6 GB cgroup cap; logs are
written to `target/p93/`.

- **G0:** the protocol hash is registered before the change is written.
- **G1 static:** fmt, clippy `-D warnings` and `cargo test --release -p actinv-core -p actinv-data`.
  The tests include:
  - Σ_g of the per-group directions reproduces the reaction part of the nominal matrix to a
    relative 1e-12, for a 709-group input and for a coarse custom input;
  - a one-group, one-reaction synthetic case at low burnup, where the relative standard uncertainty
    of the product activity equals e;
  - validation errors: channel requested with no errors, wrong length, negative or nonfinite values,
    a mesh cell without errors, and covariance omitted with channels other than `["flux"]`;
  - `descending` input mapped to the right groups.
- **G2 unchanged behaviour:**
  - (a) Bitwise with no uncertainty. The 783 P75b single specs (full result JSON without timing
    keys) and the three mesh profiles at 1 thread (bytes, with the footer compared without timing
    keys) match the reference.
  - (b) Bitwise MF=33 uncertainty. Every spec file under `examples/` that carries `uncertainty`
    matches the reference.
  - (c) Nominal invariance. On the G3 spec set, the flux-only run and the same spec without
    `uncertainty` agree on every inventory nuclide above 1e-12 of the step's maximum, and on the
    activity and heat totals, to a relative 1e-12. Whether they are bitwise identical is reported.
- **G3 directional derivatives (deterministic):**
  - Spec set:
    - every 20th P75b spec in sorted file order (40 specs);
    - 5 of them re-expressed on a custom structure made of every 10th fispact-709 boundary, with
      the flux summed;
    - 3 of them given with `descending: true`.
  - Setup:
    - each spec has e_g = 0.05 on every group with φ_g > 0;
    - flux-only mode;
    - responses `activity.total` and `heat.total`.
  - Method:
    - for 3 seeded random vectors z ~ N(0, 1) per spec, compute the predicted directional
      derivative Σ_g s_g e_g z_g;
    - compare it with the central difference (R(+) − R(−)) / (2h), where R(±) is the plain run with
      φ_g (1 ± h e_g z_g) and h = 1e-4.
  - Exclusions: a case is excluded, and counted, when either perturbed run's `mode`,
    `pruned_states` or `total_states` differs from the nominal run's.
  - Pass:
    - at most 5 % of cases are excluded;
    - at least 99 % of the remaining (spec, direction, response, step) cases with nonzero R satisfy
      |pred − FD| ≤ 1e-4 · Σ_g |s_g e_g z_g| + 1e-10 · |R|;
    - every remaining case is within 100× that tolerance.
- **G4 agreement with sampling (real tally):**
  - Setup:
    - the frozen P32 Fe-cube OpenMC tally (`~/nuclear-data/p32-work/chain/flux.ndjson`, 64 cells,
      709 groups, per-cell errors) and its mesh spec, on the v1.1.0 TENDL-2025 library;
    - K = 64 samples, drawn with P32's convention: each nonzero (cell, group) scaled by an
      independent mean-preserving lognormal factor exp(s z − s²/2), s² = ln(1 + e²), fixed seed;
    - each sample is solved with the candidate, without uncertainty;
    - the candidate's flux-only mesh run gives the first-order channel standard uncertainty.
  - Measure: for `activity.total` and `heat.total`, at every step, the ratio of the sampled
    variance to the first-order variance in each cell.
  - Pass:
    - the per-(step, response) mean of this ratio over cells lies in **[0.90, 1.10]**;
    - at least **90 %** of (cell, step, response) standard-deviation ratios lie in
      **[0.75, 1.33]**.
  - Reported only: the P32 8-sample spreads next to the first-order values, the correlation bound,
    and the run time of the flux-only mesh run against the nominal one.
- **G5 CI replay:** every step of the local CI replay exits 0.

Merge only if G0–G5 all pass. A FAIL stands; thresholds are not lowered afterwards.
