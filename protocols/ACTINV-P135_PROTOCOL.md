# P135 — Depletion table (FISPACT-II `DEPLETION`, tracker row 19)

Registered 2026-10-15, after implementation (merged in `e9a37c2`) and before any evidence.

## Change under test

Opt-in `summary.depletion: true` adds `steps[].summary.depletion` with, per initial nuclide: atoms at step start and
end, depleted atoms and fraction (step and cumulative), total removal rate per atom, and its split into decay and
each reaction MT, with shares. Reaction rates are the solver's own loss-row rates times the step multiplier (the
mean multiplier for a power step). In trace mode the reservoir is depleted analytically, N·exp(−R·dt). With an
MF=33 covariance, each reaction channel carries a relative standard uncertainty from the flux-weighted collapse of
its group covariance, and the total its joint uncertainty. Ledger and certificate record the request only when made.

Beyond FISPACT-II: channel shares are the solver's exact rates, so they sum to the total removal; per-channel and
total uncertainties come from the full group covariance including cross-MT correlation.

## Gates

Checker: `controls/check_p135.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests (including the `depletion` module and
  `depletion_prepared_tests`).
- **G2, default unchanged.** `examples/fns_fe_5min.json` and `examples/shielding_demo.json` byte-identical to the
  reference apart from `ms`.
- **G3, coupled.** FNS Fe (coupled, depletion on, and a run with flux scaled so that Fe-56 depletes measurably):
  per nuclide, start/end atoms equal the inventory; depleted atoms = start − end; channel shares sum to 1 within
  1e-12; the decay channel equals ln2/T½ from an independent MT=457 read; each reaction channel equals an independent
  collapse of the library NPZ loss rows (Σσφ·1e-24 × multiplier) within 1e-9.
- **G4, trace.** FNS Fe (trace): depleted fraction equals 1−exp(−R·t) with R from the same independent collapse,
  within 1e-9, cumulatively across steps.
- **G5, uncertainty.** FNS Fe with the TENDL-2025 MF=33 covariance: each covered channel's relative uncertainty
  equals an independent sqrt(wᵀ C_rel w) (w_g = σ_g φ_g / Σσφ, from the covariance NPZ) within 1e-6.
- **G6.** CI green on the pushed commit; committed baselines unchanged.
