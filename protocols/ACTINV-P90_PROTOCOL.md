# ACTINV-P90 — Lean R2S mesh output: step-level field selection built directly

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Background

An `ss316_r2s` mesh cell writes about 9 MB of result text. A mesh-coupled R2S workflow reads one
thing per step from it: the photon source groups. `cell_result_fields` can keep only whole top-level
fields, so the OpenMC adapter (`contrib/openmc_r2s`) keeps all of `steps`, which includes the full
inventory, activities and per-nuclide photon lines. The full result also goes through a
`serde_json::Value` of everything before the filter applies. P88 measured the Value route and text
serialization at 26–58 ms of a ~156 ms `ss316_r2s` cell.

## Change under test

Branch from master `e546292`.

1. `cell_result_fields` also accepts dotted entries `steps.<field>[.<key>…]`. `<field>` must be a
   `StepOut` field. Further segments descend through JSON objects only; a path that reaches an array
   or scalar with segments left is invalid. When any `steps.` entry is present, plain `steps` must
   not also be present (validation error). Every emitted step object then holds `step` plus the
   selected paths, and objects on a path keep only the selected keys. A selected path whose value is
   absent in a step (for example `photon_source` without photon output) is absent there, as in the
   full output.
2. With dotted entries, the cell text is built from the selected fields only. Each selected
   top-level `RunResult` field and each selected `StepOut` field is serialized on its own through the
   same `serde_json::Value` route and assembled in canonical (sorted-key) order. The whole result is
   never serialized. `pruned_states` is read from the struct. Field accessors are explicit, with unit
   tests. One checks, for a populated result, that each accessor equals the corresponding entry of
   `serde_json::to_value` of the whole struct. Another checks that the accessor name lists equal the
   key sets that struct serializes to, so a new field cannot be missed silently.
3. Without dotted entries, nothing changes: same code path, same bytes.
4. The adapter's default `cell_result_fields` becomes `["mode", "ledger", "steps.photon_source.groups"]`.
   Its parser reads the same keys, and its tests are updated to the lean records.
5. `docs/SPEC.md` documents the dotted form.

## Gates

Reference: master `e546292` release `actinv` (archived as `target/p90/ref_actinv`). Checker
`controls/check_p90.py`, under the 6 GB cgroup cap. The CI replay runs before the timed runs.

- **G0:** the protocol hash is registered before the change is written.
- **G1 static:** fmt; clippy `-p actinv-core -p actinv-data -p actinv-cli --all-targets -D warnings`;
  `cargo test --release -p actinv-core -p actinv-data`, including the accessor-consistency tests and
  dotted-path tests. Those tests cover pruning, absent paths, invalid paths, and plain `steps`
  combined with a dotted entry.
- **G2 bitwise, unchanged route:** mesh output bytes, with the footer compared without timing keys.
  This covers `fe_coupled`, `fe_p21like` and `ss316_r2s` at 1 and 3 threads, the `group_workloads`
  variant, the resumed run, and a top-level `cell_result_fields` variant (`["steps", "ledger"]`),
  each against the reference on the same spec.
- **G3 subtree equality:** `fe_coupled` and `ss316_r2s` (1 thread) are run with
  `["mode", "ledger", "pruned_states", "steps.photon_source.groups", "steps.heat_W_per_g", "steps.t_s"]`.
  For every cell, the lean record's selected subtrees must equal, as parsed JSON, the same subtrees of
  the reference's full-output record for that cell. No other keys may be present besides `step`, and
  the header and footer must match the reference's apart from timing keys and the spec fingerprint.
- **G4 adapter:** `contrib/openmc_r2s` unit tests pass with the new default.
- **G5 CI replay:** every step of the local CI replay exits 0.
- **G6 adoption threshold (pre-registered):** `ss316_r2s` at 1 thread, median wall time over
  **5** alternating repetitions. The candidate runs with the adapter's new default selection; the
  reference runs with the adapter's current selection `["mode", "steps", "ledger"]`. The candidate
  must be at least **1.25×** faster. The expected gain is about 1.4×: most of the 26–58 ms of
  serialization, plus the output write. Reported only: output bytes ratio, and 3 threads.

Merge only if G0–G6 all pass. A FAIL stands; the threshold is not lowered afterwards.
