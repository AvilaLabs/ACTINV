# ACTINV-P78 — Retire the P70 flux-scaling shortcut in the live sweep

Date: 2026-09-29. Status: **frozen before the changed workbench is built or run.**

## Why

P70 (`fde8897`) answered a live-slider position that differs from the last solved one only in
`spectrum.total` by scaling the last result, and labelled it certified when
`tau_p·|v−1| ≤ 1e-3` (`tau_p` = `ledger.max_product_optical_depth`). P75 showed:

- **G4:** on TENDL-2025 the bound never certified (0 of 672 pairs), because `tau_p` is set on every
  run by Mo-86, whose MF=3 data exceed the unitarity bound
  (`docs/defects/unitarity-violating-partial-cross-sections.md`).
- **G2:** the premise is wrong where it would matter. Flux non-linearity at vessel fluence comes
  from bulk burn-up (Eu-151) and second-order production (Co-60, Zn-65, Re-186m in pure elements)
  as well as product burn-up. A product-chain optical depth does not bound the first two.

A solve on the live tier takes about a second. Option chosen by the owner (2026-09-29): turn the
shortcut off now (C); a provisional-then-verified display (B) may follow as separate work.

## Change under test

Worktree `../actinv-wt-slider`, branch `slider-flux-scale-off` from `6160035`.

1. `crates/actinv-gui/src/sweep.rs`: the live worker solves every position; `scale_flux_result`,
   `FLUX_SCALE_MAX_CORRECTION` and the scaling tables are removed.
2. `crates/actinv-gui/src/app.rs`: the "flux-scaled · certified" tier label is removed.
3. `crates/actinv-gui/src/smoke.rs`: the P70 leg becomes a P78 leg: two flux-normalisation points
   submitted in sequence; the second must land with no `flux_scale` block, a cache-hit record and a
   positive screen `kept_states`.
4. `controls/g1_p69_live.py`: the three P70 checks are replaced by the P78 checks below. The
   committed `results/g1_p69_live.json` (5 checks) predates the P70 checks and is regenerated.

The core ledger key `max_product_optical_depth` stays (schema unchanged; no run-output hash moves).

## Gates

Builds and runs under the repository cgroup cap (6G, `CARGO_BUILD_JOBS=1`).

- **G0** the protocol hash is registered before the first build.
- **G1 static:** `cargo fmt --all -- --check`, and `cargo clippy -p actinv-gui --all-targets --
  -D warnings` pass; `cargo test -p actinv-gui` passes.
- **G2 no residue:** no occurrence of `scale_flux_result`, `flux_scale"` or
  `FLUX_SCALE_MAX_CORRECTION` remains under `crates/actinv-gui/src`.
- **G3 live control:** `controls/g1_p69_live.py` exits 0 with the binary built from this branch,
  including `flux-only point was solved, not scaled` (the report's `flux_only_point_solved` is true).
- **G4 wasm lib:** `cargo clippy -p actinv-gui --lib --no-default-features --target
  wasm32-unknown-unknown -- -D warnings` passes if that target is installed; otherwise recorded as
  NOT RUN.

A failed gate is reported as failed; the change is not committed to master unless G1–G3 pass.
