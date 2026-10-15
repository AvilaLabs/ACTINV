# P127 — Full pathways and routes analysis with exact chain integration and a closure diagnostic

Registered 2026-10-15, before any P127 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: PATH/ROUTES, LOOPS, generic pathways, UNCERTAINTY 2 pathway
output.

## Change under test

The opt-in `pathways` section (`targets`, `sources`, `max_depth`, `path_floor`, `loop_floor`, `max_paths`,
`rank_by`, `top_targets`, `by_step`) produces `RunResult.pathway_routes`. The pipeline is:

1. The per-step graph is the very triplet set the solver exponentiates.
2. A depth-first route search uses a conservative upper-bound prune.
3. Each route is integrated exactly as a linear-chain ODE through every schedule step, with each node's full removal
   rate on the diagonal. The integrator is dense scaling-and-squaring Padé-13 with the Al-Mohy–Higham refresh for
   triangular matrices.
4. Loops are reported against their loop-erased base path.
5. Generic pathways are grouped by edge-type sequence.
6. Per target, a closure is reported: total atoms, Σ routes, residual and explained fraction. FISPACT-II reports
   pathways without an accounting of what they leave unexplained; the closure supplies it.

The existing first-product `pathways` output is unchanged.

## Gates

Checker: `controls/check_p127.py`, run by the coordinator under the 6 GiB cgroup. Reference: release build of
`2f3daa4`. Candidate: release build of the implementation commit.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P127 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the `pathways.rs` tests:
  - analytic exponentials and a stiff chain at 1e-12
  - linear-chain closure
  - branching
  - equal rates
  - loops
  - reservoir across steps
  - caps
- **G2, unchanged by default.** `examples/fns_fe_5min.json` is byte-identical apart from `ms`.
- **G3, independent route recomputation.** For FNS Fe (targets Mn56, Mn54, Cr51 and Fe55), the checker recomputes
  each reported route's atoms with an independent Python integrator (mpmath or scipy `expm_multiply`) from the
  ledger's per-step rates and flux multipliers, within 1e-8 relative.
- **G4, closure.**
  - For each target, Σ routes + residual = total within 1e-12 relative.
  - The total equals the target's atoms in the result inventory within 1e-12 relative.
  - At `path_floor` 1e-6 the explained fraction is ≥ 0.999 for every target above 1e-6 of total atoms.
- **G5, trace and coupled.** Route values agree between modes within the documented trace/coupled tolerance.
- **G6, report-only comparison with FISPACT-II.** The dominant pathways of the FNS Fe 5-minute `.out` file, where
  printed, are tabulated against ACTINV's routes (libraries differ). There is no pass threshold.
- **G7.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P127-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No pathway uncertainty bands (planned later). Routes below about 1e-10 of their chain's largest term are noise.
