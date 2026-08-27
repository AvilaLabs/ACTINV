# ACTINV P10 Amendment Q — Python extension version coherence

**Date:** 2026-08-27. **Trigger:** The final CPython 3.14 wheel build produced a correctly named `actinv-0.5.0` wheel
but Cargo identified the excluded `actinv-py` extension crate as `0.2.0`. The P8 close had advanced the workspace and
Python packaging metadata while leaving `python/Cargo.toml` at the prior milestone. Amendment P's first checker repair
inspected the workspace and `pyproject.toml`, so it did not expose the separately rooted Rust crate.

## Frozen repair

1. Advance `python/Cargo.toml` and its lockfile entry to the same `0.5.0` as `pyproject.toml` and all workspace crates.
2. The P6 version/licence checker runs Cargo metadata for both the root workspace and `python/Cargo.toml`; every Rust
   package plus the Python project must share one valid three-component version at or above `0.1.0` and the dual
   licence. A wheel build must report the same crate and wheel version.
3. Rebuild the CPython 3.14 wheel, rerun P6, G1, the CI subset, clean-clone control and Rust quality gates before the
   P10 verdict. No runtime, physics, data, ownership, concurrency or acceptance-tolerance behavior changes.

This is a release-metadata coherence repair found before the P10 close. A successful close remains
**P10-CONDITIONAL**.
