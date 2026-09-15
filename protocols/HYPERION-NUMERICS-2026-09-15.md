# HYPERION numerical repair protocol

Frozen before new diagnostic executions or numerical production changes. User
requested fixing ACTINV's observed discrepancies. Preserve all previous failing
inventories, control receipts, archives and numerical thresholds. No release,
external message or public repository write authorized.

## Diagnosis

Use analytical one-way parent/daughter chains with large stable backgrounds,
including absent parents, forced long-lived-parent equilibrium and permuted
state order. Exercise CRAM16/48, dimensionless stiffness 1e-6 through 1e12 and
population scales 1 through 1e24. Compare custom LU/CRAM, OpenMC/SciPy and exact
Bateman/invariant solutions. Record failing pre-fix cases and linear-system
residuals/pivots to distinguish matrix assembly, factorization, solve and rational
approximation errors. Add a minimal Rust regression before the numerical fix.

## Repair boundaries

Correct the numerical cause rather than deleting small populations, replacing
outputs with reference values, adjusting physical inputs or relaxing acceptance
bands. Apply the same sound solve behavior to scalar, multiple-RHS and tangent
paths. Keep any change to the generic LU's pivoting/accuracy contract explicit,
with general linear-system controls and error handling. Do not claim the CRAM
alpha0 scale bounds floating-point solve errors; correct misleading numerical
floor documentation while preserving serialized compatibility where practical.

## Acceptance

- Minimal analytic invariant: no spurious absent-parent population above
  1e-6 atoms/g; stable-state relative error <=1e-12. General analytic populated
  chains: <=1e-10 relative or 1e-6 atoms/g where the selected CRAM approximation
  permits it; report the declared CRAM16 approximation floor separately.
- General complex linear systems: independently computed componentwise scaled
  backward error <=1e-12 for well-conditioned fixtures, including a matrix that
  genuinely needs pivoting. Include stiffness/background sweeps and state-order
  permutations; check batched and tangent paths and existing regressions.
- Fresh repaired-binary runs for all 15 updated upstream cases, prior ten N50
  shared-spectrum fast/thermal cases and the two joint-composition control cases.
  Compare to frozen raw independent controls and reference tables without
  changing 1% or 1 atom/g inventory, 1e-8-or-1e-12 combined-index and 1e-4 dose
  tolerances. Re-evaluate 98 updated, 167 prior and 29 joint failed rows; do not
  call the issue fixed while known outliers remain unexplained or fail.
- Rerun independent matrix/endpoints as needed, using recorded public data and
  a solver independent of the repaired production implementation. Document that
  OpenMC controls share CRAM's approximation, not identical linear algebra.
- Run cargo fmt/check/clippy/test workspace/all-targets/all-features before
  claiming a Rust checkpoint complete. Review process-launching test paths first,
  prohibit recursive test executables, bound/reap children and keep global job
  timeouts. Use a disk-backed isolated build/snapshot when required to avoid
  another agent's builds; no unrelated worktree or process changes.

All jobs sequential in verified systemd cgroups: MemoryMax=6G, MemorySwapMax=0,
TasksMax=128, CPUQuota=200%, CARGO_BUILD_JOBS=1, RUST_TEST_THREADS=1,
RAYON_NUM_THREADS=2 and disk-backed TMPDIR. Stop rather than run unlimited.
Record shared-workstation contention. Keep before/after binaries and hashes;
append diagnosis, changes, controls, failures and remaining limits to the ledger.
