# ACTINV-P83 — Verbatim mesh cell result text, gated on multi-threaded runs (exact)

Date: 2026-09-29. Status: **frozen before the candidate code is built or run on this branch.**

## Why this test exists, and what it is not

The same code change failed P82 (ledger 64): its one-thread `ss316_r2s` speedup was 1.299× against the
pre-registered 1.3×, and its `group_workloads` check compared against the wrong reference. P82's
FAIL stands and is not amended. This is a second and final test of that change, under a gate chosen
before any multi-threaded measurement. The reason for the new gate: in a mesh run, cells are solved and
turned into text in parallel on the thread pool, but the result text is parsed back and re-serialized
serially on the collecting thread before it is written. That serial work is what the change removes,
so its effect should grow with the thread count. That is a hypothesis; no multi-threaded timing of
either binary has been taken. **If P83 fails, the change is abandoned; there is no third test.**
The owner approved this test on 2026-09-29.

## Change under test

Branch `p83-raw-cell-result-threads` from master `831a256`, carrying the code of the P82 candidate
(`9210217`) unchanged: the cell record's `result` is a `serde_json::value::RawValue` holding the
result text, written verbatim instead of parsed and re-serialized, with serde_json's `raw_value`
feature enabled in `actinv-core` (no new crate; `Cargo.lock` unchanged). Byte identity rests on
`float_roundtrip`, as argued in P82.

## Gates

Reference binary: master `831a256` release. It has the same Rust sources as `6751b04`, whose build
`c2b2d688…` is used; the checker records hashes. Candidate: this branch's release build. Checker
`controls/check_p83.py`; builds and runs under the 6 GB, 300 % CPU cgroup cap, `RAYON_NUM_THREADS=1`
in the environment (the mesh pool size comes from the spec's `threads`). Mesh outputs are compared
**as bytes**, except the footer, which is compared as parsed JSON with `wall_time_s` and
`cells_per_s` removed.

- **G0** protocol hash registered before the first candidate build on this branch.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-core -p actinv-data
  -p actinv-cli --all-targets -- -D warnings`; `cargo test --release -p actinv-core -p actinv-data`
  passes and includes `spliced_result_text_equals_round_tripped_record_byte_for_byte`.
- **G2 bitwise, mesh:** reference and candidate identical on `fe_coupled`, `fe_p21like` and
  `ss316_r2s`, each with `threads` 1 and with `threads` 3. Each variant below is compared with the
  **reference run on the same spec**:
  - `fe_coupled` with `group_workloads` true;
  - resumed `fe_coupled`: a full candidate run cut to its header and first 20 cell records, then run
    again with `resume` true, compared with the reference's one-pass output of the resume spec.
- **G4 CI replay:** the runtime CI controls pass with the candidate.
- **G5 adoption threshold (pre-registered):** `ss316_r2s` with `threads` 3, wall time, median of 3
  alternating reference/candidate runs: the candidate must be at least **1.3×** faster. Reported, not
  gated: the same measure with `threads` 1 for all three profiles, and with `threads` 3 for
  `fe_coupled` and `fe_p21like`.

Merge only if G0–G5 all pass.
