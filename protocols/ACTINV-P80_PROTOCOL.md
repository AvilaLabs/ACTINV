# ACTINV-P80 — Skip unreachable production rows in reaction assembly (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Motivation (measured)

Per-cell mesh profiling of the P77 binary (`ACTINV_P14_PROFILE`, one thread, 60 cells): network
preparation takes 74.5 ms per cell on the iron profile (solve 1.2 ms) and 78.2 ms on SS316 (solve
35 ms). Network preparation collapses every activation-library row (167,735 on TENDL-2025) under the
cell's flux, although a material can only ever populate a small part of the chart.

## Change under test

Branch `p80-reachable-rows` from `5066463` (master with P77).

1. `chain::reachable_columns`: chain columns reachable from the seed columns (initial composition
   and fed nuclides) through decay edges and through **any** reaction row's product, structurally
   (independent of flux values; fission rows reach their yield products when yields are given).
2. `assemble_reaction_rates` takes an optional reachable mask. With a mask, a row is skipped before
   its collapse only if **all** hold: its target column is unreachable; it is not a total-loss row
   (`zap = −1`); it is not a fission row (MT 18, zap 0); its product is mapped (`lmf ≠ −2`); its
   target is in the decay chain; its product `(zap, lfs)` is itself in the chain. Every other row is
   processed exactly as before, in the original row order. Kept: everything that feeds a ledger
   (targets absent from decay data, products without decay data, isomer fallback, unmapped
   products, fission balance/leakage) and every diagonal loss entry (burn-up and product optical
   depth diagnostics).
3. The mask is used only when the run has no `uncertainty` block (derivatives need every row), the
   prune mode is `reach` or `rate` (with `none` the unreachable columns stay in the solved matrix),
   and no schedule step declares its own spectrum. Otherwise behaviour is unchanged by construction.

Argument for exactness: a skipped row writes only entries in an unreachable column, which starts
at zero, receives no production, and is removed by reach or rate pruning before the solve; its
entries are off-diagonal or positive diagonal, so no optical-depth diagnostic reads them; no ledger
reads them. The tracked-reservoir set in trace mode can lose only states that nothing reachable
feeds, which pruning removes.

## Gates

Reference binary: master `5066463` release (`~/Documents/actinv-wt-p77land/target/release/actinv`
at build time of this protocol, sha256 recorded by the checker). Candidate: this branch's release
binary. Checker `controls/check_p80.py`; builds and runs under the 6 GB cgroup cap.

- **G0** protocol hash registered before the first candidate build.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-core -p actinv-cli
  --all-targets -- -D warnings`; `cargo test --release -p actinv-core` passes and includes a test of
  `reachable_columns` on a small chain.
- **G2 bitwise, mesh:** the three mesh profiles in `target/meshprof/` (`fe_coupled`, `fe_p21like`,
  `ss316_r2s`; 60 cells each, one thread): every output NDJSON record identical between reference
  and candidate after removing timing keys (keys named `ms`, `elapsed_ms`, `wall_s`, or ending in
  `_ms`/`_s` inside a `timing` object).
- **G3 bitwise, single runs:** the P75b population (`target/p75b/specs`): for every spec, the full
  result JSON identical between reference and candidate after the same timing removal.
- **G4 CI replay:** the runtime CI controls (the P77 landing replay set) pass with the candidate.
- **G5 descriptive:** cells/s on the three profiles, reference vs candidate; share of rows skipped.

Any G2/G3 difference fails the gate; the change is then not merged.
