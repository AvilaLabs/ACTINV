# P126 Amendment A — Pathway attribution for power-driven steps

Registered 2026-10-15, after the first P126 checker run and before the repair is committed.

## Finding

The first run (`results/failures/p126_initial/p126_power.json`, candidate `3356b55`, SHA-256 `3227fbe1…b298`) passed
G0–G4 and failed G5. `max_substeps: 1` was byte-identical to the flux-driven run at the start-of-step multiplier
except at `/pathways` and `/pathway_closure`. The candidate suppressed trace-mode pathway attribution for
power-driven schedules, and the flux-driven run exposed the separate pre-existing defect registered as P134
(closure 6.851e-5 = 1 − multiplier).

## Amendment

1. The repair is P134's change: power-driven steps get pathway attribution over the realized sub-intervals, and the
   decomposition honours each step's multiplier and spectrum.
2. G5 is re-evaluated exactly as registered, on a candidate built from the commit carrying the repair. Every other
   gate and threshold is unchanged; G0–G4 are re-run on the same candidate.
