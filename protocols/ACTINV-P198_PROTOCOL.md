# ACTINV-P198 — The MF=33 band where the covariance does not reach

## Purpose

Two faults in ACTINV's MF=33 band, both found while writing Learn ACTINV's chapter on uncertainty (2026-11-18):

1. **Every band on some problems fails.** Since P185, a state-resolved (MF=10) row takes its reaction's covariance
   through its share of the reaction total, group by group. TENDL-2025's K-41(n,α) row to Cl-38 is nonzero at
   115–871 keV, where the reaction total (MF=3) is zero. The share is undefined there, and the collapse stops with
   "activation row 72209 is nonzero where base row 72208 is zero". A problem whose chain reaches that row has no
   band at all. Learn ACTINV's steel bracket in its moderated field is one; P185's FNS problems are not.
2. **Bands that miss most of the uncertainty say they are complete.** In the shipped v1.2.0 sidecar, the capture
   (MT=102) self-covariance is exactly zero at 0.0253 eV for all 2,850 targets, and at 100 eV for 1,582. TENDL
   gives resonance-range uncertainty as resonance-parameter covariance (MF=32), which the band does not propagate.
   Co-59 capture is zero below 119.4 keV. In a moderated field, nearly all Co-60 comes from below that, so its band
   is about 0.007% and its coverage is reported `complete`. The exclusion of MF=32 is listed only generically in
   `excluded_sources`.

P198's change:

- A state-resolved row nonzero where its reaction total is zero takes no covariance in those groups, and the run
  continues.
- Each sensitivity's parameter reports `uncovered_rate_fraction`: the share of its reaction rate (flux × cross
  section × self-shielding factor, group by group, under its spectrum) in groups the covariance does not reach.
  Such a group is either:
  - one where no self-covariance component of the parameter's (target, MT) has a positive variance on an
    overlapping covariance bin; or
  - for a state-resolved row, one where its reaction total is zero.

  The field is absent when the share is zero. The variance counted is the diagonal of a square relative or absolute
  block, or an LB=8/9 factor.
- A band with a propagated parameter (covered, not excluded, nonzero sensitivity) whose share is at least 1%:
  - reports their number as `parameters_with_uncovered_energies`;
  - reports the largest share over its propagated parameters as `largest_uncovered_rate_fraction` (absent when
    zero);
  - has coverage `partial`, its own and its `cross_section_mf33` channel's;
  - has a note on that channel naming MF=32.

No band's value changes. P198 checks five things:

- the change touches nothing else;
- the shares and bands equal an independent recomputation;
- the reporting follows the rule;
- the failing problem runs;
- the case it was made for is reported.

