# P156 Amendment D — G4 references at the new targets; Reich–Moore channel spin

Registered after development run 3 (disclosed in the ledger) and before any evidence run. Tolerances, the twelve G4
targets and every other gate are unchanged. `controls/check_p154.py` is not edited: P156's checker rebinds its
reference functions for G4 only.

## Findings that require it

Development run 3 ran the checker's gates G0 and G2–G7 on candidate `e94b230`. G4 failed for five of the twelve targets. One
failure is a candidate defect; the others are G4 references that P154's four even-even targets never exercised.

- **Al-27, elastic and bound: candidate defect.** For six groups between 331 and 794 keV, the table differs from
  the reference by up to 10.9 %. NJOY MT=445 agrees with the reference within 0.5 % there. Al-27's resolved range is
  Reich–Moore with AJ of both signs. In LRF=3 the sign of AJ selects the channel spin, and NJOY RECONR (`csrmat`,
  loop `kchanl`) treats each (J, channel spin) as its own channel with the full g_J. The candidate grouped
  resonances by |J| alone, so resonances of different channel spin interfered in one R-matrix. With the
  correction the worst Al-27 group above 1 keV is within 5e-4 of the reference. Negative AJ occurs in five evaluations
  of the corpus (Al-27, Cl-37, Mn-55, Si-29, U-235). Only the first four carry both signs within one (l, J), so only
  they change; U-235 uses one sign per J and is unchanged bit for bit.
- **Cu-63, elastic: reference defect.** Cu-63's MF=4 elastic is LTT=3: Legendre up to 20 MeV, tabulated above. At
  20 MeV the two give W = 54,425 and 61,038 eV per event. P154's reference keeps only the tabulated value at
  20 MeV and interpolates W linearly from the last refined point below it (19.49 MeV). The step is spread over
  19.49–20 MeV, so groups 648–649 fail by 4.8 % and 8.9 %. The table keeps the Legendre form up to 20 MeV, as ENDF
  specifies, and agrees with the reference within 1e-4 in every group from 17 to 30 MeV outside 19.4–20 MeV.
- **Ta-181, B2 precondition: emulation defect.** Ta-181's first level is at 6.24 keV. Between 7 and 14 keV the
  recoil range of that level straddles E_d = 90 eV. HEATR's `disbar` integrates the damage per event with a fixed
  64-point Gauss–Legendre rule, and the integrand there has a step at E_d. P154's emulation used the exact
  integral at the ladder points, so it differs from NJOY MT=446 by 0.6–1.9 % in eight groups, more than the 0.5 %
  allowed. At 8.2, 8.9, 9.7 and 10.5 keV the 64-point rule differs from the exact W by +4.6, −2.5, −1.2 and
  +1.9 %; above 16 keV, by under 0.2 %. Ta-181's table agrees with the C2 reference within 1.5 %.
- **Be-9 and Li-7: vacuous comparisons.** Be-9's MT=446 comparison has no evaluable group, and Li-7's has three;
  its B2 precondition set has three. Each has more than 60 groups excluded because the uncovered bound exceeds
  0.5 %: these light nuclei's inelastic data are mostly in forms P154 does not cover. G4's last sentence says such a
  target "is reported with its uncovered share, not dropped". P154 Amendment C1 says a vacuous set fails. The two
  conflict for these targets.

## D1. Elastic reference at an LTT=3 switch

Where an MF=4 elastic section switches from Legendre to tabulated at E_t, W just below E_t comes from the Legendre
representation at E_t, and W at E_t from the tabulated one. The reference W steps at E_t. Everything else in P154
Amendment A1 is unchanged.

## D2. HEATR's level quadrature in the B2 emulation

In P154 Amendment C2, `L_MT` uses `W_ladder,MT` computed as `disbar` computes it: the 64-point Gauss–Legendre rule over
the centre-of-mass cosine of the pdf times the damage function, with a negative sum set to zero. `W_exact,MT` stays
the exact integral. The C1 precondition and the C2 reference are formed with this `L_MT`.

## D3. Comparisons with uncovered groups only

A G4 channel comparison (MT=446 or MT=447) that is vacuous is reported, not gating, when it has no group outside
tolerance and at least 10 groups excluded because their uncovered bound exceeds 0.5 %. That channel's precondition
set (B2 for MT=446, B1 for MT=447) is then reported too, provided it has no group outside its bounds. The target's
uncovered share is reported as before. Every other comparison of the target, including elastic and P154 G5's
bound, gates as registered.

## D4. Candidate correction

The candidate's Reich–Moore reconstruction keys its channels by (|J|, sign of AJ), with AJ ≥ 0 and AJ < 0 as
separate channels, each with g_J, as NJOY does. SLBW and MLBW are unchanged. A unit test fixes that resonances of
opposite sign add as independent channels. The shipped damage table is rebuilt with the corrected candidate.

The activation library v1.1.0 artifacts were built before this correction, and G7 keeps them byte-identical. The
library takes only capture and fission from the resonance range. Comparing the 0 K reconstruction before and after
the correction on 12,000 log-spaced energies per evaluation, thermal (0.0253 eV) capture is unchanged in all four.
The capture resonance integral above 0.5 eV changes by at most 3e-5 (Al-27 +2.6e-5, Cl-37 −3.1e-5, Si-29 +1.5e-6,
Mn-55 −3e-8). Pointwise capture changes by up to 16 % near isolated Al-27 resonances (345 keV), 2.1 % for Si-29,
1.1 % for Cl-37 and 4e-5 for Mn-55. The effect is disclosed in the data notice. Rebuilding the activation library is
outside P156.
