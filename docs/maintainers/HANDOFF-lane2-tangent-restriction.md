# Handoff — Lane 2: covered-row-restricted tangent construction

Status 2026-10-15: committed; Fe gate 7/7 re-run on the committed code. U-235 end-to-end still open.

Date: 2026-09-28. Written mid-implementation: code changes are **uncommitted** in
the working tree (4 modified files + 2 untracked). Everything below is what a
continuing agent needs; nothing is committed yet.

## Goal

Make first-order uncertainty (local sensitivity / `first_order_comparison`)
tractable on fissile chains. Before this work the U-235 fo leg exceeded 3.1 GB
in tangent construction and >45 min, then died.

## Working tree state

Modified:
- `crates/actinv-core/src/run.rs` (+249) — restriction machinery, sparse
  contraction, zero-variance skip, profiler wiring, `ACTINV_TRACE` markers
- `crates/actinv-core/src/spec.rs` (+9) — `restrict_tangents_to_covered` field
  on `UncertaintyOptions` (`#[serde(skip)]` — internal only, not spec-visible,
  no hash/output effect on normal runs)
- `crates/actinv-core/src/study.rs` (+44) — sets the flag on the internal fo
  comparison spec; copies `tangents_integrated`/`active_parameters` from the
  run ledger into study-level `local_vs_nonlinear`
- `crates/actinv-core/src/uncertainty.rs` (+34) — `propagated_variance_pairs`:
  nonzero-pair quadratic-form iteration, bitwise-identical to dense

Untracked:
- `controls/g1_d2_tangent_restriction.py` — dense-vs-restricted equivalence
  gate on the Fe chain (**deletes its output dirs before each leg** — study
  resume would otherwise reuse stale records; keep that `shutil.rmtree`)
- `results/g1_d2_tangent_restriction.json` — last gate verdict

## What the code does now

1. **Restriction** (`restrict` in the uncertainty-runtime block, `run.rs`
   ~3470): `tangents`/`directions`/`parameter_spectrum` become *compact* over
   propagated params only; `propagated_parameter_positions` maps compact →
   dense. `parameters`/`covered_parameter_positions` stay dense (emission +
   covariance indexing unchanged). `build_step_uncertainty` scatters compact
   values back into a dense vector; unpropagated positions emit 0.
2. **Covered-only**: uncovered XS rows get no tangent (they never enter the
   covered contraction anyway — exact).
3. **Zero-variance skip** (`zero_variance_pairs`, ~3484): a covered param with
   `covariance_barn2[diag] == 0` has an identically-zero covariance row (PSD →
   Σ_ii=0 ⇒ Σ_i*=0) — skipped exactly, no approximation.
4. **Sparse contraction** (~3668): under restrict, `covariance_barn2` is
   scanned once for nonzero entries → `covariance_pairs` (row-major order),
   the dense matrix is dropped, and `propagated_variance` dispatches to
   `propagated_variance_pairs`. Row-major traversal is bitwise-identical to the
   dense loop's nonzero terms.
5. **Compact emission**: `sensitivities` reports only propagated positions;
   `tangents_integrated` + `active_parameters` land in the run ledger's
   `uncertainty` object and (via study.rs) in `local_vs_nonlinear`.
6. **Guards**: restricted mode errors out on `build_design`/`build_voi`/
   `build_isomer` — those index `sensitivities` positionally and would
   mis-index a compact vector.
7. **Env vars**: `ACTINV_DENSE_COMPARISON_TANGENTS=1` forces the dense fo leg
   (the gate uses it); `ACTINV_TRACE=1` prints phase markers
   (`net_done`, `cov`, `step={si} tangent_step_*`, `unc_emit_*`, `solve_done`);
   `PreparedRun::run`/`prepare` now emit profiles via `ACTINV_P14_CORE_PROFILE`.

## Verified

- `controls/g1_d2_tangent_restriction.py` → **7/7 pass** on Fe: bitwise-equal
  first-order stds dense vs restricted, sampled stds/ratios equal, restricted
  metadata present on restricted leg only, `tangents_integrated <
  active_parameters`. **Re-run it — code changed after that run.**
