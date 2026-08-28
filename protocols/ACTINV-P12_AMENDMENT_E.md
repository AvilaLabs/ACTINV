# ACTINV P12 — Amendment E: non-circular closure inventory

**Recorded:** 2026-08-27, before G6 closure. **Scope:** closure-control composition only.

## Discovery

The first G6 fixed-point attempt exposed a circular dependency. `MANIFEST.sha256` included
`results/g6_p12_complete.json` and `results/verdict_p12.json`, while both derived reports recorded whether that same
manifest reproduced. Rewriting either report necessarily changed its manifest hash, so a truthful byte-stable state
could not exist even when every underlying gate, file, commit and CI check passed.

## Frozen repair

1. The close manifest inventories every tracked or untracked repository source/evidence file except itself and the
   two reports derived from it: `results/g6_p12_complete.json` and `results/verdict_p12.json`.
2. Both the G6 producer and independent checker require that exact three-path exclusion set, exact inventory,
   per-file hashes and byte-identical regeneration. No other path may be omitted.
3. The Git closure commit binds the manifest and both derived reports in one immutable tree. Re-running G6, the
   checker and manifest generation after that commit must leave the working tree clean.
4. Add a regression assertion for the exact exclusion set and rerun the P12 checker tests, G1--G5 re-derivation,
   dependency/release controls and exact Rust quality gates before closure is pushed.

This repair changes no product source, scientific input, result, tolerance, package interface or public-release
authority. It makes the frozen G6 reproducibility requirement satisfiable without weakening file coverage. The
eventual verdict remains **P12-CONDITIONAL**.
