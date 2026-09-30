# ACTINV-P82 — Write each mesh cell's result text once (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Motivation (measured)

After P81 (ledger 63) a one-thread `ss316_r2s` mesh run takes about 300 ms per cell but only 70 ms of it is
the solve. A throwaway probe of master `6751b04` (never committed) measured per cell: solve 69.8 ms,
`RunResult` → `serde_json::Value` 34.1 ms, field filter 0.8 ms, `Value` → text 28.9 ms; the result text
is 9.2 MB per cell (553 MB for 60 cells). The runner then parses that text back into a `Value`
(`serde_json::from_str`) only to serialize it again inside the cell record, which is the rest of the
per-cell time.

## Change under test

Branch `p82-raw-cell-result` from master `6751b04`. The cell record's `result` field holds the result
text as a `serde_json::value::RawValue` and is written verbatim, instead of being parsed into a
`Value` and serialized again. The text is produced exactly as before (`to_value`, remove `ms`, field
filter, `to_string`). With serde_json's `float_roundtrip` feature (enabled in every ACTINV crate),
parsing is correctly rounded and serialization is shortest-round-trip, and both paths use the same
sorted-key map, so parsing and re-serializing reproduce the text byte for byte. Skipping the round trip
therefore cannot change the output. The resumable-prefix and memo paths keep producing the text as
before. Adds the `raw_value` feature of `serde_json` to `actinv-core` (no new crate, `Cargo.lock`
unchanged). Nothing else changes.

## Gates

Reference binary: master `6751b04` release (`c2b2d688…`, the P81 candidate). Candidate: this branch's
release build. Checker `controls/check_p82.py`; builds and runs under the 6 GB cgroup cap. Mesh outputs
are compared **as bytes**, except for the footer record, which is compared as parsed JSON with the
timing keys `wall_time_s` and `cells_per_s` removed.

- **G0** protocol hash registered before the first candidate build.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-core -p actinv-data
  -p actinv-cli --all-targets -- -D warnings`; `cargo test --release -p actinv-core -p actinv-data`
  passes and includes a test that the spliced record equals the round-tripped record byte for byte for
  a result containing −0.0, subnormal, large and fractional floats, non-finite values (serialized as
  null), u64/i64 extremes, and strings that need escaping.
- **G2 bitwise, mesh:** `fe_coupled`, `fe_p21like`, `ss316_r2s` (one thread): identical output. Also
  `fe_coupled` with `group_workloads` true (memo path), and a resumed `fe_coupled` run (the first
  20 cells written by the candidate, then `resume` true) compared with the reference one-pass output.
- **G4 CI replay:** the runtime CI controls pass with the candidate.
- **G5 adoption threshold (pre-registered):** one-thread `ss316_r2s` mesh wall time, median of 3
  alternating runs each: the candidate must be at least **1.3×** faster than the reference.
  `fe_coupled` and `fe_p21like` are reported, not gated.

Single runs (`actinv run`) do not use this path; the P75b population is not rerun.
Merge only if G0–G5 all pass.
