# ACTINV-P88 — Mesh cell result text written directly in canonical order (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Background

The P81 per-cell profile of `ss316_r2s` (SS316 with photon output, 9.2 MB of result text per cell)
spent about 34 ms converting the solver result to a `serde_json::Value` and 29 ms converting that
`Value` to text, against a solve of about 70 ms. The `Value` tree exists to produce canonical text:
object keys sorted (serde_json's map is a `BTreeMap`), the top-level `ms` removed, and the optional
`cell_result_fields` filter applied. `pruned_states` is also read from it.

## Change under test

Branch `p87-work` from master `ba97c5d`. It adds a module `actinv_core::canonical_json` with a serde
`Serializer` that writes JSON text directly with every object's keys in the order the `Value` route
produces. It mirrors that route's rules as follows:

- Entries whose keys arrive already strictly increasing are written in place.
- Otherwise the object's entries are sorted by key bytes, and for duplicate keys the last one wins,
  as in `BTreeMap::insert`.
- Scalars are converted through `serde_json::value::Serializer` and written by `serde_json`, so
  number and string formatting is by construction the same.
- Map keys follow serde_json's map-key rules for strings, chars, bools, integers, unit variants and
  newtype structs.
- Anything else, namely float keys, serde_json's private `$serde_json::` struct tokens, and any
  error, makes the writer return `Unsupported`. The caller then uses the unchanged `Value` route for
  that cell.

In `mesh.rs`, `solve_result` uses the writer. It drops the top-level `ms` and applies
`cell_result_fields` by top-level entry, and reads `pruned_states` by parsing that entry's value text
with `serde_json` and calling `as_u64`. Any mismatch in shape falls back to the unchanged route,
which keeps its error messages. Nothing else changes.

## Gates

Reference: master `ba97c5d`, `actinv` `0d8dc849…` (archived as `target/p88/ref_actinv`). Checker
`controls/check_p88.py`, under the 6 GB cgroup cap.

- **G0:** the protocol hash is registered before the first build with the change.
- **G1 static:** fmt; clippy `-p actinv-core -p actinv-data -p actinv-cli --all-targets -D warnings`;
  `cargo test --release -p actinv-core -p actinv-data`. The tests include unit tests that the writer's
  text equals `serde_json::to_string(&serde_json::to_value(x))` byte for byte for:
  - a real `RunResult` from a fixture run;
  - a synthetic value covering unsorted `HashMap` keys, integer and bool keys, duplicate keys through
    `flatten`, all four enum representations, `Option`, unit, `f32`, `-0.0`, subnormals, extreme and
    non-finite floats, 64-bit integer extremes, and strings and keys that need escaping or are
    non-ASCII.

  A further test checks that float keys and `RawValue` fall back.
- **G2 bitwise:** mesh output compared as bytes, except that the footer is compared as JSON without
  its timing keys. This covers `fe_coupled`, `fe_p21like` and `ss316_r2s` at 1 and 3 threads, the
  `group_workloads` variant, the resumed run (20 cells, then `resume`), and a `cell_result_fields`
  variant (`["steps", "ledger"]`, which drops `pruned_states` from the record), each against the
  reference on the same spec.
- **G3 CI replay:** every step of the local CI replay exits 0. It runs before the timed runs and
  never alongside them.
- **G5 adoption threshold (pre-registered):** `ss316_r2s` at 1 thread, median wall time over
  **5** alternating repetitions. The candidate must be at least **1.15×** faster. The expected gain
  is about 1.3× (about 45 ms saved out of about 176 ms per cell). The P82/P83 spread for identical
  code, 1.299× against 1.44×, is why this gate uses five repeats and a margin below the expectation.
  Reported only: `ss316_r2s` at 3 threads and the iron profiles.

Merge only if G0–G5 all pass. A FAIL stands; the threshold is not lowered afterwards.
