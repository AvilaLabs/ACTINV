# P125 — Fission parity: spontaneous-fission products, fission count and burn-up, SF neutron source

Registered 2026-10-15, before any P125 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: SF products, NUMBER OF FISSIONS and burn-up, SF neutron
source, CUMFYLD.

## Change under test

All parts are opt-in.

1. `fission_yields.spontaneous {files, nubar_endf, nubar_table}`. SF branches are routed to products by MT=454
   independent SF yields. SF branches without yield data stay on leakage and are listed.
2. `options.fission {energy_per_fission_MeV}` adds `StepOut.fission`:
   - the cumulative induced and spontaneous fission count, from an exact companion CRAM solve with counter states
   - burn-up in % of initial heavy-metal atoms (Z ≥ 90) and in MWd/kg HM
   - the SF neutron source Σλ·BR_SF·N·ν̄, with uncovered nuclides listed

## Gates

Checker: `controls/check_p125.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P125 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the new fission, chain, fission_count,
  spec and run tests.
- **G2, unchanged by default.** `examples/fns_fe_5min.json` and an existing fission-yield example (U-235 or the P43
  study spec, if runnable) are byte-identical apart from `ms`.
- **G3, fission count.**
  - For a U-235 sample in the FNS 709-group spectrum, with `options.fission` and a yield file:
    - induced fissions equal ∫ σ_f φ N_U235(t) dt, recomputed independently from the run's U-235 inventory per step
      and the one-group MT=18 rate (`inspect xs`), within 1e-6 relative
    - burn-up % and MWd/kg follow by the documented arithmetic within 1e-12
  - For a pure-decay Cf-252 sample (decay-only, no SF yield file), spontaneous fissions equal N0·(1−e^{−λt})·BR_SF
    within 1e-9.
- **G4, SF neutron source.**
  - Cf-252 neutrons/s/g equals λ·BR_SF·N·ν̄ with ν̄ from the ENDF/B-VIII.0 decay file's MT=452 (3.7676), within
    1e-12.
  - Nuclides with SF but no ν̄ are listed.
- **G5, SF products.** No SF yield sublibrary is installed locally, so routing is checked on a synthetic MT=454
  tape (unit tests), and this gate is reported as data-limited until an ENDF/B sfy file is installed. When one is,
  the Cf-252 product inventory after 1 y must equal λ·BR_SF·N̄·Y_i integrated, within 1e-9.
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P125-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

FISPACT-II's "NUMBER OF FISSIONS" is a change in fissionable-nuclide count. ACTINV reports the rate integral and
documents the difference. 200 MeV per fission is a stated default, not a FISPACT constant.
