# P134 — Trace-mode pathway attribution ignored the step's flux multiplier and spectrum

Registered 2026-10-15. The defect was found by the first P126 checker run (G5,
`results/failures/p126_initial/`). A repair was drafted in the working tree to locate the cause; it is not committed,
and no evidence is gathered before this registration is committed.

## Defect

The default trace-mode pathway decomposition (`pathways`, `pathway_closure` in every trace result) re-solves the
schedule with one unit state per bulk source. Compared with the main solve it:

1. fed every source at its unit-multiplier rate, whatever the step's flux multiplier. Attributions are off by the
   factor 1/multiplier, and `pathway_closure` equals |1 − multiplier|. P126 evidence: a flux-driven step at multiplier
   0.9999315 reports closure 6.851e-5 = 1 − 0.9999315.
2. used the base spectrum's rates on steps with a step-spectrum override, for both source feeds and tracked
   reactions.
3. zeroed the unit source states after a cooling step, so production in any irradiation after a cooling gap is
   missing from the attribution.
4. was suppressed for power-driven schedules (P126), whose multiplier varies by sub-interval.

All four are reported by `pathway_closure`, which no gate required to be small. Specs with flux 1.0, no
step-spectrum override and a single irradiation block before cooling (`examples/fns_fe_5min.json`) are unaffected.

## Change under test

The decomposition uses the matrices the solve used: per step, the step's spectrum (`rsubs[id]`), with source feeds
and reactions scaled by the multiplier applied, once per sub-interval for a power-driven step (the realized
`flux_multiplier_per_substep` and `substep_duration_s`). Unit source states are held at exactly 1, so a later
irradiation sources again. The suppression for power-driven schedules and its ledger note are removed. Suppression
for feed/removal and step-library schedules is unchanged.

## Gates

Checker: `controls/check_p134.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before the repair is committed.
- **G1.** fmt, clippy with `-D warnings`, the workspace tests, including the regression tests
  `trace_pathways_close_under_a_flux_multiplier_and_after_a_cooling_gap` and
  `power_step_in_trace_mode_reports_pathways_that_close`.
- **G2, unchanged where not triggered.** `examples/fns_fe_5min.json` and `examples/shielding_demo.json` are
  byte-identical to the reference apart from `ms`.
- **G3, closure.** On the FNS Fe spec (trace) with: (a) flux 0.37 on the irradiation step; (b) an
  irradiate–cool–irradiate schedule; (c) a step-spectrum override on an irradiation step; the candidate's
  `pathway_closure` is ≤ 1e-9, and for every nuclide above 1e-12 of the total atoms in any step the summed pathway
  atoms equal the inventory within 1e-9 relative, recomputed by the checker from the result.
- **G4, scaling.** For (a), every pathway's atoms equal 0.37 × the same pathway in the flux-1.0 run within 1e-9
  relative in the irradiation step (linearity in the multiplier).
- **G5.** CI green on the pushed commit, with the committed run-output baselines unchanged; any baseline that moves
  is listed and explained before it is regenerated.

A gate that fails is recorded as P134-FAIL with its evidence; repairs need an amendment registered first.
