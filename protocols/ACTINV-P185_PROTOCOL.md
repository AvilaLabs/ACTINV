# ACTINV-P185 — State-resolved (MF=10) product rows in the MF=33 band

## Purpose

ACTINV's activation library holds 93,417 product rows. 40,011 of them (43%) are state-resolved: TENDL-2025 gives the
production of each final state in MF=10 (LMF=10). Since P11, the MF=33 band has left those rows out. MF=33 gives the
covariance of the reaction, and the split between states (MF=40) is outside the propagated channels. An activity
made through such a row therefore has no cross-section uncertainty at all. P184's open item was one case: Fe-53's
band is about 1e-13, although Fe-54(n,2n) is covered. Others include Ni-58(n,p)Co-58, Co-59(n,2n)Co-58,
Al-27(n,2n)Al-26, W-186(n,2n)W-185, Ta-181(n,2n)Ta-180 and Nb-93(n,2n)Nb-92m.

P185's change: a state-resolved row takes its reaction's MF=33 covariance. Its perturbation is the reaction's
relative perturbation, fully correlated with the reaction total. Each affected band reports:

- `parameters_from_reaction_total`, the number of such parameters;
- coverage `partial`;
- a note on its `cross_section_mf33` channel that the isomer-branching (MF=40) uncertainty is not propagated.

The band does not become complete: the branching uncertainty is still missing, and the band says so. P185 checks
four things:

- the change touches nothing else;
- every band equals an independent recomputation;
- the reporting is right;
- the change reaches the cases it was made for.

It does not check that the band is right. Agreement with brute-force sampling is a later protocol.

## Disclosure of what has been seen

- The row census above, and the list of affected products, from the activation library's index.
- Unit tests:
  - `state_resolved_rows_take_their_reaction_covariance` (`actinv-data`): two states that sum to the total
    reproduce its variance;
  - `state_resolved_parameters_leave_the_band_partial` (`actinv-core`).

  Both pass with the change. The `actinv-data` covariance tests and the `actinv-core` library tests pass.
- One development run of the candidate binary. Setup: the FNS spectrum, 21 steps, a 40/20/20/10/10 wt% Ni/Co/Al/W/Nb
  mixture, responses Co-58 and Al-26, v1.2.0 sidecar.
  - Its output structure was read: Co-58 has 206 state-resolved parameters out of 2,251 covered, with coverage
    `partial`; 9,865 sensitivities; no excluded blocks.
  - Co-58's activity and its relative standard deviation at the end of irradiation were seen (340.5 Bq/g, 5.04%).
  - This protocol's G3 and G4 code was run on that output. Every band equals the checker's recomputation (largest
    relative difference 8.5e-16), and Co-58 and Al-26 take the rule at every step.
- No baseline run and no run of problem A has been made.

## Data and binaries

- **Activation library:** v1.2.0's `tendl-2025-neutron-709g.npz`, SHA-256 `ec4c72bf…`.
- **Covariance:** v1.2.0's sidecar `tendl-2025-neutron-709g.cov.npz`, SHA-256 `c19dec86…`, the shipped TENDL-2025
  covariance. P184's sidecar is not used (ledger 341).
- **Candidate:** `actinv` built from a clean checkout of the registration commit.
- **Baseline:** `actinv` built from a clean worktree of `b7629fb05213c15b21b01ace1adf9627d4aa93cb`. That is master
  before the change: P184's verdict plus ledger 341, with the same code as `6795423`.

## Problems

Both problems use `examples/fns_fe_5min.json`'s spectrum (FNS, 709 groups) and the activation and decay data above.
The schedule is 300 s at the example's flux, then cooling steps of 66 s, 3,600 s and 86,400 s. The uncertainty
section has channel `cross_section_mf33` only, with the v1.2.0 sidecar.

