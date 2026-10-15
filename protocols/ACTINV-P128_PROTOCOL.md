# P128 — Triton and helion projectiles; schedule-level activation-library switch

Registered 2026-10-15, before any P128 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: PROJECTILE 6/7, multi-library schedules.

## Change under test

1. `Projectile::Triton` and `Projectile::Helion` (NSUB 10030/20030; ZA (1,3)/(2,3)). They go through the same
   charged-particle builder path and use the CCFE-162 structure at 0 K.
2. `schedule[].library` (+ `projectile`): a sticky library override.
   - One decay chain is shared, so the state vector crosses a switch unchanged and only the collapsed rate
     triplets change.
   - Per-library hashes are recorded in the ledger and certificate.
   - Combinations that cannot be applied correctly are refused.

## Gates

Checker: `controls/check_p128.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P128 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including:
  - the projectile, builder and spec tests
  - the synthetic two-library chain test against its analytic solution
- **G2, unchanged by default.** `examples/fns_fe_5min.json` is byte-identical apart from `ms`.
- **G3, switch equivalence on real data.**
  1. Run 1: FNS Fe as two identical irradiation steps on the 709-group TENDL-2025 library.
  2. Run 2: the same, with step 2 naming the same library explicitly as an override. It must be byte-identical to
     run 1 apart from `ms`, the ledger `step_libraries` block and the certificate's `inputs.step_libraries`.
  3. Run 3: step 2 switches to a second real neutron library built from another evaluation (FENDL-3.2c or
     TENDL-2023, for Fe only). The checker recomputes step 2 independently from run 3's step-1 inventory and the
     second library's one-group rates (`inspect xs`), within 1e-8 relative on nuclides above 1e-12 of the total.
- **G4, triton/helion.** No TENDL t/h sublibrary is installed, so this gate is reported as data-limited: a
  synthetic NSUB 10030 tape builds and its residuals match hand arithmetic (unit tests). It is upgraded when real
  data is installed.
- **G5.** CI green on the pushed commit.

A gate that fails is recorded as P128-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No validation of triton or helion activation against FISPACT-II or measurement.
