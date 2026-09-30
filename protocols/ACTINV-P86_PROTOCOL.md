# ACTINV-P86 — P85 with the shared-prepare wiring kept

Date: 2026-09-29. Status: **frozen before the changed code is built.**

## Why

P85 (`169269e7…`) passed G0, G1, G2, G3 and G5 (`results/p85_verdict.json`; SS316 warm miss 2489 →
145 ms, 17.12×). It **failed G4**: the local CI replay step `p16` exited 1. The P16 control
`controls/g1_p16_quantities.py` checks, by a fixed source string, that `PreparedRun::prepare` goes
through the shared profiled preparation: `Self::prepare_profiled(spec, &physical, &mut profiler)`. P85
added a `groupwise` argument to `prepare_profiled`, so the call became
`Self::prepare_profiled(spec, &physical, &mut profiler, false)`, and `wiring.shared_prepare` read
false. Every other replay step exited 0. The P85 FAIL stands, and P85 is not merged as built.

The P16 control is not changed.

## Change under test

This is the P85 change with one refactor. `prepare_profiled(spec, physical, profiler)` keeps its
three-argument signature and forwards to a new private
`prepare_profiled_with(spec, physical, profiler, groupwise)`, which holds the P85 body. The P85
cache path calls `prepare_profiled_with(..., groupwise)`. Nothing else changes.

## Gates

Reference evidence: the P85 candidate build. `actinv` is `38ecc4e4…` and `cache_probe` is
`7948711e…` (archived in `target/p85/cand_*`), and its run log is `target/p85/run_log.json`.

- **G0:** the protocol hash is registered before the first build of this change.
- **G1 static:** the same commands as P85 G1 (fmt, clippy, `cargo test --release -p actinv-core -p
  actinv-data`), including `collapsed_artifact_equals_groupwise_collapse_bit_for_bit`.
- **G2 evidence transfer:** the release `actinv` and `cache_probe` built from this change are
  byte-identical to the P85 candidate. In that case P85's G2, G3 and G5 evidence applies to this
  build unchanged. If either binary differs, `controls/check_p85.py run` and `check` are rerun
  against the new build, and P85's G2, G3 and G5 must all pass again, at the same thresholds.
- **G3 CI replay:** every step of the local CI replay exits 0, including `p16`.

Merge only if G0–G3 all pass. Results are recorded in `results/p86_verdict.json` by
`controls/check_p86.py`.
