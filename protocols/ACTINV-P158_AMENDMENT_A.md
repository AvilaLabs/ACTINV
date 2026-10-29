# P158 Amendment A — value checks get P157's absolute floor

Registered before any evidence run, after development run 1 found the problem. This is not the phase's repair
round: no evidence run has been made, so that round is still available. No other gate or criterion changes.

## Finding that requires it

Development run 1 ran the worked example at 72 samples, on the candidate as it then stood. It took 54 min 16 s
wall time with a 2.5 GB peak, and no sample failed. The per-sample verification made 356 solves for 501 points
(126 skipped as capped or with no balance left) and failed 4. All four were `activity:Nb94`, role
`value_matrix_only`, sample 54, steps 2 to 5. The composed value was 2.08e-162 Bq/g, the solve gave exactly 0,
and the relative deviation was 1. Every passing point with a deviation agreed to 2.8e-15, and so did the
nominal-data points. The record is in `results/failures/p158/dev_run_1/verification.json`.

The matrix contains no niobium. Its Nb-94 population is about 1e-150 atoms/g. The solver's numerical floor in
these runs is about 1.9e6 atoms/g (`numerical_floor_atoms_per_g`, alpha0 times the largest population), about 156
orders of magnitude higher. A population that far below the floor is round-off. One solve leaves it as a tiny
positive number; another gives a tiny negative one, which the solver zeroes (`negative_atoms_zeroed`). Neither
number means anything. A purely relative check fails on this noise.

For the same sample and step, Nb-94 is 106 Bq/g at nominal and 442 Bq/g in the worst case. A deviation of 2e-162
Bq/g cannot change any reported value, limit or band.

P157's checker compares composed and solved values to 1e-6 relative, plus an absolute floor of 1e-12 times the
endpoint's scale (`ABS_FLOOR` in `controls/check_p157.py`). This phase's G4 uses the same rule. Section 5's value
check in the code lacked that floor.

## A1. Value checks have an absolute floor

- **The floor.** For a value point (section 5, Values), the floor is 1e-12 times the endpoint's scale in that
  sample at that step. The scale is the largest magnitude among the sample's matrix-only, nominal and worst-case
  values.
- **The check.** If the relative deviation exceeds 1e-6, the check applies to what is left after the floor:
  max(|solved − composed| − floor, 0) / max(|solved|, |composed|) must be at most 1e-6.
- **The record.** Such a point records `rel_dev_before_floor` and `floor` beside `rel_dev`. A point within 1e-6
  is unchanged and records neither.
- **What does not change.** Limit points (0.99 and 1.01), contribution points and P157's nominal-data
  verification stay as they are, so G2's comparisons are unaffected.

## A2. Evidence

G6 and G8 are judged on a 72-sample run of the candidate with A1. The handbook states that run's wall time.
Development run 1 is disclosed in the evidence.
