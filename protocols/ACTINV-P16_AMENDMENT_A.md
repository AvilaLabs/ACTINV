# ACTINV P16 Amendment A — make the frozen opening commit available in CI

**Date:** 2026-08-28. **Trigger:** The first pushed P16 source/evidence checkpoint,
`740f435a5569e56c3ca3d27a9e302113aff612f5`, passed the exact workspace Rust gates, release build and analytic unit
probe in GitHub Actions run `33223231861`. The new P16 boundary step then stopped before compiling its consumer
fixtures because the default one-commit checkout did not contain signed opening commit
`0332779401363d2f39722efe7a0b7218afcfb270`.

P16 deliberately compares the candidate dependency manifests and source additions with that exact `v1.0.1` opening
commit. The local control had the full repository history, while the clean CI checkout did not. No product code,
scientific relation, compatibility comparison, performance result, compiler gate or existing control failed.

## Frozen repair

1. The canonical workflow checks out complete Git history with `fetch-depth: 0`, making the already frozen opening
   commit available to the dependency and source-difference controls.
2. The comparison remains against the exact signed commit. No expected hash, type boundary, tolerance, normalization,
   runtime ceiling, fixture, or evidence value changes.
3. Re-run the complete workflow from a new commit. The P16 control must perform the opening comparison and pass on
   the clean runner, and every retained workflow step must then complete.

This is a CI evidence-availability repair only. Under the frozen closure interpretation, an otherwise passing phase
therefore closes **P16-CONDITIONAL**, not `P16-PASS`.
