# P126 — Kerma heating and power-driven schedule steps with automatic sub-stepping

Registered 2026-10-15, before any P126 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: POWER, kerma, NJOY-processed heating.

## Change under test

1. `kerma` gives a groupwise kerma table (`actinv-kerma-table-1`, MT=301 plus optional MT=444).
   `scripts/build_kerma_table.py` converts NJOY GROUPR/HEATR output into it. Each step reports instantaneous nuclear
   heating with its top contributors.
2. `power_W_cm3` or `power_W_per_g` on a step, instead of `flux`. The flux multiplier is renormalized on uniform
   sub-intervals, doubling them until the power drift is within `power_tolerance` (default 1e-3) or until
   `max_substeps` is reached. FISPACT-II normalizes once per step from the start composition; `max_substeps: 1`
   reproduces that.

## Gates

Checker: `controls/check_p126.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P126 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the kerma, power, spec and run tests.
  `scripts/test_build_kerma_table.py` passes.
- **G2, unchanged by default.** `examples/fns_fe_5min.json` is byte-identical apart from `ms`.
- **G3, kerma table from NJOY.**
  - NJOY2016 is run under the cgroup on the TENDL-2025 Fe-56 evaluation (RECONR → BROADR → HEATR → GROUPR on the
    709-group structure, with the weighting stated in the result). `scripts/build_kerma_table.py` converts the
    output.
  - The table's Fe-56 MT=301 values equal the GENDF values within 1e-12 relative.
- **G4, kerma heating.** The FNS Fe step heating equals Σ N_i·k_i·φ, recomputed independently from the run inventory,
  the table and the spectrum, within 1e-9 relative.
- **G5, power normalization.**
  - A power-driven Fe step (`power_W_per_g` chosen so the flux is near the FNS flux) reproduces the target power at
    every sub-interval start within 1e-12.
  - The reported end-of-interval drift is ≤ `power_tolerance` when converged.
  - `max_substeps: 1` equals a flux-driven run at the start-of-step multiplier, byte for byte apart from the
    power/kerma blocks and echoes.
- **G6, sub-stepping effect (report-only).** On a strongly burning case (B-10 or Gd-157, high flux, long step), the
  end-of-step power error with `max_substeps: 1` is tabulated against the default.
- **G7.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P126-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No fission-power normalization from Q-values (a follow-up), and no gamma heating transport. Kerma is local
deposition only.