- U-235 study (release binary): all sample/attr legs complete; fo leg reached
  `unc_emit` — covered=25,900, zero_diag=6,251, propagated tangents=**19,649**
  of 37,057 params. Memory bounded ~3.5 GB RSS under the 6 G cap.

## NOT yet verified

- **U-235 fo leg end-to-end.** Killed at user's request mid-`unc_emit` (RAM
  pressure) after ~40 min. `actinv study run` resumes: every leg except the fo
  comparison persists, so only the fo leg re-runs — expect ~50–70 min in
  release (step 0 solve ~22 min + emit ~15 min + step 1 same shape).
- Fe gate against the *current* binary (code changed after the 7/7 run —
  pairs path + zero-diag skip landed after).
- Release-vs-debug confirmation of identical restricted stds (sanity).

## The real performance anatomy (U-235)

| Phase | Cost driver |
|---|---|
| Prepare | ~19 s — cov file read/parse/validation (136 MB npz) |
| Tangent solve | 19,649 tangents × 8 poles × `solve_refined` ≈ 8 ms each (release) ≈ 20–22 min/step — **the floor** |
| `unc_emit` | nnz(Σ) pairs × 1,210 responses — dense-ish MF=33 blocks mean nnz is large (~10–15 min observed) |
| Sensitivity serialization | now covered-rows only (was ~18 M records/leg → this was the first pathology) |

The remaining solve cost is **intrinsic to forward mode** (one backsolve per
parameter per pole). The genuine fix for fissile chains is **adjoint-mode
sensitivity**: propagate response weights backward — 1,210 responses ≪ 19.6k
params → ~16× fewer solves. Park it as its own lane; do not fold into this work.

## Commands

```bash
# Fe equivalence gate (bounded, ~2 min, uses its own scratch dir)
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% \
  -- env TMPDIR=$PWD/target/preflight-tmp RAYON_NUM_THREADS=2 \
  python3 controls/g1_d2_tangent_restriction.py

# U-235 resume (release binary; only the fo leg re-runs)
systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% \
  -- env TMPDIR=$PWD/target/preflight-tmp RAYON_NUM_THREADS=2 ACTINV_TRACE=1 \
  target/release/actinv study run target/restrict-test/u235_study.json target/restrict-test/u235_out

# Rebuild release (required if run.rs changes)
systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% \
  -- env CARGO_BUILD_JOBS=2 RAYON_NUM_THREADS=2 TMPDIR=$PWD/target/preflight-tmp \
  cargo build --release -p actinv-cli
```

Gate binary note: `g1_d2_tangent_restriction.py` invokes
`target/debug/actinv` — rebuild `cargo build -p actinv-cli` before it (debug).

## Gotchas

- **Study resume is aggressive** — it reuses completed per-leg records. The
  gate's `shutil.rmtree(out_dir)` per leg is mandatory; otherwise stale-binary
  records silently pass/fail.
- `cov_entries=0` in `net_done` trace is `covariance_barn2.len()` — emptied by
  design under restrict; `covariance_pairs` isn't printed (mislabel, harmless).
- `params=37057` counts ALL spec params (XS + decay + yield); XS covered =
  25,900 on the U-235 chain — the covered fraction is ~70%, inverse of Fe.
- Do NOT remove the `position_of`/`covered_parameter_positions` dense
  bookkeeping — positional consumers depend on it (guarded, but only the 3
  listed combos are hard-rejected).
- `ACTINV_DENSE_COMPARISON_TANGENTS` must be unset for restricted runs; the
  gate sets it per-leg correctly.
- Debug builds are ~5–10× slower on the tangent solve; use release for U-235.

## Roadmap / claim status

- P43 named condition "not evaluated on fissile cases pending
  covered-row-restricted tangent solve" — restriction is implemented and the
  mechanism is gated on Fe; the fissile-case **completion** is still the open
  proof (see above). Roadmap update should say: completed-under-cap on U-235
  pending the resume run; fissile fo comparison is minutes-scale, not instant;
  adjoint path is the follow-up lane.
- Lane 1 (banded-corpus model-remainder) shipped in commit `25fdb8d` — done.
- Lane 3 (inverse history) already shipped as P55 `reverse-qualified`.
- P73 prescreen / P64 `decide` already shipped — Roadmap ledger may still mark
  them pending; update wording only after confirming gates.