- **A:** iron (100 wt% Fe). Responses: the activities of Fe-53, Mn-54, Mn-56, Cr-51 and Mn-57.
- **B:** 40 wt% Ni, 20 Co, 20 Al, 10 W, 10 Nb. Responses: the activities of Co-58, Co-57, Ni-57, Co-60, Al-26,
  Na-24, W-185, W-187 and Nb-92m.

Each problem is run with each binary (four runs) by `controls/p185_harness.py`.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1 (provenance):** the record shows:
  - the pins above;
  - the candidate built at the registration commit and the baseline at the commit above, both from clean trees,
    with exit status 0;
  - the builds and runs inside a bounded scope (at most 6 GiB, no swap, 128 tasks and 4 CPUs);
  - four runs with exit status 0, each with the binary whose hash its build recorded;
  - output and problem files whose hashes match the record.
- **G2 (nothing else changed),** baseline against candidate, at every step of both problems:
  - every response's activity is bit-identical;
  - a response with no state-resolved parameter in the candidate's band has the same MF=33 standard uncertainty
    within `1e-12` relative. Such a parameter is LMF=10, covered, not excluded, with nonzero sensitivity;
  - the candidate excludes no block (target, MT, MT1, reason) that the baseline did not;
  - no row is uncovered in the candidate that was covered in the baseline;
  - every row newly covered is state-resolved.
- **G3 (the band),** for every response at every step of all four runs: the checker computes s^T C s with its own
  code. The inputs are:
  - the reported sensitivities s over the covered parameters;
  - the v1.2.0 sidecar's components, read from its NPY members;
  - the library's cross sections;
  - the problem's spectrum.

  The collapse follows ACTINV's definitions:
  - relative components weight each row by its own cross section;
  - absolute and short-range components weight it by its ratio to the reaction total;
  - LB=8 contributes `F·ΔE_k/ΔE_j` and LB=9 `F·(1 − ΔE_j/ΔE_k)`;
  - cross components count in both orientations;
  - the blocks the run reports excluded are left out.

  It equals the square of the reported `mf33_standard_uncertainty` within `1e-6` of the larger of the two, plus
  `1e-12` times the same form with absolute values.
- **G4 (reporting):**
  - Every sensitivity's (target, MT, ZAP, LFS, LMF) equals its library row's.
  - Its `covariance_covered` follows the run's rule:
    - baseline: (target, MT) has an MF=33 self-covariance and LMF ≠ 10;
    - candidate: (target, MT) has an MF=33 self-covariance.
  - In the candidate, a band with a state-resolved parameter reports their number as
    `parameters_from_reaction_total`, coverage `partial` (the band's and the channel's), and the MF=40 note. A band
    without one reports neither the field nor a note. The baseline never reports the field.
  - Fe-53 in A and Co-58 in B have a state-resolved parameter at every step.
- **G5 (observation):** each response's relative standard deviation at every step, baseline and candidate. These
  are recorded; no threshold applies.
- **G6:** CI green on the evidence commit.

P185-PASS if G0–G6 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P185-PASS, filled by the checker from the record:

1. "In ACTINV's MF=33 band, a state-resolved (MF=10) product takes its reaction's covariance, fully correlated with
   the reaction total; its isomer-branching (MF=40) uncertainty is not propagated, and the band's coverage is
   reported partial."
2. "For {material} in the FNS spectrum after 5 minutes' irradiation, the cross-section part of the relative
   standard deviation of the {nuclide} activity 66 s after shutdown is {a}% without state-resolved coverage and
   {b}% with it (TENDL-2025 covariances, isomer branching not included)." This is one sentence per response that
   has a state-resolved parameter at every step.

The second sentence is about TENDL-2025's covariances, not P184's sidecar. A sentence about ACTINV's default band
applies from the first release that carries the change.

## Commands (coordinator)

- Run `controls/p185_harness.py build`, `observe` and `assemble`, then `controls/check_p185.py`.
- Run them from a worktree at the registration commit, inside that bounded scope.
- Outputs go under `target/p185-work` and are deleted after the verdict, along with the baseline worktree.
