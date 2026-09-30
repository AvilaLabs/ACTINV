# ACTINV-P96 — Flux tally-error channel, directional-derivative gate with a converged-reference rule (successor to P93)

Date: 2026-09-30. Status: **frozen before any P96 evidence is produced.**

## Why this protocol exists

P93 (`41cfc21b…`) adds the `flux` uncertainty channel. Its candidate is `d12ba06a…` on branch
`p93-tally-error`. It passed G1 (tests) and G4, the sampling check: on the P32 cube with K = 64
lognormal samples, the mean variance ratio was about 0.995 and 64/64 cells were in band. P93's G3
directional-derivative gate **failed as registered**.

The first two G3 runs came from a defective checker and are kept as evidence:
- The perturbed specs kept `spectrum.total`, so ACTINV renormalised the perturbation away.
- The custom-structure baseline was read back from JSON as a list and compared with a tuple, so
  every custom10 case was excluded.

With both fixed to match the registered text, G3 gave:
- 0 of 144 cases excluded;
- 2,189 of 2,256 comparisons within tolerance (97.0 %; ≥ 99 % required);
- 2,205 within 100×, where all are required.

All 67 failures are at late cooling steps where |R| is at most 2.4 × 10⁻¹³ of that response's
peak over the schedule; none of them is above 10⁻¹². Here the CRAM solution is at its absolute
precision floor. In one reproduced case (100-year cooling, R ≈ 2 × 10⁻¹⁹ Bq/g against 10⁶ Bq/g
at shutdown), R(+) = 9.9e-19, R(−) = 1.6e-19 and R(0) = 2.4e-19. The central difference there is
noise, not a derivative. P93's G3 counted every nonzero R, with no rule for an unconverged
reference.

The P93 FAIL stands, and P93 does not merge on its own.

## Change under test

No solver change. The candidate is P93's `d12ba06a…`, built from branch `p93-tally-error`.
Only the G3 checker is revised, as `controls/check_p96.py`. It reuses `controls/check_p93.py` with
both checker fixes above.

## Gates

- **G0:** the protocol hash is registered before any P96 evidence exists.
- **G1, G2, G4:** P93's G1, G2 and G4, executed on candidate `d12ba06a…`, carried over as
  recorded (`target/p93/g1.json`, `g2.json`, `g4.json`), whatever their outcome. They carry over
  only if the candidate SHA-256 in each equals `d12ba06a…`. P93's G2 is still running when this is
  registered; its result is taken as it comes.
- **G3 (revised, fresh sample):**
  - Spec set:
    - every 20th P75b spec in sorted file order, **starting at index 10** (39 specs, disjoint from
      P93's set);
    - the first 5 of them re-expressed on the every-10th-boundary custom structure (one-cell mesh
      route, as P93);
    - the first 3 given with `descending: true`.
  - Setup, responses, h = 1e-4 and the tolerance
    `|pred − FD| ≤ 1e-4 · Σ_g |s_g e_g z_g| + 1e-10 · |R|` are all as in P93. The seed base is
    **20261001**. R(±) is the plain run with the absolute group flux φ_g (1 ± h e_g z_g), with
    no `total` renormalisation.
  - Exclusions, both counted:
    - (a) as P93: a case whose perturbed runs change `mode`, `pruned_states` or `total_states`;
    - (b) new, the **converged-reference rule**: a (case, response, step) comparison is excluded
      when its central difference is not converged: FD(h′), with h′ = 10h = 1e-3 and the same
      z, differs from FD(h) by more than the same tolerance. The rule tests only the reference, never
      the prediction.
  - Pass:
    - at most 5 % of cases are excluded under (a);
    - at most 5 % of comparisons are excluded under (b);
    - at least 99 % of the remaining comparisons are within tolerance;
    - every remaining comparison is within 100× the tolerance.
  - Reported only: P93's spec set re-scored under rule (b) with extra FD(10h) runs, and the |R|/peak
    distribution of the excluded comparisons.
- **G5:** CI replay, every step exits 0.

Merge (P93's change) only if G0–G5 all pass. A FAIL stands; thresholds are not lowered afterwards.
