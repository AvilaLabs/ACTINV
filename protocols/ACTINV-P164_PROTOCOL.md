# ACTINV-P164 — the measurement-design block solve rejects singular covariance blocks

## Finding

The uncertainty layer's measurement-design report (`design.top_reactions`, built by `build_design` in
`crates/actinv-core/src/run.rs`) groups the covered cross-section parameters by target nuclide and computes the
variance a perfect measurement of the whole block would remove, `(Σ·s)_Bᵀ Σ_BB⁻¹ (Σ·s)_B`. The solve was a Cholesky
factorisation, which requires a positive-definite block. MF=33 covariance blocks are routinely only positive
semi-definite (fully correlated energy groups, MT sums), so the factorisation fails and the block is emitted with
`status = "ill_conditioned"` and `variance_reduction = null`. Nothing is wrong with the data: the single-parameter
`top_parameters` rank correctly.

On `examples/decide/run_spec.json` (1 g of iron, FNS 14 MeV), `actinv decide examples/decide/decision.json` reports
every `top_reactions` block as `ill_conditioned`, Fe-56 included. The list was then sorted on
`variance_reduction.unwrap_or(0.0)`, which ranks a null as zero; the sort is stable, so the blocks stay in target-ZA
order and Ca-43 to Ca-47 (not in the material; they reach the response through small sensitivities from multi-step
chains) head the list. The committed tungsten decision `results/p64_decision_w.json` shows the same: two
`ill_conditioned` Hf-177 entries follow three computed blocks.

## Change

1. **Pseudo-inverse block solve.** The Cholesky solve is replaced by a symmetric eigendecomposition of the block
   (cyclic Jacobi sweeps run to round-off) and a Moore–Penrose pseudo-inverse:
   `reduction = Σ_k (u_kᵀ b)² / λ_k` over modes with `λ_k > n · 1e-12 · λ_max`, where `b = (Σ·s)_B`.
   - The floor: block entries are products of relative covariances and cross sections in f64 (round-off about 1e-16),
     so exactly singular blocks show null eigenvalues near 1e-16·λ_max; 1e-12 leaves four orders of headroom and the
     factor n follows the usual rank-detection scale.
   - Range check: `b` must lie in the span of the kept modes. If the part of `b` outside it exceeds 1e-6·‖b‖ the block
     is inconsistent. An eigenvalue below `-n·1e-12·λ_max` means the block is not positive semi-definite. Either case
     keeps `status = "ill_conditioned"` and a null reduction. For a positive semi-definite joint covariance `b` lies in
     the block's range, so this is a defect signal, not a normal outcome.
   - A zero `b` (or a zero block with zero `b`) is a computed reduction of 0, status `emitted`; no row is dropped.
   - The existing round-off clamp (`clamp_roundoff`) still applies.
2. **Ordering guard.** `top_reactions` is ordered with computed reductions first, largest first, then blocks without
   one in their previous relative order with `status` kept. A null is no longer ranked as zero.

On positive-definite blocks the new value equals the Cholesky value to rounding (unit-tested to 1e-12 relative). Blocks
the old path computed keep their value to that tolerance; only blocks it rejected change. No field, status string,
`top_parameters` entry, total, band, verdict or nominal result changes.

## Reference, candidate and corpus

- **Reference:** `actinv` built from master at the P164 branch point (sha256 recorded by the checker).
- **Candidate:** the P164 branch.
- **Corpus:** `actinv decide examples/decide/decision.json` run by both binaries from the checker's working directory
  (the standard data and the covariance bundle installed).

## Gates

- **G0.** This protocol's SHA-256 (and any amendment) is in `protocols/protocol_hash.txt`.
- **G1.** `cargo fmt --check`, `cargo check --workspace --tests` and `cargo clippy --workspace --all-targets -D warnings`
  are clean, and the workspace tests pass, including
  `p164_computed_reductions_outrank_uncomputed_blocks`, `p164_uncomputed_blocks_keep_their_relative_order`, and the
  analytic block tests: positive-definite agreement with Cholesky (1e-12), a singular rank-1 and a rank-2 block with
  `b = Σ_B s` giving `sᵀΣ_B s`, and out-of-range or non-PSD input reported as not computed.
  The analytic block tests are named in the G1 log by the checker's G1 list.
- **G2.** The reference and candidate decision outputs are identical, apart from the timing keys `ms`, `elapsed_ms`,
  `wall_time_s`, `cells_per_s` (any depth), each `measurements.*.top_reactions` array and `run.result_sha256`. That
  field hashes the whole run result, measurement design included, so it moves with the fix; in its place both
  binaries run `actinv run examples/decide/run_spec.json`, and the two run results must be identical apart from the
  timing keys and every `top_reactions` array. Every `top_parameters` array (bit for bit), the verdict, the
  constraint values and the completeness block match.
- **G3.** In every candidate `top_reactions`: every computed entry precedes every `ill_conditioned` entry; computed
  entries are in descending `variance_reduction`; a computed entry equals the reference's entry for the same
  (target_za, target_liso) wherever the reference computed one; every `ill_conditioned` candidate entry appears,
  identical, in the reference.
- **G4 (the corpus exercises the defect).** The reference's `top_reactions` holds an `ill_conditioned` entry and no
  computed block for a target element in the material.
- **G5 (the fix computes the block).** The candidate has at least one computed block for a target element in the
  material (Fe), and every computed block satisfies, to 1e-9 relative: finite; at least the largest
  `variance_reduction` among `top_parameters` entries of the same target (conditioning on a superset cannot remove
  less); at most `total_propagated_variance`; `share_of_total = variance_reduction / total_propagated_variance`.
  `top_parameters` holds only the top entries, so the first inequality is checked against that visible maximum, which
  is a lower bound on the true one.
- **G6.** CI green on the pushed evidence commit.

Recorded, not gated: target elements listed that are not in the material, and reference `ill_conditioned` entries
absent from the candidate.

## Verdict

P164-PASS if G0–G6 hold. One repair round, registered as an amendment first.

## Not claimed

The identity "with no cross-target correlation, the block reduction equals `s_Bᵀ Σ_B s_B`" is verified by unit test
on analytic blocks only. The emitted JSON carries `variance_share` for the top-N parameters only, so it cannot be
recomputed per block from a decision output, and cross-target correlation in the bundle is not excluded here.
The floor and range thresholds are chosen on round-off arguments, not derived error bounds. Committed result files
produced before this change (`results/p64_decision_w.json`, `results/g3_p60_demo.json`, `results/verdict_p60.json`)
keep the old output; they are historical records and are not regenerated. Why target elements absent from the
material have covariance rows is a property of the sensitivities and the bundle and is not changed here. The GUI
already shows only `emitted` blocks, so with computed blocks it will now show "all of Fe-56 ... together" lines.