It does not make the band include resonance-range uncertainty. That needs MF=32, or a sidecar that fills the
range (P188's does so for the reactions IRDFF-II evaluates). P198 also does not check that the band is right.

## Disclosure of what has been seen

- The census in Purpose, read from the v1.2.0 sidecar. Co-59 MT=102's diagonal by energy was read: 0 below
  119.4 keV, then 21–42% up to 9 MeV, 2.5–19% above.
- Learn ACTINV development runs of the bracket problem:
  - with master (`6cd018e`): the error above;
  - with ACTINV 1.7.2 (before P185): its bands, including Co-60's 0.007%, Co-58's 0.0014% and Mn-54's 29%.
- Unit tests, all of which pass with the change:
  - `state_row_nonzero_where_its_total_is_zero_is_reported_not_refused` and
    `rate_where_the_variance_is_zero_is_reported` (`actinv-data`);
  - `rate_outside_the_covariance_leaves_the_band_partial` (`actinv-core`).

  The `actinv-data` and `actinv-core` library tests pass, and workspace clippy is clean.
- One development run of the candidate binary on problem C (below). Of it, the following were seen:
  - the exit status (0) and the run summary (3 steps, 268 states);
  - the result of this protocol's G3 and G4 code. Both gates had no problems: largest band difference 9.7e-15,
    largest share difference 2.0e-15, and Co-60 partial with a share of at least 1% at every step.

  No band value, relative standard deviation or share value was printed.
- One development run of the baseline binary on problem C: exit 1 with the error above. Nothing else was read.
- No run of problems A or B has been made with either binary.

## Data and binaries

- **Activation library:** v1.2.0's `tendl-2025-neutron-709g.npz`, SHA-256 `ec4c72bf…`.
- **Covariance:** v1.2.0's sidecar `tendl-2025-neutron-709g.cov.npz`, SHA-256 `c19dec86…`.
- **Bracket problem:** `web/learn/first-irradiation/files/bracket.json`, SHA-256 `32811eb6…`.
- **Candidate:** `actinv` built from a clean checkout of the registration commit.
- **Baseline:** `actinv` built from a clean worktree of `6cd018eebfdd83b2ce28e8fc7a723fbeb9f61472`, master before
  the change.

## Problems

Each problem has the uncertainty channel `cross_section_mf33` only, with the v1.2.0 sidecar.

- **A** and **B:** P185's two problems, with Amendment A's name Nb92m1. Both use the FNS spectrum, 300 s
  irradiation, then cooling steps of 66 s, 3,600 s and 86,400 s.
  - A: iron.
  - B: 40 wt% Ni, 20 Co, 20 Al, 10 W, 10 Nb.
- **C:** Learn ACTINV's 20 kg SS316 bracket in its moderated D-T field (25% 14 MeV, 55% 1/E, 20% thermal), with
  0.1 wt% Co (iron 66.8 wt%) and no photon section. The schedule is 30 days at its flux, then cooling steps of 1 day
  and 30 days. Responses: the activities of Co-60, Mn-54, Co-58, Cr-51, Fe-55 and Mn-56.

Each problem is run with each binary (six runs) by `controls/p198_harness.py`.

## Gates

- **G0:** the SHA-256s of this protocol, `controls/p198_harness.py` and `controls/check_p198.py` are in
  `protocols/protocol_hash.txt`, along with those of P185's harness and checker, which they import.
- **G1 (provenance):**
  - the pins above;
  - the candidate built at the registration commit and the baseline at the commit above, both from clean trees,
    with exit status 0;
  - the builds and runs inside a bounded scope (at most 6 GiB, no swap, 128 tasks and 4 CPUs);
  - six runs, each with the binary whose hash its build recorded. Every run but the baseline's C exits 0;
  - output and problem files whose hashes match the record.
- **G2 (nothing else changed),** baseline against candidate, on A and B at every step:
  - each response's activity, MF=33 standard uncertainty, and sensitivities (library row and value) are
    bit-identical;
  - the excluded blocks and uncovered rows are identical.
- **G3 (the numbers),** for every response at every step of the five runs that exit 0:
  - **Share.** Every covered parameter's reported share (absent as 0) equals the checker's own computation within
    1e-12. The checker computes it from the library's cross sections, the sidecar's NPY members and the problem's
    spectrum, by the rule above.
  - **Band.** The square of the reported `mf33_standard_uncertainty` equals s^T C s, computed by P185's G3 code
    with P185's tolerances. P185's rule for a row nonzero where its total is zero (refuse) is replaced by this
    protocol's (no share there).
- **G4 (reporting and the cases):**
  - In the candidate, every band's `parameters_with_uncovered_energies` and `largest_uncovered_rate_fraction` equal
    the checker's count and maximum over the reported shares.
  - A band with that count or a state-resolved parameter is `partial`, its own coverage and its channel's.
  - The MF=32 note is present exactly when the count is nonzero. P185's MF=40 note is present exactly when there is
    a state-resolved parameter.
  - An uncovered parameter reports no share, and no parameter reports a zero share.
  - The baseline reports none of the new fields.
  - The baseline's C exits nonzero with the error above. The candidate's C has a band at every step, and Co-60 is
    `partial` with a share of at least 1% at every step.
- **G5 (observation):** each response's relative standard deviation, largest share and coverage at every step of the
  five runs. These are recorded; no threshold applies.
- **G6:** CI green on the evidence commit.

P198-PASS if G0–G6 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P198-PASS, filled by the checker from the record:

1. "In ACTINV's MF=33 band, a covered parameter that takes 1% or more of its reaction rate at energies where the
   covariance is zero (in TENDL-2025, the resonance range, whose uncertainty is MF=32) leaves the band's coverage
   partial, and the band reports how many such parameters it has and the largest share."
2. "A state-resolved (MF=10) row that is nonzero where its reaction total is zero no longer stops the run; it takes
   no covariance there and that rate is reported outside the band."
3. "For Learn ACTINV's steel bracket with 0.1 wt% cobalt after 30 days in its moderated field, the cross-section part
   of the relative standard deviation of the Co60 activity is {a}%, and {b}% of the Co-59 capture rate behind it is
   at energies TENDL-2025's MF=33 covariance does not reach."

Sentence 3's {a} and {b} are taken at the end of irradiation; {b} is the share of the Co-59 capture parameter
(target Co59, MT 102) with the largest absolute sensitivity in Co-60's band.

## Commands (coordinator)

- Run `controls/p198_harness.py build`, `observe` and `assemble`, then `controls/check_p198.py`.
- Run them from the registration commit's checkout, inside that bounded scope.
- Outputs go under `target/p198-work` and are deleted after the verdict, along with the baseline worktree.
