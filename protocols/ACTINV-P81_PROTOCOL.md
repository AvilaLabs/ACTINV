# ACTINV-P81 — Chunk-batched flux collapse for mesh cells (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Motivation (measured)

P80 (ledger 61) showed that skipping rows by structural reachability removes about 1.3 % of
triplets and does not speed mesh runs. A throwaway instrumented build of master `9c266d5` (never
committed) split network preparation per cell (one thread, 60 cells):

| stage (ms/cell) | fe_coupled | ss316_r2s |
|---|---|---|
| reaction assembly | 48.2 | 48.7 |
| of which: collapse of all 167,735 rows alone | 38.6 | 38.2 |
| of which: two chain-index lookups per row | 8.6 | 8.5 |
| decay-data scaling setup | 9.9 | 9.8 |
| whole cell (core) | 69.7 | 110.7 |

The collapse sums about 34 M stored cross-section values per cell, each row as one sequential
dependent chain of floating-point additions, so it is bound by addition latency and by streaming
the 275 MB prepared library once per cell. The scaling-setup stage clones the full nuclide table,
decay edges and decay constants on every run, even when no `decay_scale` option is given.

## Change under test

Branch `p81-batched-collapse` from master `9c266d5`.

1. **Batched collapse.** For a mesh chunk, the one-group value of every library row is computed for
   a batch of cells in one pass over the prepared library: rows outer, groups next, cells innermost.
   For every (row, cell) the additions are exactly those of the single-cell collapse
   (`PreparedLibrary::collapse_row`): the same stored values, the same flux entries, the same
   increasing group order over the row span intersected with that cell's nonzero-flux window, from
   the same `+0.0` start, then the same division by the same flux denominator (computed by the same
   code as reaction assembly). Only independent chains of different cells are interleaved, which
   does not change any result bit. Each cell's run then takes its row values from the batch in
   place of calling `collapse_row` for its own spectrum. The batch holds its cell's flux vector and is
   used only when that vector is bitwise equal to the run's base spectrum; any other spectrum (per-step
   spectra, other callers) goes to the groupwise library as before. Self-shielded collapse
   (`collapse_row_scaled`), `row_cross_section` and fission average energies are delegated to the
   groupwise library unchanged. Applies only when the prepared library is groupwise (no
   `uncertainty` block); otherwise the mesh path is unchanged. Batches are bounded to at most 16
   cells; the rows of a batch are split across the mesh's thread pool.
2. **No clone without decay scaling.** When `decay_scale` is absent, the run borrows the prepared
   nuclide table, decay edges and decay constants instead of cloning them. With `decay_scale`, the
   code path is unchanged.

Nothing else changes: no output field, schema, option, tolerance or diagnostic count.

## Gates

Reference binary: master `9c266d5` release, built before the candidate (sha256 recorded by the
checker). Candidate: this branch's release binary. Checker `controls/check_p81.py`; builds and runs
under the 6 GB cgroup cap. Timing keys removed before comparison: any key named `ms`, `elapsed_ms`,
`wall_s`, `wall_time_s` or `cells_per_s`, any key ending in `_ms`, and any key ending in `_s` inside a
`timing` object. Nothing else is normalised.

- **G0** protocol hash registered before the first candidate build.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-core -p actinv-data
  -p actinv-cli --all-targets -- -D warnings`; `cargo test --release -p actinv-core -p actinv-data`
  passes and includes a test that batched values equal `collapse_row` bit for bit over cells with
  different nonzero-flux windows, an all-zero flux, and interior zero groups.
- **G2 bitwise, mesh:** the three profiles (`fe_coupled`, `fe_p21like`, `ss316_r2s`; 60 cells, one
  thread): every output record identical between reference and candidate. Also `fe_coupled` with
  `threads` 3 and with `chunk_cells` 1 on the candidate: identical to the reference one-thread
  records.
- **G3 bitwise, single runs:** the P75b population (783 specs): full result JSON identical between
  reference and candidate.
- **G4 CI replay:** the runtime CI controls pass with the candidate.
- **G5 adoption threshold (pre-registered):** one-thread mesh wall time on `fe_coupled`, median of 3
  alternating runs each: the candidate must be at least **1.3×** faster than the reference.
  `fe_p21like` and `ss316_r2s` are reported, not gated.

Merge only if G0–G5 all pass. A G2/G3 difference, or a G5 miss, means the change is not merged.
