# ACTINV-P40 — Scoped identical-data equivalence under named representation floors

**Status:** active | **Opened:** 2026-09-18 | **Parent:** P26b leg
`identical_data` | **Depends on:** P38 executability, P39 coverage

## Intent

The frozen 5e-4 identical-data tolerance can never pass on channels
where the two arms' data *representations* differ — P39 classified both
sampled residual classes as representation floors (isomer-split heritage
in ALARA's library; quasi-stable activity conventions), not ACTINV
production gaps. P40 converts the permanently-failing gate into a
bounded qualification: measure the divergence census-wide, tag every
divergence by class, and report the residual equivalence on
class-cleaned channels.

## Scope

- A stratified census of the 912 executable cases: every impurity
  element at wppm={1000,100000} x both spectra x all five time-pulsed
  irradiations, plus the four fe/w base cases -> **184 cases**
  (pre-registered count; the sample is complete on
  element x spectrum x irradiation strata at both wppm extremes).
- Per-case, per-cooling-time **full-nuclide activity vectors** for both
  arms, recovered from the persisted case directories
  (`case.stdout`/`alara.dmp`/`out.json`), not the top5 ledger fields.
- A deterministic divergence classifier:
  - `isomer_branching` — nuclides carrying ALARA metastable labels
    (`m`/`mN` suffix in the resolved name) that ACTINV does not emit
    (FENDL carries no isomer carrier for that channel).
  - `quasi_stable_convention` — nuclides whose ALARA idx half-life is
    finite but > 1e15 s (activity mathematically nonzero, physically
    zero).
  - `other` — anything else, including ACTINV-emitted nuclides absent
    from ALARA's output and magnitude differences on common nuclides.
- A scoped equivalence claim: for each case, both arms' totals are
  recomputed on class-cleaned nuclides (excluding `isomer_branching`
  and `quasi_stable_convention` tagged products) and the residual
  divergence distribution is reported with the measured floor shares.

## Pre-registered expectations (G0)

1. Every case's divergence decomposes into the three classes; `other`
   is quantified, never assumed zero.
2. `other` exceeding 1% of arm total activity on >5% of the census
   forces that nuclide/path into the verdict's named open classes —
   it cannot be folded into a floor claim.
3. The scoped claim reports measured medians and maxima per response
   (activity, decay heat, product inventory) over the census; no
   equivalence percentage may be asserted beyond the measured values.
4. `isomer_branching` is expected dominant under the IRDFF fast
   spectrum at shutdown (measured ~11% on Mn-58m1); deviations from
   this pattern are findings, not errors to hide.
5. The census is bounded to 184 cases; per-case arm failures are
   recorded, not skipped silently.

## Gates

- **G0:** protocol frozen; commit + expectations sealed
  (`results/g0_p40_seals.json`).
- **G1:** census executes all 184 cases; arm-failure rate and timing
  recorded.
- **G2:** classifier run on every executed case; class shares +
  class-cleaned residuals per case and in aggregate
  (`results/g2_p40_classes.json`).
- **G3:** negative controls — synthetic nuclides tag into the
  intended classes; stable nuclides with ALARA activity are flagged
  as inconsistencies, not silently absorbed
  (`results/g3_p40_check.json`).
- **G4:** verdict — scoped equivalence claim bounded by measured
  values, floor shares, `other` census, and the open classes
  (`results/verdict_p40.json`, `controls/check_g4_p40.py`).

## Honest bounds

- Class-cleaned equivalence is a measured residual, not a solver
  validation claim: the two arms still evaluate *different data
  representations* of the same physical problem.
- `isomer_branching` may underestimate: tagging is name-based on
  ALARA's labels; a nuclide split differently between arms but
  present in both stays in `other` (magnitude divergence) — which is
  the conservative direction.
- The census covers impurity-matrix cases only; pure-element and
  non-contract materials are out of scope.
