# ACTINV-P77 — Trace-mode reservoir fix and exact mesh collapse fast path

Date: 2026-09-29. Status: **frozen before the patched binary is executed.**

## Changes under test

Worktree `../actinv-wt-p77`, branch `p77-trace-mesh` from HEAD `25fdb8d` (the main checkout's
uncommitted lane-2 work is not included).

1. **Trace-mode hybrid reservoir** (`crates/actinv-core/src/run.rs`). In trace mode, production
   into a nuclide that is also a bulk constituent was lost. From a tracked precursor
   (Fe-59 → Co-59 with Co in the alloy) it was dropped with no ledger entry. From another bulk
   nuclide (Ca-40(n,p)K-40 in concrete) it was dropped into the `bulk_production_dropped`
   ledger. Every such product now gets a tracked state, initially empty, alongside its constant
   reservoir. Production accumulates there and reacts and decays further (decay remap, reaction
   remap, and both derivative remaps). Trace mode then approximates only the constancy of the
   bulk.
2. **Activity merge** (`run.rs`, output loop and `response_snapshot`). A tracked state's activity
   was inserted over the bulk activity of the same nuclide (latent for fed reservoir nuclides).
   It is now added, and the photon-active entry is merged.
3. **Span-bounded collapse** (`crates/actinv-data/src/prepared.rs::collapse_row`). The loop runs
   only over the intersection of the flux window and the row's stored nonzero span. Outside the
   span each former term was `+0.0`, and the sum starts at `+0.0`, so the result is bit-identical
   by construction.

## Population and gates

Binary: the worktree release `actinv` (sha256 recorded at execution). Reference: `target/p75/actinv`
(`8113231b…`). Runner: the P75b population (`target/p75b/specs`, manifest `d872e559…`), re-executed
with the patched binary into `target/p77/`. Also: the two P75-session mesh profiles
(`target/meshprof/{fe_p21like,ss316_r2s}.json`, 60 cells, one thread) and `examples/fns_fe_5min.json`.

- **G0** all runs return 0; the protocol hash is registered.
- **G1 trace additivity:** on the P75b trace arm (R) with the patched binary, `e_agg ≤ 1e-6` at
  every compared step (P75b criterion and metric, checker `check_p75b.py` logic on the new
  checkpoint).
- **G2 coupled path unchanged:** for every P75b coupled-arm (C) run, and every auto-arm (A) run
  whose mode is coupled under both binaries, the extracted step data (all per-nuclide activities,
  heat, total atoms) are **bitwise identical** to the P75b checkpoint.
- **G3 mesh exactness:** for both mesh profiles, every cell record's `steps` content is bitwise
  identical between the reference and patched binaries.
- **Descriptive:** mesh wall time and cells/s for both binaries. Old-vs-new differences on
  `examples/fns_fe_5min.json` (auto mode, trace): max relative change in total activity and heat,
  and the nuclides changed. The P75b auto arm under the patched binary.

## Expectations (the author's)

G1 passes at round-off. G2 and G3 pass bitwise. The mesh speedup is modest (2–5×), because
(n,γ)-type rows span most of the window. FNS iron changes are tiny (< 1e-6), because pure Fe has
few bulk-product channels. Any control that pins trace-mode numbers for multi-element materials
will need re-baselining, which is a known consequence, not a failure of this phase.
