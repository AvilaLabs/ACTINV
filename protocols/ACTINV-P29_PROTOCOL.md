# ACTINV P29 — response-specific numerical control

Opened 2026-09-17 after P28 closed `P28-CONDITIONAL`. P28 established the qualified
regime: neutron projectile, fispact-709 groups, 293.6 K, infinite dilution or
finite-dilution on the six covered nuclides, the five qualified responses, and the
element applicability map (103 qualified, 5 qualified-with-ledger, 5 gap). P29 delivers
user-declared numerical criteria on selected responses at specified times, with per-
component error accounting and an exact/full fallback where supported. The CONDITIONAL
scope is inherited: cases requiring gap-recorded combinations stay out of the criteria
population.

Prior verdicts are preserved byte-for-byte and never rewritten.

## Why this phase exists

A result advertised as qualified needs the error attached to the response the user
asked for, at the time they asked for it — not a global claim about the solver. P29
makes criteria first-class: the user declares `within rel epsilon` / `within abs delta`
per (response, time); the runner discharges it against reference computation and names
each error component as rigorously bounded, empirically estimated, or unresolved.
Missing support yields `unmet`/`unestablished`, never a silent pass.

## Frozen scope

P29 may:

- add a `refinement` block to the study schema (the currently refused ACT-REFINE-01
  family) carrying per-(response, time) criteria and a declared resource limit;
- add reference-solve execution inside the study runner: each criterion-bearing case
  is re-solved at reference settings (`prune: none`, `bmin_atoms_per_g: 0`,
  `cram_order: 48`, `mode: coupled`) and the difference is recorded per component;
- add controls, evidence, checkers and documentation;
- qualify ACT-REFINE-01 only for the demonstrated envelope.

P29 may not:

- change base solver numerics or data;
- promise a user-selected total physical accuracy the data cannot support —
  computational error, nuclear/input uncertainty and predictive discrepancy stay
  separate in every report;
- treat two-resolution agreement as a convergence proof;
- let a criterion pass on a hidden or unaccounted component;
- run more than one repair amendment; a second failure closes FAIL.

## Frozen execution rules

- Protocol SHA-256 registered at opening; G0 seal binds hash, opening commit, prior
  verdicts, binary/data identities, the frozen control population and tolerances.
- The `p29_qualifying` partition seals at G0; its outputs are consumed once by the
  verdict gate.
- All builds/tests/jobs under the workstation cgroup rule.
- Criteria semantics: `abs` applies near zero (|x| < scale); `rel` elsewhere; a
  criterion on a response the case cannot produce is `unestablished`, not pass.
- Error components are reported individually with their class:
  - `solver_time_integration`: CRAM order/scheduler — empirically estimated by
    order-16 vs order-48 reference difference;
  - `population_pruning`: rate/reach pruning + bmin floor — empirically estimated
    by prune=none/bmin=0 reference;
  - `processing_collapse`: group collapse and artifact processing — bounded at the
    P25c/P28 tolerances already measured;
  - `unresolved`: any component not covered by a bound or estimate.
- Adaptive refinement stops at the declared resource limit and reports `unmet` if
  the limit was reached before criteria satisfaction.

## Minimum gates (roadmap)

1. Reference controls: analytic chains compared to closed-form Bateman solutions;
   independent dense/high-accuracy solves; stiff/long histories; near-zero outputs;
   an independent processing/refinement case.
2. Criteria discharge: every frozen criterion case reports satisfied / unmet /
   unestablished with the per-component accounting attached.
3. No unresolved error may masquerade as satisfied accuracy.

## Gates

### G0 — opening seal and scope freeze

Publish `results/g0_p29_seals.json` and `results/g0_p29_check.json`: protocol hash,
opening commit, prior verdicts, binary and data identity pins, the frozen criteria
control population, tolerances, and the sealed `p29_qualifying` partition.

### G1 — criteria mechanism

Extend the study schema with the `refinement` block, reference-solve execution and
per-component accounting; publish `results/g1_p29_refinement.json`. Independent
checker re-forms criterion verdicts from raw artifacts and rejects planted mutations.

### G2 — reference controls

Publish `results/g2_p29_controls.json`: analytic-chain controls, stiff/long-history
cases, near-zero outputs and the independent dense reference. Independent checker
re-derives the analytic solutions and rejects planted mutations.

### G3 — conformance and adversarial evidence

Publish `results/g3_p29_conformance.json`: criterion cases that must not pass
(unresolvable response, resource-limit exhaustion, forbidden envelope) recorded as
unmet/unestablished, plus the mutation battery. Independent checker replays the
rejections.

### G4 — verdict and closure

Publish `results/verdict_p29.json` and `controls/check_g4_p29.py`: prior verdicts,
gate ancestry, population accounting, and the verdict re-derived under the closure
rule — PASS only if every frozen control holds and no unresolved component passed
unchecked; CONDITIONAL if an amendment was used or a frozen control is unmet/
unestablished with downstream scope revised; FAIL otherwise.
