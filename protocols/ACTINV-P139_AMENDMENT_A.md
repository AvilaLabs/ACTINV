# P139 Amendment A — closed channels

Registered 2026-10-15, after the first P139 run and before any repair or rerun.

## First run (preserved)

Candidate: release build of `2fb931a`, SHA-256 `e8e3ec1f…b752`. Reference: release build of `0c3a573`. Record and log:
`results/failures/p139_initial/`. G2 and G3 passed: ENDF/B-VIII.1 W-186 (LRF=7, IFG=0) group factors were reproduced
independently within 1e-9. G4 failed on the IFG=1 synthetic file only: one group (361) differed from the LRF=2 run by
6.5e-8 (0.01 cm) and 7.5e-8 (0.1 cm), above the 1e-9 limit. The IFG=0 file passed within 1e-12.

## Cause

The TENDL-2025 W-186 LRF=2 totals exceed their partial widths by up to 2.8e-7 (rounding; 107 resonances). The
checker carried that excess in a competitive channel with Q = −122.6 keV, closed at every resonance energy (all below
20 keV). The implementation gave a closed channel zero width under IFG=1 but its listed width under IFG=0, a rule the
protocol did not state; the IFG=1 totals therefore lacked the excess. Neither the code's asymmetry nor a fixture that
puts width in a closed channel is physical: a channel closed at the resonance energy carries no outgoing flux.

## Amendment

1. Change under test, added: a channel whose pair threshold lies at or above the resonance energy contributes zero
   width, under IFG=0 and IFG=1 alike.
2. G4: the synthetic competitive channel uses Q = 0, so it is open at every resonance energy and carries the listed
   excess in both files. Everything else in G4, and all tolerances, are unchanged.
3. G1 adds a unit test of a closed channel under IFG=0.

All of G2–G5 are rerun on a new candidate built from the repair.
